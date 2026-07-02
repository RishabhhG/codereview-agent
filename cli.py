import asyncio
import logging
import re
import warnings

import typer
from rich.box import ROUNDED
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

# Quieten third-party noise so the chat output stays readable.
warnings.filterwarnings("ignore", category=FutureWarning)
logging.getLogger("dotenv.main").setLevel(logging.ERROR)

from dotenv import load_dotenv

load_dotenv()

app = typer.Typer(help="CodeReview AI Agent — CLI")
console = Console()


@app.command()
def ingest(
    repo_path: str = typer.Option(..., "--repo-path", help="Local path to the repo"),
    repo_name: str = typer.Option(..., "--repo-name", help="e.g. owner/repo"),
):
    """Ingest a local repo into the vector store for RAG."""
    from ingest_repo import _run
    asyncio.run(_run(repo_path, repo_name))


@app.command()
def review(
    file_path: str = typer.Argument(..., help="Repo-relative file path to review"),
    repo: str = typer.Option(..., "--repo", help="e.g. RishabhhG/codereview-agent"),
):
    """Review a specific file using codebase context."""
    asyncio.run(_review_file(repo, file_path))


async def _review_file(repo: str, file_path: str):
    from db.connection import get_pool, close_pool
    from agent.state import ReviewState
    from agent.context_agent import run_context_agent
    from agent.review_agent import run_review_agent
    from agent.tools import fetch_file

    await get_pool()

    # Build a minimal state for file review
    # Fetch the file content first to build a fake diff entry
    class _FakeState:
        def __init__(self):
            self.repo = repo
            self.owner = repo.split("/")[0]
            self.repo_name = repo.split("/")[1]
            self.installation_id = None  # not needed for local review
            self.retrieved_chunks = []
            self.related_files = []
            self.tool_calls_made = 0
            self.tool_call_log = []
            self.needs_more_context = True
            self.confidence_score = None
            self.final_response = None
            self.pr_review = None

    state = _FakeState()

    # Read file from local disk
    try:
        with open(file_path, "r") as f:
            content = f.read()
    except FileNotFoundError:
        console.print(f"[red]File not found: {file_path}[/red]")
        raise typer.Exit(1)

    # Build a fake diff entry so context_agent can process it
    state.pr_diff = [{
        "filename": file_path,
        "language": _detect_language(file_path),
        "status": "modified",
        "additions": len(content.splitlines()),
        "deletions": 0,
        "patch": "\n".join(f"+{line}" for line in content.splitlines()),
        "truncated": False,
    }]

    console.print(f"[cyan]Fetching context for {file_path}...[/cyan]")
    state = await run_context_agent(state)

    console.print(f"[cyan]Running review ({len(state.retrieved_chunks)} context chunks)...[/cyan]")
    review = await run_review_agent(state)

    console.print(Markdown(state.final_response))
    await close_pool()


def _detect_language(file_path: str) -> str:
    ext_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".go": "go", ".rs": "rust", ".java": "java"
    }
    ext = "." + file_path.rsplit(".", 1)[-1] if "." in file_path else ""
    return ext_map.get(ext, "plaintext")


@app.command()
def chat(
    question: str = typer.Argument(None, help="Question about the codebase. Omit to start an interactive session."),
    repo: str = typer.Option(..., "--repo", help="e.g. RishabhhG/codereview-agent"),
    session: str = typer.Option("default", "--session", "-s", help="Conversation memory id — reuse to keep context across runs."),
):
    """
    Ask questions about a codebase. Streams the answer with source citations,
    inline code snippets, and (for how-does-X questions) an execution-flow diagram.

    Supports @path/to/file.py and #symbol references to scope the search.
    With no QUESTION argument, opens an interactive multi-turn session.
    """
    asyncio.run(_chat_entry(repo, question, session))


async def _chat_entry(repo: str, question: str | None, session: str):
    from db.connection import get_pool, close_pool, init_db
    from db.chat_store import get_or_create_session

    await get_pool()
    await init_db()
    session_id = await get_or_create_session(session, repo)

    try:
        if question:
            await _chat_turn(repo, question, session_id)
        else:
            await _chat_repl(repo, session_id, session)
    finally:
        await close_pool()


async def _chat_repl(repo: str, session_id: int, session_key: str):
    console.print(
        f"[bold cyan]Chat[/bold cyan] — repo [white]{repo}[/white], session [white]{session_key}[/white]. "
        "Type your question, or [dim]exit[/dim] to quit.\n"
        "[dim]Tip: reference files with @path/to/file.py and symbols with #function_name.[/dim]\n"
    )
    while True:
        try:
            question = (await asyncio.to_thread(console.input, "[bold green]you ›[/bold green] ")).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            return
        if not question:
            continue
        if question.lower() in {"exit", "quit", ":q"}:
            console.print("[dim]bye[/dim]")
            return
        await _chat_turn(repo, question, session_id)
        console.print()


async def _chat_turn(repo: str, question: str, session_id: int):
    from agent.chat_agent import run_chat
    from db.chat_store import save_message

    final_text = ""
    citations: list[dict] = []
    acc = ""

    # Show a spinner while the agent works instead of streaming a growing
    # Markdown preview. Rendering the answer only once — after it is complete —
    # is what keeps long answers from being re-drawn (duplicated) on the
    # terminal when they overflow the screen height.
    with console.status("[bold cyan]Thinking…[/bold cyan]", spinner="dots") as status:
        async for ev in run_chat(repo, question, session_id=session_id):
            if ev["type"] == "tool":
                console.print(f"[dim]🔍 {', '.join(ev['calls'])} …[/dim]")
                status.update("[bold cyan]Reading the code…[/bold cyan]")
            elif ev["type"] == "text":
                acc += ev["text"]
                status.update(
                    f"[bold cyan]Writing answer…[/bold cyan] [dim]{len(acc)} chars[/dim]"
                )
            elif ev["type"] == "final":
                final_text = ev["text"]
                citations = ev["citations"]

    _render_answer(final_text or acc)
    if citations:
        _render_sources(citations)

    # Persist the turn so follow-up questions reuse context
    await save_message(session_id, "user", question)
    await save_message(session_id, "assistant", final_text, citations)


# Mermaid code fences aren't rendered by Rich's Markdown, so we pull them out
# and draw them as their own panel.
_MERMAID_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)

# A Mermaid node definition: `id` followed by a bracket-pair label. Each shape is
# matched against its OWN closing bracket so a label containing "(" — e.g.
# `X[Find files (blast radius)]` — isn't truncated at the first ")".
# Longer/compound bracket shapes are listed before single ones so they win.
_NODE_DEF_RE = re.compile(
    r"([A-Za-z0-9_]+)\s*(?:"
    r"\[\[(.*?)\]\]"      # [[subroutine]]
    r"|\[\((.*?)\)\]"     # [(cylinder)]
    r"|\(\[(.*?)\]\)"     # ([stadium])
    r"|\{\{(.*?)\}\}"     # {{hexagon}}
    r"|\[(.*?)\]"         # [rectangle]
    r"|\((.*?)\)"         # (rounded)
    r"|\{(.*?)\}"         # {rhombus}
    r")"
)


def _clean_markdown(text: str) -> str:
    """Normalize the model's Markdown so Rich renders it correctly.

    The model frequently over-indents list content and code fences. CommonMark
    treats any line indented 4+ spaces as a *literal indented code block*, which
    is why `**bold**` and ``` fences leak through as raw characters. We flatten
    prose to the left margin and dedent fenced code blocks to column 0.
    """
    out: list[str] = []
    in_fence = False
    fence_indent = 0
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            if not in_fence:
                if not lang:
                    # A bare ``` with no block open is an orphan (the model
                    # often drops the opening fence and leaves a stray close).
                    # Skip it so it can't spuriously open a block that swallows
                    # the rest of the answer as literal code.
                    continue
                in_fence = True
                fence_indent = len(line) - len(stripped)
                out.append("```" + lang)  # opening fence to column 0
            else:
                in_fence = False
                out.append("```")  # closing fence to column 0
            continue
        if in_fence:
            # Drop the fence's own indent but keep indentation *within* the code.
            if line[:fence_indent].strip() == "":
                out.append(line[fence_indent:])
            else:
                out.append(stripped)
        else:
            out.append(stripped)
    return "\n".join(out)


def _render_answer(text: str) -> None:
    """Render the assistant's answer as a titled panel, with any Mermaid
    flow diagram broken out into its own nicely-formatted panel."""
    if not text.strip():
        console.print("[yellow]No answer was produced.[/yellow]")
        return

    text = _clean_markdown(text)

    segments: list[tuple[str, str]] = []
    last = 0
    for m in _MERMAID_RE.finditer(text):
        segments.append(("md", text[last:m.start()]))
        segments.append(("flow", m.group(1).strip()))
        last = m.end()
    segments.append(("md", text[last:]))

    renderables = []
    for kind, seg in segments:
        if kind == "md" and seg.strip():
            renderables.append(Markdown(seg))
        elif kind == "flow":
            renderables.append(_render_flow(seg))

    console.print()
    console.print(
        Panel(
            Group(*renderables),
            title="[bold]💬 Answer[/bold]",
            border_style="cyan",
            box=ROUNDED,
            padding=(1, 2),
        )
    )


def _render_flow(code: str):
    """Render a Mermaid `flowchart` as a readable arrow-chain plus the raw
    Mermaid source (so it can be copied into a real diagram renderer)."""
    from rich.rule import Rule
    from rich.syntax import Syntax

    code = code.strip()

    # id -> label, parsed with bracket-aware matching so "(...)" inside a [...]
    # label survives intact.
    labels: dict[str, str] = {}
    for m in _NODE_DEF_RE.finditer(code):
        node_id = m.group(1)
        label = next((g for g in m.groups()[1:] if g is not None), "")
        labels[node_id] = label.strip().strip('"').strip() or node_id

    def label_of(token: str) -> str:
        token = token.strip()
        idm = re.match(r"([A-Za-z0-9_]+)", token)
        if not idm:
            return token
        return labels.get(idm.group(1), idm.group(1))

    steps: list[list[str]] = []
    for raw in code.splitlines():
        line = raw.strip().rstrip(";")
        if "-->" not in line:
            continue
        line = re.sub(r"\|[^|]*\|", "", line)  # drop edge labels like -->|text|
        sides = [s.strip() for s in line.split("-->")]
        # `A & B` (with surrounding spaces) is Mermaid's "both A and B" join.
        steps.append(
            [" and ".join(label_of(n) for n in re.split(r"\s+&\s+", side) if n.strip())
             for side in sides]
        )

    source_block = Syntax(code, "text", theme="ansi_dark", word_wrap=True,
                          background_color="default")

    if not steps:
        # Not a flowchart we can flatten (e.g. sequenceDiagram) — show the source.
        return Panel(
            source_block,
            title="[bold]🔀 Mermaid diagram[/bold]",
            border_style="magenta",
            box=ROUNDED,
            padding=(1, 2),
        )

    flow = Text()
    for i, sides in enumerate(steps):
        if i:
            flow.append("\n")
        flow.append("● ", style="bold magenta")
        flow.append_text(Text(" → ", style="bold magenta").join(Text(s) for s in sides))

    body = Group(
        flow,
        Rule(style="magenta"),
        Text("Mermaid source — paste into mermaid.live or a Markdown viewer to "
             "render as a diagram:", style="dim italic"),
        source_block,
    )

    return Panel(
        body,
        title="[bold]🔀 Execution flow[/bold]",
        subtitle="[dim]text view + Mermaid source[/dim]",
        border_style="magenta",
        box=ROUNDED,
        padding=(1, 2),
    )


def _render_sources(citations: list[dict]) -> None:
    body = Text()
    for i, c in enumerate(citations):
        if i:
            body.append("\n")
        loc = c["file_path"]
        if c.get("start_line"):
            loc += f":{c['start_line']}-{c['end_line']}"
        body.append(loc, style="cyan")
        if c.get("function_name"):
            body.append(f"  {c['function_name']}", style="dim")

    console.print(
        Panel(
            body,
            title="[bold]📚 Sources[/bold]",
            border_style="green",
            box=ROUNDED,
            padding=(1, 2),
        )
    )


if __name__ == "__main__":
    app()