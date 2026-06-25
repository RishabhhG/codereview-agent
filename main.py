from fastapi import FastAPI
from routers.webhook import router as webhook_router
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="CodeReview AI Agent")
app.include_router(webhook_router)

@app.get("/health")
def health():
    return {"status": "ok"}