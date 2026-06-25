from fastapi import APIRouter, Request, HTTPException
import hmac, hashlib, os

router = APIRouter()

@router.post("/webhook")
async def github_webhook(request: Request):
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "").encode()
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")

    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event = request.headers.get("X-GitHub-Event")

    if event == "pull_request":
        action = payload.get("action")

        if action not in ("opened", "synchronize"):
            return {"ok": True, "skipped": action}

        repo = payload["repository"]["full_name"]
        pr_number = payload["pull_request"]["number"]
        head_sha = payload["pull_request"]["head"]["sha"]

        print(f"Processing PR #{pr_number} on {repo} | SHA: {head_sha} | Action: {action}")

    return {"ok": True}