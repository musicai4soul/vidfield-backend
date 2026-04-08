"""
/api/users — profile management & credit info
"""
from fastapi import APIRouter, Depends, HTTPException
from auth import get_current_user
from database import get_supabase

router = APIRouter(prefix="/api/users", tags=["users"])

PLAN_CREDITS = {
    "free":    10,
    "starter": 50,
    "creator": 200,
    "pro":     600,
}


def _ensure_profile(supabase, user_id: str, email: str) -> dict:
    """
    Fetches the user profile. If it doesn't exist yet (first login),
    creates it with the free plan and 10 starter credits.
    """
    res = supabase.table("profiles").select("*").eq("user_id", user_id).maybe_single().execute()

    if res.data:
        return res.data

    # First-time user — create profile
    new_profile = {
        "user_id": user_id,
        "email":   email,
        "plan":    "free",
        "credits": PLAN_CREDITS["free"],
    }
    created = supabase.table("profiles").insert(new_profile).execute()
    return created.data[0]


@router.get("/profile")
def get_profile(user=Depends(get_current_user)):
    supabase = get_supabase()
    profile = _ensure_profile(supabase, user["sub"], user.get("email", ""))
    return profile
