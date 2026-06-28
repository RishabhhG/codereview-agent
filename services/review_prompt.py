import re
import logging

logger = logging.getLogger(__name__)

# Fix 10 — don't ask for exact line numbers, Gemini will hallucinate them
SYSTEM_PROMPT = """You are a senior software engineer conducting a thorough code review.
Your job is to review the provided code diff and give actionable, specific feedback.

Follow these rules:
- Reference the file name and function/method name when possible
- Do NOT invent exact line numbers — the diff may not include them
- Prioritize issues by severity: Critical > Warning > Suggestion
- Focus on: bugs, security issues, performance problems, code clarity
- If the code is clean, say so briefly — don't invent issues
- Format your response exactly like this:

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

# Fix 6 — skip generated/vendor files
SKIP_PATTERNS = (
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    ".min.js", ".min.css", "dist/", "build/", "__generated__",
    "migrations/", ".pb.go", "_pb2.py"
)


def build_user_prompt(diff: list[dict]) -> str:
    prompt_parts = ["Review the following code changes:\n"]

    for f in diff:
        filename = f["filename"]

        # Fix 6 — skip generated/vendor files
        if any(pattern in filename for pattern in SKIP_PATTERNS):
            logger.info("Skipping generated/vendor file: %s", filename)
            continue

        header = f"### File: {filename} ({f['language']})"
        meta = f"Status: {f['status']} | +{f['additions']} additions, -{f['deletions']} deletions"

        if f.get("previous_filename"):
            meta += f" | Renamed from: {f['previous_filename']}"

        if f.get("truncated"):
            meta += " | ⚠ Large file — patch truncated at 800 lines"

        lines = f["patch"].splitlines()
        filtered = [l for l in lines if l.startswith(("+", "-", "@@"))]
        patch_block = f"```diff\n{chr(10).join(filtered)}\n```"

        prompt_parts.append(f"{header}\n{meta}\n{patch_block}")

    return "\n\n".join(prompt_parts)


def parse_verdict(llm_response: str) -> str:
    # Fix 5 — regex instead of fragile string split
    match = re.search(r"\b(APPROVE|REQUEST_CHANGES)\b", llm_response, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return "COMMENT"