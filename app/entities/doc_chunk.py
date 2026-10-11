"""RAG 文档处理实体。

解析/切分管线在各阶段产出的数据结构实体，与具体实现（MinerU/LangChain）解耦。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParseSegment:
    """解析层输出：一段带标题与页码的文本块流（中间表示）。

    由 parse_doc 产出：MinerU 每页一个元素，或 md/txt 整篇一个元素。
    title 为空时，标题层级保留在 text 的 Markdown # 中。
    """

    text: str
    title: str = ""
    page: int | None = None


@dataclass
class DocChunk:
    """入库级 chunk：经过切分与后处理的最终条目，可直接写入向量库。

    chunk_id 用 uuid5(source#index) 生成，保证同一 source 幂等。
    """

    text: str
    source: str
    chunk_index: int
    title: str = ""
    page: int | None = None
    chunk_id: str = ""
    tokens: int = 0


@dataclass
class RawChunk:
    """切分层输出：标题路径 + 正文块（parse_doc→_chunk_markdown 的中间产物）。

    由 _raw_chunks 产出，随后供 _post_process 升级为 DocChunk。
    相比 DocChunk，尚未注入 chunk_id / tokens，也未经碎片过滤。
    """

    text: str
    source: str
    chunk_index: int
    title: str = ""
    page: int | None = None