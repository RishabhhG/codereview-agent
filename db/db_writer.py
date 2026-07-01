import json
import logging
from db.connection import get_pool
from agent.models import PRReview
from agent.state import ReviewState

logger = logging.getLogger(__name__)


async def save_review(
    review: PRReview,
    state: ReviewState,
    pr_url: str | None = None,
    pr_number: int | None = None,
) -> int:
    """
    Save a PRReview + its comments + agent traces in a single transaction.
    Returns the review_id for linking traces or comments later.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():

            # 1. Insert top-level review
            review_id = await conn.fetchval(
                """
                INSERT INTO reviews
                    (pr_url, repo, pr_number, verdict, risk_score,
                     overall_summary, confidence, raw_json)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                pr_url,
                state.repo,
                pr_number,
                review.verdict,
                review.risk_score,
                review.overall_summary,
                review.confidence_score,
                json.dumps(review.model_dump()),
            )

            # 2. Insert individual comments
            for comment in review.comments:
                await conn.execute(
                    """
                    INSERT INTO review_comments
                        (review_id, severity, category, file, function,
                         line_number, issue, suggestion, confidence,
                         evidence, existing_pattern_file, existing_pattern_function)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    """,
                    review_id,
                    comment.severity.value,
                    comment.category.value,
                    comment.file,
                    comment.function,
                    comment.line_number,
                    comment.issue,
                    comment.suggestion,
                    comment.confidence,
                    comment.evidence,
                    comment.existing_pattern_file,
                    comment.existing_pattern_function,
                )

            # 3. Insert agent traces from tool_call_log
            for call in state.tool_call_log:
                await conn.execute(
                    """
                    INSERT INTO agent_traces
                        (review_id, stage, tool_name, tool_query, tool_result)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    review_id,
                    "react_loop",
                    call.get("tool"),
                    json.dumps(call.get("args", {})),
                    json.dumps(call.get("result", {}))[:2000],  # truncate large results
                )

            logger.info(
                "Saved review #%d — %d comments, %d traces",
                review_id, len(review.comments), len(state.tool_call_log)
            )
            return review_id