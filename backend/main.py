"""
main.py — DUK Bus Tracker FastAPI application entry point.
"""
import asyncio
import logging
import html as html_lib
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import engine, Base, get_db
from config import get_settings
from services.notification_scheduler import notification_scheduler_loop
from services.ml_scheduler import ml_training_loop
from models.notification import Suggestion

# Import all models so Alembic / create_all sees them
import models  # noqa: F401

# Routers
from routers import auth, gps, tracking, admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger   = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables on startup and start background tasks."""
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Auto-migrate OTP columns for multi-worker scalability
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS otp_code VARCHAR(10);"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS otp_expires_at TIMESTAMP WITH TIME ZONE;"))
    logger.info("[STARTUP] Database tables ensured.")

    # Start the background notification scheduler (fires deferred push notifications)
    scheduler_task = asyncio.create_task(notification_scheduler_loop())
    logger.info("[STARTUP] Background notification scheduler started.")

    # Start the automated ML training scheduler
    ml_task = asyncio.create_task(ml_training_loop())
    logger.info("[STARTUP] Background ML training scheduler started.")

    yield

    # Gracefully cancel the scheduler on shutdown
    scheduler_task.cancel()
    ml_task.cancel()
    try:
        await asyncio.gather(scheduler_task, ml_task, return_exceptions=True)
    except asyncio.CancelledError:
        pass
    await engine.dispose()
    logger.info("[SHUTDOWN] Engine disposed.")


app = FastAPI(
    title="DUK Bus Tracker API",
    version="2.0.0",
    description="Real-time bus tracking for Digital University Kerala",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",  # Vite dev server (React admin dashboard)
        "http://localhost:8081",  # Expo dev server
        "http://localhost:8082",  # Expo dev server (alt port)
        "http://localhost:8083",  # Expo dev server (alt port)
        "http://localhost:19006", # Expo web
        "https://103.156.188.51",
        "https://duk.ac.in",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(gps.router)
app.include_router(tracking.router)
app.include_router(admin.router)

# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "DUK Bus Tracker", "version": "2.0.0"}



class SuggestionCreate(BaseModel):
    suggestion: str
    trip:       str = ""
    location:   str = ""


@app.post("/api/v1/suggestion")
async def save_suggestion(req: SuggestionCreate, request: Request, db: AsyncSession = Depends(get_db)):
    from services.auth import decode_token
    
    auth_header = request.headers.get("Authorization")
    user_id = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        user_id = decode_token(token)
    suggestion_text = html_lib.escape(req.suggestion.strip())
    if not suggestion_text:
        return JSONResponse({"error": "Suggestion cannot be empty"}, status_code=400)
    if len(suggestion_text) > 500:
        return JSONResponse({"error": "Suggestion too long"}, status_code=400)

    s = Suggestion(
        suggestion=suggestion_text,
        trip=html_lib.escape(req.trip[:50]),
        location=html_lib.escape(req.location[:100]),
        user_id=user_id,
        status="pending"
    )
    db.add(s)
    await db.commit()
    return {"success": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=5004, reload=True)
