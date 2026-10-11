"""SQL 执行超时：死 SQL 不能挂死请求。

RED：当前 `_run_sql` 的 conn.execute 无超时，慢查询/死锁会无限等待。
期望：_run_sql 带超时（asyncio.wait_for），超时 → _exec_one 单条降级（degraded + timeout）。
"""

from __future__ import annotations

import asyncio

from app.clients import mysql_client_manager as mcm
from app.graph.nodes import query_executor as qe


class _SlowConn:
    """模拟一条永不返回的 SQL 连接（execute 挂起 5 秒）。"""

    async def execute(self, *args, **kwargs):
        await asyncio.sleep(5)
        raise AssertionError("不应执行到这里（超时后不会再等）")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def test_exec_one_timeout_degraded(monkeypatch):
    """SQL 超时 → 单条降级（degraded=True + error 含 timeout），不中断其他条。"""
    saved_timeout = qe._SQL_TIMEOUT_SECONDS
    qe._SQL_TIMEOUT_SECONDS = 0.1  # 缩短到 100ms，测试不等真实 5s

    fake_engine = type("FakeEngine", (), {"connect": lambda self: _SlowConn()})()
    monkeypatch.setattr(mcm, "engine", fake_engine)
    try:
        r = asyncio.run(qe._exec_one({"query": "SELECT 1", "metric": "m1", "period": "current"}))
    finally:
        qe._SQL_TIMEOUT_SECONDS = saved_timeout

    assert r.degraded is True
    assert "timeout" in (r.error or "").lower()


class _OkConn:
    """模拟正常返回的 SQL 连接。"""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *args, **kwargs):
        return _FakeResult(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _FakeResult:
    """模拟 sqlalchemy Result：mappings() 返回可迭代的 dict 行。"""

    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return iter(self._rows)


def test_exec_one_normal_path(monkeypatch):
    """正常路径：SQL 返回行 → degraded=False + rows 透传（主链路核心行为）。"""
    fake_engine = type("FakeEngine", (), {"connect": lambda self: _OkConn([{"gmv": 1100}])})()
    monkeypatch.setattr(mcm, "engine", fake_engine)
    r = asyncio.run(qe._exec_one({"query": "SELECT SUM(amount) AS gmv FROM fact_order",
                                  "metric": "gmv", "period": "current"}))
    assert r.degraded is False
    assert r.rows == [{"gmv": 1100}]
    assert r.confidence == 1.0


def test_exec_one_rejects_non_select(monkeypatch):
    """只读防护：非 SELECT 直接降级，绝不让 DELETE/INSERT 进入执行。"""
    fake_engine = type("FakeEngine", (), {"connect": lambda self: _OkConn([])})()
    monkeypatch.setattr(mcm, "engine", fake_engine)
    r = asyncio.run(qe._exec_one({"query": "DELETE FROM fact_order",
                                  "metric": "gmv", "period": "current"}))
    assert r.degraded is True
    assert "SELECT" in (r.error or "")
