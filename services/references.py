"""
Parse and resolve inline references in a chat question:

  @path/or/file.py   -> constrain retrieval to that file (pin it into context)
  #symbol            -> a function/class name to look up and pin

Resolution happens against the indexed code_chunks for the repo, so a partial
@name still maps to a real file path, and a #symbol maps to the file(s) that
define it.
"""

import re
import logging
from dataclasses import dataclass, field
from db.connection import get_pool

logger = logging.getLogger(__name__)

# @foo, @services/foo.py, @a/b-c.py   |   #symbol_name
_FILE_REF_RE = re.compile(r"(?<!\w)@([\w./\-]+)")
_SYMBOL_REF_RE = re.compile(r"(?<!\w)#([A-Za-z_][\w]*)")


@dataclass
class ResolvedReferences:
    file_paths: list[str] = field(default_factory=list)   # indexed paths to pin/constrain
    symbols: list[str] = field(default_factory=list)      # symbol names requested
    unresolved: list[str] = field(default_factory=list)   # raw tokens that didn't resolve

    @property
    def is_empty(self) -> bool:
        return not self.file_paths and not self.symbols and not self.unresolved

    def as_note(self) -> str:
        """Human/LLM-facing summary of what the references resolved to."""
        parts = []
        if self.file_paths:
            parts.append("Scope restricted to these files: " + ", ".join(self.file_paths))
        if self.symbols:
            parts.append("Symbols of interest: " + ", ".join(self.symbols))
        if self.unresolved:
            parts.append(
                "Could not resolve these references (not in the index): "
                + ", ".join(self.unresolved)
            )
        return "\n".join(parts)


def parse_references(question: str) -> tuple[list[str], list[str]]:
    """Return (raw @file tokens, raw #symbol tokens) found in the question."""
    files = _FILE_REF_RE.findall(question)
    symbols = _SYMBOL_REF_RE.findall(question)
    return files, symbols


async def _distinct_file_paths(repo: str) -> list[str]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT DISTINCT file_path FROM code_chunks WHERE repo = $1", repo
    )
    return [r["file_path"] for r in rows]


async def _paths_defining_symbol(repo: str, symbol: str) -> list[str]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT DISTINCT file_path FROM code_chunks
        WHERE repo = $1 AND lower(function_name) = lower($2)
        """,
        repo, symbol,
    )
    return [r["file_path"] for r in rows]


def _match_path(token: str, all_paths: list[str]) -> str | None:
    """Resolve a possibly-partial @token to a single indexed path (best match)."""
    if token in all_paths:
        return token
    # endswith is the common case: @foo.py -> services/foo.py
    suffix_matches = [p for p in all_paths if p.endswith(token) or p.endswith("/" + token)]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    if suffix_matches:
        # Ambiguous — prefer the shortest path (closest to root)
        return min(suffix_matches, key=len)
    # Last resort: substring match
    substr = [p for p in all_paths if token in p]
    if substr:
        return min(substr, key=len)
    return None


async def resolve_references(repo: str, question: str) -> ResolvedReferences:
    """Parse @file / #symbol tokens and resolve them against the indexed repo."""
    file_tokens, symbol_tokens = parse_references(question)
    resolved = ResolvedReferences()

    if not file_tokens and not symbol_tokens:
        return resolved

    all_paths = await _distinct_file_paths(repo)
    pinned: set[str] = set()

    for token in file_tokens:
        match = _match_path(token, all_paths)
        if match:
            pinned.add(match)
        else:
            resolved.unresolved.append("@" + token)

    for symbol in symbol_tokens:
        resolved.symbols.append(symbol)
        defining = await _paths_defining_symbol(repo, symbol)
        if defining:
            pinned.update(defining)
        else:
            # Not a known definition — keep the symbol as a search hint, note it
            resolved.unresolved.append("#" + symbol)

    resolved.file_paths = sorted(pinned)
    logger.info(
        "resolve_references: files=%s symbols=%s unresolved=%s",
        resolved.file_paths, resolved.symbols, resolved.unresolved,
    )
    return resolved
