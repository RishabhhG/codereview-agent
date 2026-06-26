import jwt
import time
import os
import httpx

def generate_jwt() -> str:
    """Sign a JWT with your private key — valid for 10 minutes"""
    app_id = os.getenv("GITHUB_APP_ID")
    key_path = os.getenv("GITHUB_PRIVATE_KEY_PATH")

    with open(key_path, "r") as f:
        private_key = f.read()

    now = int(time.time())
    payload = {
        "iat": now - 60,       # issued at (60s buffer for clock skew)
        "exp": now + (9 * 60), # expires in 9 minutes (max is 10)
        "iss": app_id
    }

    return jwt.encode(payload, private_key, algorithm="RS256")


async def get_installation_token(installation_id: int) -> str:
    """Exchange JWT for a short-lived installation token"""
    jwt_token = generate_jwt()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"
            }
        )
        response.raise_for_status()
        return response.json()["token"]