import httpx
import logging
import base64
from services.github_auth import get_installation_token

logger = logging.getLogger(__name__)

HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
}


async def get_pr_files(installation_id: int, owner: str, repo: str, pr_number: int) -> list[dict]:
    token = await get_installation_token(installation_id)

    async with httpx.AsyncClient(timeout=30) as client:  # Fix 3
        response = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files",
            headers={**HEADERS, "Authorization": f"Bearer {token}"},
            params={"per_page": 100}
        )
        if response.is_error:  # Fix 4
            logger.error("GitHub API error fetching files: %s", response.text)
            response.raise_for_status()
        return response.json()
    
async def get_file_content(installation_id: int, owner: str, repo: str, path: str, ref: str | None = None) -> str:
    """
    Fetch the raw text content of a single file from a repo via the Contents API.
    `ref` can be a branch, tag, or commit SHA; defaults to the repo's default branch.
    Raises httpx.HTTPStatusError on 404 (file not found) or other API errors.
    """
    token = await get_installation_token(installation_id)
    params = {"ref": ref} if ref else {}
 
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
            headers={**HEADERS, "Authorization": f"Bearer {token}"},
            params=params
        )
        if response.is_error:
            logger.error("GitHub API error fetching file %s: %s", path, response.text)
            response.raise_for_status()
 
        data = response.json()
 
        if isinstance(data, list):
            raise ValueError(f"Path '{path}' is a directory, not a file")
 
        if data.get("encoding") != "base64":
            raise ValueError(f"Unexpected encoding '{data.get('encoding')}' for {path}")
 
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return content