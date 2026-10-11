"""planner 契约：QueryItem（收敛版查询单元）/ PlannerSchema（fan_out 兼容）/ MetricDef（L3 兜底）。

命名规范：契约文件统一为 <模块>_schema.py（planner → planner_schema），类名与文件名对齐。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.entities.task_schema import Compare


class PlannerSchema(BaseModel):
    """planner 输出：一个可独立执行的查询计划项（Send 分派单元）。

    自包含原则：research_agent 收到一个 PlannerSchema 即可独立完成查询，无需回查 TaskSchema。
    """

    type: Literal["sql", "rag", "web"]
    query: str                          # 具体查询（SQL 文本 / 检索词）
    target: str                         # 表名 / 知识库主题 / 搜索域
    metric: str | None = Field(None, description="本项查的指标（research/analysis 定位用）")
    dims: list[str] = Field(default_factory=list)
    time_range: dict | None = Field(None, description='{"start","end"} 绝对日期')
    compare: Compare | None = Field(None, description="若本项是对比期查询")
    deps: list[int] = Field(default_factory=list, description="依赖项索引（S2 留空）")


class QueryItem(BaseModel):
    """planner 收敛版输出：一条可执行查询（规则拼 SQL 产物，query_executor 消费）。

    与 PlannerSchema 的区别：QueryItem 是查数主链的确定性查询单元——
      - 原子指标   → 一条 SQL（period=current；有对比时追加 compare 段）
      - 派生指标   → 拆 base 原子查询（target_metric 标注父指标，analysis 据此计算）
      - 跨期指标   → 两段（period=current / compare）

    收敛原则（实例身份 vs 全局上下文）：
      - 只带实例身份：metric / period / target_metric（N 条 item 各自归属）
      - 不带全局上下文：dims / time_range / compare 一律不落 QueryItem——
        SQL 文本已编码（GROUP BY / WHERE 日期）；executor 不读；analysis 靠
        period + target_metric + rows 配对；报告要维度从 state.task.dims 拿。
    """

    type: Literal["sql", "rag", "web"] = "sql"
    query: str = Field(..., description="SQL 文本（type=sql）/ 检索词（rag/web）")
    metric: str = Field(..., description="本项查的原子指标 canonical 名（如 gmv/orders）")
    target_metric: str | None = Field(None, description="服务的目标指标（派生/跨期拆解时为父指标名）")
    period: Literal["current", "compare"] = Field("current", description="期间标识：分析期 current / 对比期 compare")
    depends_on: list[int] = Field(default_factory=list, description="依赖项索引（base 先行）")


class MetricDef(BaseModel):
    """L3 口径兜底产物：RAG 命中口径库 → LLM 提取的指标定义（METRIC_DICT 同构条目）。

    设计：LLM 只做「文档 → 结构化口径」的提取，不决定是否触发 RAG（那是规则的事）。
    """

    found: bool = False
    defn: dict | None = Field(None, description="METRIC_DICT 同构条目（type/table/field/agg/...）；found=False 时为 None")
