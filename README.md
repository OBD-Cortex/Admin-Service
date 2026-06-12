# OBD-Cortex: Administration Service

The **Admin Service** is a secure FastAPI backend designed exclusively to handle management tasks triggered by Server Actions on the `Admin-Dashboard` web frontend.

---

## Service Architecture

1.  **Administrative Control Plane:** Exposes RESTful endpoints for generating hardware tokens, pairing devices, clearing/viewing knowledge manuals, and tracking fleet stats.
2.  **Isolated Authentication:** Bypasses public traffic completely. All administrative endpoints are guarded via a secure HS256 JWT `Authorization` header verification using `ADMIN_JWT_SECRET` in `src/core/auth.py`.
3.  **Stateless API Design:** Stripped of heavy websocket engines, UI layout rendering, or rate-limiting filters (which are pushed to `MobileApp-Service` and `Admin-Dashboard`), maximizing execution speed and minimizing VPS memory usage.
4.  **Database Connection:** Interacts with the shared MongoDB Atlas collections `devices`, `users`, and `knowledge` via the async driver Motor.
5.  **No Version Pins:** `requirements.txt` lists unpinned packages (e.g. `fastapi`, `motor`) to automatically download the latest stable versions during deployment.

---

## Repository Structure

*   `src/core/auth.py`: Implements API Key verification for proxy authentication.
*   `src/core/database.py`: Handles client connections and collections handles.
*   `src/routes/admin.py`: Administrative APIs (stats calculation, device provisioning, metadata pairing).
*   `src/main_api.py`: Uvicorn runner and middleware setups.
*   `systemd/`: Contains the service configuration templates.

---

## Local Development Setup

To run this backend locally:
1.  Verify **Python 3.10+** is installed.
2.  Initialize virtual environment:
    ```bash
    python -m venv venv && source venv/bin/activate
    ```
3.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
4.  Copy environment variables:
    ```bash
    cp .env.example .env
    ```
5.  Configure your MongoDB URI and API keys inside `.env`.
6.  Start development server:
    ```bash
    uvicorn src.main_api:app --reload
    ```

---

## Deployment Guide

*   Refer to [DEPLOYMENT.md](file:///home/bodz/OBD-Cortex/Admin-Service/DEPLOYMENT.md) for Nginx configs, systemd templates, and UFW security rules.

