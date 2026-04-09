"""
/api/videos — generate, poll status, history
"""
import os
import uuid
import asyncio
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from auth import get_current_user
from database import get_supabase
from config import get_settings
import fal_client

router = APIRouter(prefix="/api/videos", tags=["videos"])

# Credits cost per generation
CREDIT_COST = {
    5:  1,
    10: 1,
    15: 1,
    30: 2,
    60: 2,
}

# Fal.ai model ID (Kling 1.6 or similar)
FAL_MODEL = "fal-ai/kling-video/v1.6/standard/text-to-video"

# ── Beta-mode flag ─────────────────────────────────────────────────────────
# Flip BETA_MODE env var in Railway to go live. No code change needed.
# true  → skip Fal.ai + block Razorpay orders (safe for open beta / testing)
# false → real AI video generation + real payments enabled
BETA_MODE = os.getenv("BETA_MODE", "true").lower() in ("true", "1", "yes")
BETA_DEMO_VIDEO_URL = (
    "https://v3b.fal.media/files/b/0a957ddd/erUjzF9hKWwHfDSzI7OKS_output.mp4"
)
if BETA_MODE:
    print("[beta] BETA_MODE=true — Fal.ai calls and real payments DISABLED")
# ──────────────────────────────────────────────────────────────────────────


class GenerateRequest(BaseModel):
    prompt: str
    style: str = "bollywood"
    aspect_ratio: str = "9:16"
    duration: int = 15


def _deduct_credits(supabase, user_id: str, amount: int):
    """Atomically deduct credits using Supabase RPC."""
    res = supabase.rpc(
        "deduct_credits",
        {"p_user_id": user_id, "p_amount": amount}
    ).execute()
    return res.data


def _refund_credits(supabase, user_id: str, amount: int):
    """Refund credits on failure."""
    supabase.rpc(
        "add_credits",
        {"p_user_id": user_id, "p_amount": amount}
    ).execute()


async def _run_fal_generation(job_id: str, user_id: str, prompt: str,
                               aspect_ratio: str, duration: int, credit_cost: int):
    """Run Fal.ai generation in background and update job record."""
    supabase = get_supabase()
    settings = get_settings()

    # Set Fal key
    os.environ["FAL_KEY"] = settings.fal_key

    # ── Beta-mode guard ────────────────────────────────────────────────────
    if BETA_MODE:
        print(f"[beta] job {job_id}: Fal.ai skipped, returning demo video")
        get_supabase().table("videos").update({
            "status": "completed",
            "video_url": BETA_DEMO_VIDEO_URL,
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", job_id).execute()
        return
    # ────────────────────────────────────────────────────────────────────────

    try:
        # Map aspect ratio to Fal format
        ar_map = {"9:16": "9:16", "16:9": "16:9", "1:1": "1:1"}
        fal_ar = ar_map.get(aspect_ratio, "9:16")

        # Map duration: Fal Kling supports 5s or 10s natively
        fal_duration = "5" if duration <= 5 else "10"

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: fal_client.run(
                FAL_MODEL,
                arguments={
                    "prompt": prompt,
                    "aspect_ratio": fal_ar,
                    "duration": fal_duration,
                },
            )
        )

        video_url = result.get("video", {}).get("url") or result.get("url", "")
        if not video_url and isinstance(result, dict):
            # Try common result shapes
            for key in ["video_url", "output", "outputs"]:
                val = result.get(key)
                if isinstance(val, str):
                    video_url = val
                    break
                elif isinstance(val, list) and val:
                    video_url = val[0].get("url", "")
                    break

        supabase.table("video_jobs").update({
            "status":    "completed",
            "video_url": video_url,
            "completed_at": datetime.utcnow().isoformat(),
        }).eq("id", job_id).execute()

    except Exception as e:
        # Refund on failure
        _refund_credits(supabase, user_id, credit_cost)
        supabase.table("video_jobs").update({
            "status":   "failed",
            "error":    str(e)[:500],
            "completed_at": datetime.utcnow().isoformat(),
        }).eq("id", job_id).execute()


@router.post("/generate")
async def generate_video(
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
):
    supabase = get_supabase()
    user_id = user["sub"]
    credit_cost = CREDIT_COST.get(req.duration, 1)

    # Check & deduct credits
    profile_res = supabase.table("profiles").select("credits").eq("user_id", user_id).single().execute()
    if not profile_res.data or profile_res.data["credits"] < credit_cost:
        raise HTTPException(status_code=402, detail=f"Insufficient credits. Need {credit_cost}, have {profile_res.data.get('credits', 0) if profile_res.data else 0}.")

    _deduct_credits(supabase, user_id, credit_cost)

    # Create job record
    job_id = str(uuid.uuid4())
    supabase.table("video_jobs").insert({
        "id":           job_id,
        "user_id":      user_id,
        "prompt":       req.prompt,
        "style":        req.style,
        "aspect_ratio": req.aspect_ratio,
        "duration":     req.duration,
        "credit_cost":  credit_cost,
        "status":       "processing",
    }).execute()

    # Fire off background generation
    background_tasks.add_task(
        _run_fal_generation,
        job_id, user_id, req.prompt,
        req.aspect_ratio, req.duration, credit_cost
    )

    return {"job_id": job_id, "status": "processing"}


@router.get("/status/{job_id}")
def get_video_status(job_id: str, user=Depends(get_current_user)):
    supabase = get_supabase()
    res = supabase.table("video_jobs") \
        .select("id, status, video_url, prompt, style, duration, aspect_ratio, created_at, completed_at, error") \
        .eq("id", job_id) \
        .eq("user_id", user["sub"]) \
        .single() \
        .execute()

    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")

    return res.data


@router.get("/history")
def get_video_history(
    page: int = 1,
    limit: int = 12,
    user=Depends(get_current_user),
):
    supabase = get_supabase()
    offset = (page - 1) * limit

    res = supabase.table("video_jobs") \
        .select("id, status, video_url, thumbnail_url, prompt, style, duration, aspect_ratio, created_at", count="exact") \
        .eq("user_id", user["sub"]) \
        .order("created_at", desc=True) \
        .range(offset, offset + limit - 1) \
        .execute()

    total = res.count or 0
    return {
        "videos":   res.data or [],
        "total":    total,
        "page":     page,
        "limit":    limit,
        "has_more": (offset + limit) < total,
    }
