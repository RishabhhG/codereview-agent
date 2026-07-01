import asyncio
import typer
from rich.console import Console
from rich.markdown import Markdown
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
    question: str = typer.Argument(..., help="Question about the codebase"),
    repo: str = typer.Option(..., "--repo", help="e.g. RishabhhG/codereview-agent"),
):
    """Ask a question about a codebase."""
    asyncio.run(_chat(repo, question))


async def _chat(repo: str, question: str):
    from db.connection import get_pool, close_pool
    from services.retriever import search_chunks
    from services.llm_client import chat as llm_chat

    await get_pool()

    console.print(f"[cyan]Searching codebase for context...[/cyan]")
    chunks = await search_chunks(repo, question, top_k=5)

    if not chunks:
        console.print("[yellow]No relevant chunks found — is the repo ingested?[/yellow]")
        raise typer.Exit(1)

    context = "\n\n".join(
        f"### {c['file_path']}\n```\n{c['chunk_text']}\n```"
        for c in chunks
    )

    system_prompt = """You are a helpful assistant that answers questions about a codebase.
Use the provided code context to answer accurately. Reference specific files and functions.
If the context doesn't contain enough information, say so clearly."""

    user_prompt = f"## Codebase Context\n{context}\n\n## Question\n{question}"

    console.print(f"[cyan]Thinking...[/cyan]\n")
    answer = await llm_chat(system_prompt, user_prompt)
    console.print(Markdown(answer))

    await close_pool()


if __name__ == "__main__":
    app()