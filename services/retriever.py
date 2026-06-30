import logging
from db.connection import get_pool
from services.embeddings import embed_query
from services.mmr import apply_mmr

logger = logging.getLogger(__name__)


async def search_chunks(repo: str, query: str, top_k: int = 5, use_mmr: bool = True) -> list[dict]:
    query_embedding = await embed_query(query)
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    # Fetch more candidates than needed if using MMR, so it has room to diversify
    fetch_k = top_k * 3 if use_mmr else top_k

    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, file_path, chunk_text, 1 - (embedding <=> $1) AS similarity
        FROM code_chunks
        WHERE repo = $2
        ORDER BY embedding <=> $1
        LIMIT $3
        """,
        embedding_str, repo, fetch_k
    )

    candidates = [
        {"id": r["id"], "file_path": r["file_path"], "chunk_text": r["chunk_text"], "similarity": r["similarity"]}
        for r in rows
    ]

    if use_mmr and len(candidates) > top_k:
        return await apply_mmr(query_embedding, candidates, final_k=top_k)

    return candidates[:top_k]
