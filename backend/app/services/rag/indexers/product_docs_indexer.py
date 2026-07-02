"""Product-docs indexer (Brochure Phase 1) — embeds a parsed brochure into
the rag_product_docs reference corpus.

What gets a vector:
  * every section chunk — embedded as chunk_text, i.e. WITH its heading path,
    so a retrieval hit is self-contained ("context inheritance")
  * every table — embedded ONLY as its deterministic summary
    (block_type='table_summary'); the exact rows live in product_tables and
    are looked up numerically, never embedded

Product identity (uin, product_name) rides every vector so retrieval can
filter to a product, product family, or block type.
"""
from __future__ import annotations

import logging
import uuid
from typing import List

from app.services.brochure_parser import ParsedBrochure
from app.services.rag.errors import RAGDegraded, RAGIndexingFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.services.rag.ports import VectorDoc

logger = logging.getLogger(__name__)


async def index_product_document(
    product_document_id: uuid.UUID | str,
    brochure: ParsedBrochure,
) -> int:
    """Embed + upsert all blocks of one parsed brochure. Returns vector count."""
    entries = []  # (text, page_number, section_path, block_type)
    for i, sec in enumerate(brochure.sections):
        entries.append(
            (sec.chunk_text, sec.page_start, " › ".join(sec.heading_path), sec.block_type)
        )
    for t in brochure.tables:
        entries.append((t.summary, t.page_number, t.heading, "table_summary"))

    if not entries:
        logger.warning(
            f"product_docs_indexer: nothing to index for {brochure.source_file}"
        )
        return 0

    embedder = get_embedder()
    store = get_vector_store()
    try:
        vectors = await embedder.embed([e[0] for e in entries])
        docs: List[VectorDoc] = []
        for i, ((text, page, section_path, block_type), vec) in enumerate(
            zip(entries, vectors)
        ):
            docs.append(
                VectorDoc(
                    id=str(uuid.uuid4()),
                    embedding=vec,
                    fields={
                        "product_document_id": str(product_document_id),
                        "uin": brochure.uin,
                        "product_name": brochure.product_name,
                        "chunk_index": i,
                        "page_number": page,
                        "section_path": section_path,
                        "block_type": block_type,
                        "text": text,
                    },
                )
            )
        await store.upsert("rag_product_docs", docs)
        logger.info(
            f"Indexed product doc '{brochure.product_name}' "
            f"({brochure.uin}): {len(docs)} vectors "
            f"({len(brochure.sections)} sections, {len(brochure.tables)} tables)"
        )
        return len(docs)
    except RAGDegraded as e:
        raise RAGIndexingFailed(str(e)) from e
