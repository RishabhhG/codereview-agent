import os
import asyncpg
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_pool = None
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=os.getenv("DATABASE_URL"),
            min_size=2,
            max_size=10
        )
        logger.info("Database pool created")
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def init_db() -> None:
    """
    Apply db/schema.sql. All statements are additive (CREATE ... IF NOT EXISTS /
    ALTER ... ADD COLUMN IF NOT EXISTS), so this is safe to run on every startup.
    Skips the code_chunks ALTERs silently if that table doesn't exist yet.
    """
    pool = await get_pool()
    sql = _SCHEMA_PATH.read_text()
    async with pool.acquire() as conn:
        for statement in _split_statements(sql):
            try:
                await conn.execute(statement)
            except asyncpg.exceptions.UndefinedTableError:
                # code_chunks is created out-of-band; its ALTERs run once it exists
                logger.debug("Skipping statement, table not present yet: %s", statement[:60])
    logger.info("Schema applied")

def _split_statements(sql: str) -> list[str]:
    statements = []

    for stmt in sql.split(";"):
        stmt = stmt.strip()

        if not stmt:
            continue

        # Skip chunks that are only comments
        lines = [l for l in stmt.splitlines() if not l.strip().startswith("--")]
        cleaned = "\n".join(lines).strip()

        if cleaned:
            statements.append(cleaned)

    return statements