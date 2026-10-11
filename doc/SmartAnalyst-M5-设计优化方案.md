# SmartAnalyst M5・设计优化方案（v3 · 企业式收敛版）

> 关联文档：《SmartAnalyst-M5 编排开发设计.md》（混合分层 v2）、《SmartAnalyst-M5 开发文档.md》（文件级实施）、《SmartAnalyst-M5 节点链路图.html》（拓扑）
> 定位：在 v2 混合分层基础上，按企业主流做法（指标中台 + 语义层 + 规则优先）**收敛查数链路**——查数做薄、计算归代码、LLM 只做匹配；子 agent 能力集中在增值层，由 LLM 自主决定调用。
> 状态：**设计优化方案 v3（待用户敲定后更新正式文档与代码）**

***

## 一、收敛后的三条设计原则

1. **能规则的绝不 LLM**：指标 → 字段 → SQL 是确定性映射，用「指标定义表」规则化；LLM 只做「意图理解 + 指标匹配」；
2. **查数做薄**：80% 查询一条 SQL 直接完成；派生指标计算归 analysis（代码）；高频复杂指标预计算/缓存；
3. **子 agent 只加在增值层**：探索型能力注册表登记，supervisor 的 LLM 自主决定调不调；主链（查数/分析/成稿/质检）结构保证，质量门不可被 LLM 跳过。

## 二、目标架构（混合分层 · 收敛版）

```
第 1 层 入口：START → task_parser → entry_orchestrator（LLM）
   ├─ quick（知识问答，不查数）→ RAG 直答 → deliver
   ├─ clarify（口径不明）→ ⏸ 澄清 → 回 planner
   └─ analyze（分析任务）→ 查数主链
第 2 层 查数主链（强制 · 结构保证）：
   planner（指标定义表匹配 → 规则拼 SQL；未命中 → LLM 查口径兜底）
   → 查询执行（一条 SQL / 拆原子字段）→ join → analysis（算派生指标）
   → writer → reviewer（强制质量门）→(通过)→ ⏸ report_confirm → deliver → END
   reflect（证据缺口 + 迭代双闸）→(有缺口且未超限)→ replanner → 补查
   reviewer →(不通过)→ fixer → writer（≤2 轮）
第 3 层 增值层（LLM 自主 · Plan-Execute）：
   supervisor：LLM 出 RoutingPlan，自主决定调哪些增值子 agent
   ├─ benchmark_agent（行业对标）/ drilldown_agent（下钻）/ chart_agent（图表）/ web_fill_agent（补数）
   └─ 铁律：LLM 只决定增值部分，无权跳过主链
```

## 三、指标定义表（核心资产 · 替代裸 METRIC_DICT）

```python
# app/conf/metric_dict.py（或 app/entities/metric_schema.py + 数据表）
METRIC_DICT = {
    "orders": {
        "name": "订单量", "type": "atomic",          # 原子 / 派生 / 跨期
        "table": "fact_sales", "field": "orders", "agg": "SUM",
        "filter": None,                                # 统计范围（剔除退货等）
        "formula": None,                               # 派生公式（None=原子）
        "precompute": True,                            # 高频 → 预计算/缓存
    },
    "gmv": {
        "name": "成交额", "type": "atomic",
        "table": "fact_sales", "field": "amount", "agg": "SUM",
        "filter": "status != 'cancelled'", "formula": None, "precompute": True,
    },
    "conversion": {
        "name": "转化率", "type": "derived",
        "table": "fact_sales", "field": None, "agg": None,
        "formula": "SUM(orders)/SUM(visitors)", "precompute": True,
    },
    "wow_change": {
        "name": "周环比涨跌幅", "type": "cross_period",
        "formula": "(本期-对比期)/对比期", "precompute": False,
    },
}
```

**五要素**：名称/口径（公式+过滤）、数据来源（表+字段+聚合）、类型（原子/派生/跨期）、预计算标记、血缘（派生 → 原子 → 表）。

## 四、查数链路（规则优先 + LLM 兜底）

```
用户要指标 gmv
  → task_parser 解析：指标/时间/维度/对比（LLM 一次调用）
  → planner：
      字典命中（原子）      → 规则拼 SQL：SELECT SUM(amount) ... WHERE 时间+维度+filter
      字典命中（简单派生）   → 规则拼 SQL 表达式：SELECT SUM(orders)/SUM(visitors) ...
      字典命中（复杂派生）   → 拆原子字段计划 → 查回原子数据 → analysis 算
      字典未命中 / 口径模糊  → LLM 查口径 rag → 确定映射 → 回写字典（下次命中）
  → 查询执行 → 结果带 source_ref → join
```

**派生指标计算责任在 analysis（代码），不在查询层、更不在 LLM。**
**跨期指标（涨跌幅）**：planner 按「分析期 + 对比期」生成两条时间窗的原子查询 → analysis 配对计算。

## 五、复杂指标处理（对齐企业做法）

| 复杂类型 | 处理 |
| --- | --- |
| 简单派生（公式） | SQL 表达式模板直接算（一条 SQL） |
| 条件聚合（剔除/过滤） | 指标定义表 filter 固化进 WHERE |
| 跨期（环比/同比） | 两段原子查询 + analysis 配对算 |
| 复杂派生（多表/漏斗/留存） | 查原子数据 → analysis（pandas/代码）分步算 |
| 高频复杂指标 | **预计算/缓存**（跑批任务写结果表，查询直接读） |

## 六、子 agent 设计（五步法）

**添加一个子 agent = 契约 → 实现 → 注册 → 触发 → 汇入**

```python
# ① 契约（entities/benchmark_schema.py）
class BenchmarkResult(BaseModel):
    comparisons: list[dict]   # {metric, us, competitor, gap, source_ref}

# ② 实现（workers/benchmark_agent.py）
benchmark_agent = create_agent(
    model=llm,
    system_prompt="行业对标专家：搜竞品/行业数据，对比我方指标，输出差距及来源",
    tools=[web_search, rag_search],
)

# ③ 注册（workers/registry.py）
AGENT_REGISTRY = [
    {"name": "benchmark_agent", "desc": "行业对标：查竞品数据对比我方指标（需要行业对比时调用）", "mode": "optional"},
]

# ④ 触发（nodes/supervisor.py · LLM 自主）
async def supervisor(state):
    d = await llm.with_structured_output(RoutingPlan).ainvoke(
        [system: f"可用子 agent：{AGENT_REGISTRY}。按需决定，不需要的坚决不调",
         user: state["messages"][-1]])
    return {"routing_plan": d.model_dump()}

# ⑤ 汇入（state.py）
benchmark_results: Annotated[list, operator.add]   # writer 引用差距分析
```

**添加规范**：描述写清「什么时候调」；每个 agent 有 max_loop 预算；失败写 degraded + errors 不中断；结果带 source_ref；默认先加增值（optional），被证明是刚需才升主链（mandatory）。

## 七、对现有代码的调整清单

| 文件 | 现状 | 调整 |
| --- | --- | --- |
| `app/conf/metric_dict.py`（新） | 无 | 指标定义表（五要素）——查数根基 |
| `app/graph/nodes/planner.py` | 规则化按 metric×段拆 SQL | 改为「指标定义表匹配 + 规则拼 SQL」；未命中走 LLM 口径兜底；简单派生表达式、复杂派生拆原子 |
| `app/graph/workers/research_agent.py` | 多实例执行器 | 收敛为**查询执行器**（执行规则 SQL / 原子字段查询，带 source_ref） |
| `app/graph/nodes/fan_out.py` | 按 PlannerSchema 分派 | 默认 1 实例；仅多独立查询/多任务才并行 |
| `app/graph/nodes/supervisor.py`（新） | 无 | 增值路由（RoutingPlan，LLM 自主决定调子 agent） |
| `app/graph/workers/registry.py`（新） | 无 | AGENT_REGISTRY（能力菜单） |
| `app/graph/nodes/analysis.py` | 占位 | 承担**派生指标计算**（代码），接收原子数据 + 公式 |
| `app/graph/state.py` | 6 字段 | 加增值结果字段（benchmark_results 等，add） |
| `app/graph/nodes/replanner.py` | 占位 | 补查只补缺口（读 evidence_gaps） |

**保留不动**：task_parser（LLM 解析）、route_intent/entry_orchestrator（三路分流）、join（汇聚）、writer/reviewer/fixer（质量门）、deliver（幂等交付）、reflect（双闸）、clarify_interrupt/report_confirm（人工断点）。

## 八、落地顺序（收敛后）

| 步骤 | 内容 | 验收 |
| --- | --- | --- |
| C1 | 建指标定义表（先 4-6 个常用指标）+ planner 规则拼 SQL | "gmv 上周"→ 字典命中 → 一条 SQL 出数 |
| C2 | research_agent 收敛查询执行器 + join + analysis（算派生） | "转化率上周"→ 拆原子字段 → analysis 算出 |
| C3 | 入口 orchestrator 复杂度判断（简单直连 / 复杂进主链） | 简单查询 0 多余环节 |
| C4 | supervisor + registry + 1 个增值 agent（benchmark） | "GMV 为什么跌"→ LLM 自主调 benchmark |
| C5 | 高频指标预计算/缓存 + 降级矩阵 | 同指标二次查询命中缓存 |

## 九、删除/不再推进的设计（收敛裁掉）

- ~~planner 拆 N 条查询单 + fan_out 多实例并行~~ → 默认 1 实例，规则 SQL 为主
- ~~查数 agent 工具三件套自由 ReAct（sql/rag/web 自主循环）~~ → 查询执行器，LLM 只在字典未命中时兜底
- ~~sub-agent 融入 supervisor（单 agent 大杂烩）~~ → 决策分层，主链结构保证
- ~~E1-E3 逐步 agent 化 analysis/writer/reviewer~~ → 增值能力走注册表，主链保持结构

***

**一句话总结**：v3 收敛 = 「指标定义表（规则根基）+ LLM 只做匹配（薄薄一层）+ analysis 承担计算（代码）+ 增值子 agent 注册表（LLM 自主）」——查数对齐企业主流，复杂指标模板化/预计算，子 agent 能力按需生长，质量门永远结构强制。
