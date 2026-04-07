from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
import os
import hmac
import hashlib

try:
    import razorpay
    RAZORPAY_AVAILABLE = True
except (ImportError, Exception):
    razorpay = None
    RAZORPAY_AVAILABLE = False

from auth import get_current_user

router = APIRouter()

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

def get_razorpay_client():
    if not RAZORPAY_AVAILABLE or not razorpay:
        raise HTTPException(status_code=503, detail="Payment service unavailable")
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET or "placeholder" in RAZORPAY_KEY_ID:
        raise HTTPException(status_code=503, detail="Payment gateway not configured")
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


class CreateOrderRequest(BaseModel):
    amount: int  # in paise
    plan: str


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    plan: str


PLAN_CREDITS = {
    "starter": 50,
    "pro": 200,
    "studio": 1000,
}


@router.post("/create-order")
async def create_order(req: CreateOrderRequest, user=Depends(get_current_user)):
    client = get_razorpay_client()
    order_data = {
        "amount": req.amount,
        "currency": "INR",
        "receipt": f"order_{user['sub']}_{req.plan}",
    }
    order = client.order.create(data=order_data)
    return {"order_id": order["id"], "amount": order["amount"], "currency": order["currency"]}


@router.post("/verify")
async def verify_payment(req: VerifyPaymentRequest, user=Depends(get_current_user)):
    client = get_razorpay_client()
    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": req.razorpay_order_id,
            "razorpay_payment_id": req.razorpay_payment_id,
            "razorpay_signature": req.razorpay_signature,
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    credits_to_add = PLAN_CREDITS.get(req.plan, 0)
    if credits_to_add == 0:
        raise HTTPException(status_code=400, detail="Invalid plan")

    from database import supabase
    supabase.rpc("add_credits", {"user_id": user["sub"], "amount": credits_to_add}).execute()
    supabase.table("payments").insert({
        "user_id": user["sub"],
        "razorpay_order_id": req.razorpay_order_id,
        "razorpay_payment_id": req.razorpay_payment_id,
        "plan": req.plan,
        "amount": credits_to_add,
        "status": "completed",
    }).execute()

    return {"success": True, "credits_added": credits_to_add}
