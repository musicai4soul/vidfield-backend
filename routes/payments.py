"""
/api/payments — Razorpay order creation & webhook verification
"""
import hmac
import hashlib
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from auth import get_current_user
from database import get_supabase
from config import get_settings
import razorpay

router = APIRouter(prefix="/api/payments", tags=["payments"])

PLANS = {
    "starter": {"price": 29900,  "credits": 50,  "name": "Starter"},   # paise
    "creator": {"price": 79900,  "credits": 200, "name": "Creator"},
    "pro":     {"price": 199900, "credits": 600, "name": "Pro"},
}


def get_razorpay_client():
    settings = get_settings()
    return razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))


class CreateOrderRequest(BaseModel):
    plan_id: str


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id:   str
    razorpay_payment_id: str
    razorpay_signature:  str
    plan_id:             str


@router.get("/plans")
def get_plans():
    return [
        {
            "id":      plan_id,
            "name":    plan["name"],
            "price":   plan["price"],
            "credits": plan["credits"],
        }
        for plan_id, plan in PLANS.items()
    ]


@router.post("/create-order")
def create_razorpay_order(
    req: CreateOrderRequest,
    user=Depends(get_current_user),
):
    plan = PLANS.get(req.plan_id)
    if not plan:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {req.plan_id}")

    client = get_razorpay_client()
    order_data = {
        "amount":   plan["price"],
        "currency": "INR",
        "receipt":  f"vidfield_{user['sub'][:8]}_{req.plan_id}",
        "notes": {
            "user_id": user["sub"],
            "plan_id": req.plan_id,
        },
    }
    order = client.order.create(data=order_data)

    return {
        "razorpay_order_id": order["id"],
        "amount":            order["amount"],
        "currency":          order["currency"],
        "email":             user.get("email", ""),
    }


@router.post("/verify")
def verify_payment(
    req: VerifyPaymentRequest,
    user=Depends(get_current_user),
):
    settings = get_settings()

    # ── Verify Razorpay signature ────────────────────────────────────────
    body_str     = f"{req.razorpay_order_id}|{req.razorpay_payment_id}"
    expected_sig = hmac.new(
        settings.razorpay_key_secret.encode("utf-8"),
        body_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, req.razorpay_signature):
        raise HTTPException(
            status_code=400,
            detail="Payment verification failed: invalid signature."
        )

    plan = PLANS.get(req.plan_id)
    if not plan:
        raise HTTPException(status_code=400, detail="Invalid plan")

    supabase = get_supabase()
    user_id  = user["sub"]

    # ── Idempotency: check if payment already processed ──────────────────
    existing = supabase.table("payments") \
        .select("id") \
        .eq("razorpay_payment_id", req.razorpay_payment_id) \
        .execute()
    if existing.data:
        return {"status": "already_processed"}

    # ── Record payment ───────────────────────────────────────────────────
    supabase.table("payments").insert({
        "user_id":             user_id,
        "plan_id":             req.plan_id,
        "razorpay_order_id":   req.razorpay_order_id,
        "razorpay_payment_id": req.razorpay_payment_id,
        "amount":              plan["price"],
        "credits_granted":     plan["credits"],
        "status":              "success",
    }).execute()

    # ── Update profile: set plan + reset credits ─────────────────────────
    profile_res = supabase.table("profiles") \
        .select("credits") \
        .eq("user_id", user_id) \
        .single() \
        .execute()

    if profile_res.data:
        supabase.table("profiles").update({
            "plan":    req.plan_id,
            "credits": plan["credits"],
        }).eq("user_id", user_id).execute()
    else:
        supabase.table("profiles").insert({
            "user_id": user_id,
            "email":   user.get("email", ""),
            "plan":    req.plan_id,
            "credits": plan["credits"],
        }).execute()

    return {
        "status":          "success",
        "plan":            req.plan_id,
        "credits_granted": plan["credits"],
    }


@router.post("/webhook")
async def razorpay_webhook(request: Request):
    """
    Razorpay webhook — set this URL in Razorpay Dashboard > Webhooks.
    URL: https://your-api.railway.app/api/payments/webhook
    """
    settings  = get_settings()
    body      = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    expected = hmac.new(
        settings.razorpay_key_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    payload = await request.json()
    event   = payload.get("event")

    # Handle server-side payment events here if needed
    if event == "payment.captured":
        pass  # optionally sync credits again for extra safety

    return {"status": "ok"}
