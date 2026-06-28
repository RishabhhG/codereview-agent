import logging
import os
from fastapi import FastAPI
from routers.webhook import router as webhook_router
from dotenv import load_dotenv

load_dotenv()

# --- Logging setup ---
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler("logs/agent.log"),   # writes to file
        logging.StreamHandler()                   # still prints to terminal
    ]
)

app = FastAPI(title="CodeReview AI Agent")
app.include_router(webhook_router)

@app.get("/health")
def health():
    return {"status": "ok"}