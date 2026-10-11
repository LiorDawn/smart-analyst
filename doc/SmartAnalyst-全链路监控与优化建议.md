# SmartAnalyst 全链路监控与优化建议

> 目标：让「自然语言问数 → 自动撰写经营分析报告」这条链路从「能跑」到「可观测、可定位、可信任」，并为上线做可运维准备。
> 原则：**错误是数据，不是异常**——监控的目标不是消除所有失败，而是让每次失败可追溯、可复现、可降级。

---

## 一、现状盘点（可观测性基线）

| 维度 | 现状 | 缺口 |
|------|------|------|
| 日志 | 依赖已声明 `loguru`，仅 `writer.py` 用标准 `logging` | 未统一结构化、无 trace_id、无节点级日志规范 |
| 链路追踪 | 无 | 一次问数经过 12 个节点，失败时无法定位「卡在哪一步」 |
| 指标埋点 | 无 | 无节点耗时/token 消耗/重试次数统计 |
| 状态持久化 | `graph.compile(checkpointer=InMemorySaver())` 内存态 | 进程重启即丢，无法断点续跑、无法审计 |
| 依赖健康 | 无探针 | MySQL/Redis/Qdrant/Embedding/LLM 任一挂掉无预警 |
| 业务质量 | `errors` state 字段（错误是数据）+ reviewer 质量门 | 无闸命中率、降级次数等运营指标汇总 |

已有可复用的底子：
- `pyproject.toml` 已声明 `loguru`、`langgraph-checkpoint-redis`、`redis`、`elasticsearch[async]`、`aiomysql`、`sqlalchemy` —— 监控所需的存储/日志能力**依赖都在，只是没接线**。
- `AnalystState.errors`（累加）+ `status`（TaskStatus.value）已贯彻「错误是数据」。
- reviewer/fixer 质量门已闭环（可溯源校验 → 如实标注降级交付）。

---

## 二、全链路监控方案（四层：Trace / Metrics / Logs / Health）

### 2.1 链路追踪（Trace）—— 一次问数的全生命周期

核心：**一个 `trace_id` 贯穿 config → state → 每个节点 → 日志 → 落库**。

1. **trace_id 生成与注入**
   - 入口（`task_parser` 或 API 层）生成 `trace_id = uuid4().hex[:16]`。
   - 通过 LangGraph `config={"configurable": {"trace_id": ..., "user_id": ...}}` 传入，节点内经 `context.py`（已有）读取；落入 `AnalystState` 的一个只读字段或直接写 `status`/`errors` 的上下文。

2. **节点级埋点（不入侵业务逻辑）**
   - 用 LangGraph 的 `astream_events` / `callbacks`（`BaseCallbackHandler`）统一在**图外层**采集，而非在每个节点里手写计时：
     - 每个 `on_chain_start` / `on_chain_end` 事件 → 记录 `node_name`、`耗时`、`输入/输出字节`。
     - `on_chat_model_start` / `on_chat_model_end` → 记录 LLM 调用的 `token 消耗`、`模型名`、`temperature`。
   - 好处：新增节点自动纳入，无需逐个改。

3. **状态快照（审计 + 断点续跑）**
   - `checkpointer` 从 `InMemorySaver` 换成 `RedisSaver`（`langgraph-checkpoint-redis` 已声明）：
     ```python
     from langgraph.checkpoint.redis import RedisSaver
     saver = RedisSaver.from_conn_string(redis_url)
     builder.compile(checkpointer=saver)
     ```
   - 效果：进程重启后可 `resume` 同一 `thread_id`；每次 checkpoint 即一份天然审计轨迹。

### 2.2 指标（Metrics）—— 让「跑得好不好」可量化

落地建议：先不引入 Prometheus 等重型栈，**用 Redis 原子累加 + 定时刷 MySQL**，后续再可平滑替换为真实指标系统。

| 指标 | 采集点 | 用途 |
|------|--------|------|
| 端到端耗时 | 入口 → deliver | 整体健康度 |
| 各节点耗时分布 | callbacks 每节点 | 定位慢节点 |
| LLM token 消耗 | `on_chat_model_end` | 成本、限流依据 |
| 分流命中率 | `route_intent` | clarify/quick/analyze 占比 |
| 闸命中率 | clarify/reflect/reviewer | 澄清率、证据缺口率、质量门不通过率 |
| 降级次数 | `errors` 累加 + `degraded` 指标 | 数据质量趋势 |
| 结论段 LLM 失败率 | `writer._llm_conclusion` 的 warn 次数 | 报告是否经常缺结论 |

### 2.3 日志（Logs）—— 结构化 + 可检索

统一到 `loguru`，一条日志可检索：

```python
logger.bind(trace_id=trace_id, node="query_executor", step="exec")
    .info("exec_done", metric="营收", cost_ms=123, tokens=456)
```

约定：
- **每条日志带 `trace_id + node + step`** 三个检索键。
- 关键节点（`planner` 产出 query 数、`query_executor` 每 query 成败、`sql` 或数据源、`reviewer` 检出 issues、`fixer` 标注项）打 `info`。
- 丢失数据/降级路径打 `warning` 而非 `error`（错误是数据，降级不报警，趋势异常才报警）。

### 2.4 依赖健康（Health）—— 探针前置

在链路入口前做轻量探针（可复用 `llm_client.check()` 现成思路）：

| 依赖 | 探针方式 | 失败策略 |
|------|----------|----------|
| MySQL | `SELECT 1` | 降级：走已有知识库/缓存 → 标注 degraded |
| Redis | `ping()` | checkpoint 不可用 → 回退 InMemory 并 warn |
| Qdrant | `collection` 存在性 | 混合召回降级矩阵（已实现） |
| Embedding | 最小向量试算 | 召回降级单路/全文 |
| LLM | `llm_client.check()` | 结论段省略、分析降级（已实现部分） |

---

## 三、优化建议（按优先级）

### P0 —— 上线阻塞项
1. **checkpoint 落 Redis**：替换 `InMemorySaver`，否则生产无法 resume、无法审计。依赖已声明，改动只在 `graph.py` 一处。
2. **trace_id 贯穿 + 结构化日志**：全链路定位的前提，改动集中在 `context.py` + 入口，配合 callbacks 采集。
3. **补 API 入口**：`pyproject` 已声明 `fastapi[standard]`，但项目**尚无 `main.py`**。需补 FastAPI 网关（`POST /ask` → `graph.ainvoke`），这是「启动」的落点。

### P1 —— 上线前
4. **节点级埋点**（callbacks/astream_events）：耗时 + token，零业务侵入。
5. **依赖健康探针**：入口前置，配合降级矩阵形成闭环。
6. **结论段监控**：`_llm_conclusion` 失败率、单次超时上限（`ainvoke` 目前无超时），避免 LLM 挂起拖垮整条链路。

### P2 —— 迭代期
7. **LLM 调用统一收敛到 client 层**：加超时/重试/缓存/限流（现在各节点直连 `llm_client_manager`，易失控）。
8. **成本与质量看板**：把 2.2 的指标汇总成护简盘点，喂给运营。
9. **离线部署复核**：MinerU 云端（flash）仅开发期使用，上线前切本地解析（对齐项目「数据不出网」硬约束）。

---

## 四、落地分阶段清单

| 阶段 | 交付物 | 涉及文件 |
|------|--------|----------|
| ① 接线监控底座 | Redis checkpoint + trace_id + 结构化日志 | `graph.py`、`context.py`、入口 |
| ② 采集 | callbacks 采集耗时/token + 健康探针 | 新增 `app/observability/`（callback、metrics、probe） |
| ③ 汇总 | 指标落 Redis/MySQL + 简版看板 | `app/observability/` + 可选 API |
| ④ 门内嵌 | 闸命中率、降级次数告警阈值 | 各闸节点 + 指标 |

> 建议新增 `app/observability/` 目录集中承载 tracing/metrics/logging/probe，保持分层（observability → graph 单向依赖，不反向侵入业务节点）。

---

## 五、待补充

- **参考项目对齐**：本方案基于通用可观测性实践 + 项目已有依赖拟定。用户计划参考的 `deepsearch`、`shopkeeper` 项目在 `PycharmProjects` 下**未检索到**（`DeepAgent/` 仅 `02-quickstart-streaming`、`.venv`、`smart-analyst`）。拿到这两个项目的真实路径后，可补充与 mall-swarm 架构对齐的监控命名/埋点规范，做针对性增补。