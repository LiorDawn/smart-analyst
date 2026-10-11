# AI 经营分析官（Smart Analyst）—— 架构设计文档

> 版本 v0.2（方向已定，待审核）
> 技术底座：LangGraph + LangChain + Redis
> 参考项目：`deepsearch-agents`（多智能体编排）+ `shopkeeper-agent`（分层 / RAG / 向量检索 / Text-to-SQL）

---

## 一、项目定位

一个面向 C 端的**经营数据分析工具**。核心能力 = **多智能体自动思考 + RAG 检索 + 工具调用 + 文件交付**，让用户用自然语言问数、自动分析并产出报告。

一句话区别于"套壳问答"：它**不是**只调一次大模型，而是会规划、会分工、会查数、会查知识库、会反思，最后产出 Markdown / PDF 分析报告。

> ✅ 方向已定：**AI 经营分析官（Smart Analyst）** —— 连接业务数据库，自然语言问数 + 自动撰写经营分析报告。融合 shopkeeper 的 Text-to-SQL / RAG / 分层 + deepsearch 的多智能体 / 自动思考 / 文件交付。

---

## 二、整体架构与链路

采用 `deepsearch-agents` 验证过的 **Orchestrator-Workers（一主多从）** 模式，叠加 `shopkeeper-agent` 的**清晰分层**。

```
用户任务
   │  (HTTP / WebSocket)
   ▼
FastAPI 接口  ──▶  鉴权 / 限流(Redis) / 会话上下文(ContextVar)
   │
   ▼
主智能体 Orchestrator（规划 + 调度 + 汇总 + 交付）
   │  ① 自动思考：分析任务 → 制定计划
   │  ② 分派给专家子智能体（并行 / 串行）
   ├──▶ 子智能体 A：RAG 检索助手（私有知识库）
   ├──▶ 子智能体 B：结构化查询助手（MySQL / Text-to-SQL）
   └──▶ 子智能体 C：外部工具助手（搜索 / 业务 API）
   │  ③ 汇总多来源 → 反思"信息是否足够"
   │     └─ 不够 → 回到 ② 继续补充（循环）
   │  ④ 生成最终结果（Markdown / 结构化 JSON / 文件）
   ▼
monitor 通过 WebSocket / SSE 实时推送进度
   ▼
前端展示：步骤流 + 引用的来源 + 最终答案
```

**两条主线**：

| 主线 | 内容 |
|------|------|
| 智能体执行 | 规划 → 分派 → 检索/查询 → 汇总 → 反思 → 交付 |
| 前后端实时联动 | 接口接收 → 后台执行 → 事件推送 → 前端展示 |

---

## 三、技术选型对照

| 模块 | deepsearch-agents 用 | 本项目（LangGraph + LangChain + Redis） | 理由 |
|------|---------------------|----------------------------------------|------|
| 多智能体框架 | DeepAgents 0.5.7 | LangGraph 手写编排（可选用 langgraph-supervisor） | 用户指定，且更能精细控制编排 |
| 底层图/检查点 | LangGraph + InMemorySaver | LangGraph + **Redis 持久化 Checkpointer** | 突破内存 saver 不能跨重启的短板 |
| 模型接入 | OpenAI 兼容 | OpenAI 兼容（可换 DeepSeek/Qwen/硅基流动） | 统一 base_url + key |
| 网络检索 | Tavily | 可选（领域相关，可换业务 API） | 按需 |
| 结构化数据 | MySQL | MySQL（或 Postgres） | 业务数据 + 应用元数据 |
| 私有知识库 RAG | RAGFlow（外部完整引擎） | **自建 RAG**（向量库 + 重排） | 更可控、可深度定制、学习价值高 |
| 向量库 | RAGFlow 自带 | **Qdrant**（或 ES 混合检索） | shopkeeper 已验证，轻量自托管 |
| 缓存/会话/限流/队列 | ❌ 无 | **Redis** ★ | 用户指定，补上 deepsearch 缺失的生产能力 |
| 文件处理 | pypdf/docx/pandas/reportlab | 按领域裁剪 | 按需 |
| 后端接口 | FastAPI + WebSocket | FastAPI + WebSocket / SSE | 长任务用 WS 推送 |
| 监控 | 仅 monitor 事件推送 | **monitor 事件 + 日志 + 链路追踪(Langfuse) + 指标(Prometheus)** | 补生产监控 |
| 前端 | React + Vite + AntD + Tailwind | 同左 | 复用 |

---

## 四、关键技术决策（逐个回答）

### 4.1 需不需要 MySQL？

**结论：需要，但作用分两层，定位要清楚。**

| 用途 | 是否必需 | 说明 |
|------|---------|------|
| ① 垂直领域的结构化业务数据 | 视领域而定 | 有实体数据（订单/用户/商品）则要，纯文档问答可省 |
| ② 应用元数据（会话、任务、消息历史、用户） | **C 端生产应用几乎必需** | 持久化、审计、历史会话恢复 |

- 纯"文档 RAG 问答"且不做审计 → 可暂省 MySQL，用 Redis + Qdrant 就能跑。
- **本方案默认建议保留 MySQL**：C 端要持久化会话 + 审计 + 可能有结构化数据，三重需求叠加，MySQL（或 Postgres）是最稳的选型。
- Redis 作为**加速层**配合，而不是替代 MySQL（Redis 是内存缓存，不做权威持久化）。

### 4.2 节点 / 多智能体怎么编排

**结论：Orchestrator-Workers 一主多从。**

- **主智能体**：任务理解、制定计划、调度子智能体、汇总结果、反思是否足够、交付。
- **子智能体**（按领域分工，每个是独立 agent，有自己的工具和提示词）：
  - RAG 检索助手：查私有知识库，带引用。
  - 结构化查询助手：查 MySQL，必要时 Text-to-SQL（复用 shopkeeper 思路）。
  - 外部工具助手：网络搜索 / 业务 API。

编排实现二选一（文档默认 LangGraph 手写）：

```
LangGraph 手写：显式定义 routing 边 + 条件边 + 循环边
langgraph-supervisor：声明式子智能体，自动路由（更省事）
```

推荐先用 `langgraph-supervisor` 快速搭起骨架，需要精细控制时再降级到手写。

### 4.3 "自动思考"怎么实现

**结论：规划-执行-反思循环（Planning → Execution → Reflection Loop）。**

这是深度研究类 agent 的本质，用 LangGraph 实现为一个**带循环的图**：

```
plan（拆解任务）→ execute（调度子智能体）→ reflect（信息够不够？）
        ▲                                          │
        └──────────── 不够，继续补充 ◀──────────────┘
                                │够
                                ▼
                           synthesize（汇总交付）
```

- 单次子智能体内部走 **ReAct**：`思考 → 调用工具 → 观察结果 → 再思考`，直到完成。
- 主智能体走 **ReWOO / 反射循环**：先出计划，再逐项执行，最后反思是否需补查。

### 4.4 RAG 怎么设计

**结论：chunk → embed → 混合召回 → 重排 → 带引用生成。**

```
文档入库（离线）：
  上传/导入文档 → 解析(pdf/docx/md) → 分块(chunk) → embedding → 写入 Qdrant

在线检索（每次问答）：
  query → embedding → 向量召回(top-k)
        └─ + 关键字/BM25 召回 ──▶ 混合融合(Reciprocal Rank Fusion)
                                   │
                                   ▼
                            rerank（重排，精排 top-n）
                                   │
                                   ▼
                    拼进 prompt → LLM 生成 → 返回答案 + 引用来源
```

- 分块与 embedding 模型按领域选（中文优先 `bge-large-zh` / `bge-m3`）。
- 重排用 `bge-reranker` 显著提升精度。

### 4.5 向量库怎么选

**结论：推荐 Qdrant（自托管）。**

| 方案 | 优点 | 缺点 | 适用 |
|------|------|------|------|
| **Qdrant**（推荐） | 纯向量、性能好、API 简单、Docker 一行起 | 全文检索弱，需另配 BM25 | 标准 RAG |
| Elasticsearch | 向量 + BM25 全文本一站式混合检索 | 重、内存大 | 需要强全文 + 向量 |
| 自建/云 RAG 引擎（RAGFlow） | 开箱即用，深度研究场景好用 | 黑盒、定制受限、重 | 想省事 |

- 与 `deepsearch-agents` 用 RAGFlow 不同——**本项目自建 RAG**（Qdrant + 重排），换来可控性和学习价值。
- 若后续要强混合检索，再迁 Elasticsearch 也可（两者 embedding 可复用）。

### 4.6 持久化怎么做（Redis 的角色）

**结论：分层持久化，Redis 负责"快而可丢"，MySQL/Qdrant 负责"权威"。**

| 数据 | 存哪 | 说明 |
|------|------|------|
| 会话状态 / LangGraph 检查点 | **Redis**（RedisSaver） | 跨重启持久化，替代 deepsearch 的 InMemorySaver |
| 消息历史 / 任务记录 / 用户 | MySQL | 可审计、可恢复历史会话 |
| 向量 | Qdrant | 持久化落盘 |
| 缓存（检索结果、热点数据） | **Redis** | 降延迟 |
| API 限流（C 端防刷） | **Redis** | 令牌桶 / 滑动窗口 |
| 产物文件 | 本地磁盘 / 对象存储(OSS/S3) | 大文件后续上云 |
| 异步重任务队列 | Redis + 队列（或 Celery） | 长任务不阻塞接口 |

**Redis 核心价值**：会话热状态 + 限流 + 缓存 + 队列，是 C 端高并发的关键；但**不承担唯一权威存储**。

### 4.7 监控怎么做

**结论：三层监控。**

| 层 | 工具 | 看什么 |
|----|------|--------|
| 实时事件推送 | monitor.py + WebSocket | 工具调用、子智能体调用、结果、异常（沿用 deepsearch） |
| 应用日志 | loguru / structlog | request_id 全链路串联日志 |
| LLM 链路追踪 | Langfuse / LangSmith | token 消耗、每步 prompt、延迟、成本 |
| 指标告警 | Prometheus + Grafana | QPS、错误率、平均延迟、模型成本 |

> `deepsearch-agents` 只做了第一层（monitor），本方案补上日志、trace、指标三层，达到生产可观测。

### 4.8 整体链路（一次请求全貌）

```
① 前端发起任务（可带附件）
② FastAPI 鉴权 + Redis 限流 + 生成 thread_id
③ 写 ContextVar：thread_id + session_dir（会话隔离）
④ 主智能体 astream() 启动，LangGraph 用 Redis checkpointer 保存进度
⑤ 主智能体规划 → 分派子智能体 → 各子智能体调工具(RAG/MySQL/业务API)
⑥ 边执行边通过 monitor 推送事件（WebSocket → 前端）
⑦ 反思：信息不足则循环补充；足够则汇总
⑧ 生成结果（Markdown/JSON/文件）→ 写产物
⑨ 事件 + 最终答案 + 文件列表推给前端
⑩ 会话状态/消息历史落 Redis + MySQL，可审计、可恢复
```

---

## 五、目录结构（对齐两个参考项目）

```text
新项目/
├── app/
│   ├── agent/                  # 智能体编排
│   │   ├── main_agent.py       # 主智能体（规划/调度/汇总/交付）
│   │   ├── subagents/          # 子智能体
│   │   │   ├── rag_agent.py        # RAG 检索助手
│   │   │   ├── sql_agent.py        # 结构化查询助手
│   │   │   └── tool_agent.py       # 外部工具助手
│   │   ├── state.py            # LangGraph 状态定义
│   │   └── prompts.py          # 提示词加载
│   ├── api/
│   │   ├── server.py           # FastAPI 路由 / WebSocket
│   │   ├── context.py          # ContextVar 会话上下文
│   │   └── monitor.py          # 事件推送
│   ├── rag/                    # 自建 RAG
│   │   ├── chunker.py          # 文档分块
│   │   ├── embedder.py         # 向量化
│   │   ├── retriever.py        # 混合召回 + 重排
│   │   └── build_kb.py         # 知识库离线构建脚本
│   ├── clients/                # 各外部服务客户端 + 单例管理
│   │   ├── llm_client.py
│   │   ├── qdrant_client.py
│   │   ├── redis_client.py
│   │   └── mysql_client.py
│   ├── tools/                  # 子智能体可用工具
│   ├── models/                 # SQLAlchemy ORM（若用 MySQL）
│   ├── repositories/           # 仓储层（业务语义封装 client）
│   ├── conf/                   # 配置 dataclass（OmegaConf 解析 yaml）
│   ├── core/                   # 日志、trace、限流中间件
│   └── utils/
├── conf/                       # app_config.yaml 等配置文件
├── docker/                     # docker-compose + 初始化 SQL
├── docs/knowledge_base/        # 知识库示例文档
├── frontend/                   # React + Vite
├── .env.example
└── pyproject.toml
```

> 分层原则（沿用 shopkeeper-agent）：`client（技术层连服务）→ repository（业务层转实体）→ agent/tool（业务使用）`，各层只依赖下层。

---

## 六、从 0 到 1 开发路线（里程碑法）

> 每个里程碑都能"跑起来"验证，先骨架后血肉、自底向上。

| 里程碑 | 内容 | 验证标准 |
|--------|------|----------|
| **M0 地基** | 环境 + 配置 + 目录骨架 + `app_config.py` | `from ... import app_config` 不报错 |
| **M1 基础设施** | `docker-compose` 起 MySQL + Redis + Qdrant | 三个服务 Up，client 能连 |
| **M2 客户端层** | llm / qdrant / redis / mysql 四个 client 单例 | 各自连通、可读写 |
| **M3 RAG 离线** | 文档分块 → embed → 写 Qdrant（build_kb 脚本） | 知识库建成，能召回 |
| **M4 工具层** | RAG 检索 / SQL 查询 / 外部工具 + 仓储层 | 每个工具单独可调 |
| **M5 多智能体** | 先搭主+从空骨架（graph 连好边）→ 逐子智能体实现 → 主智能体规划/反思循环 | 图能 compile，跑通一次完整任务 |
| **M6 接口层** | FastAPI + WebSocket + monitor 推送 | curl/前端能收到事件流 |
| **M7 持久化+监控** | Redis chickpointer + 限流 + 日志 + Langfuse trace | 会话跨重启可恢复，链路可追踪 |
| **M8 前端联调** | React 对话界面 + 步骤流 + 结果展示 | 浏览器端到端跑通 |

---

## 七、已确认决策

| 决策点 | 结论 |
|--------|------|
| 项目方向 | **AI 经营分析官（Smart Analyst）**：自然语言问数 + 自动撰写经营分析报告 |
| 领域场景 | 电商/零售经营数据（可替换：连锁门店、金融等） |
| 子智能体分工 | SQL 查询助手 + RAG 知识助手 +（可选）网络搜索助手 |
| 多智能体实现 | LangGraph 手写编排（吃透底层，官方框架） |
| 大模型 | OpenAI 兼容，默认 DeepSeek / 通义 Qwen（.env 可换） |
| 向量库 | Qdrant 自托管 |
| MySQL | 需要（dw 业务数仓 + meta 元数据 + 会话审计） |
| Redis | 需要（会话检查点 + 限流 + 缓存 + 队列） |
| 新项目目录 | `D:\项目\agent\smart-analyst`（与参考项目并列） |
| RAG 文档来源 | 业务口径文档 + 行业分析知识（先示例，后替换） |

> 下一步：固化为可执行任务清单，从 M0 开始逐里程碑落地。