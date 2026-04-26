# agents/shared/auth.py
"""Bearer token auth dependency for FastAPI endpoints.

All services share a single VERIFYIQ_AUTH_TOKEN env var. If unset, auth is
disabled — requests pass through without validation. This keeps local dev
and existing tests working without token configuration.
"""

import os
import secrets
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer(auto_error=False)


def get_auth_token() -> Optional[str]:
    """Read VERIFYIQ_AUTH_TOKEN from env. None means auth is disabled."""
    return os.environ.get("VERIFYIQ_AUTH_TOKEN") or None


def require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """FastAPI dependency — validates bearer token or raises 401.

    If VERIFYIQ_AUTH_TOKEN is not set, auth is disabled (pass-through).
    Uses secrets.compare_digest to prevent timing attacks.
    """
    expected = get_auth_token()
    if expected is None:
        return "no-auth"

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    if not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    return credentials.credentials
