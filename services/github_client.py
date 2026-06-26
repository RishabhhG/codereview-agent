import httpx
from services.github_auth import get_installation_token


async def get_pr_files(installation_id: int, owner: str, repo: str, pr_number: int) -> list[dict]:
    """Fetch list of changed files for a PR"""
    token = await get_installation_token(installation_id)

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"
            },
            params={"per_page": 100}  # max files per page
        )
        response.raise_for_status()
        return response.json()