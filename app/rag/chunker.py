"""M3 · 文档解析与切分（MinerU 版）
========================================
管线：MinerU 解析 → LangChain 现成 splitter 切分 → 后处理 → chunk

设计要点：
1. 解析层：MinerU（云端 API，flash 免 Token / precision 高精度）替代手写
   pypdf / python-docx / 文本解析，支持 PDF/DOCX/PPTX/XLSX/图片/EPUB 等格式，
   输出带标题层级的 Markdown（表格 → HTML、公式 → LaTeX、扫描件内置 OCR）。
2. 切分层：LangChain 现成 splitter
   - MarkdownHeaderTextSplitter：按 # 标题层级切，标题路径进 metadata（title）
   - RecursiveCharacterTextSplitter：token/字符口径二次切（chunk_size=512, overlap=64）
3. 对外接口（唯一入口，统一返回 DocChunk 实体）：
   - parse_doc(path) / process_file(path) / process_batch(...)
4. 后处理：jieba token 过滤碎片 + md5 去重 + 注入 chunk_id/page/source/chunk_index/tokens
5. 幂等：chunk_id = uuid5(source#index)，重复建库 upsert 覆盖同一点
6. 单文件异常隔离：坏文件记录日志后跳过，不影响整批

注意：
- MinerU flash 模式需联网（文件上传云端解析，≤20 页/10MB，免 Token）；
  precision 模式需 MINERU_TOKEN 环境变量（≤600 页/200MB，官网免费申请）。
- 若要求完全离线解析，可改用本地 mineru-kit（pip install "mineru>=4.0,<5"），
  输出 Markdown 后复用本文件的切分/后处理逻辑。

依赖：uv pip install langchain-mineru langchain-text-splitters jieba
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from pathlib import Path
from typing import Callable, Iterable, Optional

import jieba
from langchain_mineru import MinerULoader
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from app.entities.doc_chunk import DocChunk, ParseSegment, RawChunk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# MinerU 覆盖的格式（云端 API 支持 PDF/图片/DOC/DOCX/PPT/PPTX，另可扩展）
SUPPORTED = {".pdf", ".docx", ".md", ".txt", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg", ".webp"}

CHUNK_SIZE = 512        # chunk 字符上限（与 v1 参数一致）
OVERLAP = 64            # 相邻 chunk 重叠字符
MIN_TOKENS = 20         # 后处理：过滤低于该 jieba token 数的碎片
MAX_FILE_BYTES = 20_000_000  # 输入防护：单文件上限 20MB

# 现成切分器（模块级复用）
SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=OVERLAP,
    separators=["\n\n", "\n", "。", "；", "！", "？", " "],  # 中文句末优先
)

MD_SPLITTER = MarkdownHeaderTextSplitter(
    headers_to_split_on=[("#", "H1"), ("##", "H2"), ("###", "H3"), ("####", "H4")]
)

# ---------------------------------------------------------------------------
# 解析层：MinerU
# ---------------------------------------------------------------------------


def _parse_with_mineru(path: Path, mode: str = "flash") -> list[ParseSegment]:
    """MinerU 解析：文档 → 块流 [{title, text, page}]。

    作用：把 PDF/DOCX/PPTX/图片等二进制文档交给 MinerU 云端 API 解析，
          返回带页面标记的 Markdown 块流（表格→HTML、公式→LaTeX、扫描件内置 OCR）。

    参数:
        path: 文档路径（扩展名需在 SUPPORTED 中）
        mode: "flash"（默认，免 Token，≤20 页/10MB）或 "precision"（需 MINERU_TOKEN，≤600 页/200MB）

    返回:
        list[ParseSegment]：每页一个元素（title/text/page）

    注意:
        需联网；title 留空，标题层级保留在 text 的 # 中，由 _chunk_markdown 提取。
    """
    loader = MinerULoader(
        source=str(path),
        mode=mode,
        split_pages=True,  # 每页一个 Document，metadata 带 page
    )
    docs = loader.load()
    segs: list[ParseSegment] = []
    for doc in docs:
        segs.append(ParseSegment(
            text=doc.page_content,   # Markdown（含 # 标题层级）
            page=doc.metadata.get("page"),
        ))
    return segs


def parse_doc(path: Path, mode: str = "flash") -> list[ParseSegment]:
    """v2 接口：解析成统一块流 list[ParseSegment]（title/text/page）。

    作用：按扩展名路由——md/txt 本地直接读取（离线可用，不联网），
          其余格式（pdf/docx/pptx/图片...）交给 MinerU 云端解析。

    参数:
        path: 文档路径
        mode: MinerU 模式（仅对非 md/txt 生效）

    返回:
        统一块流列表；title 为空表示标题信息在 text 的 Markdown 层级中

    异常:
        ValueError: 扩展名不在 SUPPORTED 中
    """
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"不支持的文件类型: {ext}，支持 {sorted(SUPPORTED)}")
    if ext in (".md", ".txt"):
        # 纯文本无需云端解析，直接读取（离线可用）
        return [ParseSegment(text=path.read_text(encoding="utf-8", errors="ignore"))]
    return _parse_with_mineru(path, mode)


# ---------------------------------------------------------------------------
# 切分层：LangChain 现成 splitter
# ---------------------------------------------------------------------------


def _chunk_markdown(text: str) -> list[tuple[str, str]]:
    """Markdown 文本 → [(标题路径, chunk文本)]。

    作用：两段式切分——
      第一段：MarkdownHeaderTextSplitter 按 # 层级切，把标题路径（H1 > H2 > H3）提取为 title；
      第二段：每个标题块下的正文若超长，用 RecursiveCharacterTextSplitter 按 512/64 二次切碎。

    参数:
        text: Markdown 原文（含 # 标题层级）

    返回:
        [(标题路径, 正文chunk), ...]；无标题时返回 [("", chunk), ...]

    示例:
        "# 第一章\\n正文..." → [("第一章", "正文..."), ...]
    """
    pieces = MD_SPLITTER.split_text(text)
    if not pieces:
        return [("", t) for t in SPLITTER.split_text(text)]
    out: list[tuple[str, str]] = []
    for piece in pieces:
        # 标题层级显式按 H 数字排序（H1<H2<H3<H4），避免字典序侥幸
        headers = [v for k, v in sorted(
            piece.metadata.items(),
            key=lambda kv: int(re.search(r"\d+", kv[0]).group(0)),
        )]
        title = " > ".join(h for h in headers if h)
        for chunk in SPLITTER.split_text(piece.page_content):
            out.append((title, chunk))
    return out


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def _raw_chunks(path: Path, source: str, mode: str = "flash") -> list[RawChunk]:
    """内部：解析 + 切分 → RawChunk 实体列表（尚未后处理）。

    作用：把 parse_doc 的块流经 _chunk_markdown 切分，组装为带来源/标题/页码的
          RawChunk 中间实体，供 _post_process 升级为 DocChunk。

    参数:
        path: 文档路径
        source: 来源标识
        mode: MinerU 模式

    返回:
        list[RawChunk]
    """
    results: list[RawChunk] = []
    for seg in parse_doc(path, mode):
        for title, text in _chunk_markdown(seg.text):
            results.append(RawChunk(
                text=text,
                source=source,
                chunk_index=len(results),
                title=title or seg.title,
                page=seg.page,
            ))
    return results


def _post_process(chunks: Iterable[RawChunk], source: str) -> list[DocChunk]:
    """后处理：过滤碎片 + md5 去重 + 注入 chunk_id / page / tokens（幂等）。

    作用：把 RawChunk 升级为入库级 DocChunk——
      1. 过滤 jieba token 数 < MIN_TOKENS 的碎片；
      2. md5 去重（相同文本只保留一块）；
      3. 注入 chunk_id（uuid5(source#index)，同一 source 幂等）、tokens 数。

    参数:
        chunks: 切分层输出的 RawChunk 实体
        source: 来源标识（用于 chunk_id 命名空间）

    返回:
        list[DocChunk]：入库级 chunk 实体（含 chunk_id/tokens）
    """
    seen: set[str] = set()
    results: list[DocChunk] = []
    for chunk in chunks:
        tokens = _token_count(chunk.text)
        if tokens < MIN_TOKENS:
            continue
        digest = hashlib.md5(chunk.text.encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        idx = len(results)
        results.append(DocChunk(
            chunk_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}#{idx}")),
            text=chunk.text,
            title=chunk.title,
            page=chunk.page,
            source=source,
            chunk_index=idx,
            tokens=tokens,
        ))
    return results


def process_file(
    path: Path,
    source: Optional[str] = None,
    mode: str = "flash",
) -> list[DocChunk]:
    """v2 接口：单文件处理，异常隔离——坏文件返回空列表并打日志，不影响调用方。

    作用：单文件完整管线（解析→切分→后处理），并做输入防护——
      超 20MB 直接跳过；任何异常捕获后返回 [] 并记录日志，调用方无需 try。

    参数:
        path: 文档路径
        source: 来源标识（默认用文件路径字符串）
        mode: MinerU 模式

    返回:
        list[DocChunk]：入库级 chunk 实体列表；失败/超限时返回 []
    """
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            logger.warning("文件超限(>%d bytes)已跳过: %s", MAX_FILE_BYTES, path)
            return []
        src = source or str(path)
        results = _post_process(_raw_chunks(path, src, mode), src)
        logger.info("processed %s -> %d chunks", path, len(results))
        return results
    except Exception:
        logger.exception("failed to process %s", path)
        return []


def process_batch(
    paths: Iterable[Path],
    source_fn: Optional[Callable[[Path], str]] = None,
    mode: str = "flash",
) -> Iterable[DocChunk]:
    """v2 接口：批量处理（生成器），坏文件被隔离，其余正常产出。

    作用：逐文件调用 process_file 并 yield，惰性生成——
      大目录不一次性载入内存；单个坏文件只影响自身，不中断整批。

    参数:
        paths: 文件路径的可迭代对象
        source_fn: 可选函数，path → source，按需自定义来源标识
        mode: MinerU 模式

    返回:
        生成器，逐个产出 DocChunk 实体
    """
    for path in paths:
        yield from process_file(path, source_fn(path) if source_fn else None, mode)


def _token_count(text: str) -> int:
    """内部：jieba 分词数，作为 token 数的近似口径（过滤碎片/统计用）。

    作用：用 jieba 对中文文本分词并返回词数，用于 _post_process
          的碎片过滤与 DocChunk.tokens 填充；非精确 tokenizer。
    """
    return len(jieba.lcut(text))


# ---------------------------------------------------------------------------
# 示例
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for p in [Path("示例.md"), Path("示例.pdf"), Path("示例.docx")]:
        if p.exists():
            for c in process_file(p):
                print(c.chunk_id[:8], c.title, "|", c.text[:40], "...", c.tokens)
