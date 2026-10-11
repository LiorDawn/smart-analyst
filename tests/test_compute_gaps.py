"""compute 分组缺口不静默：派生/跨期指标分组时，缺某维度键的数据必须记录。

RED：当前派生分组 `if row is None: continue` 静默跳过；跨期只取交集（差集静默丢失）。
期望：输出带 gaps 字段，列出缺失的维度键与缺失方。
"""

from __future__ import annotations

from app.graph.nodes import compute as compute_mod


def _make_result(metric, period, target, rows, source_ref="sql#abc"):
    return {
        "metric": metric, "period": period, "target_metric": target, "rows": rows,
        "source_ref": source_ref, "confidence": 1.0, "degraded": False,
    }


def test_derived_group_gap_reported():
    """派生指标：某维度键缺一个 base 数据 → gaps 记录该键与缺失 base。"""
    m = {"type": "derived", "base": ["gmv", "orders"], "formula": "gmv / orders"}
    by_key = {
        ("gmv", "current", "rpt"): _make_result("gmv", "current", "rpt", [
            {"category": "数码", "gmv": 100},
            {"category": "家居", "gmv": 50},
        ]),
        ("orders", "current", "rpt"): _make_result("orders", "current", "rpt", [
            {"category": "数码", "orders": 10},  # 家居 缺 orders
        ]),
    }
    out = compute_mod._calc_derived("rpt", m, by_key, ["category"])
    assert "gaps" in out
    assert any(g["key"] == ("家居",) and "orders" in g["missing"] for g in out["gaps"])
    # 不缺口的部分正常计算
    assert any(r["category"] == "数码" and r["value"] == 10.0 for r in out["rows"])


def test_cross_period_group_gap_reported():
    """跨期指标：对比期缺某维度键 → gaps 记录（当前期有、对比期无）。"""
    m = {"type": "cross_period", "base": "gmv", "formula": "(current - compare) / compare"}
    by_key = {
        ("gmv", "current", "rpt"): _make_result("gmv", "current", "rpt", [
            {"category": "数码", "gmv": 120},
            {"category": "家居", "gmv": 60},
        ]),
        ("gmv", "compare", "rpt"): _make_result("gmv", "compare", "rpt", [
            {"category": "数码", "gmv": 100},  # 家居 缺对比期
        ]),
    }
    out = compute_mod._calc_cross("rpt", m, by_key, ["category"])
    assert "gaps" in out
    assert any(g["key"] == ("家居",) and "compare" in g["missing"] for g in out["gaps"])


def test_derived_group_no_gap():
    """派生分组无缺口 → gaps 为空，正常计算。"""
    m = {"type": "derived", "base": ["gmv", "orders"], "formula": "gmv / orders"}
    by_key = {
        ("gmv", "current", "rpt"): _make_result("gmv", "current", "rpt", [
            {"category": "数码", "gmv": 100},
        ]),
        ("orders", "current", "rpt"): _make_result("orders", "current", "rpt", [
            {"category": "数码", "orders": 10},
        ]),
    }
    out = compute_mod._calc_derived("rpt", m, by_key, ["category"])
    assert out["gaps"] == []
    assert out["rows"][0]["value"] == 10.0


def test_atomic_no_group_single_value():
    """原子指标无分组：直接取单值（确定性计算的主路径）。"""
    m = {"type": "atomic", "base": [], "formula": None}
    by_key = {
        ("gmv", "current", None): _make_result("gmv", "current", None,
                                               [{"gmv": 1100}]),
    }
    out = compute_mod._calc_atomic("gmv", m, by_key, [])
    assert out["value"] == 1100.0
    assert out["degraded"] is False
