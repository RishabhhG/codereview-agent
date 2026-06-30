import logging
from agent.state import ReviewState
from agent.models import PRReview
from agent.tools import fetch_file
from agent.review_agent import run_review_agent

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.6


async def run_retry_agent(state: ReviewState, initial_review: PRReview) -> PRReview:
    """
    Triggered when confidence_score < CONFIDENCE_THRESHOLD.
    Fetches full file contents for related files (not just chunks),
    then re-runs the review agent with richer context.
    """
    logger.info(
        "Retry agent triggered — initial confidence: %.2f (threshold: %.2f)",
        initial_review.confidence_score, CONFIDENCE_THRESHOLD
    )

    # Fetch full content of related files — chunks may have been incomplete
    newly_fetched = []
    for file_path in state.related_files[:3]:  # cap at 3 to avoid token blowup
        logger.info("Retry agent fetching full file: %s", file_path)
        result = await fetch_file(state, file_path)
        if result.get("content"):
            # Inject as a pseudo-chunk with max similarity so it stays at top
            newly_fetched.append({
                "file_path": file_path,
                "chunk_text": result["content"][:3000],  # truncate large files
                "similarity": 1.0,
                "source": "retry_fetch"
            })
            logger.info("Fetched %d chars from %s", len(result["content"]), file_path)
        else:
            logger.warning("Could not fetch %s: %s", file_path, result.get("error"))

    if not newly_fetched:
        logger.warning("Retry agent found nothing new to add — returning initial review")
        return initial_review

    # Prepend newly fetched full files to retrieved_chunks
    state.retrieved_chunks = newly_fetched + state.retrieved_chunks

    logger.info("Retry agent re-running review with %d total chunks", len(state.retrieved_chunks))
    retry_review = await run_review_agent(state)

    logger.info(
        "Retry agent done — new confidence: %.2f | verdict: %s",
        retry_review.confidence_score, retry_review.verdict
    )
    return retry_review