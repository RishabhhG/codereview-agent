import hmac
import hashlib
import logging
import os
from dotenv import load_dotenv

load_dotenv()
from fastapi import APIRouter, Request, HTTPException
from services.github_client import get_pr_files
from services.diff_parser import parse_pr_files
from services.llm_client import chat
from services.review_prompt import SYSTEM_PROMPT, build_user_prompt, build_rag_context_block, parse_verdict
from services.github_comments import post_pr_review
from services.retriever import search_chunks

router = APIRouter()
logger = logging.getLogger(__name__)

_secret = os.getenv("GITHUB_WEBHOOK_SECRET")
if not _secret:
    raise RuntimeError("GITHUB_WEBHOOK_SECRET is not set")
SECRET = _secret.encode()

USE_RAG_CONTEXT = os.getenv("USE_RAG_CONTEXT", "false").lower() == "true"


@router.post("/webhook")
async def github_webhook(request: Request):
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")

    if not sig:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")

    expected = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = request.headers.get("X-GitHub-Event")

    if event == "ping":
        logger.info("Received ping event — webhook configured successfully")
        return {"ok": True}

    if event != "pull_request":
        logger.info("Ignoring event: %s", event)
        return {"ok": True, "skipped": event}

    payload = await request.json()
    action = payload.get("action")

    HANDLED_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}
    if action not in HANDLED_ACTIONS:
        logger.info("Skipping PR action: %s", action)
        return {"ok": True, "skipped": action}

    if payload["pull_request"].get("draft"):
        logger.info("Skipping draft PR")
        return {"ok": True, "skipped": "draft"}

    installation = payload.get("installation")
    if not installation:
        raise HTTPException(status_code=400, detail="Missing installation in payload")
    installation_id = installation["id"]

    owner = payload["repository"]["owner"]["login"]
    repo = payload["repository"]["name"]
    pr_number = payload["pull_request"]["number"]
    head_sha = payload["pull_request"]["head"]["sha"]

    logger.info("Processing PR #%d on %s/%s | SHA: %s", pr_number, owner, repo, head_sha)

    raw_files = await get_pr_files(installation_id, owner, repo, pr_number)
    diff = parse_pr_files(raw_files)

    if not diff:
        logger.info("No reviewable files found in PR #%d", pr_number)
        return {"ok": True, "skipped": "no reviewable files"}

    for f in diff:
        logger.info(
            "Parsed: %s (%s) | %s | +%d -%d | %d lines",
            f["filename"], f["language"], f["status"],
            f["additions"], f["deletions"], len(f["patch"].splitlines()),
        )
        if f["truncated"]:
            logger.warning("Patch truncated at 800 lines: %s", f["filename"])

    # --- RAG retrieval (feature-flagged) ---
    rag_context = ""
    if USE_RAG_CONTEXT:
        repo_identifier = f"{owner}/{repo}"
        # Build a query from changed filenames — a simple but effective heuristic
        query = " ".join(f["filename"] for f in diff)
        try:
            chunks = await search_chunks(repo_identifier, query, top_k=5)
            rag_context = build_rag_context_block(chunks)
            logger.info("RAG context retrieved: %d chunks", len(chunks))
        except Exception as e:
            logger.warning("RAG retrieval failed, continuing without context: %s", e)
    else:
        logger.info("RAG context disabled (USE_RAG_CONTEXT=false)")

    user_prompt = build_user_prompt(diff, rag_context=rag_context)
    logger.info("Calling Gemini for PR #%d... (RAG: %s)", pr_number, USE_RAG_CONTEXT)

    review = await chat(SYSTEM_PROMPT, user_prompt)

    logger.info("LLM REVIEW — PR #%d:\n%s", pr_number, review)

    verdict = parse_verdict(review)
    result = await post_pr_review(
        installation_id, owner, repo, pr_number, review, verdict
    )
    logger.info("Review posted — verdict: %s | review_id: %s", verdict, result.get("id"))

    return {"ok": True}