# Smart Analyst —— 从 0 到 100 手写开发手册

> 配套文档：《SmartAnalyst-完整架构设计文档.md》（v1.0，架构与决策依据）
> 本手册定位：**手把手、每步可运行验证**的开发路线，你照着一步步写即可。
> 目标目录：`D:\项目\agent\smart-analyst`

---

## 0. 使用说明与开发原则

### 0.1 核心纪律（务必遵守）

1. **自底向上**：依赖先建，下层能跑再写上层。
2. **先骨架后血肉**：先把结构搭出来能 `import`/能跑空流程，再填业务逻辑。
3. **每步可验证**：每个里程碑结束给出「验证命令」，跑通才能进下一步，不要一口气写到底再调试。
4. **错误是数据不是异常**：节点失败写进 `state.errors`，不要 `raise` 让整图崩。

### 0.2 里程碑总览

| 里程碑 | 主题 | 落地产物 |
|--------|------|---------|
| M0 | 地基 | 目录 + 依赖 + 配置类 |
| M1 | 基础设施 | Docker + 建库建表 SQL |
| M2 | 客户端层 | 4 个 client 管理器 |
| M3 | RAG 离线 | 文档分块 + 入库脚本 |
| M4 | 工具层 | repository + 工具 |
| M5 | 多智能体 | 三层图 + 节点 + 子图（重点） |
| M6 | 接口层 | FastAPI + WebSocket |
| M7 | 持久化+监控 | Redis 检查点 + 限流 + 日志 |
| M8 | 前端联调 | React 界面 |

---

## M0 · 地基：环境 + 配置 + 目录

### 目标
项目能 `from app.conf.app_config import app_config` 且不报错。

### 步骤

**① 建目录骨架**

```text
smart-analyst/
├── app/
│   ├── agent/nodes/      # 任务图节点
│   ├── agent/subgraphs/  # sql/rag/web 子图
│   ├── api/              # FastAPI
│   ├── clients/          # 客户端
│   ├── conf/             # 配置类
│   ├── core/             # 日志/trace
│   ├── entities/         # 业务实体
│   ├── models/           # ORM
│   ├── repositories/     # 仓储
│   ├── rag/              # RAG 离线模块
│   ├── services/         # 服务编排
│   └── scripts/          # 构建脚本
├── conf/                 # yaml 配置
├── docker/               # compose + sql
├── frontend/             # React
├── .env.example
└── pyproject.toml
```

**② 初始化 uv 项目 + 声明依赖**

```bash
cd D:\项目\agent\smart-analyst
uv init
uv python pin 3.12
```

`pyproject.toml` 里 `[project].dependencies` 加入核心依赖：

```toml
[project]
name = "smart-analyst"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "langgraph>=0.2",
    "langchain>=0.3",
    "langchain-openai>=0.2",
    "langchain-huggingface>=0.1",
    "sqlalchemy[asyncio]>=2.0",
    "asyncmy>=0.2",
    "redis>=5.0",
    "qdrant-client>=1.10",
    "elasticsearch>=8.0",
    "omegaconf>=2.3",
    "python-dotenv>=1.0",
    "jieba>=0.42",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "loguru>=0.7",
    "pydantic>=2.7",
]

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]
```

```bash
uv sync
```

**③ 写配置文件 `conf/app_config.yaml`**

```yaml
llm:
  model_name: deepseek-chat
  base_url: https://api.deepseek.com/v1
  temperature: 0.0
mysql:
  meta:
    host: 127.0.0.1
    port: 3306
    user: root
    password: root
    database: meta
  dw:
    host: 127.0.0.1
    port: 3306
    user: root
    password: root
    database: dw
redis:
  host: 127.0.0.1
  port: 6379
qdrant:
  host: 127.0.0.1
  port: 6333
embedding:
  host: 127.0.0.1
  port: 8081
```

**④ 写配置类 `app/conf/app_config.py`**

```python
# 用 dataclass + OmegaConf 把 yaml 变成强类型单例
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv
from omegaconf import OmegaConf

@dataclass
class LLMConfig:
    model_name: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    temperature: float = 0.0

@dataclass
class DBConfig:
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = "root"
    database: str = ""

@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    mysql_meta: DBConfig = field(default_factory=DBConfig)
    mysql_dw: DBConfig = field(default_factory=DBConfig)
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    qdrant_host: str = "127.0.0.1"
    qdrant_port: int = 6333
    embedding_host: str = "127.0.0.1"
    embedding_port: int = 8081

# 模块加载时执行：读 .env + yaml → 合并成强类型对象
_project_root = Path(__file__).resolve().parents[2]
load_dotenv(_project_root / ".env")
_context = OmegaConf.load(_project_root / "conf" / "app_config.yaml")
_schema = OmegaConf.structured(AppConfig)
app_config: AppConfig = OmegaConf.to_object(OmegaConf.merge(_schema, _context))
```

**⑤ 写 `.env.example`**

```env
LLM_API_KEY=your_api_key_here
```

### 验证

```bash
uv run python -c "from app.conf.app_config import app_config; print(app_config.llm.model_name, app_config.mysql_dw.database)"
```

预期看到 `deepseek-chat dw`。

---

## M1 · 基础设施：Docker + 建库建表

### 目标
MySQL / Redis / Qdrant 三个服务 Up，`dw` 和 `meta` 两个库有表有数据。

### 步骤

**① 写 `docker/docker-compose.yaml`**

```yaml
services:
  mysql:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: root
    ports: ["3306:3306"]
    volumes:
      - ./mysql:/docker-entrypoint-initdb.d
      - mysql_data:/var/lib/mysql
  redis:
    image: redis:7
    ports: ["6379:6379"]
  qdrant:
    image: qdrant/qdrant:latest
    ports: ["6333:6333", "6334:6334"]
volumes:
  mysql_data:
```

> MySQL 首次启动会自动执行 `docker/mysql/` 下的 `.sql`（按文件名顺序）。

**② 写 `docker/mysql/001_schema.sql`（建 dw + meta 库和表）**

dw 数仓：`fact_order` + 4 张维度表（`dim_region/dim_customer/dim_product/dim_date`）。
meta 元数据：4 张表（`table_info/column_info/metric_info/column_metric`）+ C 端应用 10 张表（`users/conversations/...`，见架构文档 §7.1）。

> 手写 SQL 时，关键约束照架构文档 §7.1 建索引；`evidence` 表要 `UNIQUE(task_id, source_ref)`。

**③ 写 `docker/mysql/002_data.sql`（模拟数据）**

往 dw 灌 90 条订单、20 客户、15 商品（仿 shopkeeper；`INSERT` 语句）。

**④ 下载 Embedding 模型（TEI 用）**

```bash
uv run hf download BAAI/bge-large-zh-v1.5 --local-dir docker/embedding/bge-large-zh-v1.5
```

### 验证

```bash
docker compose -f docker/docker-compose.yaml up -d
docker ps   # 3 个容器 Up
```

---

## M2 · 客户端层：4 个 client 管理器

### 目标
llm / qdrant / redis / mysql 各自能连通、可读写。

### 模式（统一复用）
每个客户端用「**单例管理器 + 懒初始化**」模式，导入模块不联网，显式 `init()` 才建立连接：

```python
# app/clients/llm_client.py
from langchain_openai import ChatOpenAI
from app.conf.app_config import app_config

class LLMClientManager:
    def __init__(self):
        self.client = None
    def init(self):
        import os
        self.client = ChatOpenAI(
            model=app_config.llm.model_name,
            base_url=app_config.llm.base_url,
            api_key=os.getenv("LLM_API_KEY"),
            temperature=app_config.llm.temperature,
        )

llm_client_manager = LLMClientManager()
```

其余三个同理：

| 文件 | 底层库 | init 时做什么 |
|------|--------|--------------|
| `clients/qdrant_client.py` | qdrant-client | 连 `host:port` |
| `clients/redis_client.py` | redis | `redis.asyncio.from_url` |
| `clients/mysql_client.py` | sqlalchemy+asyncmy | `create_async_engine`（含 meta 和 dw 两个 engine） |
| `clients/embedding_client.py` | langchain-huggingface | `HuggingFaceEndpointEmbeddings(model=url)` |

### 验证

每个 client 文件底部写 `if __name__ == "__main__":` 自测段，分别验证：
- llm：`client.invoke("hi")` 有返回
- qdrant：`upsert` + `search` 一次
- redis：`set` + `get`
- mysql：`SELECT 1`

---

## M3 · RAG 离线：分块 + 入库

### 目标
`docs/knowledge_base` 里的 md 文档，能切块、向量化、写入 Qdrant。

### 步骤

**① `app/rag/chunker.py`（文档分块）**

```python
# 按标题/段落切 chunk，带 overlap，保留 source 元数据
def split_markdown(text: str, chunk_size: int = 500, overlap: int = 50) -> list[dict]:
    # 简化：按段落切 + 滑窗
    chunks = []
    # ... 实现切块，每条返回 {"text":..., "source":文件名, "chunk_id":...}
    return chunks
```

**② `app/entities/doc_chunk.py`（业务实体）**

```python
from dataclasses import dataclass

@dataclass
class DocChunk:
    chunk_id: str
    text: str
    source: str      # 来源文件名
    metadata: dict
```

**③ `app/repositories/qdrant/doc_qdrant.py`（向量仓储）**

```python
class DocQdrantRepository:
    def __init__(self, client, embedding_client):
        self.client = client
        self.embedding_client = embedding_client
    async def upsert(self, chunks: list[DocChunk]): ...
    async def search(self, query_embedding, top_k=5) -> list[DocChunk]: ...
```

**④ `app/scripts/build_doc_knowledge.py`（离线入口）**

```python
# 读 docs/knowledge_base → chunker 切块 → embedding → doc_qdrant.upsert
```

### 验证

```bash
uv run python -m app.scripts.build_doc_knowledge
# 再用 search 查一个 query，能召回相关 chunk
```

---

## M4 · 工具层：SQL 查询 + 仓储

### 目标
SQL 查询工具、RAG 检索工具单独可调。

### 步骤

**① `app/entities/` 补齐业务实体**：`ColumnInfo/MetricInfo/TableInfo`（仿 shopkeeper）。

**② `app/models/` + `app/mappers/`（meta 库 ORM）**

```python
# app/models/column_info.py
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
class Base(DeclarativeBase): pass
class ColumnInfoMySQL(Base):
    __tablename__ = "column_info"
    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column()
    # ... 其余字段
```

**③ `app/repositories/mysql/meta_mysql.py` + `dw_mysql.py`（读写仓储）**

**④ `app/tools/sql_tool.py`（Text-to-SQL 查询工具，C 端高危面）**

工具必须带安全边界：
- 只读账号 + 强制 `LIMIT`
- 表/列白名单校验
- 参数化，禁字符串拼接

### 验证
`sql_tool` 单独调一次：传一个 SQL，返回结构化结果。

---

## M5 · 多智能体：三层图（本手册重点）

> 顺序原则：**先把守卫/降级/断点这类骨架机制搭好，再填专家血肉**。

### M5.1 三层图骨架 + State + 空 worker 子图

**① `app/agent/state.py`**（代码见架构文档 §5.2，`AnalystState` + `Budget` + `TaskStatus`）

**② 空 worker 子图**（先只返回占位结果，保证能跑通）

```python
# app/agent/subgraphs/sql/sql_graph.py —— 空骨架
from langgraph.graph import StateGraph, START, END
def build_sql_worker():
    g = StateGraph(dict)
    async def placeholder(state):
        return {"worker_results": [{"type": "sql", "source_ref": None, "data": None}]}
    g.add_node("placeholder", placeholder)
    g.add_edge(START, "placeholder")
    g.add_edge("placeholder", END)
    return g.compile()
```

**③ `app/agent/graph.py` 搭主图**（代码见架构文档 §5.4，含 `fan_out`/`route_after_reflect`/`route_after_validate`）

### 验证

```bash
uv run python -c "from app.agent.graph import graph; graph.invoke({'task': ...})"
```

空跑通过，checkpoint 能存。

### M5.2 fan_out Send 并行 + join 汇聚
3 个 mock worker 并行返回，`join` 正确合并 `worker_results`（`operator.add` 累加）。

### M5.3 reflect 量化判断 + 循环守卫 + replanner
`reflect` 计算覆盖率/引用数/置信度，产出 `evidence_gaps`，`iteration += 1`。验证：
- 注入缺口 → 触发 `replanner` 补查
- `iteration >= max_iterations` → 强转 `validator`

### M5.4 validator + fixer 质量门
`validator` 检查：每个数字有 `source_ref`、SQL 结果一致性抽查、口径一致。验证：注入"无 source_ref 的数字"被拦截→进 fixer。

### M5.5 人工断点
`graph.compile(checkpointer=..., interrupt_before=["synthesizer"])` + `clarify_interrupt`。验证 interrupt/resume 全流程。

### M5.6 降级矩阵 + 幂等 + 缓存
把 §六健壮性落地：`safe_call` 封装、降级路由、`deliver` 幂等（SETNX）。验证：杀 MySQL 后任务走 RAG 兜底出报告。

---

## M6 · 接口层：FastAPI + WebSocket

### 目标
`curl` / 前端能收到事件流。

### 步骤

**① `app/api/server.py`**

```python
from fastapi import FastAPI, WebSocket
from app.agent.graph import graph

app = FastAPI()

@app.post("/api/task")
async def create_task(task: dict):
    thread_id = task["thread_id"]
    # 分配 thread_id，异步执行图，返回 task_id
    ...

@app.websocket("/ws/{thread_id}")
async def ws_stream(ws: WebSocket, thread_id: str):
    await ws.accept()
    # graph.astream(..., stream_mode="updates") 逐节点推事件
    async for event in graph.astream(...):
        await ws.send_json(event)
```

> 事件推送直接复用 `graph.stream(stream_mode="updates")`，不另起事件源（架构文档 §6.9）。

**② `app/api/context.py`（ContextVar 会话隔离）**
**③ `app/api/monitor.py`（事件格式化 + 推送）**

### 验证

```bash
uv run uvicorn app.api.server:app --reload
```

用 `websocat` 或前端连 `/ws/xxx` 能收到节点进度事件。

---

## M7 · 持久化 + 监控

### 目标
会话跨重启可恢复，链路可追踪。

### 步骤
1. **Redis Checkpointer**：`RedisSaver.from_conn_info(...)` 挂到 `graph.compile`。
2. **限流中间件**：`rate:{}` 令牌桶。
3. **日志**：`loguru` + `request_id`（ContextVar 贯穿全链路）。
4. **LLM 链路追踪**：Langfuse 回调注入每个节点。
5. **全局错误兜底**：图最外层 `except` 返回结构化失败 + thread_id。

### 验证
- 任务执行中杀掉进程，重启后从 Redis checkpoint 续跑。
- 一次请求的所有日志带同一 `request_id`。

---

## M8 · 前端联调

### 目标
浏览器端到端跑通：提问 → 步骤流 → 结果表格。

### 步骤
1. Vite + React + Tailwind 初始化，配 `/api` 和 `/ws` 代理。
2. `agentApi.ts`：建 WS 连接，消费事件流。
3. 组件：`Composer`（输入）/ `StepRail`（步骤流）/ `ResultTable`（结果）/ `MessageBubble`（对话）。
4. 全链路联调 + request_id 排查。

### 验证
浏览器提问「分析上季度各地区的销售表现」，看到步骤流转 + 最终报告。

---

## 附录 · 常见坑

| 坑 | 现象 | 解法 |
|----|------|------|
| 子图 state 与外层不对齐 | 子图结果取不到 | 子图用自己的 state，外层用 `Send` 传输入、字段对齐接输出 |
| Send 改成固定边 | 子任务数一变就跑错 | 子任务数不固定必须用 `Send` |
| 反思死循环 | 一直"觉得不够" | `iteration` 显式计数 + `max_iterations` 双闸 |
| exception 穿透整图 | 一个工具抖全盘崩 | 节点内 try/except 转 `errors` |
| checkpoint 恢复重复推送 | 重试重复写库 | `deliver` 幂等 + SETNX |
| SQL 注入 | C 端高风险 | 只读账号 + 白名单 + 参数化 + LIMIT |