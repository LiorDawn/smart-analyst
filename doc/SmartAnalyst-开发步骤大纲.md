# Smart Analyst —— 开发步骤大纲（每步要完成什么功能）

> 配套：《从0到100开发手册》（怎么写）、《完整架构设计文档》（为什么这么设计）
> 本文档是**目标清单**：每一步要完成什么功能、输入输出是什么、怎么算做完。

---

## 一、里程碑大纲（M0 → M8，每个完成什么）

| 步骤 | 要完成的功能 | 关键交付物 | 验收（做完标准） |
|------|-------------|-----------|-----------------|
| **M0 地基** | 让项目能 import、配置能读、目录就位 | pyproject.toml + app_config.yaml + app_config.py | `from app.conf.app_config import app_config` 不报错 |
| **M1 基础设施** | 起 3 个服务，建好 dw/meta 两个库及全部表和数据 | docker-compose + 建库建表 SQL | `docker ps` 3 容器 Up，库表可用 |
| **M2 客户端层** | 打通 4 个外部服务连接 | llm/qdrant/redis/mysql/embedding 管理器 | 各自能连通读写 |
| **M3 RAG 离线** | 把知识文档变成向量存进 Qdrant | chunker + build_doc_knowledge | 建库后能召回相关 chunk |
| **M4 工具层** | 提供可单独调用的查询/检索工具 | repository + sql 工具 + rag 工具 | 每个工具单独可调 |
| **M5 多智能体** | 搭建三层图编排，跑通一次完整分析 | state + graph + 节点 + 子图 | 图能 compile，跑通完整任务 |
| **M6 接口层** | 暴露 HTTP/WS 接口，推送事件流 | FastAPI + WebSocket + monitor | curl/前端收到事件流 |
| **M7 持久化+监控** | 会话可恢复、链路可追踪 | Redis 检查点 + 限流 + 日志 | 跨重启续跑、request_id 串联 |
| **M8 前端联调** | 浏览器端到端问数出报告 | React 界面 | 浏览器跑通完整链路 |

---

## 二、节点编排大纲（每个节点要完成什么功能）

### A. 入口图节点（第 1 层，会话级）

| 节点 | 要完成的功能 | 输入 → 输出 | 验收 |
|------|-------------|------------|------|
| `task_parser` | 解析用户的意图、指标、时间范围、维度；把"上周"这类相对时间转成绝对日期 | `messages` → `task` / `needs_clarify` | 能解析出结构化 task 规格 |
| `intent_router` | 三路分流：口径不明走澄清、纯问答走快问、分析任务进任务图 | `task` → 路由分支 | 三类输入各走对分支 |

### B. 任务图节点（第 2 层，主状态机）

| 节点 | 要完成的功能 | 输入 → 输出 | 验收 |
|------|-------------|------------|------|
| `planner` | 把总任务拆成子任务计划；先做指标口径对齐 | `task` → `plan` | 产出带 type/query/deps 的计划 |
| `fan_out` | 按计划动态并行分派子任务（纯函数） | `plan` → `Send` 列表 | 子任务数变化也能正确分派 |
| `sql_worker` | Text-to-SQL 全流程查数 | `task` → `worker_results` | 返回带 source_ref 的结构化数据 |
| `rag_worker` | 混合召回+重排查知识库 | `task` → `worker_results` | 返回带 chunk 引用的上下文 |
| `web_worker` | 外部搜索/业务 API | `task` → `worker_results` | 返回带 url 引用的结果 |
| `join` | 汇聚去重 worker 结果，打来源标签 | 多路结果 → 合并结果 | 结果去重、来源清晰 |
| `reflect` | 量化判断信息是否足够（覆盖率/引用数/置信度） | `worker_results` → `evidence_gaps`/`iteration` | 能产出缺口清单并自增轮数 |
| `replanner` | 针对缺口生成补充子任务（不回退原计划） | `evidence_gaps` → 追加 `plan` | 只补缺口不推翻 |
| `validator` | 质量门：数字可溯源、SQL 一致性、口径一致 | 结果+报告 → `report_ok` | 无 source_ref 的数字被拦截 |
| `fixer` | 局部修正校验失败项 | 校验报告 → 修正 `report` | 只修失败章节 |
| `synthesizer` | 生成结构化报告（数字取结果，不靠 LLM 编） | `worker_results` → `report` | 报告数字全部可溯源 |
| `deliver` | 写产物、落库、推事件、缓存（幂等） | `report` → `status` | 产物落盘、事件发出、幂等 |

### C. SQL 子图节点（第 3 层，Text-to-SQL 链路）

| 节点 | 要完成的功能 | 输入 → 输出 | 验收 |
|------|-------------|------------|------|
| `extract_keywords` | 分词 + LLM 扩展关键词 | `task` → `keywords` | 提取出实词关键词 |
| `recall_column` | 关键词转向量，Qdrant 召回相关字段 | `keywords` → 字段列表 | 召回语义相关字段 |
| `recall_metric` | Qdrant 召回相关指标 | `keywords` → 指标列表 | 召回语义相关指标 |
| `recall_value` | ES 召回字段取值（枚举值） | `keywords` → 值列表 | 召回"华北"等级别值 |
| `merge_retrieved` | 三路召回结果汇总 | 三路 → 汇总 | 字段/指标/值合并 |
| `filter_table` | LLM 过滤无关表 | 汇总 → `table_infos` | 去掉无关表 |
| `generate_sql` | 生成候选 SQL | 上下文 → `sql` | 产出可执行 SQL 草稿 |
| `validate_sql` | EXPLAIN 校验 SQL | `sql` → `sql_error` | 错误被识别 |
| `correct_sql` | 修正错误 SQL | `sql`+error → 修正 `sql` | 修正后可通过校验 |
| `execute_sql` | 执行 SQL 查真实数据 | `sql` → `query_result` | 返回查询结果 |

### D. RAG 子图节点（第 3 层，文档检索链路）

| 节点 | 要完成的功能 | 输入 → 输出 | 验收 |
|------|-------------|------------|------|
| `embed_query` | query 向量化 | `task` → `query_embedding` | 产出 query 向量 |
| `hybrid_retrieve` | 向量 + BM25 混合召回（RRF 融合） | 向量 → 候选集 | 更多相关候选 |
| `rerank` | bge-reranker 精排 top-n | 候选 → 精排 | 相关性排序提升 |
| `build_context` | 组装带 source_ref 的上下文 | 精排 → `context` | 上下文带可溯源引用 |

---

## 三、节点编排里的「骨架机制」步骤（先做，后填血肉）

| 步骤 | 要完成的功能 | 验收 |
|------|-------------|------|
| S1 三层图骨架 + State | 搭出图的"架子"，节点先返回空结果能跑通 | `graph.invoke` 空跑通过 |
| S2 Send 并行 + join | 动态并行分派 + 汇聚 | 3 个 mock worker 并行正确汇聚 |
| S3 循环守卫 + replanner | 反思循环 + 双闸防死循环 | 缺口触发补查，超限强转 validator |
| S4 validator + fixer | 质量门 + 局部修正 | 无 source_ref 被拦截 |
| S5 人工断点 | 澄清 + 报告确认 interrupt/resume | 全流程可用 |
| S6 降级 + 幂等 + 缓存 | 故障兜底 + 防重复副作用 | 杀 MySQL 走 RAG 兜底出报告 |

> **顺序原则**：S1→S6 是「结构性机制」，必须先搭好再填专家血肉；否则后补要改图，成本高。

---

## 四、一句话执行顺序

```
M0 地基 → M1 起服务建库 → M2 客户端 → M3 RAG离线 → M4 工具
       → M5 多智能体（先 S1~S6 骨架机制，再填 A/B/C/D 节点血肉）
       → M6 接口 → M7 持久化监控 → M8 前端
```

每个步骤动手前，先看这张表里「要完成的功能」和「验收」，明确目标再写代码；写完后跑验收，通过才进下一步。