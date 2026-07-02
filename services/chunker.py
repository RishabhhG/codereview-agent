import re
import hashlib
import logging
import tiktoken

logger = logging.getLogger(__name__)

_enc = tiktoken.get_encoding("cl100k_base")

MAX_TOKENS = 512
OVERLAP_TOKENS = 50

LANGUAGE_PATTERNS = {
    ".py": [
        r"^(async\s+)?def\s+\w+",
        r"^class\s+\w+",
    ],
    ".js": [
        r"^(export\s+)?(default\s+)?(async\s+)?function\s+\w+",
        r"^(export\s+)?(default\s+)?class\s+\w+",
        r"^(export\s+)?const\s+\w+\s*=\s*(async\s+)?\(",      # arrow function: const foo = () =>
        r"^(export\s+)?const\s+\w+\s*=\s*(async\s+)?\w*\s*=>", # const foo = async x =>
        r"^let\s+\w+\s*=\s*function",
    ],
    ".ts": [],   # filled below
    ".tsx": [],
    ".jsx": [],
    ".go": [r"^func\s+\w+"],
    ".rs": [r"^(pub\s+)?(async\s+)?fn\s+\w+"],
    ".java": [
        r"^(public|private|protected)\s+(static\s+)?[\w<>\[\]]+\s+\w+\s*\(",
    ],
    ".cs": [
        r"^(public|private|protected)\s+(static\s+)?(async\s+)?[\w<>\[\]]+\s+\w+\s*\(",
    ],
}
LANGUAGE_PATTERNS[".ts"] = LANGUAGE_PATTERNS[".js"]
LANGUAGE_PATTERNS[".tsx"] = LANGUAGE_PATTERNS[".js"]
LANGUAGE_PATTERNS[".jsx"] = LANGUAGE_PATTERNS[".js"]

SUPPORTED_EXTENSIONS = set(LANGUAGE_PATTERNS.keys())

# Decorator/annotation lines that should stick to the boundary below them
DECORATOR_PATTERN = re.compile(r"^\s*(@\w+.*|\[\w+.*\])\s*$")

# Captures the identifier following a definition keyword — used to label a chunk
# with the function/class it primarily contains (for source citations).
_NAME_RE = re.compile(
    r"^\s*(?:export\s+|default\s+|public\s+|private\s+|protected\s+|static\s+|async\s+|pub\s+)*"
    r"(?:def|class|function|func|fn|interface|struct|enum|trait|impl|type|const|let|var)\s+"
    r"([A-Za-z_$][\w$]*)"
)

# Keywords that look like `name(` but aren't definitions — used by the fallback name scan
_NON_NAMES = {"if", "for", "while", "switch", "catch", "return", "with", "elif", "except"}


def _get_boundary_regex(file_path: str) -> re.Pattern:
    ext = "." + file_path.rsplit(".", 1)[-1] if "." in file_path else ""
    patterns = LANGUAGE_PATTERNS.get(ext, [])
    if not patterns:
        # Fallback — generic patterns across common languages
        patterns = [p for plist in LANGUAGE_PATTERNS.values() for p in plist]
    return re.compile("|".join(patterns), re.MULTILINE)


def count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def checksum(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _attach_decorators(content: str, boundaries: list[int]) -> list[int]:
    """Fix #4 — pull decorator lines back into the boundary they belong to"""
    lines_with_pos = []
    pos = 0
    for line in content.split("\n"):
        lines_with_pos.append((pos, line))
        pos += len(line) + 1

    adjusted = []
    for b in boundaries:
        # walk backward through preceding lines, absorb decorator lines
        idx = next((i for i, (p, _) in enumerate(lines_with_pos) if p >= b), len(lines_with_pos) - 1)
        new_start = b
        i = idx - 1
        while i >= 0:
            line_pos, line_text = lines_with_pos[i]
            if DECORATOR_PATTERN.match(line_text):
                new_start = line_pos
                i -= 1
            else:
                break
        adjusted.append(new_start)
    return adjusted


def _line_range(content: str, start_char: int, end_char: int) -> tuple[int, int]:
    """1-based inclusive line range covered by content[start_char:end_char]."""
    start_line = content.count("\n", 0, start_char) + 1
    last_char = max(start_char, end_char - 1)
    end_line = content.count("\n", 0, last_char) + 1
    return start_line, end_line


def _extract_function_name(text: str) -> str | None:
    """Best-effort name of the primary definition in a chunk — for citations."""
    lines = text.splitlines()
    for line in lines:
        m = _NAME_RE.match(line)
        if m:
            return m.group(1)
    # Fallback: first `name(` on an early line (catches Java/C# methods with no keyword)
    for line in lines[:5]:
        m = re.search(r"([A-Za-z_$][\w$]*)\s*\(", line)
        if m and m.group(1) not in _NON_NAMES:
            return m.group(1)
    return None


def _build_chunk(file_path: str, content: str, start_char: int, end_char: int) -> dict | None:
    """Build a chunk dict with citation metadata, or None if the slice is blank."""
    text = content[start_char:end_char].strip()
    if not text:
        return None
    start_line, end_line = _line_range(content, start_char, end_char)
    return {
        "file_path": file_path,
        "chunk_text": text,
        "checksum": checksum(text),
        "start_line": start_line,
        "end_line": end_line,
        "function_name": _extract_function_name(content[start_char:end_char]),
    }


def _token_ranges(text: str) -> list[tuple[int, int]]:
    """Token-window char ranges within `text` (exact, via decode-prefix lengths)."""
    tokens = _enc.encode(text)
    ranges = []
    start = 0
    while start < len(tokens):
        end = min(start + MAX_TOKENS, len(tokens))
        cs = len(_enc.decode(tokens[:start]))
        ce = len(_enc.decode(tokens[:end]))
        ranges.append((cs, ce))
        start += MAX_TOKENS - OVERLAP_TOKENS
    return ranges


def split_into_chunks(file_path: str, content: str) -> list[dict]:
    """
    Split file content at function/class boundaries.
    Falls back to token-based splitting if no boundaries found.

    Each returned chunk carries start_line/end_line/function_name (1-based, in
    original-file coordinates) so answers can cite exact source locations.
    """
    if "." + file_path.rsplit(".", 1)[-1] not in SUPPORTED_EXTENSIONS:
        logger.info("Unsupported extension, using token-based fallback: %s", file_path)
        return _token_chunks(file_path, content)

    boundary_re = _get_boundary_regex(file_path)
    raw_boundaries = [m.start() for m in boundary_re.finditer(content)]

    if not raw_boundaries:
        return _token_chunks(file_path, content)

    # Fix #4 — attach decorators to their function/class
    boundaries = sorted(set(_attach_decorators(content, raw_boundaries)))

    # Segment char ranges: [header) + one per boundary. The buffer is always a
    # contiguous slice of `content`, so we can track it as (start, end) offsets.
    seg_ranges = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(content)
        seg_ranges.append((start, end))

    chunks: list[dict] = []

    def emit(s: int, e: int):
        c = _build_chunk(file_path, content, s, e)
        if c:
            chunks.append(c)

    # Fix #2 — preserve header (imports, module docstring) before first boundary
    header_end = boundaries[0]
    if content[:header_end].strip():
        buffer_start, buffer_end = 0, header_end
        buffer_tokens = count_tokens(content[:header_end])
    else:
        buffer_start = buffer_end = None
        buffer_tokens = 0

    for s, e in seg_ranges:
        seg_tokens = count_tokens(content[s:e])  # Fix #19 — count once per segment

        if buffer_start is not None and buffer_tokens + seg_tokens <= MAX_TOKENS:
            buffer_end = e
            buffer_tokens += seg_tokens
        elif buffer_start is None and seg_tokens <= MAX_TOKENS:
            buffer_start, buffer_end, buffer_tokens = s, e, seg_tokens
        else:
            if buffer_start is not None:
                emit(buffer_start, buffer_end)
                buffer_start = buffer_end = None
                buffer_tokens = 0

            if seg_tokens > MAX_TOKENS:
                # Single oversized definition — token-split it, keeping absolute offsets
                for cs, ce in _token_ranges(content[s:e]):
                    emit(s + cs, s + ce)
            else:
                buffer_start, buffer_end, buffer_tokens = s, e, seg_tokens

    if buffer_start is not None:
        emit(buffer_start, buffer_end)

    return chunks


def _token_chunks(file_path: str, content: str) -> list[dict]:
    """Token-window fallback for files without detectable boundaries."""
    chunks = []
    for cs, ce in _token_ranges(content):
        c = _build_chunk(file_path, content, cs, ce)
        if c:
            chunks.append(c)
    return chunks