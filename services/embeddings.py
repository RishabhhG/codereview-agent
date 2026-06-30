import google.generativeai as genai
import asyncio
import logging
import os
import hashlib

logger = logging.getLogger(__name__)

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set")

EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "models/embedding-001")

genai.configure(api_key=API_KEY)

# Fix #16 — cap concurrent embedding calls to avoid free-tier rate limits
_semaphore = asyncio.Semaphore(5)

# Fix #14 — in-memory cache by checksum, avoids re-embedding identical chunks
_embedding_cache: dict[str, list[float]] = {}

MAX_RETRIES = 3
TIMEOUT_SECONDS = 30


async def _embed_with_retry(content: str, task_type: str) -> list[float]:
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            async with _semaphore:  # Fix #16
                response = await asyncio.wait_for(  # Fix #2
                    asyncio.to_thread(
                        genai.embed_content,
                        model=EMBEDDING_MODEL,
                        content=content,
                        task_type=task_type
                    ),
                    timeout=TIMEOUT_SECONDS
                )
            return response["embedding"]

        except Exception as e:
            last_error = e
            logger.warning(
                "Embedding attempt %d/%d failed: %s",
                attempt + 1, MAX_RETRIES, e
            )
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)  # Fix #1 — exponential backoff

    logger.error("Embedding failed after %d attempts", MAX_RETRIES)
    raise last_error


async def embed(text: str, task_type: str, use_cache: bool = True) -> list[float]:
    """
    Get embedding vector for text.
    task_type: "retrieval_document" (indexing) or "retrieval_query" (searching)
    """
    if not text.strip():  # Fix #4
        raise ValueError("Cannot embed empty text")

    # Fix #14 — cache lookup (only useful for document embeddings, queries are unique)
    cache_key = None
    if use_cache and task_type == "retrieval_document":
        cache_key = hashlib.md5(f"{text}:{task_type}".encode()).hexdigest()
        if cache_key in _embedding_cache:
            logger.debug("Cache hit for embedding (%d chars)", len(text))
            return _embedding_cache[cache_key]

    logger.debug("Embedding %d chars, task_type=%s", len(text), task_type)
    embedding = await _embed_with_retry(text, task_type)

    if cache_key:
        _embedding_cache[cache_key] = embedding

    return embedding


async def embed_text(text: str) -> list[float]:
    """Embed text for storage/indexing"""
    return await embed(text, "retrieval_document")


async def embed_query(text: str) -> list[float]:
    """Embed text for search queries — not cached, queries are typically unique"""
    return await embed(text, "retrieval_query", use_cache=False)