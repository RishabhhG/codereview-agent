import logging
from fnmatch import fnmatch

logger = logging.getLogger(__name__)

LANGUAGE_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript", ".go": "go",
    ".rs": "rust", ".java": "java", ".rb": "ruby",
    ".cpp": "cpp", ".c": "c", ".cs": "csharp",
    ".swift": "swift", ".kt": "kotlin", ".md": "markdown",
    ".yml": "yaml", ".yaml": "yaml", ".sql": "sql",
    ".sh": "bash", ".toml": "toml", ".graphql": "graphql",
    ".proto": "protobuf", ".json": "json", ".xml": "xml",
    ".html": "html", ".css": "css", ".scss": "scss",
}

SPECIAL_FILES = {
    "Dockerfile": "dockerfile",
    "Makefile": "makefile",
    ".gitignore": "gitignore",
    "package.json": "json",
    "tsconfig.json": "json",
    "Cargo.toml": "toml",
    ".env.example": "dotenv",
}

# Files not worth spending LLM tokens on
SKIP_PATTERNS = [
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "pnpm-lock.yml",
    "*.min.js",
    "*.min.css",
    "dist/*",
    "build/*",
    "coverage/*",
    "vendor/*",
    "*.lock",
]

MAX_PATCH_LINES = 800
MAX_PATCH_CHARS = 100_000


def get_language(filename: str) -> str:
    # Check special filenames first (Dockerfile, Makefile, etc.)
    basename = filename.rsplit("/", 1)[-1]
    if basename in SPECIAL_FILES:
        return SPECIAL_FILES[basename]

    ext = "." + filename.rsplit(".", 1)[-1] if "." in basename else ""
    return LANGUAGE_MAP.get(ext, "plaintext")


def should_skip(filename: str) -> bool:
    basename = filename.rsplit("/", 1)[-1]
    for pattern in SKIP_PATTERNS:
        if fnmatch(basename, pattern) or fnmatch(filename, pattern):
            return True
    return False


def parse_pr_files(raw_files: list[dict]) -> list[dict]:
    """
    Convert GitHub's raw file list into clean structured diffs.

    Handles:
    - Binary files (no patch) → skipped
    - Rename-only files (renamed + no patch) → included with rename_only=True
    - Large omitted patches (GitHub omits patch for huge diffs) → included with too_large=True
    - Empty patches → skipped
    - Deleted files → skipped
    - Generated/lock files → skipped
    - Oversized patches → truncated
    """
    parsed = []

    for f in raw_files:
        filename = f.get("filename", "")
        status = f.get("status")        # added, modified, removed, renamed
        patch = f.get("patch")          # unified diff string — missing for binary/huge files
        additions = f.get("additions", 0)
        deletions = f.get("deletions", 0)

        # Skip generated files and lock files — not worth reviewing
        if should_skip(filename):
            logger.info("Skipping generated/lock file: %s", filename)
            continue

        # Skip deleted files — nothing to review
        if status == "removed":
            logger.info("Skipping deleted file: %s", filename)
            continue

        # Handle rename-only: GitHub returns patch=None or patch="" for pure renames
        if status == "renamed" and not patch:
            previous_filename = f.get("previous_filename")
            logger.info("Rename-only: %s → %s", previous_filename, filename)
            parsed.append({
                "filename": filename,
                "previous_filename": previous_filename,
                "status": status,
                "language": get_language(filename),
                "patch": None,
                "truncated": False,
                "too_large": False,
                "rename_only": True,
                "additions": additions,
                "deletions": deletions,
            })
            continue

        # Handle missing patch — either binary or GitHub omitted it (diff too large)
        if patch is None:
            if additions + deletions > 0:
                # GitHub omitted patch because the diff is too large
                logger.info("Patch omitted by GitHub (too large): %s", filename)
                parsed.append({
                    "filename": filename,
                    "previous_filename": f.get("previous_filename"),
                    "status": status,
                    "language": get_language(filename),
                    "patch": None,
                    "truncated": False,
                    "too_large": True,
                    "rename_only": False,
                    "additions": additions,
                    "deletions": deletions,
                })
            else:
                logger.info("Skipping binary file: %s", filename)
            continue

        # Skip empty patches — nothing to show the LLM
        if not patch.strip():
            logger.info("Skipping empty patch: %s", filename)
            continue

        # Cap by characters first (handles minified single-line files)
        truncated = False
        if len(patch) > MAX_PATCH_CHARS:
            patch = patch[:MAX_PATCH_CHARS]
            truncated = True

        # Cap by lines
        lines = patch.splitlines()
        if len(lines) > MAX_PATCH_LINES:
            lines = lines[:MAX_PATCH_LINES]
            truncated = True

        parsed.append({
            "filename": filename,
            "previous_filename": f.get("previous_filename"),
            "status": status,
            "language": get_language(filename),
            "patch": "\n".join(lines),
            "truncated": truncated,
            "too_large": False,
            "rename_only": False,
            "additions": additions,
            "deletions": deletions,
        })

    return parsed