"""planner L3 回写并发安全。

RED：当前 planner 直接 `METRIC_DICT[metric] = m`（无锁），并发 miss → 重复 L3 RAG +
互相覆盖。期望的修复 API：`_writeback_locked(metric, m)`（加锁 + first-write-wins）与
`_resolve_metric_locked(metric)`（加锁读 L1/L2）。
"""

from __future__ import annotations

import asyncio

import pytest

from app.conf.metric_dict import METRIC_DICT
from app.graph.nodes import planner as planner_mod

_NEW_METRIC = "测试指标_并发回写"

_DEF = {"type": "atomic", "table": "fact_order", "field": "amount", "agg": "SUM",
        "filter": None, "formula": None, "base": []}


@pytest.fixture
def clean_dict():
    """测试前后恢复 METRIC_DICT 原状，避免污染全局字典。"""
    saved = dict(METRIC_DICT)
    METRIC_DICT.clear()
    yield
    METRIC_DICT.clear()
    METRIC_DICT.update(saved)


def test_writeback_concurrent_single_write(clean_dict):
    """并发回写同一新指标：只真正写入一次（first-write-wins），且最终值一致。"""
    async def run():
        return await asyncio.gather(*[
            asyncio.to_thread(planner_mod._writeback_locked, _NEW_METRIC, _DEF)
            for _ in range(20)
        ])

    results = asyncio.run(run())
    assert _NEW_METRIC in METRIC_DICT  # 只存了一条
    assert len(results) == 20
    # 所有调用者拿到同一最终定义（后到的不覆盖先到的）
    finals = {id(r[0]) for r in results}
    assert len(finals) == 1


def test_lookup_after_writeback_hits_l1(clean_dict):
    """回写后 L1 直接命中（0 token 路径，无需再走 RAG）。"""
    planner_mod._writeback_locked(_NEW_METRIC, _DEF)
    resolved = planner_mod._resolve_metric_locked(_NEW_METRIC)
    assert resolved is not None
    assert resolved[1] == _NEW_METRIC  # canonical 即新指标名


_DEF_OTHER = {"type": "atomic", "table": "fact_refund", "field": "amount", "agg": "SUM",
              "filter": "status='APPROVED'", "formula": None, "base": []}


def test_writeback_different_defs_first_wins(clean_dict):
    """并发回写两个不同定义：first-write-wins——只存先到者，后到不覆盖、字典不膨胀。"""
    async def run():
        return await asyncio.gather(*[
            asyncio.to_thread(planner_mod._writeback_locked, _NEW_METRIC, _DEF),
            asyncio.to_thread(planner_mod._writeback_locked, _NEW_METRIC, _DEF_OTHER),
        ])

    results = asyncio.run(run())
    assert len(METRIC_DICT) == 1                      # 只写了一条，不互相覆盖
    stored = METRIC_DICT[_NEW_METRIC]
    assert stored in (_DEF, _DEF_OTHER)               # 最终是二者之一
    # 所有调用者拿到的最终定义与字典一致（后到者复用先到者结果）
    assert all(r[0] == stored for r in results)


@pytest.mark.asyncio
async def test_planner_node_l3_writeback(clean_dict, monkeypatch):
    """planner 主流程：L1/L2 miss → L3 RAG 命中 → 回写字典 + 生成 SQL 计划（核心修复路径）。"""
    monkeypatch.setattr(planner_mod, "_resolve_metric_via_rag", _async_def(_DEF))
    state = {"task": {
        "metrics": [_NEW_METRIC],
        "time_range": {"start": "2026-10-05", "end": "2026-10-11"},
        "dims": [], "compare": None,
    }}
    out = await planner_mod.planner(state)
    assert _NEW_METRIC in METRIC_DICT                    # L3 结果已回写
    assert out["plan"] and out["plan"][0]["type"] == "sql"  # 回写后正常拼出 SQL 计划
    assert not out["errors"]


@pytest.mark.asyncio
async def test_planner_node_l3_miss_errors(clean_dict, monkeypatch):
    """planner 主流程：L3 也未命中 → errors 记录"口径未定义"，不猜不拼 SQL。"""
    monkeypatch.setattr(planner_mod, "_resolve_metric_via_rag", _async_def(None))
    state = {"task": {
        "metrics": [_NEW_METRIC],
        "time_range": {"start": "2026-10-05", "end": "2026-10-11"},
        "dims": [], "compare": None,
    }}
    out = await planner_mod.planner(state)
    assert out["plan"] == []
    assert any("口径未定义" in e["msg"] for e in out["errors"])
    assert _NEW_METRIC not in METRIC_DICT  # 未命中不落库


def _async_def(value):
    """构造一个返回固定值的异步 _resolve_metric_via_rag 替身。"""
    async def fake(metric):
        return value
    return fake
