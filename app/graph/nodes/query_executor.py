"""Worker：query_executor（查询执行器 · 原 research_agent 收敛）。

职责：按 QueryPlan（list[QueryItem]）逐条执行规则 SQL（只读），
      返回带 source_ref 的 ResearchResult 列表。

收敛点（《查数链路设计方案》§二.4 / §七）：
  - 多查询项并行执行（asyncio.gather + 限并发，SQL 相互独立）；
  - 无 sql/rag/web 三工具自由调用——查询 = 规则 SQL 执行；
  - 单条失败 → degraded 标记（错误是数据不是异常），不中断其他条；
  - source_ref = sql#{md5(sql)[:8]}：join 去重 + reviewer 溯源依据。

安全：
  - 只执行 SELECT（非 SELECT 直接拒绝，防注入/误用）；
  - SQL 来自 planner 规则拼装（字段名受控于指标定义表 + 日期格式校验）。

输入：plan（list[QueryItem] dict，planner 写入）
输出：research_results（list[ResearchResult] dict，operator.add）
所属 Step：M5 查数段 · 收敛版
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from sqlalchemy import text

from app.clients.mysql_client import mysql_client_manager
from app.entities.research_result import ResearchResult
from app.graph.state import AnalystState

logger = logging.getLogger(__name__)

_SQL_TIMEOUT_SECONDS = 10.0  # 单条 SQL 执行超时：死 SQL/慢查询不能无限挂死请求（超时 → 单条降级）


async def _run_sql(sql: str) -> list[dict]:
    """执行一条只读 SQL → 行字典列表。连接/执行失败向上抛，由调用方降级。"""
    if not sql.lstrip().upper().startswith("SELECT"):  # 只读防护：规则 SQL 均为 SELECT，防注入/误用
        raise ValueError(f"仅允许只读 SELECT，拒绝: {sql[:80]}")
    if mysql_client_manager.engine is None:
        mysql_client_manager.init()
    async with mysql_client_manager.engine.connect() as conn:
        # asyncio.wait_for：超时抛 TimeoutError → _exec_one 捕获降级，不阻塞其他查询项
        result = await asyncio.wait_for(conn.execute(text(sql)), timeout=_SQL_TIMEOUT_SECONDS)
        return [dict(row) for row in result.mappings()]


_MAX_CONCURRENCY = 4  # 并行 SQL 上限（防连接池被打满）


async def _exec_one(item: dict) -> ResearchResult:
    """执行单个查询项；失败不抛异常，返回 degraded 结果（错误是数据）。"""
    sql = item.get("query", "")
    metric = item.get("metric", "")
    period = item.get("period", "current")
    target = item.get("target_metric")
    ref = f"sql#{hashlib.md5(sql.encode('utf-8')).hexdigest()[:8]}"  # 溯源锚点：同 SQL 同 ref → join 去重依据
    try:
        rows = await _run_sql(sql)
        return ResearchResult(
            metric=metric, period=period, target_metric=target, rows=rows,
            source_ref=ref, confidence=1.0, degraded=False,
        )
    except asyncio.TimeoutError:
        msg = f"SQL timeout 执行超时（>{_SQL_TIMEOUT_SECONDS}s）: {ref}"
        logger.warning(msg)
        return ResearchResult(
            metric=metric, period=period, target_metric=target, rows=[],
            source_ref=ref, confidence=0.0, degraded=True, error=msg,
        )
    except Exception as e:  # noqa: BLE001 —— 单条失败降级，不中断整批
        logger.warning("SQL 执行失败（%s）: %s", ref, e)
        return ResearchResult(
            metric=metric, period=period, target_metric=target, rows=[],
            source_ref=ref, confidence=0.0, degraded=True, error=str(e),
        )


async def query_executor(state: AnalystState) -> dict:
    """并行执行 plan 中的只读 SQL（限并发），单条失败降级、不中断整批。"""
    plan = state.get("plan") or []
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def _guarded(item: dict) -> ResearchResult:
        async with sem:
            return await _exec_one(item)

    results = await asyncio.gather(*[_guarded(item) for item in plan])
    return {"research_results": [r.model_dump() for r in results]}
