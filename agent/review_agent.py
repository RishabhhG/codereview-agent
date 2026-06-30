import json
import logging
import re
from agent.state import ReviewState
from agent.models import PRReview, ReviewComment, Severity
from services.llm_client import chat

logger = logging.getLogger(__name__)

MAX_CHUNK_TOKENS = 300   # Fix #1 — truncate large chunks in prompt
MAX_CHUNKS_IN_PROMPT = 10

REVIEW_SYSTEM_PROMPT = """You are a senior software engineer doing a thorough code review.

You will receive:
1. A code diff (what changed)
2. Related files from the codebase (for context on existing patterns)

Your job:
- Compare the new code against existing patterns in the related files
- Flag deviations in auth, error handling, naming, HTTP client usage, etc.
- Be specific — name the file and function where the pattern already exists correctly
- Every comment MUST include evidence from the retrieved context
- Do NOT invent issues that aren't grounded in the provided context

Review priorities (in order):
1. Security issues
2. Correctness / functional bugs
3. Breaking changes to existing callers
4. Error handling gaps
5. Performance problems
6. Style / naming (only if significant)

You MUST respond with a valid JSON object. No markdown, no explanation, just the raw JSON.

Schema:
{
  "summary": "One sentence describing what this PR does",
  "comments": [
    {
      "severity": "critical" | "warning" | "suggestion",
      "file": "path/to/file.py",
      "function": "function_name or null",
      "issue": "Description of the issue",
      "suggestion": "How to fix it",
      "confidence": 0.0-1.0,
      "existing_pattern_file": "file where correct pattern exists, or null",
      "existing_pattern_function": "function where correct pattern exists, or null",
      "evidence": "Quote or describe the specific code from context that proves this issue"
    }
  ],
  "verdict_reason": "One line reason for the verdict",
  "confidence_score": 0.0-1.0
}

Note: verdict is derived by the system from your comments — do not include it.

confidence_score meaning:
- 0.9+  : Full context, very certain about all findings
- 0.7-0.9: Good context, minor uncertainty
- 0.5-0.7: Partial context, some findings may be incomplete
- below 0.5: Insufficient context, review likely missing important issues
"""


def _derive_verdict(comments: list[ReviewComment]) -> str:
    """Fix #9 — derive verdict deterministically from structured comments."""
    critical = [c for c in comments if c.severity == Severity.CRITICAL]
    warnings = [c for c in comments if c.severity == Severity.WARNING]

    if critical:
        return "REQUEST_CHANGES"
    if len(warnings) >= 3:
        return "REQUEST_CHANGES"
    return "APPROVE"


def _extract_json(raw: str) -> dict:
    """Fix #7 — robust JSON extraction using brace matching."""
    # Strip markdown fences first
    cleaned = re.sub(r"```json|```", "", raw).strip()

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Brace matching — find first complete {...} block
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")

    depth = 0
    for i, ch in enumerate(cleaned[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(cleaned[start:i+1])
                except json.JSONDecodeError as e:
                    raise ValueError(f"Brace-matched JSON failed to parse: {e}")

    raise ValueError("Unbalanced braces in JSON response")


def _truncate_chunk(chunk_text: str, max_tokens: int = MAX_CHUNK_TOKENS) -> str:
    """Fix #1 — rough token truncation (1 token ≈ 4 chars)."""
    max_chars = max_tokens * 4
    if len(chunk_text) <= max_chars:
        return chunk_text
    return chunk_text[:max_chars] + "\n... [truncated]"


def _build_review_prompt(state: ReviewState) -> str:
    parts = []

    # Section 1 — diff
    parts.append("## Code Changes (PR Diff)\n")
    for f in state.pr_diff:
        lines = f.get("patch", "").splitlines()
        filtered = [l for l in lines if l.startswith(("+", "-", "@@"))]
        parts.append(
            f"### {f['filename']} ({f['language']}) | {f['status']} "
            f"| +{f['additions']} -{f['deletions']}\n"
            f"```diff\n{chr(10).join(filtered)}\n```"
        )

    # Section 2 — retrieved context (capped)
    chunks_to_use = state.retrieved_chunks[:MAX_CHUNKS_IN_PROMPT]  # Fix #1
    if chunks_to_use:
        parts.append(f"\n## Related Codebase Context ({len(chunks_to_use)} files)\n")
        for chunk in chunks_to_use:
            truncated = _truncate_chunk(chunk["chunk_text"])
            parts.append(
                f"### {chunk['file_path']} (similarity: {chunk['similarity']:.2f})\n"
                f"```\n{truncated}\n```"
            )

    # Section 3 — related files not in chunks
    chunk_files = {c["file_path"] for c in chunks_to_use}
    extra_related = [f for f in state.related_files if f not in chunk_files]
    if extra_related:
        parts.append(
            f"\n## Additional Files in Import Graph\n"
            + "\n".join(f"- {f}" for f in extra_related)
        )

    return "\n\n".join(parts)


def _parse_review_response(raw: str, state: ReviewState) -> PRReview:
    try:
        data = _extract_json(raw)  # Fix #7

        comments = []
        for c in data.get("comments", []):
            try:
                comments.append(ReviewComment(
                    severity=Severity(c.get("severity", "suggestion")),
                    file=c.get("file", "unknown"),
                    function=c.get("function"),
                    issue=c.get("issue", ""),
                    suggestion=c.get("suggestion"),
                    confidence=float(c.get("confidence", 0.5)),
                    existing_pattern_file=c.get("existing_pattern_file"),
                    existing_pattern_function=c.get("existing_pattern_function"),
                    evidence=c.get("evidence"),
                ))
            except Exception as e:
                logger.warning("Skipping malformed comment: %s | %s", e, c)

        verdict = _derive_verdict(comments)  # Fix #9

        return PRReview(
            summary=data.get("summary", ""),
            comments=comments,
            verdict=verdict,
            verdict_reason=data.get("verdict_reason", ""),
            confidence_score=float(data.get("confidence_score", 0.5)),
            tool_calls_used=state.tool_calls_made,
            context_files_used=[c["file_path"] for c in state.retrieved_chunks],
        )

    except Exception as e:
        logger.error("Failed to parse review response: %s\nRaw: %s", e, raw[:500])
        return PRReview(
            summary="Review parsing failed",
            comments=[],
            verdict="COMMENT",
            verdict_reason="Could not parse structured review output",
            confidence_score=0.0,
            tool_calls_used=state.tool_calls_made,
            context_files_used=[],
        )


async def run_review_agent(state: ReviewState) -> PRReview:
    logger.info(
        "Review agent starting — %d chunks, %d related files",
        len(state.retrieved_chunks), len(state.related_files)
    )

    user_prompt = _build_review_prompt(state)
    raw = await chat(REVIEW_SYSTEM_PROMPT, user_prompt)

    review = _parse_review_response(raw, state)

    state.confidence_score = review.confidence_score
    state.final_response = _format_review_as_markdown(review)

    logger.info(
        "Review agent done — confidence: %.2f | verdict: %s | %d comments",
        review.confidence_score, review.verdict, len(review.comments)
    )
    return review


def _format_review_as_markdown(review: PRReview) -> str:
    lines = [f"## Summary\n{review.summary}\n", "## Issues Found\n"]

    critical = [c for c in review.comments if c.severity == Severity.CRITICAL]
    warnings = [c for c in review.comments if c.severity == Severity.WARNING]
    suggestions = [c for c in review.comments if c.severity == Severity.SUGGESTION]

    lines.append("### 🔴 Critical")
    if critical:
        for c in critical:
            fn = f", function: `{c.function}`" if c.function else ""
            lines.append(f"- **{c.issue}** (file: `{c.file}`{fn})")
            if c.suggestion:
                lines.append(f"  - **Fix**: {c.suggestion}")
            if c.evidence:
                lines.append(f"  - **Evidence**: {c.evidence}")
            if c.existing_pattern_file:
                ref = f"`{c.existing_pattern_file}`"
                if c.existing_pattern_function:
                    ref += f" → `{c.existing_pattern_function}`"
                lines.append(f"  - **Correct pattern in**: {ref}")
    else:
        lines.append("None")

    lines.append("\n### 🟡 Warning")
    if warnings:
        for c in warnings:
            fn = f", function: `{c.function}`" if c.function else ""
            lines.append(f"- **{c.issue}** (file: `{c.file}`{fn})")
            if c.suggestion:
                lines.append(f"  - **Fix**: {c.suggestion}")
            if c.evidence:
                lines.append(f"  - **Evidence**: {c.evidence}")
            if c.existing_pattern_file:
                ref = f"`{c.existing_pattern_file}`"
                if c.existing_pattern_function:
                    ref += f" → `{c.existing_pattern_function}`"
                lines.append(f"  - **Correct pattern in**: {ref}")
    else:
        lines.append("None")

    lines.append("\n### 🟢 Suggestion")
    if suggestions:
        for c in suggestions:
            fn = f", function: `{c.function}`" if c.function else ""
            lines.append(f"- **{c.issue}** (file: `{c.file}`{fn})")
            if c.suggestion:
                lines.append(f"  - **Fix**: {c.suggestion}")
    else:
        lines.append("None")

    lines.append(f"\n## Verdict\n{review.verdict} — {review.verdict_reason}")

    if review.context_files_used:
        lines.append(
            f"\n---\n*Review used context from: "
            f"{', '.join(f'`{f}`' for f in review.context_files_used)}*"
        )

    return "\n".join(lines)