# Smart Analyst（AI 经营分析官）—— 完整架构设计文档

> 版本 v1.0（由 v0.2 架构总览 + v0.3 企业级编排细化整合而成）
> 技术底座：LangGraph + LangChain + Redis
> 参考项目：`deepsearch-agents`（多智能体编排）+ `shopkeeper-agent`（分层 / RAG / 向量检索 / Text-to-SQL）

---

## 一、项目定位

一个面向 C 端的**经营数据分析工具**。核心能力 = **多智能体自动思考 + RAG 检索 + 工具调用 + 文件交付**，让用户用自然语言问数、自动分析并产出报告。

> 它**不是**只调一次大模型，而是会规划、会分工、会查数、会查知识库、会反思、会校验，最后产出 Markdown / PDF 分析报告。

融合两个参考项目的精华：

| 来自 shopkeeper | 来自 deepsearch |
|----------------|----------------|
| Text-to-SQL（元数据召回→SQL生成→校验→执行） | 多智能体编排（Orchestrator-Workers） |
| RAG 向量检索（Qdrant 语义召回） | 自动思考（规划-执行-反思循环） |
| 清晰分层 client→repository→entity→agent | 文件交付 + WebSocket 实时监控 |
| meta 元数据知识库 + dw 数仓 | 会话隔离 + 多来源检索 |

---

## 二、整体架构与完整链路

采用 **Orchestrator-Workers（一主多从）** 模式，叠加三条主线：

1. **智能体执行**：规划 → 分派 → 检索/查询 → 汇总 → 反思 → 校验 → 交付
2. **前后端实时联动**：接口接收 → 后台执行 → 事件推送 → 前端展示
3. **健壮性**：预算守卫 / 降级 / 幂等 / 断点 / 可观测

### 2.1 完整链路（一次请求全貌）

```
① 前端发起任务（可带附件）
② FastAPI 鉴权 + Redis 限流 + 生成 thread_id
③ task_parser 解析意图+指标+时间范围（相对时间转绝对日期）
④ intent_router 三路分流：
     ├─ 口径/时间不明 → ⏸ clarify_interrupt（人工澄清，resume 后继续）
     ├─ 纯知识问答   → rag_worker（快问，省预算）→ deliver
     └─ 分析任务     → 进入任务图 planner
⑤ planner 制定结构化子任务计划（先做指标口径对齐）
⑥ fan_out 用 Send API 并行分派到各 worker 子图
⑦ worker 并行执行：
     ├─ sql_worker：Text-to-SQL → 只读执行 → 结构化为表格
     ├─ rag_worker：混合召回 + 重排，结果带 chunk 引用
     └─ web_worker：外部搜索 / 业务 API（可选）
⑧ join 汇聚结果（去重、排序、打来源标签）
⑨ reflect 量化充分性（覆盖率/引用数/置信度）→ 产出 evidence_gaps，iteration+1
     ├─ 有缺口且未超限 → replanner 补缺口 → 回 ⑥（循环）
     └─ 无缺口或超限   → validator
⑩ validator 质量门（每个数字有 source_ref、SQL 结果一致性抽查、口径一致）
     ├─ 不通过 → fixer 局部修正（≤2 轮）→ synthesizer
     └─ 通过   → synthesizer
⑪ synthesizer 生成结构化报告（数字一律取自 worker_results）
⑫ ⏸ report_confirm 人工断点（报告方向确认，resume 后继续）
⑬ deliver 写产物文件 + 落 MySQL 审计 + 推 WebSocket 事件 + 回写缓存（幂等）
⑭ 会话状态落 Redis（checkpoint），权威数据落 MySQL，可审计、可恢复
```

全程由 `graph.stream(stream_mode="updates")` 的流驱动 monitor，逐节点推事件到前端（步骤流零额外状态）。

---

## 三、技术选型对照

| 模块 | 本项目选型 | 理由 |
|------|-----------|------|
| 多智能体框架 | LangGraph 手写编排（三层图） | 吃透底层、精细控制、官方框架 |
| 底层检查点 | LangGraph + **Redis 持久化 Checkpointer** | 跨重启，替代 InMemorySaver |
| 模型接入 | OpenAI 兼容（DeepSeek/Qwen/硅基流动 .env 可换） | 统一 base_url + key |
| 结构化数据 | MySQL（dw 业务数仓 + meta 元数据 + 会话审计） | 业务 + 应用元数据 |
| 私有知识库 RAG | **自建 RAG**（Qdrant 向量库 + 重排） | 可控、可定制、学习价值高 |
| 向量库 | Qdrant（自托管） | shopkeeper 已验证，轻量 |
| 缓存/会话/限流/队列 | **Redis** | 补 deepsearch 缺失的生产能力 |
| 后端接口 | FastAPI + WebSocket / SSE | 长任务用 WS 推送 |
| 监控 | monitor 事件 + 日志 + Langfuse 追踪 + Prometheus | 补生产可观测 |
| 前端 | React + Vite + AntD + Tailwind | 复用 |

---

## 四、关键决策

### 4.1 需不需要 MySQL
**需要**，分两层：① 垂直领域结构化业务数据（订单/用户）→ dw 数仓；② C 端应用元数据（会话/任务/消息/审计）→ 权威持久化。Redis 做加速层不替代。

### 4.2 编排方式
**三层图**（企业级细化，见 §五）：
- 第 1 层入口图：会话 + 意图路由 + 澄清 + 快问分流
- 第 2 层任务图：规划→执行→反思→校验→交付的主状态机
- 第 3 层 Worker 子图：SQL/RAG/Web 各自独立 ReAct 子图

### 4.3 自动思考
**规划-执行-反思循环**，主智能体走反射循环（先计划→逐项执行→反思是否补查），单 worker 内部走 ReAct（思考→调工具→观察→再思考）。

### 4.4 RAG 设计
`chunk → embed → 混合召回 → 重排 → 带引用生成`：
- 离线：文档解析 → 分块 → embedding → 写 Qdrant
- 在线：query → embedding → 向量召回(top-k) + BM25 → RRF 混合 → bge-reranker 重排 → 拼 prompt → 生成 + 引用

分块与 embedding 按领域选（中文优先 `bge-large-zh` / `bge-m3`）。

### 4.5 向量库
**Qdrant 自托管**（纯向量、性能好、API 简单、Docker 一行起）。

### 4.6 Redis 的角色
分层持久化：会话检查点（RedisSaver）、消息历史（MySQL）、向量（Qdrant）、缓存（Redis）、限流（Redis）、队列（Redis）。核心价值是**快而可丢**的加速层，永不承担权威存储。

### 4.7 监控
四层：monitor 实时事件 + loguru 日志（request_id 串联）+ Langfuse 链路追踪（token/延迟/成本）+ Prometheus 指标（QPS/错误率/成本）。

---

## 五、LangGraph 三层图编排（核心细化）

### 5.1 三层图拓扑

```
┌─ 第 1 层：入口图 SessionGraph（多轮会话级）─────────────┐
│  START → task_parser → intent_router                   │
│     ├─ 口径/时间范围不明 ──▶ ⏸ interrupt（人工澄清）      │
│     ├─ 纯知识问答 ────────▶ 快问子图（rag-only，省预算）  │
│     └─ 分析任务 ──────────▶ 进入任务图                   │
└────────────────────────────────────────────────────────┘
                    ▼
┌─ 第 2 层：任务图 TaskGraph（单次分析任务级）──────────────┐
│  planner ─▶ fan_out(Send 并行) ─▶ join ─▶ reflect        │
│    ▲                                      │ 缺口&未超限     │
│    └──── replanner ◀──────────────────────┘               │
│  reflect ─▶ validator ─▶ synthesizer ─▶ deliver ─▶ END    │
│                 │不通过        │⏸ 报告方向确认(interrupt)   │
│                 ▼              │                          │
│               fixer ──────────┘                           │
└────────────────────────────────────────────────────────────┘
                    ▼
┌─ 第 3 层：Worker 子图（每个专家独立 ReAct 子图）───────────┐
│  sql_worker ｜ rag_worker ｜ web_worker                   │
│  自带：工具集 / 重试策略 / 超时 / 权限边界 / 单独可测        │
└──────────────────────────────────────────────────────────┘
```

**三层职责**：

| 层 | 职责 | 生命周期 | 对应 LangGraph 能力 |
|----|------|---------|---------------------|
| 入口图 | 会话上下文、意图路由、澄清、快问分流 | 多轮对话全程 | `thread_id` + RedisSaver 持久化 |
| 任务图 | 规划→执行→反思→校验→交付主状态机 | 单次任务 | 主 `StateGraph` + 条件边/循环 |
| Worker 子图 | 领域专家内部 ReAct 循环 | 单步执行 | 节点挂子图，失败隔离、独立重试 |

**子图三大价值**：① 工具权限隔离（sql_worker 才持有 DB 工具）；② 单点故障不影响其他 worker；③ 每个 worker 可脱离主图单独单测。

### 5.2 State 设计

```python
# state.py —— 核心
from typing import Annotated, TypedDict, Any
from langgraph.graph.message import add_messages
import operator

class TaskStatus(str, Enum):
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"   # 人工中断中
    DONE = "done"
    FAILED = "failed"

class Budget(TypedDict):
    max_iterations: int      # 反思循环上限（建议 3）
    max_steps: int           # 工具调用总次数上限（防 runaway）
    max_cost_cents: float    # 单任务成本上限

class AnalystState(TypedDict):
    # ── 会话层 ──
    messages: Annotated[list, add_messages]
    thread_id: str
    user_id: str
    # ── 任务层 ──
    task: dict | None          # {intents, metrics, time_range, dims, compare}
    plan: list | None          # [{type, query, target, deps}]
    worker_results: Annotated[list, operator.add]   # 每条必带 source_ref
    evidence_gaps: Annotated[list, operator.add]
    artifacts: Annotated[list, operator.add]
    # ── 控制层 ──
    iteration: int
    budget: Budget
    errors: Annotated[list, operator.add]
    fallbacks_used: Annotated[list, operator.add]
    # ── 交付层 ──
    report: dict | None
    status: TaskStatus
```

**三条设计要点**：
1. **错误是数据不是异常**：worker 失败写入 `state.errors`，主图用条件边决定降级，而非让异常穿透中断整图——这是企业编排与 demo 编排的分水岭。
2. **reducer 契约**：`worker_results / errors / fallbacks_used` 用 `operator.add` 累加；`task / plan / report` 用 replace。加字段不改已有 reducer 语义，保证 Redis 旧 checkpoint 兼容。
3. **`iteration` 是显式计数器**：由 `reflect` 自增、条件边读取，比用消息条数推断循环次数可靠。

### 5.3 节点职责与路由矩阵

| 节点 | 职责 | 失败/降级行为 |
|------|------|--------------|
| `task_parser` | 意图+指标+时间范围+维度解析；相对时间转绝对日期 | 解析失败→降级通用问答 |
| `intent_router` | 三路分流（澄清/快问/分析） | 无法判定→默认澄清 interrupt |
| `planner` | 输出子任务计划（含依赖、优先级），先做口径对齐 | 校验失败→重试 1 次→模板计划 |
| `fan_out` | 按计划动态并行分派（Send API） | — |
| `sql_worker` | Text-to-SQL→只读执行→结构化表格 | 连库失败重试 2 次→口径 RAG 兜底 |
| `rag_worker` | 混合召回+重排，结果带 chunk 引用 | rerank 不可用→直用向量 top-k |
| `web_worker` | 外部搜索/业务 API | 失败→标记 gap，报告注明 |
| `join` | 汇聚结果，去重排序打来源标签 | — |
| `reflect` | 量化充分性（覆盖率/引用数/置信度），产出 gaps，iteration+1 | — |
| `replanner` | 针对缺口生成补充子任务 | 重复同缺口→计入 iteration 触发守卫 |
| `validator` | 质量门：数字有 source_ref、SQL 一致性抽查、口径一致 | 校验器自身失败→放行并记 warn |
| `fixer` | 针对失败项局部修正 | 超 2 轮→放弃修正，报告标注 |
| `synthesizer` | 生成结构化报告，数字一律取 worker_results | 失败→重试→降级 Markdown 摘要 |
| `deliver` | 写产物、落审计、推事件、回写缓存（幂等） | 推送失败不影响产物落盘 |

**路由矩阵**：

```
task_parser ──(口径不明)──▶ ⏸ clarify_interrupt ──resume──▶ planner
task_parser ──(知识问答)──▶ rag_worker(单发) ──▶ deliver
task_parser ──(分析任务)──▶ planner

reflect ──(gaps 空 或 iteration>=max)──▶ validator
reflect ──(有 gaps 且 未超限)──────────▶ replanner ──▶ fan_out

validator ──(通过)──▶ synthesizer
validator ──(不通过)──▶ fixer ──(≤2 轮)──▶ synthesizer

synthesizer ──▶ ⏸ report_confirm(interrupt_before) ──resume──▶ deliver
```

### 5.4 核心代码骨架

```python
# ============ graph.py（任务图主体） ============
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from langgraph.checkpoint.redis import RedisSaver

# 动态并行分派：子任务数不固定，必须用 Send API
def fan_out(state):
    sends = []
    for t in state["plan"]:
        worker = {"sql": "sql_worker", "rag": "rag_worker", "web": "web_worker"}[t["type"]]
        sends.append(Send(worker, {"task": t}))
    return sends

def route_after_reflect(state):
    if state["iteration"] >= state["budget"]["max_iterations"]:
        return "validator"          # 闸 1：迭代上限
    if not state["evidence_gaps"]:
        return "validator"          # 信息充分
    return "replanner"              # 有缺口 → 补查

def route_after_validate(state):
    return "synthesizer" if state["report_ok"] else "fixer"

builder = StateGraph(AnalystState)
builder.add_node("task_parser", task_parser)
builder.add_node("planner", planner)
builder.add_node("fan_out", fan_out)
builder.add_node("sql_worker", sql_worker_graph)   # 子图
builder.add_node("rag_worker", rag_worker_graph)
builder.add_node("web_worker", web_worker_graph)
builder.add_node("join", join_results)
builder.add_node("reflect", reflect)
builder.add_node("replanner", replanner)
builder.add_node("validator", validator)
builder.add_node("fixer", fixer)
builder.add_node("synthesizer", synthesizer)
builder.add_node("deliver", deliver)

builder.add_edge(START, "task_parser")
builder.add_conditional_edges("task_parser", route_intent,
    {"clarify": "clarify_interrupt", "quick": "rag_worker", "analyze": "planner"})
builder.add_edge("planner", "fan_out")
builder.add_edge("fan_out", "join")
builder.add_edge("join", "reflect")
builder.add_conditional_edges("reflect", route_after_reflect,
    {"validator": "validator", "replanner": "replanner"})
builder.add_edge("replanner", "fan_out")          # 循环回边
builder.add_conditional_edges("validator", route_after_validate,
    {"synthesizer": "synthesizer", "fixer": "fixer"})
builder.add_edge("fixer", "synthesizer")
builder.add_edge("synthesizer", "deliver")
builder.add_edge("deliver", END)

graph = builder.compile(
    checkpointer=RedisSaver.from_conn_info(host="127.0.0.1", port=6379),
    interrupt_before=["synthesizer"],   # 报告方向人工确认
)
```

```python
# ============ 节点级健壮性封装（worker 通用） ============
async def safe_call(fn, *, budget, node, state, retries=3, timeout_s=60):
    """统一封装：预算检查 → 重试退避 → 超时 → 错误进 state 而非抛出"""
    if state["budget"]["max_steps"] - used(state) <= 0:
        return BudgetExceeded(node=node)
    for i in range(retries):
        try:
            return await asyncio.wait_for(fn(), timeout=timeout_s)
        except Exception as e:
            if i == retries - 1:
                state["errors"].append(ErrorRecord(node=node, exc=str(e), ts=now()))
                return FallbackNeeded(node=node)
            await asyncio.sleep(2 ** i + random.random())
```

---

## 六、企业级健壮性机制（10 项）

### 6.1 循环守卫（双闸）
- 闸 1：`max_iterations`（建议 3）—— iteration≥max 强制进 validator
- 闸 2：`budget`（max_steps / max_cost_cents）—— 每节点执行前检查，超预算直接交付降级
- 由条件边 + 节点内预算检查**双重**实现；预算用完后报告开头显式声明未覆盖指标，**不静默降级**。

### 6.2 重试与超时
| 对象 | 策略 |
|------|------|
| LLM 调用 | 指数退避+抖动 3 次；失败换小模型 |
| SQL 连接 | 退避重试 2 次→口径 RAG 兜底 |
| 节点级超时 | `asyncio.wait_for`（SQL≤30s，LLM≤60s） |
| 任务级超时 | 整体 deadline（建议 5min）→ 输出部分结果+失败说明 |

### 6.3 降级矩阵
| 故障 | 降级路径 | 用户可见提示 |
|------|---------|-------------|
| sql_worker 连库失败 | → RAG 口径文档定性回答 | "数据库暂不可用，以下为基于口径文档的定性结论" |
| Text-to-SQL 生成/校验失败 | 重试→预置 SQL 模板兜底 | "该指标暂无法精确查询" |
| LLM 超时/限流 | 重试 3 次→换小模型 | 无（透明重试） |
| 外部搜索失败 | 跳过该来源 | "未检索到外部数据" |
| rerank 不可用 | 直用向量 top-k | 无 |

原则：**能出结果就出结果**，所有降级路径记录 `fallbacks_used` 并写入报告/审计。

### 6.4 幂等
- 每次工具调用生成 `call_id`，执行前 `Redis SETNX(call_id)` 去重（防 checkpoint 恢复后重复副作用）。
- 副作用操作（写产物、推事件、落审计）只在 `deliver` 集中执行且带幂等键。
- 恢复场景：checkpoint 重启后，通过 `worker_results` 的 task_id 集合跳过已完成步骤。

### 6.5 缓存
| 缓存对象 | key | TTL |
|---------|-----|-----|
| SQL 查询结果 | `sha256(db+sql+params)` + user 隔离 | 订单类 5min，维度类 30min |
| RAG 检索结果 | query embedding 近似 + top_k | 30min |
| 报告模板/指标口径定义 | 静态 | 永久 |

### 6.6 人工断点（HITL）
- `interrupt_before = ["synthesizer"]`：报告方向确认，用户可改口径/维度后 resume。
- `clarify_interrupt`（task_parser 后）：时间范围/指标口径不明时问清再规划。
- 实现：`checkpointer` + `interrupt_before`，恢复用 `Command(resume=...)`。
- 经营分析涉及业务决策，关键节点人工把关是 C 端信任的护城河。
- 支持 `thread_id` 复用实现"追问上次报告为什么下降"（历史 state 自动带上）。

### 6.7 失败隔离与全局错误处理
- 每个 worker 子图内部 `try/except`，异常转 `ErrorRecord` 进 state，绝不抛圈子图。
- `errors` 达到阈值（≥3）→ 条件边直接进 `deliver` 出降级报告。
- 图最外层再包一层兜底 `except`：未捕获异常 → 返回结构化失败消息 + thread_id。

### 6.8 安全（Text-to-SQL 是 C 端最高风险面）
- 数据库**只读账号**（GRANT SELECT），禁 DDL/DML；`max_execution_time` + 强制 LIMIT。
- 表/列**白名单**：由元数据字典生成，SQL 生成后解析校验仅含白名单标识符。
- 参数值一律参数化，禁止字符串拼接。
- 敏感列（手机号/身份证）查询后**脱敏**再进 worker_results。
- LLM 输出全部 `Pydantic` 校验（防格式幻觉与注入溢出）。

### 6.9 可观测
| 层 | 工具 | 指标 |
|----|------|------|
| LLM 链路 | Langfuse 回调 | prompt、token、延迟、成本 |
| 图指标 | Prometheus+Grafana | 执行数、成功率、节点 p50/p95、循环次数、缓存命中率 |
| 日志 | loguru + request_id | 节点进出、降级触发、错误明细 |
| 事件 | monitor + WebSocket | 复用 `graph.stream(stream_mode="updates")` 逐节点推送 |

### 6.10 状态演进与测试
- schema 演进：State 加字段只加不改；`schema_version` 写入 checkpoint；旧 checkpoint 缺失字段用默认值。
- 测试体系：worker 单测（fake 工具）→ 图级集成测试（`graph.invoke` 断言 state）→ LangSmith 数据集回归 → 混沌测试（模拟超时/失败验证降级路径）。

---

## 七、数据库与持久化设计

### 7.1 MySQL Schema（权威存储）

| 表 | 职责 | 与编排的关系 |
|----|------|-------------|
| `users` | 用户/套餐/配额 | 鉴权、限流、配额判定 |
| `conversations` | 会话 | **thread_id ↔ LangGraph 检查点** |
| `messages` | 消息历史（含工具消息） | 审计、历史会话恢复（replay） |
| `tasks` | 任务记录 | 与 `AnalystState` 一一对应 |
| `task_events` | 节点/工具/错误事件审计 | 图执行的审计留痕 |
| `evidence` | 证据/引用（溯源核心） | **validator 校验 source_ref 的落点** |
| `reports` | 报告产物 | `deliver` 写产物 |
| `metric_dict` | 指标口径字典 | `planner` 口径对齐的数据源 |
| `quota_usage` | 配额日账 | C 端防刷 |
| `audit_log` | 安全审计 | 合规留痕 |

> `evidence` 表是经营分析的**数字信任核心**：报告里每个数字都要能通过 `source_ref` 反查到原始数据（SQL query_id+行键 / RAG chunk_id / web url），validator 质量门校验的就是这条链。

关键表字段要点：
- `conversations.thread_id` UNIQUE —— LangGraph 检查点锚点。
- `tasks` 含 `iteration / max_iterations / steps_used / max_steps / cost_cents` —— 与循环守卫双闸一一对应。
- `task_events.call_id` —— 幂等键，与 Redis `dedup:{call_id}` 对应。
- `reports.status`（draft/confirmed/delivered）—— 与 interrupt 人工确认联动。
- `evidence` UNIQUE `(task_id, source_ref)` —— 防重复证据。

### 7.2 持久化分层与 Redis key

| 数据 | 存储 | 角色 | TTL |
|------|------|------|-----|
| LangGraph 检查点/热状态 | Redis（RedisSaver） | 断点续跑、interrupt/resume、多轮上下文 | 会话期+7d |
| 会话/消息/任务/证据/报告 | MySQL | 权威持久化、审计、历史恢复 | 永久 |
| 向量 | Qdrant | RAG 语义检索 | 永久 |
| SQL 查询/检索结果缓存 | Redis | 降延迟 | 5–30min |
| 限流/配额计数 | Redis | C 端防刷 | 窗口期 |
| 产物文件 | 本地盘→OSS | 交付物 | 永久 |
| 重任务队列 | Redis Stream/Celery | 长任务异步 | — |

**Redis key 规范**：

| key | 用途 |
|-----|------|
| `checkpoint:{thread_id}:{checkpoint_id}` | 图状态检查点 |
| `session:{thread_id}` | 会话热状态 |
| `dedup:{call_id}` | 工具调用幂等（SETNX） |
| `cache:sql:{user_id}:{sha256(...)}` | SQL 结果缓存 |
| `rate:{user_id}:{window}` | 接口限流 |
| `quota:{user_id}:{yyyy-mm-dd}` | 日配额计数 |

### 7.3 写路径与一致性（先热后冷）

```
执行中（每 super-step）：checkpoint → Redis   （快、可丢，支撑断点续跑）
关键节点/完成（deliver）：统一落 MySQL       （权威、审计，带幂等键）
缓存：SQL/检索结果带 TTL 回写 Redis
崩溃恢复：图执行从 Redis checkpoint 续跑（秒级）
历史会话恢复：MySQL replay messages → 重建 checkpoint
```

**一致性原则**：Redis 是「快而可丢」的加速层，**永不承担权威存储**；MySQL 是唯一权威。所有写 MySQL 集中在 `deliver`（幂等），中途崩溃最多丢热状态，权威数据不丢。

---

## 八、目录结构

```text
smart-analyst/
├── app/
│   ├── agent/                  # 智能体编排
│   │   ├── graph.py            # 任务图主体（三层图的第 2 层）
│   │   ├── session_graph.py    # 入口图（第 1 层）
│   │   ├── state.py            # AnalystState 状态定义
│   │   ├── context.py          # 运行时资源绑定（client/repository）
│   │   ├── nodes/              # 任务图节点（parser/planner/reflect/validator/...）
│   │   └── subgraphs/          # Worker 子图（第 3 层）
│   │       ├── sql/            #   sql_worker（Text-to-SQL 全链路）
│   │       ├── rag/            #   rag_worker（混合召回+重排）
│   │       └── web/            #   web_worker（搜索）
│   ├── api/
│   │   ├── server.py           # FastAPI 路由/WebSocket
│   │   ├── context.py          # ContextVar 会话上下文
│   │   └── monitor.py          # 事件推送（基于 graph.stream）
│   ├── rag/                    # 自建 RAG 离线模块
│   │   ├── chunker.py          # 文档分块
│   │   ├── embedder.py         # 向量化
│   │   ├── retriever.py        # 混合召回+重排
│   │   └── build_kb.py         # 知识库离线构建脚本
│   ├── clients/                # 外部服务客户端管理（llm/qdrant/redis/mysql/embedding）
│   ├── tools/                  # 子智能体可用工具
│   ├── models/                 # SQLAlchemy ORM（MySQL 表）
│   ├── repositories/           # 仓储层（业务语义封装 client）
│   ├── conf/                   # 配置 dataclass（OmegaConf 解析 yaml）
│   ├── entities/               # 业务实体（ColumnInfo/DocChunk/...）
│   ├── mappers/                # entity ↔ ORM 转换
│   ├── core/                   # 日志、trace、限流中间件
│   └── scripts/                # 构建脚本（build_meta_knowledge / build_doc_knowledge）
├── conf/                       # app_config.yaml / meta_config.yaml
├── docker/                     # docker-compose + 初始化 SQL
├── docs/knowledge_base/        # 知识库示例文档
├── frontend/                   # React + Vite
├── .env.example
└── pyproject.toml
```

> 分层原则（沿用 shopkeeper）：`client（技术层连服务）→ repository（业务层转实体）→ agent/tool（业务使用）`，各层只依赖下层。

---

## 九、从 0 到 1 开发路线（里程碑法）

> 每个里程碑都能"跑起来"验证，先骨架后血肉、自底向上。

| 里程碑 | 内容 | 验证标准 |
|--------|------|----------|
| **M0 地基** | 环境+配置+目录骨架+`app_config.py` | `import app_config` 不报错 |
| **M1 基础设施** | docker-compose 起 MySQL+Redis+Qdrant | 三服务 Up，client 能连 |
| **M2 客户端层** | llm/qdrant/redis/mysql 四个 client 单例 | 各自连通可读写 |
| **M3 RAG 离线** | 文档分块→embed→写 Qdrant | 知识库建成能召回 |
| **M4 工具层** | RAG 检索/SQL 查询/外部工具+仓储层 | 每个工具单独可调 |
| **M5 多智能体** | 三层图骨架+State+空 worker→逐层实现 | 图能 compile，跑通完整任务 |
| **M6 接口层** | FastAPI+WebSocket+monitor 推送 | curl/前端收到事件流 |
| **M7 持久化+监控** | Redis checkpointer+限流+日志+Langfuse | 会话跨重启可恢复 |
| **M8 前端联调** | React 对话界面+步骤流+结果展示 | 浏览器端到端跑通 |

**M5 拆分（先骨架后血肉，顺序原则）**：

| 步骤 | 内容 | 验收 |
|------|------|------|
| M5.1 | 三层图骨架+State+空 worker 子图 | `graph.invoke` 空跑通过，checkpoint 可存 |
| M5.2 | fan_out Send 并行+join 汇聚 | 3 个 mock worker 并行返回并正确汇聚 |
| M5.3 | reflect 量化判断+循环守卫+replanner | 注入缺口可触发补查，超限强转 validator |
| M5.4 | validator+fixer 质量门 | 注入"无 source_ref 数字"被拦截 |
| M5.5 | 人工断点：澄清+报告确认 | interrupt/resume 全流程可用 |
| M5.6 | 降级矩阵+幂等+缓存 | 杀 MySQL 后任务走 RAG 兜底出报告 |

> **先把守卫/降级/断点这类骨架机制搭好，再填专家血肉** —— 机制是结构性的，后补要改图，成本高。

---

## 十、已确认决策

| 决策点 | 结论 |
|--------|------|
| 项目方向 | AI 经营分析官（Smart Analyst）：自然语言问数 + 自动撰写经营分析报告 |
| 领域场景 | 电商/零售经营数据（可替换：连锁门店、金融等） |
| 编排拓扑 | 三层图：入口图（会话+意图路由）/ 任务图（主状态机）/ Worker 子图（领域 ReAct） |
| 子智能体分工 | SQL 查询助手 + RAG 知识助手 +（可选）网络搜索助手 |
| 多智能体实现 | LangGraph 手写编排（吃透底层，官方框架） |
| 自动思考 | 规划-执行-反思循环 + 量化充分性判断（覆盖率/引用数/置信度） |
| 质量保障 | 独立 validator 质量门 + fixer 局部修正 + 人工断点（澄清/确认） |
| 健壮性 | 预算双闸 + 降级矩阵 + 幂等 + 缓存 + 失败隔离 |
| 大模型 | OpenAI 兼容，默认 DeepSeek/Qwen（.env 可换） |
| 向量库 | Qdrant 自托管 |
| MySQL | 需要（dw 数仓 + meta 元数据 + 会话/任务/审计 10 张表） |
| Redis | 需要（会话检查点 + 限流 + 缓存 + 队列 + 幂等） |
| RAG 文档来源 | 业务口径文档 + 行业分析知识（先示例，后替换） |
| 新项目目录 | `D:\项目\agent\smart-analyst`（与参考项目并列） |