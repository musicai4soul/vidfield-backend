"""
JWT auth dependency — validates Supabase Bearer tokens using Supabase Admin API.
Supports both ES256 (new Supabase projects) and HS256 tokens.
"""
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from database import get_supabase

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    Validates the Supabase JWT by calling Supabase Auth's getUser API.
    Works with both ES256 (new) and HS256 (legacy) Supabase projects.
    Returns a dict with: sub (user UUID), email, role.
    """
    token = credentials.credentials
    supabase = get_supabase()

    try:
        response = supabase.auth.get_user(token)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token verification failed: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not response or not response.user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = response.user
    return {
        "sub":   user.id,
        "email": user.email,
        "role":  "authenticated",
    }
