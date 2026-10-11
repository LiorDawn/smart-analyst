"""时间工具：给 LLM 一个确定性的"今天几号"。

子 agent（预测/异常/报告）必须知道时间基准；LLM 参数知识里的日期可能过期，
此工具返回运行环境当前日期时间，确定性零成本。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from langchain_core.tools import tool

from app.api.monitor import monitor


@tool
def get_current_time(offset_days: int = 0) -> str:
    """返回当前日期时间（YYYY-MM-DD HH:MM:SS + 星期），可选前/后偏移 N 天。

    何时调用：
      - 任何需要"今天几号/上周几/下周几"做时间推算的场景（预测、异常检测、报告时间基准）；
      - 用户说"最近/上周/下月"但没给绝对日期时，先取当前时间再换算。
    何时不要调用：
      - 用户已给出绝对日期范围时，直接用即可，不必调用。

    参数 offset_days 填写建议：
      - 0 = 今天；-1 = 昨天；-7 = 上周同日；+7 = 下周同日。默认 0，一般不用填。
    """
    monitor.report_tool("get_current_time", {"offset_days": offset_days})
    now = datetime.now()
    if offset_days:
        now = now + timedelta(days=offset_days)
    weekday = "一二三四五六日"[now.weekday()]
    return now.strftime(f"%Y-%m-%d %H:%M:%S 星期{weekday}（偏移 {offset_days} 天）")
