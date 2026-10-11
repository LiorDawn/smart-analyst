"""节点：task_parser（任务解析）。

功能：用户一句话 → TaskSchema（replace）：
  ① LLM with_structured_output(TaskSchema) 强约束解析
  ② system prompt 注入当前日期，相对时间（上周/近30天）直接转绝对日期
  ③ time_range 缺失 → 兜底默认近 7 天；metrics 缺失 → 写入 missing 走 clarify
  ④ LLM 不可用/解析失败 → 规则兜底（从问题提取已知指标/维度/时间/对比，0 token）
     ——有命中出完整 task，无命中才降级默认 + missing 走 clarify
输出：task（TaskSchema dict）、status（TaskStatus.RUNNING.value）
所属 Step：S1
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.clients.llm_client import llm_client_manager
from app.conf.metric_dict import METRIC_DICT, SYNONYM_DICT
from app.entities.task_schema import TaskSchema
from app.graph.state import AnalystState, TaskStatus, last_user_text


def _default_task_spec() -> dict:
    """降级任务：默认近 7 天 + 指标缺失（走 clarify）。"""
    end = date.today()
    start = end - timedelta(days=6)
    return {
        "intents": [],
        "metrics": [],
        "time_range": {"start": start.isoformat(), "end": end.isoformat()},
        "dims": [],
        "compare": None,
        "missing": ["metrics"],
        "rewritten": None,  # 降级无改写（原问题由调用方回退）
    }


# ── 规则兜底：LLM 挂时从问题提取指标/维度/时间/对比（对齐 planner 的 SYNONYM_DICT 资产） ──
_DIM_KEYWORDS = {"category": ["品类", "类目", "分类", "类别"], "channel": ["渠道", "店铺", "通路"]}


def _extract_metrics(question: str) -> list[str]:
    """提指标：优先长别名（多字词先匹配，避免「客单」误吞「客单价」），命中 SYNONYM → canonical。"""
    found: list[str] = []
    # 按别名长度降序匹配，长的先命中
    for alias in sorted(SYNONYM_DICT, key=len, reverse=True):
        if alias and alias.lower() in question.lower() and SYNONYM_DICT[alias] not in found:
            found.append(SYNONYM_DICT[alias])
    # 英文/拼音直写指标名（gmv/uv/roi/ctr 等已在 SYNONYM，此处补 METRIC_DICT 全名直写）
    # 注意：Python re 默认 Unicode 模式下中文属于 \w，\b 在「的gmv和」中不成立——
    # 必须用 ASCII 字符类负向断言做边界（中文前后不影响英文指标匹配）。
    for metric in METRIC_DICT:
        if metric not in found and re.search(
            rf"(?<![a-z0-9]){re.escape(metric)}(?![a-z0-9])", question.lower()
        ):
            found.append(metric)
    return found


def _extract_time(question: str) -> tuple[dict, dict | None]:
    """时间窗 + 对比要求。返回 (time_range, compare)；都缺 → 默认近 7 天、无对比。"""
    today = date.today()
    q = question

    def iso(d: date) -> str:
        return d.isoformat()

    # 对比：环比上周 / 同比（去年同段）
    if "环比上周" in q or "和上周比" in q or "较上周" in q:
        monday = today - timedelta(days=today.weekday())
        compare = {"type": "wow", "start": iso(monday - timedelta(days=7)), "end": iso(monday - timedelta(days=1))}
    elif "同比" in q or "和去年" in q:
        compare = {"type": "yoy", "start": iso(today.replace(year=today.year - 1) - timedelta(days=6)), "end": iso(today.replace(year=today.year - 1))}
    else:
        compare = None

    # 时间窗：上周 / 本周 / 本月 / 近 N 天
    m = re.search(r"近\s*(\d+)\s*天", q)
    if m:
        days = int(m.group(1))
        return {"start": iso(today - timedelta(days=days - 1)), "end": iso(today)}, compare
    if "上周" in q:
        monday = today - timedelta(days=today.weekday() + 7)
        return {"start": iso(monday), "end": iso(monday + timedelta(days=6))}, compare
    if "本周" in q:
        monday = today - timedelta(days=today.weekday())
        return {"start": iso(monday), "end": iso(today)}, compare
    if "本月" in q:
        return {"start": iso(today.replace(day=1)), "end": iso(today)}, compare
    return {"start": iso(today - timedelta(days=6)), "end": iso(today)}, compare


def _rule_fallback_task(question: str) -> dict | None:
    """LLM 不可用时的规则兜底（0 token）。命中指标 ≥1 → 出完整 task；否则 None（走 clarify）。"""
    metrics = _extract_metrics(question)
    if not metrics:
        return None
    dims = [dim for dim, words in _DIM_KEYWORDS.items() if any(w in question for w in words)]
    time_range, compare = _extract_time(question)
    return {
        "intents": ["trend"] if not compare else ["trend", "compare"],
        "metrics": metrics,
        "time_range": time_range,
        "dims": dims,
        "compare": compare,
        "missing": [],
        "rewritten": question,  # 规则兜底 0 token：不改写，原问题直传
    }


async def task_parser(state: AnalystState) -> dict:
    question = last_user_text(state.get("messages") or [])

    if llm_client_manager.client is None:
        llm_client_manager.init()

    try:
        structured_llm = llm_client_manager.client.with_structured_output(TaskSchema)
        today = date.today().isoformat()
        resp = await structured_llm.ainvoke([
            {
                "role": "system",
                "content": (
                    f"你是经营分析助手。今天是 {today}。"
                    "把用户请求解析成 TaskSchema，规则："
                    "① 相对时间一律转成绝对日期（YYYY-MM-DD，含跨月/跨年计算）；"
                    "② 需要查数/算指标的 → intents 填 trend/compare/attribution 等分析意图；"
                    "    纯知识/口径问答（不查数）→ intents 填 ['quick']；"
                    "③ 指标、维度、对比缺失时如实填进 missing，绝不编造默认值；"
                    "④ 输出 rewritten —— 把用户问题改写为书面、自包含的标准问句："
                    "去口语化（这周卖得咋样→本周销售额表现如何）、补全指代/上下文、"
                    "术语归一（客单→客单价）、明确时间/维度；不得改变原意、不得编造用户没说的内容；"
                    "问题已标准时 rewritten 原样返回。"
                ),
            },
            {"role": "user", "content": question},
        ])
        task = resp.model_dump()
        # 兜底：用户没给时间 → 默认近 7 天（业务默认，少打断）；指标缺失 → 保留 missing 走 clarify
        if task.get("time_range") is None:
            end = date.today()
            start = end - timedelta(days=6)
            task["time_range"] = {"start": start.isoformat(), "end": end.isoformat()}
        # 改写兜底：LLM 未输出 rewritten → 原问题直传（不阻塞下游）
        if not task.get("rewritten"):
            task["rewritten"] = question
    except Exception as e:
        # 规则兜底：LLM 不可用时从问题提取已知指标（0 token，不打断主链）
        rule_task = _rule_fallback_task(question)
        if rule_task is not None:
            return {
                "task": rule_task,
                "errors": [{"node": "task_parser", "msg": f"LLM 解析不可用，规则兜底提取: {rule_task.get('metrics')}"}],
                "status": TaskStatus.RUNNING.value,
            }
        task = _default_task_spec()
        return {
            "task": task,
            "errors": [{"node": "task_parser", "msg": f"解析失败，降级默认任务: {e}"}],
            "status": TaskStatus.RUNNING.value,
        }

    return {"task": task, "errors": [], "status": TaskStatus.RUNNING.value}
