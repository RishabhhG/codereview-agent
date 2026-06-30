import logging
import os
from fastapi import FastAPI
from fastapi.concurrency import asynccontextmanager
from routers.webhook import router as webhook_router
from dotenv import load_dotenv
from db.connection import get_pool, close_pool

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()       # startup
    yield
    await close_pool()     # shutdown

app = FastAPI(title="CodeReview AI Agent")
app.include_router(webhook_router)

@app.get("/health")
def health():
    return {"status": "ok"}