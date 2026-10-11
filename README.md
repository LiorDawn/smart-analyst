# SmartAnalyst · AI 经营分析官

> 自然语言问数 + 受控 SQL 查数 + 多智能体深度分析 + 自动生成经营分析报告

基于 **LangGraph** 的智能经营分析应用：用户用一句话提问（"本周 GMV 和订单量按品类看"），系统完成意图解析 → 受控查数 → 指标计算 → 多智能体分析 → 报告生成的全链路，关键数字全程带 `source_ref` 可溯源。

**设计理念：固定流程查数，智能体增值分析**

- **查数是规则化的**：planner 依据指标口径字典生成受控 SQL，compute 确定性求值——数字不靠 LLM 编造，核心链路可离线可降级。
- **分析是智能的**：supervisor 主智能体按需调度 5 个子智能体（分析 / 异常检测 / 趋势预测 / 行业对标 / 方案建议），增值能力即插即用。

## 主要特性

- 🔗 **12 节点 LangGraph 编排**：`task_parser → clarify → quick_answer → planner → query_executor → join → compute → supervisor → writer → reviewer → fixer → deliver`，澄清中断（interrupt+resume）带轮数闸，杜绝死循环。
- 🧠 **supervisor + 5 子智能体**：主智能体依据子 agent description 自主路由，深度分析 / 异常检测 / 趋势预测 / 行业对标 / 方案建议即插即用。
- 🛡️ **5 类中间件闸**：模型调用上限（30 次/run）、工具调用上限（15 次/run）、主模型失败自动降级备用模型、Human-in-the-loop 重操作（存文档 / 导 PDF）执行前人工确认、长对话自动总结——一中间件一文件，可插拔装配。
- 📊 **指标口径字典 + 受控 SQL**：原子 / 派生 / 跨期安全求值，SQL 由受控指标定义表生成，LLM 不直接拼 SQL，杜绝数字幻觉。
- 🔍 **RAG 双路混合召回**：dense（bge-large-zh-v1.5）+ sparse（BM25）双路召回，Qdrant 服务端 RRF 融合排序。
- 🧩 **未知指标自学习**：首次经 RAG 解析指标口径并回写字典，二次查询 0 token。
- ⚡ **SSE 事件流 + Vue3 实时监控**：节点流转、子智能体调用、工具执行全程埋点，前端节点链路实时可视化。
- 🛟 **降级矩阵**：LLM / Embedding / Qdrant 任一挂掉，核心查数链路 0 中断，规则兜底保证任务可用。

## 技术栈

| 类别 | 技术 |
| --- | --- |
| 核心框架 | LangGraph, LangChain, deepagents |
| 向量数据库 | Qdrant（dense + BM25 sparse 双路 RRF） |
| 业务数据库 | MySQL（aiomysql + SQLAlchemy） |
| 缓存 / 会话 | Redis（langgraph-checkpoint-redis） |
| 模型服务 | DeepSeek / OpenAI-compatible + bge-large-zh-v1.5 |
| 检索增强 | fastembed（BM25）、jieba |
| API 服务 | FastAPI + SSE + WebSocket（预留） |
| 前端 | Vue3 + TypeScript + Vite |
| 报告输出 | Markdown / DOCX / PDF（reportlab） |

## 环境要求

- Python **3.12+**（`uv` 管理依赖）
- MySQL（业务事实表，`app/scripts/sql.sql` 提供建表）
- Redis（checkpointer 会话状态）
- Qdrant（向量库，默认 `127.0.0.1:6333`）
- Embedding 服务（bge-large-zh-v1.5，默认 `127.0.0.1:8081`）
- LLM API（DeepSeek 或任意 OpenAI-compatible 服务）

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填写：

```bash
cp .env.example .env
```

| 变量 | 说明 |
| --- | --- |
| `LLM_API_KEY` | 主模型 API Key（DeepSeek） |
| `LLM_BACKUP_API_KEY` | 备用模型 Key（主模型失败时自动降级） |
| `MYSQL_*` | 业务库连接 |
| `QDRANT_*` | 向量库连接 |
| `EMBEDDING_*` | 向量化服务地址 |

### 3. 初始化知识库与演示数据

```bash
# 建向量库（首次全量重建）
python -m app.rag.build_kb --rebuild

# 建业务事实表并造演示数据
python -m app.scripts.seed_demo
```

### 4. 启动后端 API

```bash
uvicorn app.api.server:app --host 127.0.0.1 --port 8000
```

### 5. 启动前端（可选）

```bash
cd frontend
npm install
npm run dev
```

访问 `http://localhost:8000` 即可开始提问。也可以直接跑 CLI：

```bash
python -m app.main
```

## 项目结构

```
smart-analyst/
├── app/
│   ├── api/            # FastAPI 服务（chat/resume/SSE 监控）、ContextVar 上下文
│   ├── clients/        # 外部客户端：LLM / Embedding / MySQL / Redis / Qdrant
│   ├── conf/           # 全局配置（OmegaConf）+ 指标/字段口径字典
│   ├── entities/       # 契约模型：TaskSchema / PlannerSchema / ResearchResult
│   ├── graph/          # LangGraph 编排
│   │   ├── graph.py    # 12 节点图定义与条件边
│   │   ├── state.py    # AnalystState（含 Annotated 合并）
│   │   ├── nodes/      # 12 个节点实现（task_parser → deliver）
│   │   ├── workers/    # 5 个子智能体（analysis/anomaly/forecast/benchmark/proposal）
│   │   └── middleware/ # 5 类中间件（调用上限 / 降级 / HITL / 总结）
│   ├── rag/            # 知识库：chunker / embedder / sparse_embedder / retriever / build_kb
│   ├── repositories/   # 仓储层（Qdrant 文档库，dense+sparse 混合召回）
│   ├── scripts/        # 建表 SQL + 演示数据种子
│   ├── tools/          # 子智能体工具：查数 / RAG / 联网 / 文档 / 时间
│   └── main.py         # CLI 入口
├── frontend/           # Vue3 + TypeScript 对话界面（节点链路监控）
├── doc/knowledge_base/ # 指标口径知识库（RAG 数据源）
├── tests/              # 回归测试（14 用例，覆盖率 65%）
├── pyproject.toml      # 依赖与工程配置
└── .env.example        # 环境变量模板
```

## 架构设计

### 节点链路

```
task_parser → clarify_interrupt → quick_answer → planner → query_executor → join
     → compute → supervisor → writer → reviewer → fixer → deliver
```

| 阶段 | 节点 | 职责 |
| --- | --- | --- |
| 理解 | task_parser / clarify_interrupt / quick_answer | 意图解析、缺失澄清、快捷问答 |
| 查数 | planner / query_executor / join / compute | 受控 SQL 计划 → 并发执行 → 汇聚 → 确定性计算 |
| 分析 | supervisor | 主智能体调度 5 个子智能体（深度分析 / 异常 / 预测 / 对标 / 方案） |
| 产出 | writer / reviewer / fixer / deliver | 报告撰写 → 溯源校验 → 修正 → 交付落盘 |

**双闸防环**：reviewer 质量门 + fixer 迭代上限，杜绝 writer↔fixer 无限循环；clarify 澄清带轮数闸（默认 2 轮）。

### 降级矩阵

| 故障 | 行为 |
| --- | --- |
| LLM 挂 | 规则兜底：关键词意图识别 + 字典直查，查数链路 0 中断 |
| Embedding 挂 | 降级纯 BM25 全文检索 |
| Qdrant 挂 | 返回空结果 + degraded 标注，报告明示降级 |
| 主模型失败 | 自动降级备用模型（ModelFallbackMiddleware） |

## 测试

```bash
# 运行全部测试
uv run pytest

# 带覆盖率
uv run pytest --cov=app --cov-report=term-missing
```

当前 14 个回归用例全绿，核心文件覆盖率约 65%（planner 59% / query_executor 81% / compute 64% / monitor 65%）；依赖真实外部环境的路径（Qdrant / Embedding / MySQL）由端到端链路覆盖。

## License

MIT
