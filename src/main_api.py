import os
import uuid
import uvicorn
import logging
from fastapi import FastAPI, Request

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager

# Set up logging for the application
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from routes.admin import router as admin_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    from core.database import init_db
    await init_db()
    yield

# Disable interactive docs in production (reduces attack surface)
app = FastAPI(
    title="OBD-Cortex API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)



# Mount admin application router
app.include_router(admin_router)


# ----------------------------------------------------------
# SECURITY: Request ID Middleware
# ----------------------------------------------------------
# Attaches a unique request ID to every request for audit
# trail correlation across logs.
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

app.add_middleware(RequestIDMiddleware)


# ----------------------------------------------------------
# SECURITY: Global Exception Handler
# ----------------------------------------------------------
# Catches unhandled exceptions and returns a generic error
# response. Stack traces are logged server-side but never
# exposed to the client.
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(f"[{request_id}] Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "request_id": request_id},
    )




@app.get("/api/health")
async def health_endpoint():
    """Returns database and service health status."""
    try:
        from core.database import db
        await db.client.admin.command("ping")
        db_status = "connected"
    except Exception:
        db_status = "disconnected"
    
    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status
    }

if __name__ == "__main__":
    logger.info("="*60)
    logger.info(" OBD-CORTEX SERVER IS LIVE!")
    logger.info("="*60)
    logger.info(f" Loopback:            http://127.0.0.1:8000")
    logger.info("="*60)

    # SECURITY: Bind to loopback only. In production, Nginx
    # reverse proxy handles public-facing traffic on port 443
    # and forwards to this process on 127.0.0.1:8000.
    # --proxy-headers trusts X-Forwarded-For from Nginx.
    uvicorn.run(
        "main_api:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
