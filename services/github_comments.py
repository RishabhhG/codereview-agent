import httpx
import logging
from services.github_auth import get_installation_token

logger = logging.getLogger(__name__)

HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
}


async def post_pr_review(
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    body: str,
    verdict: str
) -> dict:
    token = await get_installation_token(installation_id)

    # Fix 9 — no need for identity map
    event = verdict.upper() if verdict.upper() in {"APPROVE", "REQUEST_CHANGES"} else "COMMENT"

    async with httpx.AsyncClient(timeout=30) as client:  # Fix 3
        response = await client.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            headers={**HEADERS, "Authorization": f"Bearer {token}"},
            json={"body": body, "event": event}
        )
        if response.is_error:  # Fix 4
            logger.error("GitHub API error posting review: %s", response.text)
            response.raise_for_status()
        return response.json()