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


def split_into_chunks(file_path: str, content: str) -> list[dict]:
    """
    Split file content at function/class boundaries.
    Falls back to token-based splitting if no boundaries found.
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

    # Fix #2 — preserve header (imports, module docstring) before first boundary
    header = content[:boundaries[0]].strip()

    segments = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(content)
        segments.append(content[start:end])

    chunks = []
    buffer = header + "\n\n" if header else ""
    buffer_tokens = count_tokens(buffer) if buffer else 0

    for segment in segments:
        segment_tokens = count_tokens(segment)  # Fix #19 — count once per segment

        if buffer_tokens + segment_tokens <= MAX_TOKENS:
            buffer += segment
            buffer_tokens += segment_tokens
        else:
            if buffer.strip():
                chunks.append(buffer.strip())

            if segment_tokens > MAX_TOKENS:
                chunks.extend(_token_chunks(file_path, segment, raw=True))
                buffer = ""
                buffer_tokens = 0
            else:
                buffer = segment
                buffer_tokens = segment_tokens

    if buffer.strip():
        chunks.append(buffer.strip())

    return [
        {
            "file_path": file_path,
            "chunk_text": chunk,
            "checksum": checksum(chunk)
        }
        for chunk in chunks if chunk
    ]


def _token_chunks(file_path: str, content: str, raw=False):
    tokens = _enc.encode(content)
    chunks = []
    start = 0

    while start < len(tokens):
        end = min(start + MAX_TOKENS, len(tokens))
        chunk_text = _enc.decode(tokens[start:end])
        chunks.append(chunk_text)
        start += MAX_TOKENS - OVERLAP_TOKENS

    if raw:
        return chunks

    return [
        {
            "file_path": file_path,
            "chunk_text": chunk,
            "checksum": checksum(chunk)
        }
        for chunk in chunks
    ]