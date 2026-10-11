# SmartAnalyst M5・查数链路设计方案（收敛版・定稿）

> 关联文档：《SmartAnalyst-M5 设计优化方案.md》（v3 全局收敛）、《SmartAnalyst-M5 编排开发设计.md》、《SmartAnalyst-M5 开发文档.md》
> 定位：
>
> **专项设计查数段**
>
> —— 从 
>
> `task_parser`
>
>  到 
>
> `analysis`
>
>  的整条查数链路（6 个节点），按企业式收敛：指标定义表 + 规则拼 SQL + LLM 只做匹配；派生计算归 analysis；默认单实例执行。
> 范围：
>
> **不含**
>
>  writer 之后的链路（writer/reviewer/fixer/deliver，沿用 v2/v3 设计）；
>
> **不含**
>
>  增值层（supervisor + 子 agent 注册表，见 v3 § 六）。
> 状态：
>
> **设计定稿**
>
> （query_executor 定名；业务指标按电商经营分析实际建模；含复杂指标应对方案）



***

## 一、查数段范围与链路总览



```
task_parser（LLM：解析意图/指标/时间/维度/对比 → TaskSchema）
   ↓
route_intent（规则三路：quick / clarify / analyze）
   └─ analyze ─┐
                ▼
planner（指标定义表匹配 → 规则拼 SQL → QueryPlan；未命中 → LLM 查口径兜底）
   ↓
query_executor（执行 QueryPlan：逐条 SQL / 原子查询 · 默认 1 实例）
   ↓
join（汇聚：去重 / 按 metric+purpose 分组 / 排序）
   ↓
analysis（派生指标计算 + 跨期配对 + LLM 归因 → analysis_results）
   ↓
（接 writer 链路 · 本方案不展开）
```

**边界**：查数段的职责 = 「理解任务 → 决定查什么 → 执行查询 → 汇聚 → 算出指标」。它不写报告（writer）、不质检（reviewer）、不做增值（supervisor）。

## 二、节点逐一设计

### 1. `task_parser`（LLM・保留现有实现）



| 项  | 设计                                                                                              |
| -- | ----------------------------------------------------------------------------------------------- |
| 输入 | 用户一句话（messages）                                                                                 |
| 逻辑 | `llm.with_structured_output(TaskSchema)`；相对时间转绝对日期（独立 date 工具）；metrics 缺失 → missing 字段走 clarify |
| 输出 | `task`（TaskSchema：intents / metrics / time\_range / dims / compare / missing）                   |
| 降级 | 解析失败 → 重试 1 次 → 降级 quick（通用问答）                                                                  |

### 2. `route_intent`（规则・保留现有实现）



| 项  | 设计                                                                |
| -- | ----------------------------------------------------------------- |
| 输入 | `task`（TaskSchema）                                                |
| 逻辑 | 优先级：**quick**（知识问答，不查数）> **clarify**（missing 含阻断性缺失）> **analyze** |
| 输出 | 路由字符串（quick → RAG 直答；clarify → ⏸ 澄清；analyze → planner）            |
| 说明 | 「要不要查数」的判断在这里（规则化，不引入 LLM—— 能规则的绝不 LLM）                           |

### 3. `planner`（规则 + LLM 兜底・重写）

**职责**：把 TaskSchema 的每个 metric 翻译成查询计划（QueryPlan），**SQL 由指标定义表规则生成，LLM 只在字典未命中时介入**。

**核心逻辑**：



```
async def planner(state: AnalystState) -> dict:
    task = state["task"]
    items: list[QueryItem] = []
    for metric in task["metrics"]:
        m = METRIC_DICT.get(metric)
        if m is None:
            m = await resolve_metric_via_rag(metric)   # ① LLM 兜底：查口径 → 回写字典
            if m is None:
                errors.append({"node": "planner", "msg": f"指标 {metric} 口径未知"})
                continue
        items += build_query_items(m, task)             # ② 规则生成（见下）
    return {"plan": {"items": items, "max_steps": len(items) + 2}}
```

**规则生成规则（build\_query\_items）**：



| 指标类型              | 生成逻辑                                                                                                       |
| ----------------- | ---------------------------------------------------------------------------------------------------------- |
| atomic            | 一条 SQL：`SELECT {agg}({field}) FROM {table} WHERE date BETWEEN {time_range} [AND {filter}] [GROUP BY dims]` |
| derived（简单公式）     | 拆 base 指标原子查询 → analysis 计算（**不在 SQL 层现拼公式**，见 § 四）                                                        |
| cross\_period（跨期） | 解析 base 指标，生成两段：分析期（purpose=current）+ 对比期（purpose=compare，携带 compare 标记）                                   |

**输出**：`plan`（QueryPlan：items\[{type:sql, sql, metric, purpose, depends\_on}]）——**SQL 是规则的产物，不是 LLM 的产物**。

### 4. `query_executor`（原 research\_agent・收敛定名・重写）

**职责**：按 QueryPlan 逐条执行 SQL（只读），返回带 source\_ref 的结果。**默认单实例顺序执行**；仅多独立查询 / 多任务时才 fan\_out 并行（可选优化，本段默认关闭）。



```
async def query_executor(state: AnalystState) -> dict:
    plan = state["plan"]
    results = []
    for i, item in enumerate(plan["items"]):
        if item["depends_on"] and not deps_satisfied(item, results):
            continue
        try:
            rows = await run_readonly_sql(item["sql"])     # 只读执行
            results.append({"metric": item["metric"], "purpose": item["purpose"],
                            "result": rows,
                            "source_ref": f"sql#{item['sql']}#{i}",
                            "confidence": 1.0, "degraded": False})
        except SQLExecError as e:
            results.append({**item, "degraded": True, "error": str(e)})   # 单条失败不中断
    return {"research_results": results}
```

**工具边界**：本段**没有** sql/rag/web 三工具自由调用 —— 查询 = 规则 SQL 执行；rag 只出现在「planner 查口径」和「quick 直答」；web 只出现在增值层。

### 5. `join`（纯函数・实现）



| 项  | 设计                                                          |
| -- | ----------------------------------------------------------- |
| 逻辑 | 按 source\_ref 去重 → 按 (metric, purpose) 分组 → 按 confidence 排序 |
| 输出 | `research_results`（结构化，供 analysis 配对）                       |

### 6. `analysis`（代码计算 + LLM 归因・实现）

**职责**：把原子数据 + 指标公式 → 指标结果；跨期配对算涨跌；LLM 只做归因解释。



```
async def analysis(state: AnalystState) -> dict:
    data = group_by_metric_purpose(state["research_results"])   # {(metric, purpose): rows}
    out = []
    for metric in state["task"]["metrics"]:
        m = METRIC_DICT[metric]
        if m["type"] == "atomic":
            out.append({"metric": metric, "value": data[(metric, "current")]})
        elif m["type"] == "derived":
            out.append({"metric": metric,
                        "value": compute_formula(m, data)})      # ① 代码算（按公式解析 base → pandas/纯函数）
        elif m["type"] == "cross_period":
            cur, cmp = data[(metric, "current")], data[(metric, "compare")]
            out.append({"metric": metric, "value": cur, "compare": cmp,
                        "change": (cur - cmp) / cmp if cmp else None})   # ② 跨期配对（代码）
    # ③ LLM 归因：基于计算结果解释变化（一次调用）
    return {"analysis_results": out}
```

**计算全在代码**：派生公式、跨期配对、涨跌幅都是确定性计算；LLM 只在最后一步「归因解释」介入。

## 三、业务指标定义表（按电商经营分析实际建模）

### 3.1 数据表结构（口径来源）



| 表                | 说明    | 关键字段                                                                                                     |
| ---------------- | ----- | -------------------------------------------------------------------------------------------------------- |
| `fact_sales`     | 订单事实表 | order\_id / customer\_id / product\_id / category / channel / amount / date / status / is\_new\_customer |
| `fact_traffic`   | 流量事实表 | date / channel / visitors / clicks / impressions                                                         |
| `fact_refund`    | 退款事实表 | order\_id / amount / date                                                                                |
| `fact_marketing` | 投放事实表 | date / channel / spend / clicks                                                                          |
| `dim_product`    | 商品维度表 | product\_id / category / stock                                                                           |

### 3.2 指标定义表（首批 16 个，覆盖核心经营分析）



```
METRIC_DICT = {
    # ========== 原子指标（直接查事实表字段，规则拼 SQL） ==========
    "gmv":          {"type": "atomic", "table": "fact_sales",   "field": "amount",
                     "agg": "SUM", "filter": "status != 'cancelled'",
                     "precompute": True, "desc": "成交额（剔除取消订单）"},
    "orders":       {"type": "atomic", "table": "fact_sales",   "field": "order_id",
                     "agg": "COUNT_DISTINCT", "filter": "status != 'cancelled'",
                     "precompute": True, "desc": "成交订单数"},
    "visitors":     {"type": "atomic", "table": "fact_traffic", "field": "visitors",
                     "agg": "SUM", "precompute": True, "desc": "访客数"},
    "customers":    {"type": "atomic", "table": "fact_sales",   "field": "customer_id",
                     "agg": "COUNT_DISTINCT", "filter": "status != 'cancelled'",
                     "desc": "成交客户数"},
    "new_customers": {"type": "atomic", "table": "fact_sales",  "field": "customer_id",
                     "agg": "COUNT_DISTINCT", "filter": "is_new_customer = 1",
                     "desc": "新客数"},
    "refund_amount": {"type": "atomic", "table": "fact_refund", "field": "amount",
                     "agg": "SUM", "desc": "退款额"},
    "ad_spend":     {"type": "atomic", "table": "fact_marketing", "field": "spend",
                     "agg": "SUM", "desc": "广告花费"},
    "clicks":       {"type": "atomic", "table": "fact_traffic", "field": "clicks",
                     "agg": "SUM", "desc": "点击量"},
    "impressions":  {"type": "atomic", "table": "fact_traffic", "field": "impressions",
                     "agg": "SUM", "desc": "曝光量"},
    "stock":        {"type": "atomic", "table": "dim_product", "field": "stock",
                     "agg": "SUM", "dims_supported": ["category"], "desc": "库存量"},

    # ========== 派生指标（原子 + 公式，analysis 代码计算） ==========
    "conversion_rate": {"type": "derived", "base": ["orders", "visitors"],
                        "formula": "orders / visitors", "precompute": True,
                        "desc": "转化率 = 成交订单数 / 访客数"},
    "avg_order_value": {"type": "derived", "base": ["gmv", "orders"],
                        "formula": "gmv / orders", "desc": "客单价 = 成交额 / 订单数"},
    "refund_rate":     {"type": "derived", "base": ["refund_amount", "gmv"],
                        "formula": "refund_amount / gmv", "desc": "退款率"},
    "roi":             {"type": "derived", "base": ["gmv", "ad_spend"],
                        "formula": "gmv / ad_spend", "desc": "投放回报 = 成交额 / 广告花费"},
    "ctr":             {"type": "derived", "base": ["clicks", "impressions"],
                        "formula": "clicks / impressions", "desc": "点击率"},

    # ========== 跨期指标（两段时间窗配对计算） ==========
    "gmv_wow":    {"type": "cross_period", "base": "gmv",   "period": "week",
                   "formula": "(current - compare) / compare", "desc": "GMV 周环比涨跌幅"},
    "orders_wow": {"type": "cross_period", "base": "orders", "period": "week",
                   "formula": "(current - compare) / compare", "desc": "订单量周环比涨跌幅"},
    "gmv_mom":    {"type": "cross_period", "base": "gmv",   "period": "month",
                   "formula": "(current - compare) / compare", "desc": "GMV 月环比涨跌幅"},
}
```

### 3.3 指标五要素（注册规范）

**名称 / 类型（原子・派生・跨期）/ 数据来源（表 + 字段 + 聚合）/ 口径（公式 + filter）/ 预计算标记 + 血缘（base 依赖）**

新增指标 = 注册表加一条（含血缘），**规则即可查**—— 不用改代码、不用大模型现猜。

## 四、复杂指标应对方案（逐类策略）



| #  | 复杂类型            | 例子                                                  | 应对方案（收敛后）                                                                                            |
| -- | --------------- | --------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| 1  | **简单派生**（同粒度公式） | 转化率 = orders/visitors                               | planner 拆 base 原子查询（orders、visitors 各一条）→ analysis 按公式算（代码）。**不在 SQL 层拼公式**—— 口径统一、可测                |
| 2  | **跨期指标**        | GMV 周环比                                             | planner 解析 base（gmv），生成两段（current + compare）原子查询 → analysis 配对算 `(cur-cmp)/cmp`；compare 携带 period 标记 |
| 3  | **条件聚合**（口径过滤）  | GMV 剔除取消订单                                          | filter 固化进指标定义表（`status != 'cancelled'`），规则拼 SQL 时自动带 WHERE                                          |
| 4  | **多口径同词**       | GMV（含 / 不含运费）                                       | 注册两个指标（`gmv` / `gmv_excl_freight`），口径分开，不混用                                                          |
| 5  | **多表关联派生**      | ROI = gmv(fact\_sales) / ad\_spend(fact\_marketing) | 拆到各事实表的原子查询 → analysis 按公共粒度（date + dims）merge 后算                                                    |
| 6  | **跨粒度合并**       | 客单价 = 总额 / 订单数（不同粒度）                                | base 各自带粒度标注；analysis 先聚合到目标粒度再算                                                                     |
| 7  | **占比 / 结构**     | 品类 GMV 占比                                           | 两条原子查询（总值 + 品类值）→ analysis 算占比                                                                       |
| 8  | **累计 / 滚动**     | 近 30 天累计                                            | 时间窗加宽的一条 SQL（date >= 起点），无需特殊机制                                                                      |
| 9  | **漏斗 / 留存（序列）** | 浏览→加购→下单→支付                                         | MVP 阶段**不建模**；企业做法 = 预计算物化表（跑批任务写结果表），查询直接读 —— 作为 C5 后续项                                             |
| 10 | **高频复杂指标**      | GMV、转化率                                             | `precompute=True` → MVP 用缓存（同参数命中）；后续跑批物化结果表，查询直读                                                    |

**原则**：



* **复杂计算全部代码化**（planner 拆解 + analysis 计算），LLM 不碰计算；

* **口径受控**（filter / 公式固化在定义表），新口径 = 注册新指标；

* **预计算兜高频**（物化 / 缓存），复杂指标查询仍快；

* **序列类（漏斗 / 留存）** MVP 明确不做，标注 C5 后续。

## 五、state 数据流（查数段）



```
task（TaskSchema）        ← task_parser，replace
plan（QueryPlan）          ← planner，replace
research_results（list）   ← query_executor，operator.add（join 内聚合同字段）
analysis_results（list）   ← analysis，replace
errors（list）             ← 各节点，operator.add（错误是数据）
```

## 六、降级矩阵（查数段）



| 故障                   | 降级行为                                                   |
| -------------------- | ------------------------------------------------------ |
| task\_parser 解析失败    | 重试 → 降级 quick 直答                                       |
| 指标字典未命中 + LLM 口径兜底失败 | 记 errors，跳过该指标（其余照常）                                   |
| 单条 SQL 执行失败          | 该条 degraded，不中断其他条                                     |
| 全部 SQL 失败            | research\_results 空 → analysis 降级「无数据说明」→ writer 出降级报告 |
| 派生计算失败（公式错 / 数据缺）    | 记 errors + 该指标标注「计算失败」                                 |

## 七、对现有代码的调整清单（定稿）



| 文件                                                            | 调整                                                           |
| ------------------------------------------------------------- | ------------------------------------------------------------ |
| `app/conf/metric_dict.py`（新）                                  | 指标定义表（§ 三 首批 16 个）                                           |
| `app/graph/nodes/planner.py`                                  | **重写**：字典匹配 + 规则拼 SQL（原子直查 / 派生拆 base / 跨期两段）+ LLM 兜底        |
| `app/graph/workers/query_executor.py`（新，原 research\_agent.py） | **重写并改名**：规则 SQL 执行，默认 1 实例，带 source\_ref                    |
| `app/graph/nodes/join.py`                                     | 实现：去重 / 分组 / 排序                                              |
| `app/graph/nodes/analysis.py`                                 | 实现：派生公式计算 + 跨期配对 + LLM 归因                                    |
| `app/entities/planner_schema.py`                              | 加 `QueryItem`（type/sql/metric/purpose/depends\_on）           |
| `app/graph/state.py`                                          | 确认 research\_results/analysis\_results/errors reducer 就位     |
| `app/graph/graph.py`                                          | 接线：planner → query\_executor → join → analysis（默认无 fan\_out） |
| `app/graph/nodes/fan_out.py`                                  | 保留为可选（多独立任务时启用），默认不接入                                        |

**保留不动**：task\_parser、route\_intent、clarify\_interrupt、report\_confirm、writer/reviewer/fixer/deliver、reflect/replanner（后续段）。

## 八、验收标准（查数段）



| 场景          | 验收                                                                       |
| ----------- | ------------------------------------------------------------------------ |
| "上周 GMV 多少" | 字典命中 gmv → 1 条 SQL（含 filter）→ 出数带 source\_ref                            |
| "转化率上周"     | planner 拆 orders+visitors → analysis 算转化率                                |
| "GMV 周环比"   | planner 出分析期 + 对比期两段 → analysis 配对算涨跌幅                                   |
| "ROI 上周"    | 拆 fact\_sales + fact\_marketing 两条原子查询 → analysis 按 date+channel merge 算 |
| 字典未命中指标     | LLM 查口径兜底 → 回写字典 → 下次规则直查                                                |
| 单条 SQL 失败   | degraded 标记，其余照常，errors 记录                                               |



***

**一句话总结**：查数段定稿 = 「task\_parser (LLM 解析) → route\_intent (规则分流) → planner (指标定义表 + 规则拼 SQL) → query\_executor (单实例执行) → join (汇聚) → analysis (代码计算 + LLM 归因)」——**LLM 只出现在解析与归因两处，查询与计算全部规则 / 代码**；复杂指标按「拆 base 原子查询 → analysis 代码计算 → 高频预计算」应对，口径全部受控于指标定义表。