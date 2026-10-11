"""M5 全链路 Pydantic 契约：TaskSchema / PlannerSchema / Report / ResearchResult。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TimeRange(BaseModel):
    """分析期（绝对日期，LLM 把相对时间转成它）。"""

    start: str = Field(..., description="起，YYYY-MM-DD，如 2026-09-21")
    end: str = Field(..., description="止，YYYY-MM-DD，如 2026-09-27")


class Compare(BaseModel):
    """对比要求。"""

    type: Literal["mom", "yoy", "wow", "custom"] = Field(..., description="环比/同比/周环比/自定义")
    start: str = Field(..., description="对比期起点（绝对日期）")
    end: str = Field(..., description="对比期终点（绝对日期）")


class TaskSchema(BaseModel):
    """task_parser 输出：用户一句话的结构化解析。"""

    intents: list[str] = Field(default_factory=list, description="trend/compare/attribution/quick（纯知识问答）")
    metrics: list[str] = Field(default_factory=list, description="指标，如 ['gmv','orders']")
    time_range: TimeRange | None = Field(None, description="分析期；None 时由节点兜底默认近 7 天")
    dims: list[str] = Field(default_factory=list, description="维度，如 ['category','channel']")
    compare: Compare | None = Field(None, description="对比要求")
    missing: list[str] = Field(default_factory=list, description="缺失关键项（route_intent 据此判定 clarify）")
    rewritten: str | None = Field(None, description="改写后的标准问题（口语→书面、补全指代/时间/术语，自包含）；无需改写时为原问题")
