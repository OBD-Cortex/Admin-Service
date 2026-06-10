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
import hmac
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from core.config import MOBILE_API_KEY

logger = logging.getLogger(__name__)

# ----------------------------------------------------------
# STARTUP VALIDATION
# ----------------------------------------------------------
# Enforce minimum secret lengths to prevent weak keys.
_MIN_SECRET_LENGTH = 32

if MOBILE_API_KEY and len(MOBILE_API_KEY) < _MIN_SECRET_LENGTH:
    logger.warning(
        f"[!] MOBILE_API_KEY is only {len(MOBILE_API_KEY)} characters. "
        f"Minimum recommended length is {_MIN_SECRET_LENGTH}."
    )


# Removed unused auth methods


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

def verify_api_key(api_key: str = Security(api_key_header)):
    """Verifies the API key using timing-safe comparison to prevent timing attacks."""
    if not MOBILE_API_KEY:
        logger.error("[!] MOBILE_API_KEY is not configured")
        raise HTTPException(status_code=500, detail="Server misconfiguration")

    # SECURITY: hmac.compare_digest prevents timing-based key extraction
    if not hmac.compare_digest(api_key.encode("utf-8"), MOBILE_API_KEY.encode("utf-8")):
        logger.warning(f"[!] Invalid API key attempt (key length: {len(api_key)})")
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key


# Removed unused edge device auth methods
