"""Web 检索工具：把通用网络搜索包装成 LLM 可调用的 LangChain 工具。

分层：工具层只做「适配 + 输出契约」，检索交给通用搜索（general_search）——
本模块不重复实现任何检索逻辑，只负责：
  - 把 general_search 结果转成 LLM 友好的文本摘要（标题 + 站点 + 摘要片段）；
  - 保留 source 链接，供 evidence 溯源。

何时调用（供 LLM 判断）：
  - 需要外部公开信息：行业趋势、竞品动态、宏观政策、公开数据；
  - 内部知识库（rag_search）无命中且问题依赖外部事实时。
何时不要调用：
  - 内部业务数据（用 SQL 工具）；口径类问题（用 rag_search）。
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.api.monitor import monitor


@tool
def web_search(query: str, max_results: int = 5) -> dict:
    """检索互联网公开信息，返回标题 + 来源站点 + 摘要片段，用于补充行业/竞品/政策背景。

    参数 query 填写建议：
      - 完整问题而非孤立关键词："2026 年电商直播行业 GMV 增速" 优于 "GMV"；
      - 保留关键限定（时间、地区、行业），如"2026 年 9 月 中国电商 大盘";
      - 保持用户原意，不要改写、缩写或翻译。
    """
    monitor.report_tool("web_search", {"query": query, "max_results": max_results})
    results = _search(query)
    items = []
    for r in results[:max_results]:
        items.append({
            "title": r.get("title", ""),
            "site": r.get("site", ""),
            "snippet": (r.get("snippet") or r.get("content") or "")[:500],
            "url": r.get("url", ""),
        })
    return {"query": query, "results": items, "count": len(items)}


def _search(query: str) -> list[dict]:
    """适配层：调用通用搜索（general_search），统一返回 [{title, site, snippet, url}]。

    说明：general_search 是本会话环境注入的搜索工具；若在纯 Python 环境不可用，
    降级为返回空结果（错误是数据不是异常，不抛给 LLM）。
    """
    try:
        import builtins

        # 优先用全局注入的 general_search
        gs = globals().get("general_search")
        if gs is None:
            gs = getattr(builtins, "general_search", None)
        if gs is None:
            return []
        out = gs(query)
        if isinstance(out, str):
            return []
        return list(out) if out else []
    except Exception:
        return []
