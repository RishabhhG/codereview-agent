"""
Postgres-backed conversation memory for the chat feature.

A *session* groups a multi-turn conversation about one repo (identified by a
caller-supplied `session_key`, e.g. "default" or a uuid). Each turn is stored as
a *message* so follow-up questions can reuse earlier context.
"""

import json
import logging
from db.connection import get_pool

logger = logging.getLogger(__name__)

# How many prior messages to load back into context by default.
DEFAULT_HISTORY_LIMIT = 12


async def get_or_create_session(session_key: str, repo: str) -> int:
    """Return the session id for `session_key`, creating the row if needed."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, repo FROM chat_sessions WHERE session_key = $1",
            session_key,
        )
        if row:
            # Touch updated_at so recent sessions sort first
            await conn.execute(
                "UPDATE chat_sessions SET updated_at = NOW() WHERE id = $1",
                row["id"],
            )
            if row["repo"] != repo:
                logger.warning(
                    "Session '%s' was created for repo '%s' but used with '%s'",
                    session_key, row["repo"], repo,
                )
            return row["id"]

        session_id = await conn.fetchval(
            """
            INSERT INTO chat_sessions (session_key, repo)
            VALUES ($1, $2)
            RETURNING id
            """,
            session_key, repo,
        )
        logger.info("Created chat session '%s' (id=%d) for %s", session_key, session_id, repo)
        return session_id


async def load_recent_messages(session_id: int, limit: int = DEFAULT_HISTORY_LIMIT) -> list[dict]:
    """
    Load the most recent `limit` messages for a session, returned oldest-first
    so they can be replayed as conversation history.
    """
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT role, content, citations
        FROM chat_messages
        WHERE session_id = $1
        ORDER BY id DESC
        LIMIT $2
        """,
        session_id, limit,
    )
    messages = [
        {
            "role": r["role"],
            "content": r["content"],
            "citations": json.loads(r["citations"]) if r["citations"] else [],
        }
        for r in rows
    ]
    messages.reverse()  # oldest-first for replay
    return messages


async def save_message(
    session_id: int,
    role: str,
    content: str,
    citations: list[dict] | None = None,
) -> None:
    """Append one message to a session's transcript."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO chat_messages (session_id, role, content, citations)
            VALUES ($1, $2, $3, $4)
            """,
            session_id, role, content,
            json.dumps(citations) if citations else None,
        )
        await conn.execute(
            "UPDATE chat_sessions SET updated_at = NOW() WHERE id = $1",
            session_id,
        )
