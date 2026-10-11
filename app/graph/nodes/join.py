"""节点：join（汇聚 · 去重 / 排序）。

功能：query_executor 结果（research_results，operator.add 累加）→ 整理为
      analysis 可消费的权威版本（replace）：

  ① 去重：键 = (metric, period, target_metric, source_ref) 四元组
     - source_ref（同 SQL 稳定指纹）→ 幂等重跑（同 plan 再执行）不产生重复记录；
     - target_metric 必须入键：同一条 SQL 可能服务两个目标
       （用户直接要 gmv + 作为 gmv_wow 的 base，SQL 相同、target 不同）
       —— 只按 source_ref 去重会误删其中一条；
  ② 排序：按 (metric, period) 稳定排序，组内 confidence 降序
     —— 降级结果（degraded）排后但不丢弃：analysis 需要知道数据缺口，
        据此标注结果可信度 / 触发兜底；
  ③ 不丢弃 degraded：错误是数据不是异常，交给 analysis 判断。

纯函数，无 LLM。输出 research_results（整理后，replace）。
输入：research_results（operator.add 累加）
输出：research_results（整理后，replace）
所属 Step：查数段 · 收敛版
"""

from __future__ import annotations

from app.graph.state import AnalystState

# period 显式顺序（字典序 compare < current，不能直接按字符串排）
_PERIOD_ORDER = {"current": 0, "compare": 1}


def join(state: AnalystState) -> dict:
    """去重（四元组键）→ 稳定排序（metric/period + confidence desc）→ replace research_results。"""
    results = state.get("research_results") or []

    deduped: dict[tuple, dict] = {}
    for r in results:
        key = (
            r.get("metric"),
            r.get("period"),
            r.get("target_metric"),
            r.get("source_ref"),
        )
        if key not in deduped:
            deduped[key] = r

    merged = list(deduped.values())
    merged.sort(
        key=lambda r: (
            r.get("metric") or "",
            _PERIOD_ORDER.get(r.get("period"), 0),
            -(r.get("confidence") or 0.0),
        )
    )
    return {"research_results": merged}
