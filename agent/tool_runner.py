import logging

from agent.state import ReviewState
from agent.tools import search_codebase, fetch_file, find_related_files, search_docs

logger = logging.getLogger(__name__)

# Maps tool name (as the LLM sees it, from TOOL_SCHEMAS) -> implementation.
# Every implementation takes `state` as its first arg.
TOOL_REGISTRY = {
    "search_codebase": search_codebase,
    "fetch_file": fetch_file,
    "find_related_files": find_related_files,
    "search_docs": search_docs,
}


async def execute_tool_call(state: ReviewState, tool_name: str, tool_args: dict) -> dict:
    """
    Execute a single tool call requested by the LLM, recording it on `state`.
    Returns a dict suitable for feeding back to the model as a tool result —
    never raises, errors are captured and returned instead.
    """
    fn = TOOL_REGISTRY.get(tool_name)

    if fn is None:
        error = f"Unknown tool '{tool_name}'"
        logger.error(error)
        state.add_tool_call(tool_name, tool_args, result=None, error=error)
        return {"error": error}

    logger.info(">>> EXECUTING TOOL: %s | args: %s", tool_name, tool_args)

    try:
        result = await fn(state, **tool_args)
        state.add_tool_call(tool_name, tool_args, result=result, error=None)
        logger.info("<<< TOOL DONE: %s | returned %s item(s)",tool_name, len(result) if isinstance(result, list) else "1")
        return {"result": result}

    except TypeError as e:
        # Most likely the LLM passed an arg name/shape that doesn't match the tool signature
        error = f"Bad arguments for '{tool_name}': {e}"
        logger.error(error)
        state.add_tool_call(tool_name, tool_args, result=None, error=error)
        return {"error": error}

    except Exception as e:
        error = f"'{tool_name}' raised: {e}"
        logger.error(error)
        state.add_tool_call(tool_name, tool_args, result=None, error=error)
        return {"error": error}


async def execute_tool_calls(state: ReviewState, tool_calls: list[dict]) -> list[dict]:
    """
    Execute a batch of tool calls (as returned by the LLM in a single turn) in order,
    stopping early if MAX_TOOL_CALLS is hit mid-batch.
    Each tool_call dict is expected to look like: {"name": str, "args": dict}.
    """
    results = []

    for call in tool_calls:
        if state.is_done():
            logger.info("Tool call budget exhausted, skipping remaining calls in batch")
            break

        name = call.get("name")
        args = call.get("args", {}) or {}
        result = await execute_tool_call(state, name, args)
        results.append({"name": name, **result})

    return results