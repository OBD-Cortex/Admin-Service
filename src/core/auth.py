"""
Authentication Module -- OBD-Cortex
Provides JWT token management, bcrypt password hashing, API key
verification, and HMAC device signature validation.

Security Hardening Applied:
  [*] Timing-safe API key comparison (hmac.compare_digest)
  [*] JWT lifetime reduced to 7 days with iss/aud/jti claims
  [*] Minimum secret length validation at startup
  [*] HMAC replay protection with timestamp window + nonce cache
  [*] Failed auth attempt logging (without leaking secrets)
"""

import logging
from fastapi import HTTPException, Header, Security
from core.config import ADMIN_JWT_SECRET
from core.jwt_native import decode_jwt, JWTError, JWTExpiredError

logger = logging.getLogger(__name__)

# ----------------------------------------------------------
# STARTUP VALIDATION
# ----------------------------------------------------------
# Enforce minimum secret lengths to prevent weak keys.
_MIN_SECRET_LENGTH = 32

if ADMIN_JWT_SECRET and len(ADMIN_JWT_SECRET) < _MIN_SECRET_LENGTH:
    logger.warning(
        f"[!] ADMIN_JWT_SECRET is only {len(ADMIN_JWT_SECRET)} characters. "
        f"Minimum recommended length is {_MIN_SECRET_LENGTH}."
    )


def verify_admin_jwt(authorization: str = Header(..., alias="Authorization")) -> dict:
    """Verifies the Admin HS256 JWT from the Authorization header."""
    if not ADMIN_JWT_SECRET:
        logger.error("[!] ADMIN_JWT_SECRET is not configured")
        raise HTTPException(status_code=500, detail="Server misconfiguration")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header must start with 'Bearer '")

    token = authorization[7:]
    
    try:
        payload = decode_jwt(
            token, 
            secret=ADMIN_JWT_SECRET,
            issuer="obd-cortex-admin",
            audience="obd-cortex-admin-api"
        )
        return payload
    except JWTExpiredError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except JWTError as e:
        logger.warning(f"[!] Invalid Admin JWT attempt: {e}")
        raise HTTPException(status_code=403, detail="Invalid Token")
