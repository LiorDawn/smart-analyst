"""query_executor 契约：ResearchResult（查询执行结果 · 溯源锚点）。

命名规范：契约文件统一为 <模块>_schema.py，但本契约是 worker 输出（数据包），
沿用现有 naming：entities 层放执行结果实体。

设计（对齐《查数链路设计方案》§二.4）：
  - 单条 SQL 一条 ResearchResult（degraded 标记失败，错误是数据不是异常）；
  - source_ref = sql#{md5(sql)[:8]}：稳定指纹 → join 去重依据 + reviewer 溯源锚点；
  - target_metric：派生/跨期拆解时标注父指标，analysis 据此配对计算。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ResearchResult(BaseModel):
    """一条规则 SQL 的执行结果（query_executor 输出，operator.add 进 research_results）。"""

    metric: str = Field(..., description="本项查的原子指标 canonical 名（如 gmv/orders）")
    period: Literal["current", "compare"] = Field("current", description="期间标识：分析期 current / 对比期 compare")
    target_metric: str | None = Field(None, description="服务的目标指标（派生/跨期拆解时为父指标名）")
    rows: list[dict] = Field(default_factory=list, description="查询结果行（[{字段: 值}]）")
    source_ref: str = Field(..., description='溯源锚点：sql#{md5(sql)[:8]}（join 去重 + reviewer 校验）')
    confidence: float = Field(1.0, description="结果可信度：正常 1.0 / 降级 0.0")
    degraded: bool = Field(False, description="本条是否降级（SQL 失败/非只读拒绝）")
    error: str | None = Field(None, description="降级原因（degraded=True 时）")
