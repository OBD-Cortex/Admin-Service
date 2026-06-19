"""
Database Connection Module — OBD-Cortex RAG
Establishes a single, pooled MongoDB Atlas connection and exposes named
collection handles for the entire application to import.
"""

import sys
from motor.motor_asyncio import AsyncIOMotorClient
import certifi
import logging
from core.config import MONGO_URI

logger = logging.getLogger(__name__)

# ==========================================
# 1. CLIENT INITIALIZATION
# ==========================================
# MongoClient manages an internal connection pool — a set of reusable TCP
# sockets to the Atlas cluster. We configure it for a lightweight edge server.

_CLIENT_OPTIONS = {
    "tlsCAFile": certifi.where(),       # CA bundle for TLS certificate verification
    "serverSelectionTimeoutMS": 5000,   # Fail fast if cluster is unreachable (5s cap)
    "maxPoolSize": 10,                  # Right-sized for a single Droplet (default 100 is excessive)
    "minPoolSize": 1,                   # Keep one socket warm to avoid cold-start latency
    "appName": "obd-cortex-api",        # Identifies this app in Atlas performance profiler
    "retryWrites": True,                # Auto-retry writes on transient network failures
    "retryReads": True,                 # Auto-retry reads on transient network failures
}

logger.info("[*] Initializing Async MongoDB Connection...")

try:
    client = AsyncIOMotorClient(MONGO_URI, **_CLIENT_OPTIONS)
except Exception as e:
    error_msg = str(e)
    if MONGO_URI:
        error_msg = error_msg.replace(MONGO_URI, "[REDACTED_URI]")
    logger.error(f"[!] Database Connection Failed: {error_msg}")
    sys.exit(1)

# ==========================================
# 2. DATABASE & COLLECTION HANDLES
# ==========================================
# These are lightweight references — no network calls or memory allocation
# occurs until an actual read/write operation is performed on them.

db = client["rag_db"]

col_knowledge = db["knowledge"]             # Vector-embedded repair manuals & DTC docs
col_devices = db["devices"]                 # Pi device registration & owner pairing
col_users = db["users"]                     # Mobile app user accounts
col_jobs = db["jobs"]                       # Background processing tasks (ingestion)

async def init_db():
    """Asynchronously initialize database indexes and verify connection."""
    try:
        await client.admin.command("ping")
        logger.info("[✓] Database Connected and Pinged Successfully.")
    except Exception as e:
        logger.error(f"[!] Database Ping Failed: {e}")
        sys.exit(1)

    # Removed unused indexes

    # Create unique username index for mobile app user accounts
    try:
        await col_users.create_index("username", unique=True)
    except Exception as e:
        logger.warning(f"[!] Warning: Could not create index on col_users.username: {e}")

    # Enforce 1 user per device token (partial: indexes only string values to avoid null uniqueness conflict)
    try:
        try:
            await col_users.drop_index("device_token_1")
        except Exception:
            pass
        await col_users.create_index(
            "device_token",
            unique=True,
            partialFilterExpression={"device_token": {"$type": "string"}}
        )
    except Exception as e:
        logger.warning(f"[!] Warning: Could not create index on col_users.device_token: {e}")

    # Enforce 1 owner per device (partial: indexes only string values to avoid null uniqueness conflict)
    try:
        try:
            await col_devices.drop_index("owner_id_1")
        except Exception:
            pass
        await col_devices.create_index(
            "owner_id",
            unique=True,
            partialFilterExpression={"owner_id": {"$type": "string"}}
        )
    except Exception as e:
        logger.warning(f"[!] Warning: Could not create index on col_devices.owner_id: {e}")

    # Create unique index on device_token in col_devices
    try:
        await col_devices.create_index("device_token", unique=True)
    except Exception as e:
        logger.warning(f"[!] Warning: Could not create unique index on col_devices.device_token: {e}")

    # Create unique index on device_id in col_devices (partial: indexes only exists values to avoid null uniqueness conflict)
    try:
        await col_devices.create_index(
            "device_id",
            unique=True,
            partialFilterExpression={"device_id": {"$exists": True}}
        )
    except Exception as e:
        logger.warning(f"[!] Warning: Could not create unique index on col_devices.device_id: {e}")

    # Create index on created_at for fast sorting
    try:
        await col_devices.create_index([("created_at", -1)])
    except Exception as e:
        logger.warning(f"[!] Warning: Could not create index on col_devices.created_at: {e}")

    logger.info("[✓] Database Indexes Verified.")
