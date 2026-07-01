import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Hard cap on tool calls per review — prevents runaway agent loops
MAX_TOOL_CALLS = 8


@dataclass
class ReviewState:
    # --- Identity / inputs ---
    repo: str                      # "owner/repo"
    owner: str
    repo_name: str
    installation_id: int
    pr_diff: list[dict] = field(default_factory=list)
    pr_review: object = None   # stores PRReview after review_agent runs

    # --- Context gathered during the review ---
    retrieved_chunks: list[dict] = field(default_factory=list)
    related_files: list[str] = field(default_factory=list)

    # --- Tool call bookkeeping ---
    tool_calls_made: int = 0
    tool_call_log: list[dict] = field(default_factory=list)

    # --- Agent control flow ---
    needs_more_context: bool = True

    # --- Output ---
    review_comments: list[dict] = field(default_factory=list)
    confidence_score: float | None = None
    final_response: str | None = None

    def add_tool_call(self, tool_name: str, tool_args: dict, result: object, error: str | None = None) -> None:
        """Record a tool call and increment the call counter."""
        self.tool_calls_made += 1
        entry = {
            "tool": tool_name,
            "args": tool_args,
            "result": result,
            "error": error,
            "call_number": self.tool_calls_made,
        }
        self.tool_call_log.append(entry)

        if error:
            logger.warning("Tool call #%d failed: %s(%s) -> %s",
                            self.tool_calls_made, tool_name, tool_args, error)
        else:
            logger.info("Tool call #%d: %s(%s)", self.tool_calls_made, tool_name, tool_args)

    def is_done(self) -> bool:
        """
        True when the agent should stop gathering context and produce a final review.
        Stops if the model says it has enough context, or if we've hit the call cap.
        """
        if self.tool_calls_made >= MAX_TOOL_CALLS:
            logger.info("Stopping: reached MAX_TOOL_CALLS (%d)", MAX_TOOL_CALLS)
            return True

        if not self.needs_more_context:
            return True

        return self.final_response is not None