# SmartAnalyst M5・开发文档（文件级实施指南）

> 关联文档：《SmartAnalyst-M5 - 编排开发设计.md》（设计・Step 0-6 增量）、《SmartAnalyst-M5 - 节点链路图.html》（拓扑）
> 定位：本文档回答
>
> **三个问题**
>
> ——① 每个文件的功能是什么；② 按什么流程编写（依赖序）；③ 核心实现逻辑（关键决策与代码骨架）。
> 范围：**混合分层**落地形态——强制主链（确定性图，结构保证必走）+ 增值层（supervisor = `create_agent` 封装，LLM 自主调子 agent 工具），Step 0-6；质量门强制边不被 LLM 跳过（决策分层见《编排开发设计》§ 六）。
>
> ⚠️ 同步说明（2026-10-07）：supervisor 已从「手写 RoutingPlan + 注册表循环」收敛为「langchain `create_agent` 封装」（见 §9.6）；`registry.py` / `supervisor_schema.py` 已删除。本文件目录树仍含早期设计条目（fan_out / research_agent / web_fill 等），与实际代码存在出入，全面对齐另起一轮。



***

## 一、M5 文件总览（功能一览表）



```
app/
├── graph/                        # M5 编排层（本次开发核心）
│   ├── __init__.py               # 导出 state 符号（AnalystState/Budget/TaskStatus）
│   ├── state.py                  # 状态定义 + reducer 契约（已落盘）
│   ├── graph.py                  # 图组装：唯一拓扑权威 + 验证入口（已落盘，逐步扩展）
│   ├── context.py                # 运行时资源绑定：checkpointer / budget 注入
│   ├── safe_call.py              # 节点统一健壮性封装（预算→重试→超时→错误转数据）
│   ├── nodes/                    # 任务图节点（每个文件一组相关节点）
│   │   ├── task_parser.py        # 任务解析 + 意图路由（S1）
│   │   ├── planner.py            # 计划生成（S2）
│   │   ├── fan_out.py            # Send 并行分派（S2）
│   │   ├── supervisor.py         # 增值层节点（S3+：create_agent 封装，LLM 自主调子 agent 工具）
│   │   ├── join.py               # 汇聚去重排序（S2）
│   │   ├── analysis.py           # 指标计算 + 归因（S3）
│   │   ├── reflect.py            # 反思 + 双闸路由（S3）
│   │   ├── replanner.py          # 补查计划（S3）
│   │   ├── writer.py             # 报告撰写（S4）
│   │   ├── reviewer.py           # 质量门（S4）
│   │   ├── fixer.py              # 局部修正（S4）
│   │   └── deliver.py            # 幂等交付（S5）
│   └── workers/
│       ├── analysis_agent.py    # 增值：归因专家（LLM 自主调用 · 工具 rag+web+time+calc）
│       ├── benchmark_agent.py   # 增值：行业对标（LLM 自主调用 · 同上）
│       ├── anomaly_agent.py     # 增值：异常检测（LLM 自主调用 · 同上）
│       ├── forecast_agent.py    # 增值：趋势预测（LLM 自主调用 · 同上）
│       └── proposal_agent.py    # 增值：方案产出（LLM 自主调用 · 工具含 save_doc/export_pdf）
├── entities/
│   ├── task_schema.py            # TaskSchema / TimeRange / Compare（Pydantic 契约）
│   ├── planner_schema.py         # PlannerSchema（plan 项）
│   └── research_result.py        # ResearchResult（query_executor 输出契约）
├── tools/                        # M4 已有，M5 只依赖不重写
│   ├── sql_query.py              # Text-to-SQL 全链路（research_agent 工具）
│   ├── rag_search.py             # 混合召回（M4 retriever 封装）
│   └── web_search.py             # 外部搜索
└── repositories/qdrant/          # M3/M4 已有（doc_qdrant_repository）
```

**编写依赖序**（严格按此顺序，保证每一步可跑可验）：



```
state.py → __init__.py（S0，已有）
→ entities/task_schema.py → nodes/task_parser.py（S1）
→ safe_call.py → nodes/planner.py → fan_out.py → workers/research_agent.py(mock) → join.py（S2）
→ nodes/analysis.py → reflect.py → replanner.py（S3）
→ nodes/supervisor.py（create_agent 封装 + 子 agent 工具化）→ 增值 agents（S3+）
→ MySQL 建表 → nodes/writer.py → reviewer.py → fixer.py（S4）
→ graph.py 挂 interrupt → nodes/deliver.py（S5）
→ safe_call 全面接入 → RedisSaver → 幂等/缓存（S6）
```



***

## 二、每个文件的编写流程与核心实现逻辑

### 1. `app/graph/state.py`（已落盘，S0）

**功能**：`AnalystState`（四组字段）+ `Budget` + `TaskStatus` + reducer 契约 —— 全图数据的唯一权威定义。

**编写流程**：



1. 字段分四组声明（会话层 / 任务层 / 控制层 / 交付层）；

2. 关键字段加 reducer 注解；

3. 枚举只存 `.value`。

**核心逻辑**：



```
# reducer 契约（定死，后续加字段不改语义）
research_results: Annotated[list, operator.add]   # 并行结果累加
evidence_gaps:    Annotated[list, operator.add]   # 缺口累加
errors:           Annotated[list, operator.add]   # 错误是数据
task: dict | None                                 # replace（planner/writer 覆盖式写）
plan: list | None                                 # replace
status: str                                       # 存 TaskStatus.value，不存枚举实例
```

> 坑（已验证）：
>
> `TaskStatus`
>
>  枚举实例进 checkpoint 会报 
>
> `Deserializing unregistered type`
>
>  警告（未来报错）——
>
> **必须存&#x20;**
>
> `.value`
>
> **&#x20;字符串**
>
> 。

### 2. `app/graph/__init__.py`（已落盘，S0）

**功能**：导出 `AnalystState / Budget / TaskStatus`，供 nodes/workers/graph.py 统一引用。

**编写流程**：从 state 导入再 `__all__` 导出即可，不再引入其他符号。

### 3. `app/graph/graph.py`（已落盘，S0 → 每 Step 扩展）

**功能**：**唯一图拓扑权威**—— 所有节点注册、条件边、interrupt、checkpointer 都在此；自带 `python -m app.graph.graph` 验证入口。

**编写流程**（每个 Step 都在此文件加一段）：



1. 引入新节点模块 → `builder.add_node`；

2. 加边（普通边 / 条件边 / Send 动态边）；

3. 更新 `__main__` 验证入口（逐步扩展断言）。

**核心逻辑**：



```
builder = StateGraph(AnalystState)
builder.add_node("task_parser", task_parser)
builder.add_conditional_edges("task_parser", route_intent,
    {"clarify": "clarify_interrupt", "quick": "research_agent", "analyze": "planner"})
builder.add_conditional_edges("reflect", route_after_reflect,
    {"writer": "writer", "replanner": "replanner"})   # 双闸守卫
graph = builder.compile(
    checkpointer=InMemorySaver(),          # S6 换 RedisSaver
    interrupt_before=["deliver"],          # S5 报告确认断点
)
```

> 铁律：节点业务逻辑一律不写在 graph.py，只做接线。

### 4. `app/graph/context.py`（新，S1）

**功能**：运行时资源绑定 —— 把 `checkpointer`、`budget`、`llm` 等外部依赖注入图的 config，节点不直接 import 全局单例。

**编写流程**：



1. 定义 `build_config(thread_id, user_id, budget) -> dict`（含 `configurable`）；

2. 定义 `get_budget(state)` 读取预算（节点内 safe\_call 用）。

**核心逻辑**：



```
def build_config(thread_id: str, user_id: str, budget: dict) -> dict:
    return {"configurable": {"thread_id": thread_id, "user_id": user_id, "budget": budget}}
```

> 原则：
>
> **thread_id 永远走 config，不进 state**
>
> （checkpoint 锚点，已验证的坑）。

### 5. `app/graph/safe_call.py`（新，S2 起逐步使用，S6 全面接入）

**功能**：节点统一健壮性封装 —— 预算检查 → 重试退避 → 超时 → **错误转数据**（ErrorRecord 进 state）→ 返回降级信号（FallbackNeeded）。

**编写流程**：



1. 定义 `ErrorRecord` dataclass（node / exc / ts）；

2. 定义 `BudgetExceeded`、`FallbackNeeded` 信号类；

3. 实现 `safe_call(fn, *, node, state, retries=3, timeout_s=60)`。

**核心实现逻辑**：



```
async def safe_call(fn, *, node: str, state: AnalystState, retries=3, timeout_s=60):
    if budget_exhausted(state):                      # 闸 2：步骤预算
        return BudgetExceeded(node=node)
    for i in range(retries):
        try:
            return await asyncio.wait_for(fn(), timeout=timeout_s)
        except Exception as e:
            if i == retries - 1:                     # 末次失败：错误转数据
                state["errors"].append(ErrorRecord(node=node, exc=str(e), ts=now()))
                return FallbackNeeded(node=node)
            await asyncio.sleep(2 ** i + random.random())   # 指数退避+抖动
```

> 这是「错误是数据不是异常」的载体：节点不裸 try/except 抛断全图。

### 6. `app/entities/*_schema.py`（新，S1/S2/S3+）

**功能**：全链路 Pydantic 契约，文件名 = 类名对齐：
- `task_schema.py` → `TaskSchema`（解析输出）/ `TimeRange` / `Compare`；
- `planner_schema.py` → `PlannerSchema`（计划项，fan_out 分派单元）；
- `research_result.py` → `ResearchResult`（query_executor 输出：data + source_ref 指纹）。
- ~~`routing_schema.py` → `RoutingPlan`~~（已删除：supervisor 收敛为 create_agent 封装，不再需要结构化路由契约；"能力菜单"即工具 docstring）。

**编写流程**：按下游消费方定义字段，先定义最小的，随 Step 扩。

**核心实现逻辑**：



```
class TaskSchema(BaseModel):
    intents: list[str]          # 问数/分析/对比/归因（S3 起可加 预测/异动）
    metrics: list[str]
    time_range: dict            # {start, end} 绝对日期
    dims: list[str]
    compare: dict | None

class PlannerSchema(BaseModel):
    type: Literal["sql", "rag", "web"]
    query: str
    target: str
    deps: list[str]

class ResearchResult(BaseModel):        # 子 agent 输出契约（下游只认这个）
    result: str | dict
    source_ref: list[str]               # sql#qid#row / chunk_id / url
    confidence: float
    degraded: bool = False
```

> 这些 schema 同时是 
>
> `with_structured_output`
>
>  的强约束，防 LLM 格式幻觉。

### 7. `app/graph/nodes/task_parser.py`（新，S1）

**功能**：把用户一句话解析成 `TaskSchema`（意图 / 指标 / 时间范围 / 维度 / 对比），并把相对时间转绝对日期；同时提供 `route_intent` 三分支路由。

**编写流程**：



1. 写 `parse_task(state)`：调 `llm.with_structured_output(TaskSchema)`；

2. 相对时间解析：抽独立 `date` 工具（"上周"→上周一～周日），不靠 LLM 猜日期；

3. 写 `route_intent(state) -> str` 路由函数；

4. 失败降级：解析失败 → 重试 1 次 → 降级通用问答。

**核心逻辑**：



```
async def task_parser(state) -> dict:
    result = await safe_call(
        lambda: llm.with_structured_output(TaskSchema).ainvoke([...]), node="task_parser", state=state)
    if isinstance(result, FallbackNeeded):
        return {"task": {"intents": ["ask"], "metrics": []}, "errors": [...]}  # 降级快问
    spec = resolve_time_range(result)          # date 工具：相对时间 → 绝对日期
    return {"task": spec.model_dump()}
```

### 8. `app/graph/nodes/planner.py`（新，S2）

**功能**：指标口径对齐后，把任务拆成 `PlannerSchema` 列表（sql/rag/web 各取所需、带依赖）。

**编写流程**：



1. 先做口径对齐：查 metric\_dict / 口径 RAG（"GMV"→确认指标定义与所属表）；

2. 再让 LLM 拆计划（`with_structured_output(list[PlannerSchema])`）；

3. 失败 → 重试 → 模板计划（按 metrics 默认拆 sql + rag）。

**核心逻辑**：`return {"plan": plan.model_dump()}`（replace）。

### 9. `app/graph/nodes/fan_out.py`（新，S2）

**功能**：按 plan 动态并行分派子任务（子任务数不固定，必须 Send API）。

**编写流程**：极简 —— 一个函数返回 `list[Send]`，不写任何业务逻辑。

**核心逻辑**：



```
def fan_out(state: AnalystState) -> list[Send]:
    return [Send("research_agent", {"plan_item": item}) for item in state["plan"]]
```

> 注意：Send 目标节点是
>
> `research_agent`
>
> （子图），每个 PlannerSchema 一个实例 ——
>
> **多实例不加类型**
>
> ；payload 用
>
> `plan_item`
>
> 而非 `task`（不污染主 state 的 TaskSchema）。

### 9.5 ~~`app/graph/workers/registry.py`~~（已删除，S3+）

**变更（2026-10-07）**：注册表已被 supervisor.py 内的**工具化**取代——5 个子 agent 的 `run_xxx_agent` 经 `@tool` 包装为工具，docstring 即"能力菜单"（干什么 + 何时调 + 参数）。不再需要独立的 AGENT_REGISTRY / SUPERVISOR_CATALOG。

```
# 取代注册表：每个子 agent = 一个工具，description 写清"干什么 / 何时调 / 参数"
@tool
async def analysis_agent(question: str) -> str:
    """分析专家：基于主链算出的确切数据做归因解释…用户问"为什么涨/跌…"时调用；只问数值不调。"""
    return json.dumps(await run_analysis_agent(*_agent_inputs(question)), ensure_ascii=False)
```

### 9.6 `app/graph/nodes/supervisor.py`（S3+ · 已收敛为 create_agent 封装）

**功能**：增值层入口 —— **`langchain.agents.create_agent`** 封装：5 个子 agent 包装成工具，LLM 在 ReAct 循环里自主决定"调不调、调哪个、调几个"（简单问答不调，省 token）。不再手写 RoutingPlan / 注册表循环（对比 deepsearch 的 `create_deep_agent`：同一流派，supervisor 即框架封装 agent，只是用 langchain 而非 deepagents）。

**核心逻辑**：

```
TOOLS = [analysis_agent, benchmark_agent, anomaly_agent, forecast_agent, proposal_agent]  # @tool 包装

def _build_supervisor_agent():
    return create_agent(
        model=llm_client_manager.client,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,   # 告诉 LLM：简单问答不调工具，需要深度分析才调
    )

async def supervisor(state) -> dict:
    ...
    resp = await agent.ainvoke({"messages": [...]})   # LLM 自主决定调哪几个
    return {"analysis_insights": insights}            # 解析工具结果（ToolMessage）+ 总结
```

**关键设计**：
1. **上下文注入**：子 agent 需要的 `analysis_results / task / research_results` 经 **ContextVar** 注入（工具签名只暴露 `question`，LLM 无需关心上下文）——照 deepsearch 的 `set_session_context` 模式；
2. **降级**：LLM 不可用/超时 → 空 insights + errors（主链照走）；单个工具失败 → 该条记 error，不中断；
3. **API 坑**：langchain 1.4.2 的 `create_agent` 参数是 **`system_prompt=`**（langgraph 的 `create_react_agent` 才是 `prompt=`）。

> 铁律：supervisor 只决定增值 agent，**无权跳过强制主链**（质量门是结构边，不是 LLM 选项）。

### 10. `app/graph/workers/research_agent.py`（S2 mock → S6 真实）

**功能**：强制主链子 agent（数据采集专家），内部 ReAct 多步决策，输出带 source\_ref 的数据包。

**编写流程**：



1. **S2 先写 mock**：一个 async 函数按 `task.type` 返回固定 `ResearchResult`（验证并行与汇聚）；

2. **S6 换真实**：`create_agent(model, system_prompt, tools=[sql_query, rag_search, web_search])`。

**核心实现逻辑（S6 真实版）**：



```
research_agent = create_agent(
    model=llm,
    system_prompt=("数据采集专家：只采集不分析；判断查内库/外网/知识库；"
                   "每条结果必须带 source_ref；失败标 degraded 并说明缺什么；返回结构化数据包"),
    tools=[sql_query, rag_search, web_search],
)
```

> 输出契约 = 
>
> `ResearchResult`
>
> （见 entities），
>
> `source_ref`
>
>  是 reviewer 质量门的校验锚点。

### 11. `app/graph/nodes/join.py`（新，S2）

**功能**：汇聚所有子 agent 结果 —— 去重（同 source\_ref 只留一条）、排序（confidence desc）、打来源标签。

**编写流程**：纯函数，无 LLM，可单测。

**核心逻辑**：



```
async def join(state) -> dict:
    merged = dedup_by_source_ref(state["research_results"])
    merged.sort(key=lambda r: r.confidence, reverse=True)
    return {"research_results": merged}
```

### 12. `app/graph/nodes/analysis.py`（新，S3）

**功能**：指标计算（pandas 确定性）+ 变化归因（LLM 解释）。失败 → 数据包直传 writer。

**编写流程**：



1. 确定性部分：pandas 算同环比 / 占比 / 趋势；

2. LLM 部分：基于计算结果 + 口径做归因解释（`with_structured_output`）；

3. 输出 `analysis_results`（replace）。

### 13. `app/graph/nodes/reflect.py`（新，S3）

**功能**：量化信息充分性（计划覆盖率 / 引用数 / 置信度）→ 产出 `evidence_gaps`，`iteration + 1`；提供 `route_after_reflect` 双闸路由。

**编写流程**：



1. `compute_gaps(plan, research_results)`：逐子任务检查有无结果、source\_ref 数、置信度阈值；

2. 路由函数：闸 1 迭代上限 / 闸 2 由 safe\_call 预算检查承担。

**核心逻辑**：



```
def route_after_reflect(state) -> str:
    if state["iteration"] >= state["budget"]["max_iterations"]:
        return "writer"           # 闸 1
    if not state["evidence_gaps"]:
        return "writer"           # 信息充分
    return "replanner"            # 有缺口 → 补查
```

### 14. `app/graph/nodes/replanner.py`（新，S3）

**功能**：只把 `evidence_gaps` 转成新 `PlannerSchema` 追加进 plan（不推翻原计划；重复同缺口 → 不再补，靠 iteration 触发闸 1）。

**核心逻辑**：`return {"plan": state["plan"] + new_items}`（replace 但含原计划）。

### 15. `app/graph/nodes/writer.py`（新，S4）

**功能**：组织报告结构、生成图表、插入引用；**数字只取自 research/analysis 结果，LLM 不新造数**。

**编写流程**：



1. `with_structured_output(Report)` 强约束结构；

2. 构造 prompt 时只传入已有结果与口径；

3. 失败 → 重试 → Markdown 摘要降级。

### 16. `app/graph/nodes/reviewer.py`（新，S4）

**功能**：质量门 —— 报告每个数字必须有 `source_ref` 且口径正确 → `report_ok` + issues。

**核心逻辑**：



```
async def reviewer(state) -> dict:
    issues = []
    for num in extract_numbers(state["report"]):
        if not has_source_ref(num, state["research_results"]):
            issues.append(f"数字 {num} 缺少 source_ref")
    return {"report_ok": not issues, "report_issues": issues}
```

> 校验器自身失败 → 放行 + warn（不阻塞主链路）。

### 17. `app/graph/nodes/fixer.py`（新，S4）

**功能**：只修 issues 指出的章节，计数 ≤2 轮；超限 → 报告显式标注 "未通过校验"。

**编写流程**：LLM 局部改写 + 轮次计数器（存 state 或闭包）。

### 18. `app/graph/nodes/deliver.py`（新，S5）

**功能**：副作用集中点 —— 写产物文件 → 落 MySQL（reports/evidence/tasks）→ WebSocket 推送 → Redis 缓存；**幂等**（SETNX 去重）。

**编写流程**：



1. 生成幂等键 `call_id` → Redis `SETNX(dedup:{call_id})`；

2. 已有记录 → 直接返回（不重复写）；

3. 首次 → 写产物 + 落库 + 推送 + 缓存，更新 `status`。

### 19. 支撑层（M4 已有，M5 依赖不重写）



| 文件                    | 功能                                                        | M5 消费方             |
| --------------------- | --------------------------------------------------------- | ------------------ |
| `tools/sql_query.py`  | Text-to-SQL 全链路（元数据召回→生成→校验→只读执行）                         | research\_agent 工具 |
| `tools/rag_search.py` | M4 retriever 封装（混合召回 + RRF + 可选 rerank）                   | research\_agent 工具 |
| `tools/web_search.py` | 外部搜索（失败标记 gap）                                            | research\_agent 工具 |
| MySQL 建表（Step 4 前）    | conversations/messages/tasks/evidence/reports（5 表，meta 库） | deliver / reviewer |



***

## 三、每个 Step 的 "写完什么 → 验证什么" 清单



| Step | 写完的文件                                                          | 验证命令 / 断言                                                      |
| ---- | -------------------------------------------------------------- | -------------------------------------------------------------- |
| S0   | state /\_\_init\_\_/graph（骨架）                                  | `python -m app.graph.graph`：空跑 + checkpoint 复用（✅ 已过）           |
| S1   | entities/task\_spec + nodes/task\_parser + graph 接边            | `ainvoke("分析上周 GMV")` → `task.time_range` 为绝对日期、intent=analyze |
| S2   | safe\_call + planner + fan\_out + research\_agent(mock) + join | plan 含 3 项 → 3 个 mock 并行返回 → join 去重排序正确                       |
| S3   | analysis + reflect + replanner                                 | 注入缺口 → 触发补查；iteration=3 → 强制 writer                            |
| S4   | MySQL 建表 + writer + reviewer + fixer                           | 注入无引用数字 → reviewer 拦截 → fixer 修正 → 二轮通过                        |
| S5   | graph 挂 interrupt + deliver                                    | interrupt/resume 全流程；二次 resume 不重复写                            |
| S6   | safe\_call 全面接入 + RedisSaver + 幂等 / 缓存                         | 杀 MySQL → 走 RAG 兜底出报告；重启后 checkpoint 可取                        |



***

## 四、编码守则（M5 全文件通用）



1. **节点 = 函数**：签名 `async def xxx(state: AnalystState) -> dict`，只读声明字段、只返回增量更新；

2. **LLM 调用一律过 safe\_call**：预算 → 重试 → 超时 → 错误转 `errors`，不裸抛；

3. **LLM 输出一律&#x20;**`with_structured_output`：防格式幻觉破坏下游；

4. **决策分层**：必走步骤（查数/分析/成稿/质检）由结构边强制；增值步骤（补数/下钻/对比/图表）由 supervisor 的 LLM 自主决定——**质量门永远不被 LLM 跳过**；

5. **副作用只进 deliver**：幂等键 + 集中写库，其余节点纯计算；

6. **graph.py 只接线**：节点逻辑全在 nodes/，拓扑权威唯一；

7. **每 Step 跑验证**：`python -m app.graph.graph` 逐步扩展断言，失败只回滚一步。



***

**一句话总结**：M5 开发 = 按「state → 契约 → 节点 → 图接线」的依赖序，19 个文件逐个落盘，**每个 Step 写完即验证**；核心三件套（safe\_call 健壮性、with\_structured\_output 契约、reducer 状态语义）贯穿所有文件。