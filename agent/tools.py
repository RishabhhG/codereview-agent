import re
import logging
from db.connection import get_pool
from services.retriever import search_chunks
from services.github_client import get_file_content

logger = logging.getLogger(__name__)

# Matches: import x.y.z / import x.y.z as z / from x.y.z import a, b
_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w\.]+)\s+import|import\s+([\w\.]+))",
    re.MULTILINE
)

DOC_EXTENSIONS = (".md", ".rst", ".txt")
DOC_PATH_HINTS = ("readme", "docs/", "documentation/")


def _path_to_module(file_path: str) -> str:
    """'services/embedder.py' -> 'services.embedder'"""
    no_ext = re.sub(r"\.py$", "", file_path)
    return no_ext.replace("/", ".")


def _module_to_basename(module: str) -> str:
    """'services.embedder' -> 'embedder' (last segment, for loose matching)"""
    return module.split(".")[-1]


async def _get_distinct_file_paths(repo: str) -> list[str]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT DISTINCT file_path FROM code_chunks WHERE repo = $1",
        repo
    )
    return [r["file_path"] for r in rows]


async def _get_file_text(repo: str, file_path: str) -> str:
    """Reassemble a file's stored chunks into one text blob, in id order (insertion order)."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT chunk_text FROM code_chunks WHERE repo = $1 AND file_path = $2 ORDER BY id",
        repo, file_path
    )
    return "\n".join(r["chunk_text"] for r in rows)


# ---------------------------------------------------------------------------
# Tool implementations
# Each tool takes `state` as the first arg for repo/installation context;
# tool_runner.py injects state automatically — it's NOT part of the LLM-facing schema.
# ---------------------------------------------------------------------------

def _citation_fields(r: dict) -> dict:
    """Common projection: chunk text + citation metadata (path/function/lines)."""
    return {
        "file_path": r["file_path"],
        "function_name": r.get("function_name"),
        "start_line": r.get("start_line"),
        "end_line": r.get("end_line"),
        "chunk_text": r["chunk_text"],
        "similarity": round(r["similarity"], 3),
    }


async def search_codebase(state, query: str, top_k: int = 5, path_filter: list[str] | None = None) -> list[dict]:
    """Semantic search over the indexed codebase for code relevant to `query`."""
    results = await search_chunks(state.repo, query, top_k=top_k, path_filter=path_filter)
    logger.info("search_codebase('%s', path_filter=%s) -> %d results: %s",
                query, path_filter, len(results), [r["file_path"] for r in results])
    return [_citation_fields(r) for r in results]


async def fetch_file(state, path: str) -> dict:
    """Fetch the full current contents of a file directly from GitHub (PR head not assumed — uses default ref)."""
    try:
        content = await get_file_content(state.installation_id, state.owner, state.repo_name, path)
        logger.info("fetch_file('%s') -> %d chars", path, len(content))
        return {"path": path, "content": content, "error": None}
    except Exception as e:
        logger.warning("fetch_file failed for %s: %s", path, e)
        return {"path": path, "content": None, "error": str(e)}


async def find_related_files(state, filename: str) -> list[str]:
    """
    Find files that import `filename`, or that `filename` imports.
    Regex-based on Python import statements — best-effort, not AST-accurate.
    """
    all_paths = await _get_distinct_file_paths(state.repo)
    if filename not in all_paths:
        # Loose match in case the LLM passed a partial path
        candidates = [p for p in all_paths if p.endswith(filename)]
        if not candidates:
            logger.info("find_related_files: %s not found in indexed files", filename)
            return []
        filename = candidates[0]
    

    target_module = _path_to_module(filename)
    target_basename = _module_to_basename(target_module)

    related = set()

    # 1. Files that THIS file imports (look at its own content)
    own_text = await _get_file_text(state.repo, filename)
    for match in _IMPORT_RE.finditer(own_text):
        imported = match.group(1) or match.group(2)
        imported_basename = _module_to_basename(imported)
        for p in all_paths:
            if p == filename:
                continue
            if _module_to_basename(_path_to_module(p)) == imported_basename:
                related.add(p)

    # 2. Files that import THIS file (scan other files' content for our basename)
    for p in all_paths:
        if p == filename:
            continue
        text = await _get_file_text(state.repo, p)
        for match in _IMPORT_RE.finditer(text):
            imported = match.group(1) or match.group(2)
            if _module_to_basename(imported) == target_basename:
                related.add(p)
                break
    
    logger.info("find_related_files('%s') -> %d related: %s", filename, len(related), sorted(related))
    return sorted(related)


async def search_docs(state, query: str, top_k: int = 5) -> list[dict]:
    """
    Semantic search restricted to documentation-like files (README, docs/, *.md, *.rst, *.txt).
    Uses the same embedding index as search_codebase — assumes docs were ingested alongside code.
    """
    results = await search_chunks(state.repo, query, top_k=top_k * 3, use_mmr=False)

    def is_doc(file_path: str) -> bool:
        lower = file_path.lower()
        return lower.endswith(DOC_EXTENSIONS) or any(hint in lower for hint in DOC_PATH_HINTS)

    filtered = [r for r in results if is_doc(r["file_path"])][:top_k]

    logger.info("search_docs('%s') -> %d doc results: %s", query, len(filtered), [r["file_path"] for r in filtered])

    if not filtered:
        logger.info("search_docs: no doc-like chunks matched '%s' — are docs ingested?", query)

    return [_citation_fields(r) for r in filtered]