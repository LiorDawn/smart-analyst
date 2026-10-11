# SmartAnalyst M5・节点编排开发设计（增量式・从基础开始）

> 关联文档：《SmartAnalyst-M5 开发文档.md》（文件级实施）、《SmartAnalyst-M5 节点链路图.html》（拓扑）、《SmartAnalyst - 完整架构设计文档.md》
> 定位：本文档描述
>
> **怎么从最基础一步步搭到最终形态（混合分层：强制主链 + LLM 增值路由）**
>
> —— 增量式编排开发设计，每步独立可验收，跑通才进下一步。
> 状态：
>
> **设计稿 v2（混合分层，方向已敲定）**



***

## 一、编排开发哲学（三条硬原则）



1. **先骨架后血肉**：先把 "图能跑、checkpoint 能存、条件边能路由" 这类结构性机制搭好，再填节点业务逻辑 —— 机制后补要改图，成本高；

2. **每步可验收**：每一步都有独立验收标准（跑通才进下一步），失败只回滚一步，不推倒重来；

3. **最小可用优先**：先跑通单链（START→...→END），再加分支（意图路由）、再并行（Send）、再循环（反思守卫）—— 复杂度按需生长，不一次性堆全。

## 二、最终形态回顾（混合分层 · 本文档逐步逼近它）

> **v2 定案**：放弃「唯一 research_agent + 全图节点」与「纯 Supervisor（LLM 可跳过质量门）」两种极端，
> 采用 **混合分层** —— 强制主链（结构保证，不可跳过）+ 增值路由层（LLM 自主决定调不调）。

```
第 1 层 入口图：START → task_parser → intent_router
   ├─ 口径不明 → ⏸ clarify_interrupt → resume → planner
   ├─ 知识快问 → research_agent(rag 单发) → deliver
   └─ 分析任务 → 进入任务图
第 2 层 任务图（强制主链 · 结构保证，一步不可省）：
   planner → fan_out → research_agent → join → analysis → writer → reviewer →(通过)→ ⏸ report_confirm → deliver → END
     ↑                                          │(有缺口且未超限)
     └── replanner ◀────────────────────────────┘
   reviewer →(不通过)→ fixer → writer（修正环 ≤2 轮）
第 3 层 增值路由层（LLM 自主决定调不调 · Plan-Execute）：
   supervisor：LLM 出计划，自主决定是否调用增值子 agent
   ├─ web 补数 agent / 下钻分析 agent / 历史对比 agent / 图表 agent
   └─ 铁律：LLM 只被授权决定「增值部分」，永远无权跳过「必要部分」（质量门强制边）
```

## 三、增量路线图总览（Step 0 → Step 6）



| 步骤         | 加什么                                                             | 图拓扑变化                                         | 验收标准（跑通才进下一步）                                      |
| ---------- | --------------------------------------------------------------- | --------------------------------------------- | -------------------------------------------------- |
| **Step 0** | 目录 + `state.py` + 最小图（占位节点）                                     | `START → task_parser(占位) → END`               | `graph.ainvoke` 空跑通过，checkpoint 可存可取、messages 跨轮累积 |
| **Step 1** | `task_parser`（真实解析）+ `intent_router` 条件边                        | 单链变三分支：澄清 / 快问 / 分析                           | 输入 "分析上周 GMV"→ 输出结构化 `TaskSchema`（相对时间转绝对日期）         |
| **Step 2** | `planner` + `fan_out`(Send) + **mock research\_agent** + `join` | 分支汇聚：planner → 并行 → join                      | 3 个 mock 子任务并行返回，join 汇聚正确                         |
| **Step 3** | `analysis` + `reflect` + `replanner`（双闸守卫）                      | 出现循环回边：join → reflect →(replanner→fan\_out)   | 注入缺口触发补查；iteration≥3 强制收束到 writer                  |
| **Step 4** | `writer` + `reviewer` + `fixer`（质量门）                            | 循环 + 质量门：reflect → writer → reviewer →(fixer) | 注入 "无 source\_ref 数字" 被拦截；**此步前建 MySQL 表**         |
| **Step 5** | ⏸ `report_confirm` + `deliver`（人工断点 + 交付）                       | 完整主链收束：... → ⏸ → deliver → END                | interrupt/resume 全流程可用                             |
| **Step 6** | 降级矩阵 + 幂等 (SETNX) + 缓存 + RedisSaver + 可观测                       | 拓扑不再变，只加固                                     | 杀 MySQL 后任务走 RAG 兜底出报告                             |

## 四、每步详细设计

### Step 0・最小骨架（已验证 ✅）

**目录**：



```
app/graph/
├── __init__.py      # 导出 AnalystState / Budget / TaskStatus
├── state.py         # 状态定义（字段全量声明，reducer 契约定死）
└── graph.py         # 图组装（唯一拓扑权威）+ 空跑验证入口
```

**state.py 设计要点**：



* 字段四组：会话层（messages/thread\_id/user\_id）、任务层（task/plan/research\_results/analysis\_results/evidence\_gaps/artifacts）、控制层（iteration/budget/errors/fallbacks\_used）、交付层（report\_ok/report/status）；

* **reducer 契约定死**：`research_results / evidence_gaps / artifacts / errors / fallbacks_used` 用 `operator.add` 累加；`task / plan / analysis_results / report / report_ok / status / iteration` 用 replace；

* 枚举只存 `.value`（`status: str`），避免 checkpoint msgpack 序列化问题（已验证）。

**验收结果**（已跑通）：空跑通过 /checkpoint 复用（messages 1→2 累积）/checkpoint 可取。

### Step 1・任务解析 + 意图路由（三分支）

**task\_parser（LLM 节点）**：



```
class TaskSchema(BaseModel):
    intents: list[str]          # 意图：问数 / 分析 / 对比 / 归因
    metrics: list[str]          # 指标：gmv / conversion_rate ...
    time_range: dict            # {start, end}，相对时间转绝对日期
    dims: list[str]             # 维度：品类 / 渠道 / 地区
    compare: dict | None        # 对比：同比 / 环比 / 指定基准

async def task_parser(state: AnalystState) -> dict:
    # llm.with_structured_output(TaskSchema) + 独立 date 工具（"上周"→具体区间）
    return {"task": task_spec.model_dump()}     # replace
```

降级：解析失败 → 重试 1 次 → 降级为通用问答（快问通道）。

**intent\_router（编排节点，条件边）**：



```
def route_intent(state: AnalystState) -> str:
    t = state["task"]
    if t 缺失口径字段: return "clarify"      # → ⏸ 澄清
    if 纯知识问答: return "quick"            # → research_agent 单发
    return "analyze"                         # → planner
```

无法判定 → 默认走澄清（不猜）。

**图**：`START → task_parser → intent_router ──(clarify)→ ⏸ ──resume→ planner`，`(quick) → research_agent(占位) → deliver(占位) → END`，`(analyze) → planner(占位) → END`。

**验收**：`ainvoke("分析上周 GMV 变化")` → `task.time_range == {上周一~上周日}`，intent=analyze。

### Step 2・规划 → 并行执行 → 汇聚

**planner（LLM 节点）**：



```
class PlannerSchema(BaseModel):
    type: Literal["sql", "rag", "web"]   # 数据源
    query: str                           # 子任务描述
    target: str                          # 要取的指标/口径
    deps: list[str]                      # 依赖的其他子任务

async def planner(state: AnalystState) -> dict:
    # 先做指标口径对齐（查 metric_dict/口径 RAG），再拆 Plan
    return {"plan": plan.model_dump()}   # replace
```

**fan\_out（编排节点，Send 动态分派）**：



```
def fan_out(state: AnalystState) -> list[Send]:
    return [Send("research_agent", {"task": item}) for item in state["plan"]]
```

子任务数不固定 → 必须 Send API，不能手写固定边。

**research\_agent（子 agent，本步先 mock）**：



* 先以 mock 实现（按 task.type 返回固定数据包 `{result, source_ref, confidence}`），验证并行与汇聚；

* Step 6 再替换为真实 `create_agent(model, system_prompt, tools=[sql_query, rag_search, web_search])`。

**join（纯函数节点）**：去重（同 source\_ref 只留一条）、排序（confidence desc）、打来源标签。



```
async def join(state: AnalystState) -> dict:
    return {"research_results": dedup_and_sort(state["research_results"])}
```

**图**：`planner → fan_out → research_agent(子图) → join → reflect(占位) → END`。

**验收**：plan 含 3 项（sql/rag/web）→ 3 个 mock 实例并行返回 → join 输出 3 条带 source\_ref 的结果。

### Step 3・分析 + 反思循环 + 双闸守卫

**analysis（LLM + 纯函数节点）**：pandas 计算指标（确定性）+ LLM 归因（解释变化）。失败 → 数据包直传 writer。

**reflect（规则为主，LLM 辅助）**：



```
async def reflect(state: AnalystState) -> dict:
    gaps = compute_gaps(state["plan"], state["research_results"])   # 覆盖率/引用数/置信度
    return {"evidence_gaps": gaps, "iteration": state["iteration"] + 1}
```

**双闸守卫（条件边 route\_after\_reflect）**：



```
def route_after_reflect(state: AnalystState) -> str:
    if state["iteration"] >= state["budget"]["max_iterations"]:
        return "writer"          # 闸 1：迭代上限
    if not state["evidence_gaps"]:
        return "writer"          # 信息充分
    return "replanner"           # 有缺口 → 补查
```

闸 2（budget/max\_steps）在节点内 safe\_call 检查。

**replanner（LLM 节点）**：只补缺口（把 evidence\_gaps 转成新的 PlannerSchema 追加进 plan），不推翻原计划；重复产出同缺口 → 计入 iteration 触发闸 1。

**图**：`join → analysis → reflect →(replanner → fan_out 循环 | writer 占位)`。

**验收**：注入 "缺口 = 缺少外部行业数据"→ 触发 replanner + 二轮 fan\_out；构造 iteration=3 → 强制走 writer，不补查。

### Step 4・撰写 + 质量门（此步前建 MySQL 表）

**writer（LLM 节点）**：`with_structured_output(Report)`；数字一律取自 research\_results/analysis\_results（LLM 只组织文字）；失败 → 重试 → Markdown 摘要降级。

**reviewer（规则 + LLM 节点）**：



```
async def reviewer(state: AnalystState) -> dict:
    issues = []                                   # 确定性校验
    for num in extract_numbers(state["report"]):
        if not has_source_ref(num, state["research_results"]):
            issues.append(f"数字 {num} 缺少 source_ref")   # 质量门拦截
    return {"report_ok": not issues, "report_issues": issues}
```

**fixer（LLM 节点）**：只修 issues 指出的章节；计数 ≤2 轮；超限 → 报告显式标注 "未通过校验"。

**前置**：MySQL 5 表建表（evidence/reports/tasks 等），deliver 落库依赖。

**图**：`reflect → writer → reviewer →(通过)→ ⏸(占位) | (不通过)→ fixer → writer`。

**验收**：writer 产出含无引用数字 → reviewer 拦截 → fixer 修正 → 二轮通过。

### Step 5・人工断点 + 交付

**两处 interrupt**：



* clarify（Step 1 已有）：口径不明时问清再规划；

* report\_confirm：`interrupt_before=["deliver"]`，报告生成后等用户确认（可改口径 / 维度），`Command(resume=...)` 恢复。

**deliver（副作用节点，幂等）**：



* 写产物（Markdown/PDF/JSON）→ 落 MySQL（reports/evidence/tasks）→ WebSocket 推送 → Redis 缓存；

* 幂等：副作用只在此节点，带幂等键（SETNX dedup）。

**图**：`... → ⏸ report_confirm → deliver → END`。

**验收**：ainvoke 到 report\_confirm 中断 → 前端确认 / 修改 → resume → deliver 落库 → 二次 resume 不重复写（幂等）。

### Step 6・健壮性收尾（拓扑不再变，只加固）



| 机制   | 落地                                                               | 验收                                    |
| ---- | ---------------------------------------------------------------- | ------------------------------------- |
| 降级矩阵 | 节点包 `safe_call`（预算→重试退避→超时→错误转 errors）→ 条件边读 errors 走 fallback   | 杀 MySQL → research\_agent 走 RAG 兜底出报告 |
| 幂等   | `call_id` + Redis `SETNX(dedup:{call_id})`                       | 重复 resume 不重复写库 / 推送                  |
| 缓存   | SQL 结果（sha256 键，5–30min）、RAG 检索（30min）                           | 同 query 二次命中缓存                        |
| 持久化  | InMemorySaver → **RedisSaver**                                   | 重启后 checkpoint 可取，任务可续跑               |
| 可观测  | `graph.stream(stream_mode="updates")` 事件流 + loguru (request\_id) | 前端步骤流逐节点显示                            |

## 五、节点职责总表（每个节点完成了什么）

> 按执行顺序排列。**"作用" = 该节点把什么输入变成了什么输出、对任务完成贡献了什么**；"产出" = 它写回 state 的字段（reducer 语义见 §六）。

| 节点 | 类型 | 作用（它完成了什么） | 产出（写入 state） | 加入 Step |
| --- | --- | --- | --- | --- |
| **task_parser** | LLM | 把用户一句话解析成结构化任务：意图、指标、时间范围（相对时间转绝对日期）、维度、对比基准 | `task`（TaskSchema） | S1 |
| **intent_router** | 编排（条件边） | 判定走哪条路：口径不明 → 澄清；纯知识问答 → 快问；分析任务 → 进任务图 | 路由字符串 | S1 |
| **clarify_interrupt** | ⏸ 人工断点 | 口径不明时挂起图、向用户提问；resume 后带澄清结果回到 planner | —（interrupt 状态） | S1 |
| **planner** | LLM | 指标口径对齐后，把任务拆成可执行子任务计划（sql/rag/web 各取所需、带依赖关系） | `plan`（PlannerSchema 列表） | S2 |
| **fan_out** | 编排（Send） | 按 plan 动态并行分派子任务给 research_agent 实例（子任务数不固定） | Send 列表 | S2 |
| **research_agent** | 子 agent（子图） | 数据采集专家：决定查什么、先内库还是外网、结果够不够，多步工具调用；**每条结果必带 source_ref** | `research_results`（累加） | S2 mock / S6 真实 |
| **supervisor** | LLM 编排 | 增值路由：看用户请求 + 子 agent 注册表 → 输出 RoutingPlan（决定调哪些增值 agent）；**只决定增值部分，无权跳过主链** | `routing_plan` | S3+ |
| **web 补数 / 下钻 / 对比 / 图表 agent** | 增值子 agent（注册表） | 由 supervisor 的 LLM 自主决定调不调；简单任务全不调（零成本），复杂任务按需调 | 各自结果字段（add） | S3+ |
| **join** | 纯函数 | 汇聚：去重（同 source_ref 只留一条）、按置信度排序、打来源标签 | `research_results`（聚合） | S2 |
| **analysis** | LLM + 纯函数 | 指标计算（pandas 确定性）+ 变化归因（LLM 解释）；失败 → 数据包直传 writer | `analysis_results` | S3 |
| **reflect** | 规则 + LLM | 检查信息够不够：计划覆盖率 / 引用数 / 置信度；产出缺口清单，迭代计数 +1 | `evidence_gaps`（累加）、`iteration` | S3 |
| **replanner** | LLM | 只把缺口转成新子任务追加进 plan（**不推翻原计划**，不重复产缺口） | `plan`（追加） | S3 |
| **writer** | LLM | 组织报告结构、生成图表、插入引用；**数字只取自已有结果，LLM 不新造数** | `report` | S4 |
| **reviewer** | 规则 + LLM | 质量门：每个数字必须有 source_ref、口径正确 → pass/fail + 修改意见 | `report_ok`、`report_issues` | S4 |
| **fixer** | LLM | 按 issues 局部修正 ≤2 轮；超限 → 报告显式标注"未通过校验" | `report`（局部） | S4 |
| **report_confirm** | ⏸ 人工断点 | 报告生成后等用户确认（可改口径/维度）再交付 | —（interrupt 状态） | S5 |
| **deliver** | 副作用 | 幂等落库（reports/evidence/tasks）+ 推送 + 缓存 + 状态收尾 | `status` | S5 |

**职责边界速记**：

- **编排节点（不干活，只路由）**：intent_router、fan_out —— 返回路由字符串 / Send 列表，无业务逻辑；
- **纯函数节点（确定性）**：join、reviewer 的校验部分 —— 无 LLM，输入输出可单测；
- **LLM 节点（理解/生成）**：task_parser、planner、analysis、replanner、writer、fixer —— 一律 `with_structured_output(Pydantic)`，失败重试 → 降级兜底；
- **人工断点（⏸）**：clarify_interrupt、report_confirm —— 图挂起等用户，resume 恢复；
- **强制主链子 agent**：research_agent —— 数据采集，结构保证必走（fan_out 分派）；
- **增值路由子 agent**（LLM 自主决定调不调）：web 补数 / 下钻分析 / 历史对比 / 图表 —— 注册表登记、即插即用，不调用不烧 token；
- **监督者**：主图条件边承担强制主链路由；supervisor（LLM）承担增值层路由 —— 决策分层，各管各的。

## 六、多 agent 设计（混合分层：注册表 + LLM 自主路由）

> **定位：多 agent 是形态，不是远期演进目标。** 子 agent 分两类：强制主链的 research_agent（结构保证必走）+ 增值子 agent（注册表登记，supervisor 的 LLM 自主决定调不调）。
> 铁律：**LLM 只被授权决定「增值部分」，永远无权跳过「必要部分」（质量门强制边）**——纯 Supervisor 允许 AI 关闭自己的质量门，是架构缺陷。

### 6.1 两类子 agent

| 类别 | 子 agent | 调用方式 | 能否跳过 |
| --- | --- | --- | --- |
| 强制主链 | research_agent | 结构边（fan_out 动态分派） | ❌ 不可跳过 |
| 增值层（注册表） | web 补数 / 下钻分析 / 历史对比 / 图表 | supervisor 的 LLM 计划决定 | ✅ LLM 自主 |

### 6.2 子 agent 注册表（supervisor 的能力菜单）

```python
AGENT_REGISTRY = [
    {"name": "research_agent",  "desc": "查数：SQL/RAG/Web",              "mode": "mandatory"},
    {"name": "web_fill_agent",  "desc": "补充外网行业/竞品数据",             "mode": "optional"},
    {"name": "drilldown_agent", "desc": "多维下钻定位异常",                 "mode": "optional"},
    {"name": "compare_agent",   "desc": "历史/竞品对比",                    "mode": "optional"},
    {"name": "chart_agent",     "desc": "图表生成",                       "mode": "optional"},
]
```

### 6.3 supervisor 节点（LLM 自主决定 · Plan-Execute）

```python
class RoutingPlan(BaseModel):
    agents: list[str] = Field(..., description="本次要调用的增值 agent（按顺序）；不需要则空列表")
    reason: str = Field(..., description="为什么这么决定")

async def supervisor(state: AnalystState) -> dict:
    plan = await llm.with_structured_output(RoutingPlan).ainvoke([
        {"role": "system", "content": f"你是编排大脑。可用子 agent：{AGENT_REGISTRY}。"
                                      "只决定增值部分：简单任务不调任何增值 agent；复杂任务按需调。"},
        {"role": "user", "content": state["messages"][-1]["content"]},
    ])
    return {"routing_plan": plan.model_dump()}   # 计划先出 → 可审计、可回滚（非黑盒 ReAct）
```

### 6.4 决策分层原则（为什么不是纯 Supervisor）

| 决策类型 | 交给谁 | 原因 |
| --- | --- | --- |
| 必走步骤（查数 / 分析 / 成稿 / 质检） | 结构强制（条件边） | 质量门不能被 AI 自己关闭 |
| 增值步骤（补数 / 下钻 / 对比 / 图表） | LLM 自主（supervisor） | 简单任务不调 = 零成本 |
| 预算 / 迭代上限 | 规则双闸 | 防 runaway |

### 6.5 与「节点里塞子 agent」（v1/v2 静态接线）的区别

- **v1/v2 静态接线**：子 agent 是图的一个固定节点，fan_out 按 plan 固定分派，**调不调由拓扑写死**——所有请求都走 research_agent，简单请求也被全链路处理。
- **本方案（混合分层）**：增值子 agent 是注册表能力，**调不调由 LLM 运行时决定**（动态路由）——简单任务只走主链（省 token），复杂任务 LLM 自主追加增值 agent。
- 两者统一：主链保底（正确性）+ 增值按需（成本）——能力上限提高，成本按需支付。

### 6.6 与方案 B（按场景切）的关系

按「角色阶段」切，方案 B 按「分析场景」切（insight / forecast / industry agent），**两者正交可叠加**：未来扩展预测 / 异动 / 行业对标场景时，可在增值注册表上追加场景 agent——但**强制主链不变**（质量门永远结构强制）。

## 七、state 字段演进表（哪步启用哪个字段）



| 字段                                            | reducer       | 启用步骤   | 说明                                |
| --------------------------------------------- | ------------- | ------ | --------------------------------- |
| messages                                      | add           | Step 0 | 对话历史                              |
| thread\_id / user\_id                         | replace       | Step 0 | 会话锚点 / 归属                         |
| iteration / budget / errors / fallbacks\_used | replace / add | Step 0 | 控制层（空跑即初始化）                       |
| task                                          | replace       | Step 1 | task\_parser 输出                   |
| plan                                          | replace       | Step 2 | planner 输出                        |
| research\_results                             | add           | Step 2 | research\_agent 输出（带 source\_ref） |
| analysis\_results                             | replace       | Step 3 | analysis 输出                       |
| evidence\_gaps                                | add           | Step 3 | reflect 输出                        |
| report / report\_ok                           | replace       | Step 4 | writer /reviewer 输出               |
| status                                        | replace       | Step 5 | deliver 收尾                        |

> 原则：
>
> **字段先声明（Step 0 全量）、按需启用**
>
> ——checkpoint 结构从头稳定，旧 checkpoint 兼容。

## 八、每步失败时的回滚策略



* 每步完成后 checkpoint / 代码即上一步的 "备份"；

* 某步验收失败 → 只回退该步新增的节点 / 边（图拓扑权威在 graph.py，改回上一版即可）；

* 结构性步骤（Step 0/2 的 Send、Step 3 的循环）成功后**不再回退**，后续只在其上叠加。

## 八、敲定清单（请在以下决策点上确认）



1. **Step 0 骨架**：已落盘并验证通过（`app/graph/state.py`、`graph.py`）—— 保留为起点，还是删除后按文档重建？

2. **Step 2 的 research\_agent**：先用 mock 跑通并行，Step 6 再换真实 create\_agent—— 确认这个节奏？

3. **Step 4 前置 MySQL 建表**：建表 SQL 此前已给过（5 表）—— 落库时机是否就定在 Step 4 前？

4. **验收粒度**：每步是否都要在 `graph.py` 里留验证入口（`python -m app.graph.graph` 逐步扩展）？

5. **多 agent 形态**：已定案为**混合分层**（§六）——research_agent 强制主链 + 增值子 agent 注册表（supervisor 的 LLM 自主决定调不调）；质量门强制边不被 LLM 跳过。确认后按此落地。



***

**一句话总结本文档**：从「START→END 空跑」出发，按 规划→分支→并行→循环→质量门→人工断点→健壮性 的顺序逐步长成三层图，**每步有独立验收、可回滚**；state 字段 Step 0 全量声明、按需启用，checkpoint 结构从头稳定。