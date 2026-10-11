"""RAG 离线建库脚本：扫描知识库目录，经 chunker 解析切分 → embedder 向量化 → 仓储入库。

分层（沿用 shopkeeper）：chunker(解析切分) → embedder + sparse_embedder(向量化)
→ doc_qdrant_repository(写库)。本脚本只负责**编排**，不直接触碰 Qdrant client /
PointStruct——写库语义收敛在仓储层。

- 幂等：DocChunk 的 chunk_id = uuid5(source#index) 稳定，重复运行 upsert 覆盖同一点，点不增长。
- 混合召回：每个 chunk 同时生成 dense（embedder）与 sparse（sparse_embedder，BM25）两路向量入库。
- 异常隔离：单文件失败仅计数，不中断整批。
- 批量：每攒满 BATCH 个点做一次「embed → upsert」，控内存与请求量。

用法（项目根目录下）：
    python -m app.rag.build_kb                  # 增量建库（幂等，重复运行覆盖同一点）
    python -m app.rag.build_kb --rebuild        # 全量重建（先删 collection 再建）
    python -m app.rag.build_kb --dir path/to/kb # 指定知识库目录
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.conf.app_config import app_config
from app.rag.chunker import SUPPORTED, process_file
from app.rag.embedder import embedder
from app.rag.sparse_embedder import sparse_embedder
from app.repositories.qdrant.doc_qdrant import COLLECTION, doc_qdrant_repository

BATCH = 100
KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "doc" / "knowledge_base"


async def main(knowledge_dir: Path = KNOWLEDGE_DIR, rebuild: bool = False) -> None:
    """扫描 knowledge_dir 下全部支持文件，解析切分 → 向量化 → 分批入库。

    参数:
        knowledge_dir: 知识库根目录
        rebuild: True 时先删 collection 再重建（全量重建，幂等重跑用 False）
    """
    await doc_qdrant_repository.ensure_collection(
        rebuild=rebuild,
        vector_size=app_config.qdrant.embedding_size,
    )

    files = failed = total_chunks = total_tokens = 0
    batch_chunks = []          # list[DocChunk]
    batch_texts = []           # list[str]，与 batch_chunks 一一对应

    async def flush() -> None:
        nonlocal batch_chunks, batch_texts
        if not batch_chunks:
            return
        vectors = await embedder.aembed_documents(batch_texts)
        sparse_vectors = await sparse_embedder.aembed_documents(batch_texts)
        await doc_qdrant_repository.upsert(batch_chunks, vectors, sparse_vectors)
        batch_chunks, batch_texts = [], []

    for path in sorted(knowledge_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED:
            continue
        source = path.relative_to(knowledge_dir).as_posix()
        chunks = process_file(path, source)
        if not chunks:
            failed += 1
            print(f"[跳过/失败] {source}")
            continue
        files += 1
        total_chunks += len(chunks)
        total_tokens += sum(c.tokens for c in chunks)
        batch_chunks.extend(chunks)
        batch_texts.extend(c.text for c in chunks)
        if len(batch_chunks) >= BATCH:
            await flush()

    await flush()

    count = await doc_qdrant_repository.count()
    print(f"完成：文件 {files}(失败 {failed}) / 块 {total_chunks} / token {total_tokens}")
    print(f"collection 现存点数: {count}（collection: {COLLECTION}）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="M3 离线建库")
    parser.add_argument("--dir", type=Path, default=KNOWLEDGE_DIR, help="知识库根目录")
    parser.add_argument("--rebuild", action="store_true", help="先删 collection 再全量重建")
    args = parser.parse_args()
    asyncio.run(main(knowledge_dir=args.dir, rebuild=args.rebuild))