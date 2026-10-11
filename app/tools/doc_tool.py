"""文档产出工具：给 LLM 保存文档 / 导出 PDF 的能力（方案、报告、建议的落盘交付）。

分层：工具层只做「内容 → 文件」的确定性管道，不负责内容生成（内容由 LLM 组织）。
  - save_doc   ：Markdown 内容 → .md 文件（默认落 doc/output/）
  - export_pdf ：Markdown 文本 → PDF（md→HTML→PDF；缺依赖时返回明确错误，不静默降级）

安全：文件名做白名单清洗（只允许 中文/字母/数字/下划线/连字符），杜绝路径注入；
      内容只写 .md/.html 临时文件，不执行任何内容。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from langchain_core.tools import tool

from app.api.monitor import monitor

# 默认输出目录：项目根/doc/output（可由环境变量 SMARTANALYST_OUTPUT_DIR 覆盖）
DEFAULT_OUTPUT_DIR = (
    Path(__file__).resolve().parents[2] / "doc" / "output"
)

_FILENAME_SAFE = re.compile(r"^[\w\u4e00-\u9fff-]+$")


def _safe_filename(name: str) -> str:
    """文件名清洗：只保留 中文/字母/数字/下划线/连字符，防路径注入。"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]", "_", name).strip("_")
    if not cleaned:
        cleaned = "document"
    return cleaned[:80]


@tool
def save_doc(title: str, content: str, filename: str | None = None) -> str:
    """把 Markdown 内容保存为 .md 文档，返回绝对文件路径（供后续引用/交付）。

    何时调用：
      - 用户要求"生成一份文档/方案/报告/建议"且需要落盘交付时；
      - 分析结论需要沉淀成可分享文档时。
    何时不要调用：
      - 用户只要对话里回答、不要文件时。

    参数填写建议：
      - title：文档标题（也会写入文档首行标题）；
      - content：Markdown 正文（标题/表格/要点均可）；
      - filename：可选，建议简短（如"Q3经营复盘方案"）；不填用标题。
    """
    monitor.report_tool("save_doc", {"title": title, "filename": filename})
    out_dir = DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = _safe_filename(filename or title) + ".md"
    path = out_dir / fname
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = f"# {title}\n\n> 生成时间：{stamp}\n\n{content}\n"
    path.write_text(body, encoding="utf-8")
    return f"已保存：{path}"


@tool
def export_pdf(markdown_text: str, filename: str) -> str:
    """把 Markdown 文本转为 PDF 文件，返回绝对路径。

    何时调用：
      - 用户明确要求 PDF/可打印/正式对外版本时。
    何时不要调用：
      - 用户只要 Markdown/文档文件，或只要求对话回答时。

    参数填写建议：
      - markdown_text：完整 Markdown 内容（标题/表格/段落）；
      - filename：PDF 文件名（如"Q3经营复盘方案"），不填扩展名。
    """
    monitor.report_tool("export_pdf", {"filename": filename})
    fname = _safe_filename(filename) + ".pdf"
    out_dir = DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / fname

    try:
        import markdown as md_lib
    except ImportError:
        return "PDF 导出失败：缺少 markdown 库（pip install markdown）——可先用 save_doc 保存 .md"

    html_body = md_lib.markdown(markdown_text, extensions=["tables", "fenced_code"])
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>body{font-family:'Microsoft YaHei',sans-serif;margin:2cm;line-height:1.6}"
        "table{border-collapse:collapse}td,th{border:1px solid #999;padding:4px 8px}</style>"
        f"</head><body>{html_body}</body></html>"
    )

    # 依次尝试可用的 PDF 引擎（weasyprint / pdfkit / reportlab 简化路径）
    try:
        import weasyprint

        weasyprint.HTML(string=html).write_pdf(str(path))
        return f"已导出 PDF：{path}"
    except ImportError:
        pass
    try:
        import pdfkit

        pdfkit.from_string(html, str(path))
        return f"已导出 PDF：{path}"
    except ImportError:
        pass

    return (
        f"PDF 导出失败：缺少 PDF 引擎（pip install weasyprint 或 pdfkit）——"
        f"可先用 save_doc 保存 .md，文件将落在 {out_dir}"
    )
