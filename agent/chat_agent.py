"""
Multi-hop, streaming chat agent for asking questions about an indexed codebase.

Ties together:
  - conversation memory (db.chat_store)          -> follow-ups reuse context
  - @file / #symbol references (services.references) -> scoped retrieval
  - the existing tool loop (agent.tools + tool_runner) -> multi-hop retrieval
  - streaming LLM turns (services.llm_client)     -> live output
and prompts the model to answer with source citations, inline code snippets,
and Mermaid execution-flow diagrams.

run_chat() is an async generator yielding events:
  {"type": "tool",  "calls": [name, ...]}    a retrieval hop is running
  {"type": "text",  "text": delta}           streamed answer text
  {"type": "final", "text": str, "citations": [...]}  end of turn
"""

import logging

from agent.chat_state import ChatState
from agent.tool_schemas import TOOL_SCHEMAS
from agent.tool_runner import execute_tool_calls
from services.references import resolve_references
from services.retriever import search_chunks
from services.llm_client import start_tool_chat_stream, send_tool_results_stream
from db.chat_store import load_recent_messages

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a codebase expert answering questions about an indexed repository.

You have retrieval tools — use them to gather evidence BEFORE answering, and make
multiple hops when needed:
- search_codebase(query, top_k, path_filter): semantic search over the code. Pass
  path_filter=[...] to restrict the search to specific files.
- fetch_file(path): full current contents of a file (when a chunk is partial).
- find_related_files(filename): what imports, or is imported by, a file.
- search_docs(query): search README/docs files.

Investigation strategy:
- Start broad, then refine: if the first search is off-target, search again with a
  better query or fetch the most relevant file before answering.
- For "how does X work" / flow / end-to-end questions, trace the call path across
  files (use find_related_files and follow-up searches) rather than guessing.
- Stop once you have concrete evidence for every claim you'll make.

Your final answer MUST:
1. CITE SOURCES inline for every claim, as `path/to/file.py:function_name (Lstart-Lend)`.
   Use the start_line/end_line from the tool result VERBATIM — cite the chunk's real
   range. Do NOT invent, narrow, or guess a sub-range you didn't get from a tool result.
2. GROUND CLAIMS IN IMPLEMENTATION CODE, not documentation. Docstrings, comments, and
   numbered summaries (e.g. a docstring line like "1. Find files in import graph") state
   INTENT — they are not evidence of behavior. Base every explanation on the statements
   that actually do the work (function calls, loops, queries, conditionals, assignments)
   and quote THOSE lines. Never cite a docstring or comment as the sole evidence for how
   something works; if the substantive code is the real answer, cite and quote the code.
3. SHOW CODE, don't just describe it: include the relevant implementation lines in fenced
   code blocks (```python ... ```), quoting the actual retrieved snippet.
4. For "how does X work" / flow questions, include a Mermaid execution-flow diagram:
   ```mermaid
   flowchart TD
     A[caller] --> B[function] --> C[...]
   ```
5. If the retrieved context is insufficient to answer confidently, say so explicitly
   rather than inventing details.

Be precise and concrete. Prefer real identifiers and paths over vague description.

FORMATTING RULES (follow exactly — output is rendered as Markdown in a terminal):
- Start every line at the left margin. Do NOT indent list items, their sub-points,
  or code — leading spaces get misrendered as literal text.
- Fence every code block with triple backticks on their own line at the left margin,
  ALWAYS with a language (```python) and ALWAYS closed with a matching ```. Never use
  indentation to denote code, and never leave a stray/unbalanced fence.
- Keep lists flat and separate blocks with a single blank line.
"""


async def _fetch_pinned_chunks(repo: str, paths: list[str]) -> list[dict]:
    """Grab a few chunks (with citation metadata) for each pinned file."""
    if not paths:
        return []
    out = []
    for path in paths:
        chunks = await search_chunks(repo, path, top_k=4, use_mmr=False, path_filter=[path])
        out.extend(chunks)
    return out


def _format_history(messages: list[dict]) -> str:
    if not messages:
        return ""
    lines = ["## Conversation so far"]
    for m in messages:
        who = "User" if m["role"] == "user" else "Assistant"
        lines.append(f"**{who}:** {m['content']}")
    return "\n\n".join(lines)


def _format_pinned(chunks: list[dict]) -> str:
    if not chunks:
        return ""
    blocks = ["## Pinned files (referenced with @ / #) — prefer these"]
    for c in chunks:
        loc = c["file_path"]
        if c.get("start_line"):
            loc += f" (L{c['start_line']}-{c['end_line']})"
        fn = f" — {c['function_name']}" if c.get("function_name") else ""
        blocks.append(f"### {loc}{fn}\n```\n{c['chunk_text']}\n```")
    return "\n\n".join(blocks)


async def run_chat(
    repo: str,
    question: str,
    session_id: int | None = None,
    history_limit: int = 12,
):
    """
    Answer `question` about `repo`, streaming the response. If `session_id` is
    given, prior turns from that session are loaded as context.
    Yields event dicts (see module docstring).
    """
    owner, _, repo_name = repo.partition("/")
    state = ChatState(repo=repo, owner=owner, repo_name=repo_name, installation_id=None)

    # 1. Conversation memory
    history = await load_recent_messages(session_id, limit=history_limit) if session_id else []

    # 2. @file / #symbol references -> scoped context
    refs = await resolve_references(repo, question)
    pinned_chunks = await _fetch_pinned_chunks(repo, refs.file_paths)
    state.collect_citations([{"result": pinned_chunks}])

    # 3. Assemble the user prompt
    sections = []
    hist = _format_history(history)
    if hist:
        sections.append(hist)
    if not refs.is_empty:
        note = refs.as_note()
        if note:
            sections.append("## References\n" + note)
        if refs.file_paths:
            sections.append(
                "When searching, pass path_filter="
                + repr(refs.file_paths)
                + " to stay within the referenced files."
            )
    pinned = _format_pinned(pinned_chunks)
    if pinned:
        sections.append(pinned)
    sections.append(f"## Question\n{question}")
    user_prompt = "\n\n".join(sections)

    # 4. Streaming multi-hop loop
    chat_session, stream = await start_tool_chat_stream(SYSTEM_PROMPT, user_prompt, TOOL_SCHEMAS)

    while True:
        turn_text = ""
        tool_calls: list[dict] = []
        async for ev in stream:
            if ev["type"] == "text":
                turn_text += ev["text"]
                yield {"type": "text", "text": ev["text"]}
            elif ev["type"] == "done":
                tool_calls = ev.get("tool_calls", [])

        if not tool_calls:
            state.final_response = turn_text
            break

        yield {"type": "tool", "calls": [c["name"] for c in tool_calls]}

        if state.is_done():
            # Budget exhausted — force a final answer from gathered context
            stream = send_tool_results_stream(chat_session, [{
                "name": "_system",
                "result": "Tool budget reached. Answer now using the context gathered so far.",
            }])
            turn_text = ""
            async for ev in stream:
                if ev["type"] == "text":
                    turn_text += ev["text"]
                    yield {"type": "text", "text": ev["text"]}
                elif ev["type"] == "done" and ev["text"]:
                    turn_text = ev["text"]
            state.final_response = turn_text
            break

        results = await execute_tool_calls(state, tool_calls)
        state.collect_citations(results)
        stream = send_tool_results_stream(chat_session, results)

    final_text = state.final_response or "I couldn't produce an answer for that."
    yield {"type": "final", "text": final_text, "citations": state.retrieved}
