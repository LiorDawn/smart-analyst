"""
企业级文档解析与切分模块（现成库版）
====================================
管线：格式解析 → LangChain 现成 splitter 切分 → 后处理 → chunk

设计要点：
1. 解析层：保留轻量自研（PDF/DOCX——现成库不覆盖的版面块 / 标题层级能力）
2. 切分层：LangChain RecursiveCharacterTextSplitter（中文句末分隔符，无需额外依赖）
3. MD / TXT：直接走现成 splitter，不再手写标题栈 / 空行分段
4. token 计数：字符近似（≈ 2 字/token），不引入 tiktoken（保持离线部署可用）
5. 后处理：hash 去重 + 过滤过短 + 注入 chunk_id / source / 标题 / page / token 数
6. 单文件异常隔离：坏文件记录日志后跳过，不影响整批

依赖：pip install pymupdf python-docx langchain-text-splitters
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SUPPORTED = {".pdf", ".docx", ".md", ".txt"}
MAX_TOKENS = 512            # chunk token 上限（近似）
OVERLAP_TOKENS = 64         # 相邻 chunk 重叠 token
MIN_TOKENS = 20             # 后处理：过滤低于该 token 数的碎片
TITLE_MIN_RATIO = 1.15      # PDF 标题判定：字号 > 正文基准 × 系数
BODY_SIZE = 10.5            # PDF 正文基准字号（可按语料调整）
MAX_FILE_BYTES = 20_000_000  # 输入防护：单文件上限 20MB，防 buffer 炸弹
MAX_PDF_PAGES = 200         # 输入防护：PDF 页数上限

# 现成切分器（模块级复用）：中文句末标点优先，不引入 tiktoken（离线可用）
SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=MAX_TOKENS * 2,          # 字符口径 ≈ token × 2
    chunk_overlap=OVERLAP_TOKENS * 2,
    separators=["\n\n", "\n", "。", "；", "！", "？", " "],   # 分隔符优先级
)


# ---------------------------------------------------------------------------
# 工具：编码探测 / token 计数
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    """编码探测读取文本，避免 errors='ignore' 静默丢字。"""
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError(f"无法识别的文本编码: {path}")


def _count_tokens(text: str) -> int:
    """token 近似：中文 ≈ 2 字/token。不引入 tiktoken，保持离线部署可用。"""
    return (len(text) + 1) // 2


# ---------------------------------------------------------------------------
# 解析层：仅保留现成库不覆盖的格式（PDF / DOCX），输出统一块流
# ---------------------------------------------------------------------------

def _parse_pdf(path: Path, ocr_fn: Optional[Callable[[Path], str]] = None) -> list[dict[str, Any]]:
    import fitz  # PyMuPDF

    doc = fitz.open(path)
    if doc.page_count > MAX_PDF_PAGES:
        doc.close()
        raise ValueError(f"PDF 页数 {doc.page_count} 超上限 {MAX_PDF_PAGES}，已拒绝")
    segs: list[dict[str, Any]] = []

    for page_idx, page in enumerate(doc, start=1):
        blocks = _pdf_blocks(page)
        if not blocks:
            # 扫描件/图片型 PDF：提取为空 → OCR 兜底或告警
            if ocr_fn is not None:
                text = ocr_fn(path).strip()
                if text:
                    segs.append({"title": f"第{page_idx}页", "text": text, "page": page_idx})
                    continue
            logger.warning("第 %d 页无文本（疑似扫描件，已跳过；可传 ocr_fn 启用 OCR）", page_idx)
            continue

        # 块级标题识别：字号大于正文基准 → 标题边界
        buf: list[str] = []
        title = ""
        for blk in blocks:
            if blk["is_title"]:
                if buf:
                    segs.append({"title": title, "text": "\n".join(buf), "page": page_idx})
                    buf = []
                title = blk["text"]
            else:
                buf.append(blk["text"])
        if buf:
            segs.append({"title": title, "text": "\n".join(buf), "page": page_idx})

    doc.close()
    return segs


def _pdf_blocks(page, body_size: float = BODY_SIZE) -> list[dict[str, Any]]:
    """按版面块提取文本，用 span 字号识别标题块。"""
    raw = page.get_text("dict")
    blocks = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:          # 只取文本块，跳过图片块
            continue
        lines, max_size = [], 0.0
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if spans:
                max_size = max(max_size, max(s.get("size", 0) for s in spans))
            text = "".join(s.get("text", "") for s in spans)
            if text.strip():
                lines.append(text.strip())
        text = "\n".join(lines).strip()
        if text:
            blocks.append({"text": text, "is_title": max_size > body_size * TITLE_MIN_RATIO})
    return blocks


def _parse_docx(path: Path) -> list[dict[str, Any]]:
    from docx import Document

    doc = Document(path)
    segs: list[dict[str, Any]] = []
    title_stack: list[tuple[int, str]] = []   # 标题路径栈 [(level, text)]
    buf: list[str] = []

    def flush():
        nonlocal buf
        if buf:
            path_str = " > ".join(t for _, t in title_stack)
            segs.append({"title": path_str, "text": "\n".join(buf)})
            buf = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        m = re.search(r"(?:heading|标题)\s*(\d+)", (para.style.name or "").lower())
        if m:
            level = int(m.group(1))
            flush()
            # 维护层级：遇到同级/更高级标题，弹出栈顶
            while title_stack and title_stack[-1][0] >= level:
                title_stack.pop()
            title_stack.append((level, text))
        else:
            buf.append(text)

    # 表格结构化提取（追加到当前段落后）
    for table in doc.tables:
        rows = []
        for row in table.rows:
            rows.append(" | ".join(cell.text.strip().replace("\n", " ") for cell in row.cells))
        if rows:
            buf.append("表格:\n" + "\n".join(rows))

    flush()
    return segs


# ---------------------------------------------------------------------------
# 统一入口：按扩展名路由（MD/TXT 直接走现成 splitter，无需自研解析）
# ---------------------------------------------------------------------------

def parse_doc(path: Path) -> list[dict[str, Any]]:
    """解析成统一块流 [{title, text, page}]。MD/TXT 整篇为一个块，交给 splitter。"""
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"不支持的文件类型: {ext}，支持 {sorted(SUPPORTED)}")
    if ext == ".md":
        return [{"title": "", "text": _read_text(path)}]
    if ext == ".pdf":
        return _parse_pdf(path)
    if ext == ".docx":
        return _parse_docx(path)
    return [{"title": "", "text": _read_text(path)}]   # .txt


# ---------------------------------------------------------------------------
# 后处理：现成 splitter 切分 + 去重 / 过滤过短 / 元数据注入
# ---------------------------------------------------------------------------

def _post_process(segs: Iterable[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for seg in segs:
        for piece in SPLITTER.split_text(seg["text"]):
            tokens = _count_tokens(piece)
            if tokens < MIN_TOKENS:
                continue
            digest = hashlib.md5(piece.encode("utf-8")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            idx = len(results)
            results.append({
                "chunk_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}#{idx}")),
                "text": piece,
                "title": seg.get("title", ""),
                "page": seg.get("page"),
                "source": source,
                "chunk_index": idx,
                "tokens": tokens,
            })
    return results


# ---------------------------------------------------------------------------
# 对外入口：单文件 / 批量，异常隔离 + 日志
# ---------------------------------------------------------------------------

def process_file(path: Path, source: Optional[str] = None,
                 ocr_fn: Optional[Callable[[Path], str]] = None) -> list[dict[str, Any]]:
    """处理单个文件；任何异常记录日志并返回空列表，不影响调用方。"""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            logger.warning("文件超限(>%d bytes)已跳过: %s", MAX_FILE_BYTES, path)
            return []
        chunks = _post_process(parse_doc(path), source or str(path))
        logger.info("processed %s -> %d chunks", path, len(chunks))
        return chunks
    except Exception:
        logger.exception("failed to process %s", path)
        return []


def process_batch(paths: Iterable[Path], source_fn: Optional[Callable[[Path], str]] = None,
                  ocr_fn: Optional[Callable[[Path], str]] = None) -> Iterable[dict[str, Any]]:
    """批量处理（生成器）：坏文件被隔离，其余正常产出。"""
    for path in paths:
        yield from process_file(path, source_fn(path) if source_fn else None, ocr_fn)


# ---------------------------------------------------------------------------
# 兼容层：旧 API（build_kb.py 仍引用，待其同步后移除）
# ---------------------------------------------------------------------------

def chunk_document(path: Path, source: str) -> list[dict[str, Any]]:
    """旧接口兼容：等价于 process_file，字段结构保持不变。"""
    return process_file(path, source)


def word_count(text: str) -> int:
    """旧接口兼容：token 近似计数。"""
    return _count_tokens(text)


# ---------------------------------------------------------------------------
# 示例
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for p in [Path("示例.md"), Path("示例.pdf"), Path("示例.docx"), Path("示例.txt")]:
        if p.exists():
            for c in process_file(p):
                print(c["chunk_id"][:8], c["title"], "|", c["text"][:40], "...", c["tokens"])