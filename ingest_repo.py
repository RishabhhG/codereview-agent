import argparse
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from db.connection import get_pool, close_pool, init_db
from services.chunker import split_into_chunks, SUPPORTED_EXTENSIONS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# Directories to skip while walking the repo
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", "target", ".idea", ".vscode"
}


def find_source_files(repo_path: str) -> list[str]:
    """Walk repo, return list of file paths matching supported extensions"""
    files = []
    for root, dirs, filenames in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]  # prune in-place

        for filename in filenames:
            ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
            if ext in SUPPORTED_EXTENSIONS:
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, repo_path)
                files.append(rel_path)

    return files


async def ingest_repo(repo_path: str, repo_name: str):
    from services.embedder import embed_and_store_file  # import here, after env loaded

    files = find_source_files(repo_path)
    logger.info("Found %d source files in %s", len(files), repo_path)

    total_embedded = 0
    total_skipped = 0

    for rel_path in files:
        full_path = os.path.join(repo_path, rel_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            logger.warning("Could not read %s: %s", rel_path, e)
            continue

        if not content.strip():
            continue

        chunks = split_into_chunks(rel_path, content)
        stats = await embed_and_store_file(repo_name, rel_path, chunks)

        total_embedded += stats["embedded"]
        total_skipped += stats["skipped"]

    logger.info(
        "Ingestion complete — %d chunks embedded, %d skipped (unchanged)",
        total_embedded, total_skipped
    )


def main():
    parser = argparse.ArgumentParser(description="Ingest a repo into pgvector for RAG")
    parser.add_argument("--repo-path", required=True, help="Local path to the repo")
    parser.add_argument("--repo-name", required=True, help="Identifier, e.g. 'owner/repo'")
    args = parser.parse_args()

    if not Path(args.repo_path).exists():
        logger.error("Path does not exist: %s", args.repo_path)
        return

    asyncio.run(_run(args.repo_path, args.repo_name))


async def _run(repo_path: str, repo_name: str):
    await get_pool()
    await init_db()  # ensure code_chunks citation columns / chat tables exist
    try:
        await ingest_repo(repo_path, repo_name)
    finally:
        await close_pool()


if __name__ == "__main__":
    main()