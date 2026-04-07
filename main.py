"""
Vidfield FastAPI Backend
========================
Run locally:  uvicorn main:app --reload --port 8000
Production:   managed by Railway via Procfile
"""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from config import get_settings

# ── Import routers ──────────────────────────────────────────────────────
from routes.users    import router as users_router
from routes.videos   import router as videos_router
from routes.payments import router as payments_router

settings = get_settings()

app = FastAPI(
    title="Vidfield API",
    description="AI Video Generation SaaS for Indian Creators",
    version="1.0.0",
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

# ── CORS ────────────────────────────────────────────────────────────────
origins = [
    settings.frontend_url,
    "http://localhost:5173",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register routers ────────────────────────────────────────────────────
app.include_router(users_router)
app.include_router(videos_router)
app.include_router(payments_router)

# ── Health check ────────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "service": "vidfield-api", "version": "1.0.0"}


@app.get("/", tags=["system"])
def root():
    return {"message": "Vidfield API — see /docs for endpoints"}


# ── Global error handler ────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    import traceback
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )
