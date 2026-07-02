import asyncio
import logging
import time
from agent.state import ReviewState
from agent.tools import search_codebase, find_related_files
from db.connection import get_pool

logger = logging.getLogger(__name__)

TOP_K_PER_FILE = 3
MAX_TOTAL_CHUNKS = 15  # Fix #6 — retrieval budget


async def _get_chunks_for_file(repo: str, file_path: str) -> list[dict]:
    """Fetch stored chunks for a specific file path directly from pgvector."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT file_path, chunk_text, start_line, end_line, function_name, 1.0 AS similarity
        FROM code_chunks
        WHERE repo = $1 AND file_path = $2
        ORDER BY id
        LIMIT 5
        """,
        repo, file_path
    )
    return [
        {
            "file_path": row["file_path"],
            "chunk_text": row["chunk_text"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "function_name": row["function_name"],
            "similarity": float(row["similarity"]),
            "source": "related_file_fetch"
        }
        for row in rows
    ]


async def _process_single_file(state: ReviewState, f: dict) -> tuple[list[dict], list[str]]:
    """
    Process one changed file — run find_related_files and search_codebase concurrently.
    Returns (chunks, related_files) for this file.
    Fix #1 — this runs concurrently across all changed files via asyncio.gather.
    """
    filename = f["filename"]

    # Build query from actual added lines
    patch_lines = f.get("patch", "")
    added_lines = " ".join(
        line[1:] for line in patch_lines.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    query = f"{filename} {added_lines}"[:500]

    # Fix #1 — run both concurrently per file
    related_task = asyncio.create_task(_safe_find_related(state, filename))
    search_task = asyncio.create_task(_safe_search(state, filename, query))

    related, chunks = await asyncio.gather(related_task, search_task)
    return chunks, related


async def _safe_find_related(state: ReviewState, filename: str) -> list[str]:
    try:
        result = await find_related_files(state, filename)
        logger.info("Related files for %s: %s", filename, result)
        return result
    except Exception as e:
        logger.warning("find_related_files failed for %s: %s", filename, e)
        return []


async def _safe_search(state: ReviewState, filename: str, query: str) -> list[dict]:
    try:
        t0 = time.monotonic()
        chunks = await search_codebase(state, query, top_k=TOP_K_PER_FILE)
        elapsed = (time.monotonic() - t0) * 1000
        logger.info(
            "search_codebase for %s -> %d chunks: %s (%.0fms)",
            filename,
            len(chunks),
            [c["file_path"] for c in chunks],
            elapsed
        )
        return chunks
    except Exception as e:
        logger.warning("search_codebase failed for %s: %s", filename, e)
        return []


async def run_context_agent(state: ReviewState) -> ReviewState:
    """
    For each changed file in the PR diff — concurrently:
    1. Find files in import graph (blast radius)
    2. Search codebase for similar patterns

    Then fetch chunks for related files so the review LLM can actually read them.
    Applies a retrieval budget to avoid prompt overflow.
    """
    logger.info("Context agent starting — %d changed files", len(state.pr_diff))

    # Fix #1 — process all changed files concurrently
    tasks = [_process_single_file(state, f) for f in state.pr_diff]
    results = await asyncio.gather(*tasks)

    all_chunks = []
    all_related = set()
    diff_filenames = {f["filename"] for f in state.pr_diff}

    for chunks, related in results:
        all_chunks.extend(chunks)
        all_related.update(related)

    # Remove changed files from related (no point reviewing what's already in the diff)
    all_related -= diff_filenames

    # Fix #5 — fetch actual chunks for related files so LLM can read them
    related_chunks = []
    for file_path in sorted(all_related):
        chunks = await _get_chunks_for_file(state.repo, file_path)
        if chunks:
            related_chunks.extend(chunks)
            logger.info("Fetched %d chunks for related file: %s", len(chunks), file_path)
        else:
            logger.info("No chunks found for related file: %s (not indexed?)", file_path)

    # Combine semantic search chunks + related file chunks
    all_chunks.extend(related_chunks)

    # Deduplicate by file_path — keep highest similarity per file
    seen = {}
    for chunk in all_chunks:
        fp = chunk["file_path"]
        if fp not in seen or chunk["similarity"] > seen[fp]["similarity"]:
            seen[fp] = chunk

    deduped = list(seen.values())

    # Fix #6 — apply retrieval budget, sort by similarity descending
    deduped.sort(key=lambda c: c["similarity"], reverse=True)
    deduped = deduped[:MAX_TOTAL_CHUNKS]

    state.retrieved_chunks = deduped
    state.related_files = sorted(all_related)

    logger.info(
        "Context agent done — %d unique chunks (budget: %d), %d related files",
        len(state.retrieved_chunks), MAX_TOTAL_CHUNKS, len(state.related_files)
    )
    return state