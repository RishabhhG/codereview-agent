LANGUAGE_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript", ".go": "go",
    ".rs": "rust", ".java": "java", ".rb": "ruby",
    ".cpp": "cpp", ".c": "c", ".cs": "csharp",
    ".swift": "swift", ".kt": "kotlin", ".md": "markdown",
}

MAX_PATCH_LINES = 800


def get_language(filename: str) -> str:
    ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
    return LANGUAGE_MAP.get(ext, "plaintext")


def parse_pr_files(raw_files: list[dict]) -> list[dict]:
    """
    Convert GitHub's raw file list into clean structured diffs.
    Skips binary files, deleted-only files, and truncates huge patches.
    """
    parsed = []

    for f in raw_files:
        filename = f.get("filename", "")
        status = f.get("status")        # added, modified, removed, renamed
        patch = f.get("patch")          # unified diff string — missing for binary files

        # Skip binary files (no patch field)
        if patch is None:
            additions = f.get("additions", 0)
            deletions = f.get("deletions", 0)
            
            # Large diff — GitHub omitted patch
            if additions + deletions > 0:
                print(f"Skipping large diff (no patch returned): {filename}")
            else:
                print(f"Skipping binary file: {filename}")
            continue

        # Skip deleted-only files — nothing to review
        if status == "removed":
            print(f"Skipping deleted file: {filename}")
            continue

        # Truncate huge patches
        lines = patch.splitlines()
        truncated = False
        if len(lines) > MAX_PATCH_LINES:
            lines = lines[:MAX_PATCH_LINES]
            truncated = True

        additions = f.get("additions", 0)
        deletions = f.get("deletions", 0)

        parsed.append({
        "filename": filename,
        "previous_filename": f.get("previous_filename"),  # None if not a rename
        "status": status,
        "language": get_language(filename),
        "additions": additions,
        "deletions": deletions,
        "patch": "\n".join(lines),
        "truncated": truncated
})

    return parsed