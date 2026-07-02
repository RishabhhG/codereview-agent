import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Multi-hop budget: how many tool calls the chat agent may make before it must
# answer with what it has gathered.
MAX_CHAT_TOOL_CALLS = 6


@dataclass
class ChatState:
    """
    Lightweight state for the chat agent. Deliberately shaped so the existing
    agent.tool_runner + agent.tools work unchanged (they only need repo/owner/
    repo_name/installation_id and add_tool_call/is_done).
    """
    repo: str
    owner: str
    repo_name: str
    installation_id: int | None = None

    # --- Tool call bookkeeping ---
    tool_calls_made: int = 0
    tool_call_log: list[dict] = field(default_factory=list)

    # --- Citations gathered from tool results (dedup by file_path + start_line) ---
    retrieved: list[dict] = field(default_factory=list)

    final_response: str | None = None

    def add_tool_call(self, tool_name: str, tool_args: dict, result: object, error: str | None = None) -> None:
        self.tool_calls_made += 1
        self.tool_call_log.append({
            "tool": tool_name,
            "args": tool_args,
            "result": result,
            "error": error,
            "call_number": self.tool_calls_made,
        })
        if error:
            logger.warning("Chat tool #%d failed: %s(%s) -> %s",
                           self.tool_calls_made, tool_name, tool_args, error)
        else:
            logger.info("Chat tool #%d: %s(%s)", self.tool_calls_made, tool_name, tool_args)

    def is_done(self) -> bool:
        """True once the multi-hop tool budget is exhausted."""
        if self.tool_calls_made >= MAX_CHAT_TOOL_CALLS:
            logger.info("Chat stopping: reached MAX_CHAT_TOOL_CALLS (%d)", MAX_CHAT_TOOL_CALLS)
            return True
        return False

    def collect_citations(self, tool_results: list[dict]) -> None:
        """Pull citation metadata out of search-style tool results."""
        seen = {(c["file_path"], c.get("start_line")) for c in self.retrieved}
        for tr in tool_results:
            result = tr.get("result")
            if not isinstance(result, list):
                continue
            for item in result:
                if not isinstance(item, dict) or "file_path" not in item:
                    continue
                key = (item["file_path"], item.get("start_line"))
                if key in seen:
                    continue
                seen.add(key)
                self.retrieved.append({
                    "file_path": item["file_path"],
                    "function_name": item.get("function_name"),
                    "start_line": item.get("start_line"),
                    "end_line": item.get("end_line"),
                })
