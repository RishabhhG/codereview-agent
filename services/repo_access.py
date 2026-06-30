import os
import httpx


async def check_repo_access(installation_id: int, owner: str, repo: str) -> bool:
    """Check if the installation has access to a given repo"""
    token = os.getenv("GITHUB_TOKEN")  # hardcoded env lookup, no JWT flow

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers={"Authorization": f"token {token}"}
        )
        return response.status_code == 200
