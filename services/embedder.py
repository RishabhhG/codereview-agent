import logging
from db.connection import get_pool
from services.embeddings import embed_text

logger = logging.getLogger(__name__)


async def get_existing_checksums(repo: str, file_path: str) -> set[str]:
    """Fetch checksums already stored for this file — used to skip unchanged chunks"""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT checksum FROM code_chunks WHERE repo = $1 AND file_path = $2",
        repo, file_path
    )
    return {row["checksum"] for row in rows}


async def delete_file_chunks(repo: str, file_path: str):
    """Remove all chunks for a file — used before re-inserting updated chunks"""
    pool = await get_pool()
    await pool.execute(
        "DELETE FROM code_chunks WHERE repo = $1 AND file_path = $2",
        repo, file_path
    )


async def store_chunk(repo: str, chunk: dict, embedding: list[float]):
    pool = await get_pool()
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
    await pool.execute(
        """
        INSERT INTO code_chunks (repo, file_path, chunk_text, embedding, checksum)
        VALUES ($1, $2, $3, $4, $5)
        """,
        repo, chunk["file_path"], chunk["chunk_text"], embedding_str, chunk["checksum"]
    )


async def embed_and_store_file(repo: str, file_path: str, chunks: list[dict]) -> dict:
    """
    Embed and store chunks for one file.
    Skips re-embedding if checksums match what's already in DB (file unchanged).
    Returns stats: {embedded: N, skipped: N}
    """
    if not chunks:
        return {"embedded": 0, "skipped": 0}

    existing_checksums = await get_existing_checksums(repo, file_path)
    new_checksums = {c["checksum"] for c in chunks}

    # File completely unchanged — every chunk checksum already exists
    if new_checksums == existing_checksums:
        logger.info("Unchanged, skipping: %s", file_path)
        return {"embedded": 0, "skipped": len(chunks)}

    # File changed — delete old chunks, re-embed everything for this file
    # (Simpler and safer than partial diffing chunk-by-chunk)
    await delete_file_chunks(repo, file_path)

    embedded_count = 0
    for chunk in chunks:
        try:
            embedding = await embed_text(chunk["chunk_text"])
            await store_chunk(repo, chunk, embedding)
            embedded_count += 1
        except Exception as e:
            logger.error("Failed to embed chunk in %s: %s", file_path, e)

    logger.info("Embedded %d/%d chunks for %s", embedded_count, len(chunks), file_path)
    return {"embedded": embedded_count, "skipped": 0}