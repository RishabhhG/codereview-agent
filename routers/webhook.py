from fastapi import APIRouter, Request, HTTPException
import hmac, hashlib, os
from services.github_client import get_pr_files
from services.diff_parser import parse_pr_files

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

        owner, repo = payload["repository"]["full_name"].split("/")
        pr_number = payload["pull_request"]["number"]
        head_sha = payload["pull_request"]["head"]["sha"]
        installation_id = payload["installation"]["id"]  # needed for auth

        print(f"Processing PR #{pr_number} on {owner}/{repo} | SHA: {head_sha}")

        # Fetch and parse diff
        raw_files = await get_pr_files(installation_id, owner, repo, pr_number)
        diff = parse_pr_files(raw_files)

        for f in diff:
            print(f"\n{'='*50}")
            print(f"File: {f['filename']} ({f['language']})")
            print(f"Status: {f['status']} | +{f['additions']} -{f['deletions']}")
            if f['truncated']:
                print("⚠ Patch truncated at 800 lines")
            print(f"Patch preview:\n{f['patch'][:300]}")

    return {"ok": True}