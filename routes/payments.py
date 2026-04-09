from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import os, hmac, hashlib

# Ensure pkg_resources is available (required by razorpay, missing in Python 3.12 Railway venvs)
try:
    import pkg_resources
except ImportError:
    import sys as _sys, types as _types
    _DistNotFound = type("DistributionNotFound", (Exception,), {})
    _pkg = _types.ModuleType("pkg_resources")
    _pkg.get_distribution = lambda name: type("D", (), {"version": "0", "project_name": name})()
    _pkg.require = lambda *a, **kw: [type("Req", (), {"version": "1.4.1"})()]
    _pkg.DistributionNotFound = _DistNotFound
    _pkg.RequirementParseError = type("RequirementParseError", (Exception,), {})
    _sys.modules["pkg_resources"] = _pkg

try:
    import razorpay
    RAZORPAY_AVAILABLE = True
    print("[payments] razorpay imported OK")
except (ImportError, Exception) as e:
    print(f"[payments] razorpay import FAILED: {e}")
    razorpay = None
    RAZORPAY_AVAILABLE = False

from auth import get_current_user
from database import get_supabase

router = APIRouter(prefix="/api/payments", tags=["payments"])

RAZORPAY_KEY_ID     = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

# ── Beta-mode flag (flip BETA_MODE env var in Railway — no code change needed)
# true  → block real Razorpay orders + skip Fal.ai (safe open beta)
# false → live payments enabled
BETA_MODE = os.getenv("BETA_MODE", "true").lower() in ("true", "1", "yes")
if BETA_MODE:
    print("[beta] BETA_MODE=true — real Razorpay orders DISABLED")

# Plan prices in paise (INR × 100)
PLAN_PRICES = {
    "starter": 29900,
    "creator": 79900,
    "pro":     199900,
}

# Credits granted per plan
PLAN_CREDITS = {
    "free":    10,
    "starter": 50,
    "creator": 200,
    "pro":     600,
}

PLAN_NAMES = {
    "starter": "Starter",
    "creator": "Creator",
    "pro":     "Pro",
}


def get_razorpay_client():
    if not RAZORPAY_AVAILABLE or not razorpay:
        raise HTTPException(status_code=503, detail="Payment service unavailable")
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET or "placeholder" in RAZORPAY_KEY_ID:
        raise HTTPException(status_code=503, detail="Payment gateway not configured")
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


class CreateOrderRequest(BaseModel):
    plan_id: str


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id:   str
    razorpay_payment_id: str
    razorpay_signature:  str
    plan_id:             str


@router.get("/plans")
def list_plans():
    """Return available paid plans with prices."""
    return [
        {"id": k, "name": PLAN_NAMES[k], "amount": v, "credits": PLAN_CREDITS[k]}
        for k, v in PLAN_PRICES.items()
    ]


@router.post("/create-order")
async def create_order(req: CreateOrderRequest, user=Depends(get_current_user)):
    # ── Beta-mode guard ────────────────────────────────────────────────────
    if BETA_MODE:
        raise HTTPException(
            status_code=503,
            detail="Payments are disabled during beta. Enjoy your free credits!"
        )
    # ────────────────────────────────────────────────────────────────────────
    amount = PLAN_PRICES.get(req.plan_id)
    if amount is None:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {req.plan_id}")

    client = get_razorpay_client()
    order_data = {
        "amount":   amount,
        "currency": "INR",
        "receipt":  f"order_{user['sub'][:8]}_{req.plan_id}",
    }
    order = client.order.create(data=order_data)
    return {
        "razorpay_order_id": order["id"],
        "amount":            order["amount"],
        "currency":          order["currency"],
        "email":             user.get("email", ""),
    }


@router.post("/verify")
async def verify_payment(req: VerifyPaymentRequest, user=Depends(get_current_user)):
    client = get_razorpay_client()

    # Verify signature
    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id":   req.razorpay_order_id,
            "razorpay_payment_id": req.razorpay_payment_id,
            "razorpay_signature":  req.razorpay_signature,
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    credits_to_add = PLAN_CREDITS.get(req.plan_id, 0)
    if credits_to_add == 0:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {req.plan_id}")

    supabase = get_supabase()

    # Add credits using consistent RPC param names
    supabase.rpc("add_credits", {"p_user_id": user["sub"], "p_amount": credits_to_add}).execute()

    # Update profile plan
    supabase.table("profiles").update({"plan": req.plan_id}).eq("user_id", user["sub"]).execute()

    # Record payment
    supabase.table("payments").insert({
        "user_id":             user["sub"],
        "razorpay_order_id":   req.razorpay_order_id,
        "razorpay_payment_id": req.razorpay_payment_id,
        "plan":                req.plan_id,
        "amount":              PLAN_PRICES.get(req.plan_id, 0),
        "credits_added":       credits_to_add,
        "status":              "completed",
    }).execute()

    return {"success": True, "credits_added": credits_to_add, "plan": req.plan_id}
