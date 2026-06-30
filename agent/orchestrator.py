import logging
import os
from agent.state import ReviewState
from agent.models import PRReview
from agent.context_agent import run_context_agent
from agent.review_agent import run_review_agent
from agent.retry_agent import run_retry_agent, CONFIDENCE_THRESHOLD
from agent.tool_schemas import TOOL_SCHEMAS
from agent.tool_runner import execute_tool_calls
from services.llm_client import (
    start_tool_chat,
    send_tool_results,
    extract_tool_calls,
    extract_text,
)

logger = logging.getLogger(__name__)

# --- Mode selection ---
# AGENT_MODE=react     → old single-agent ReAct tool-calling loop
# AGENT_MODE=pipeline  → new multi-agent pipeline (default)
AGENT_MODE = os.getenv("AGENT_MODE", "pipeline").lower()

REACT_SYSTEM_PROMPT = """You are a senior software engineer conducting a code review.

You have tools available to investigate the codebase before writing your review:
- search_codebase: semantic search over the indexed repo
- fetch_file: get the full current contents of a specific file
- find_related_files: find what imports, or is imported by, a given file
- search_docs: semantic search over documentation files

Investigation strategy:
- For any diff touching auth, HTTP calls, error handling, or external APIs — always
  search for the existing canonical implementation before judging the new code.
- If search_codebase returns a relevant file but only a partial chunk, use fetch_file
  to get the full implementation before concluding.
- If a first search doesn't surface what you expected, try a more specific query or
  fetch the most likely file directly.
- A simple, self-contained diff may need zero tool calls.
- Stop investigating once you have concrete evidence to support each issue you'll raise.

When you are ready to give your final review, respond with the review text directly
(not a tool call), formatted exactly like this:

## Summary
One sentence describing what this PR does.

## Issues Found

### 🔴 Critical
- <issue> (file: X, function: Y)

### 🟡 Warning
- <issue>

### 🟢 Suggestion
- <issue>

## Verdict
APPROVE / REQUEST_CHANGES — one line reason.

If no issues found in a category, write "None".
"""


async def _run_react_loop(state: ReviewState, user_prompt: str) -> str:
    """Single-agent ReAct loop — LLM drives tool calls autonomously."""
    chat_session, response = await start_tool_chat(
        REACT_SYSTEM_PROMPT, user_prompt, TOOL_SCHEMAS
    )

    while True:
        tool_calls = extract_tool_calls(response)

        if not tool_calls:
            text = extract_text(response)
            if not text:
                logger.warning("Model returned neither tool calls nor text — forcing stop")
                text = "## Summary\nReview could not be generated.\n\n## Verdict\nCOMMENT — model returned empty response."
            state.final_response = text
            state.needs_more_context = False
            return text

        if state.is_done():
            logger.info("is_done() true — forcing final answer")
            break

        logger.info(
            "Model requested %d tool call(s): %s",
            len(tool_calls), [c["name"] for c in tool_calls]
        )

        tool_results = await execute_tool_calls(state, tool_calls)

        if state.is_done():
            logger.info("Tool call budget hit mid-loop — forcing final answer")
            break

        response = await send_tool_results(chat_session, tool_results)

    # Budget exhausted — nudge model to wrap up
    try:
        wrapup_response = await send_tool_results(
            chat_session,
            [{"name": "_system", "result": "Tool call budget reached. Provide your final review now using only the context gathered so far."}],
        )
        text = extract_text(wrapup_response) or "## Summary\nReview incomplete — budget exhausted.\n\n## Verdict\nCOMMENT"
    except Exception as e:
        logger.error("Forced wrap-up failed: %s", e)
        text = "## Summary\nReview incomplete — budget exhausted.\n\n## Verdict\nCOMMENT"

    state.final_response = text
    state.needs_more_context = False
    return text


async def _run_pipeline(state: ReviewState) -> str:
    """Multi-agent pipeline — context_agent → review_agent → retry_agent."""
    logger.info("=== STAGE 1: Context Agent ===")
    state = await run_context_agent(state)

    logger.info("=== STAGE 2: Review Agent ===")
    review: PRReview = await run_review_agent(state)

    if review.confidence_score < CONFIDENCE_THRESHOLD:
        logger.info(
            "=== STAGE 3: Retry Agent (confidence %.2f < %.2f) ===",
            review.confidence_score, CONFIDENCE_THRESHOLD
        )
        review = await run_retry_agent(state, review)
    else:
        logger.info(
            "Skipping retry (confidence %.2f >= %.2f)",
            review.confidence_score, CONFIDENCE_THRESHOLD
        )

    return state.final_response or "## Summary\nReview could not be generated.\n\n## Verdict\nCOMMENT"


async def run_agent_review(state: ReviewState, user_prompt: str = "") -> str:
    """
    Entry point for all agent-based reviews.
    Mode controlled by AGENT_MODE env var:
      - "react"    → single-agent ReAct tool-calling loop
      - "pipeline" → multi-agent pipeline (default)
    """
    logger.info("Agent mode: %s", AGENT_MODE)

    if AGENT_MODE == "react":
        return await _run_react_loop(state, user_prompt)
    else:
        return await _run_pipeline(state)