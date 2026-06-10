# OBD-Cortex: Admin Service

The **Admin Service** is a lightweight, secure FastAPI backend dedicated entirely to servicing the `Admin-Dashboard` frontend. It handles critical administrative actions such as device generation, telemetry monitoring, and triggering manual ingestion jobs.

## Architecture Overview

1. **Decoupled Security**: This service sits entirely isolated from edge device traffic and mobile app user traffic. Authentication is strictly handled via an internal `MOBILE_API_KEY` injected by the Next.js dashboard proxy.
2. **Resource Efficiency**: Purged of heavy websocket connections and bloated rate-limiting libraries, this service strictly exposes RESTful endpoints, scaling independently of the high-traffic RAG engine.
3. **Database Architecture**: Connects to the centralized MongoDB Atlas cluster via `core/database.py`. All indexes and connection handling are encapsulated cleanly in the application startup events.

## Repository Structure

- `src/core/`: Configuration, database handles, and foundational utilities.
- `src/routes/`: FastAPI routing definitions for administrative actions (e.g., `admin.py`).
- `src/main_api.py`: The root Uvicorn entrypoint for the service.
- `systemd/`: Contains the daemon deployment configurations for Linux hosts.

## Local Development (Quick Start)

To run this backend service locally for development or API testing:
1. Ensure **Python 3.10+** is installed.
2. Create and activate a virtual environment: `python -m venv venv && source venv/bin/activate`
3. Install dependencies: `pip install -r requirements.txt`
4. Copy the environment variables: `cp .env.example .env` and fill in your MongoDB URI.
5. Start the development server with hot-reloading: `uvicorn src.main_api:app --reload`

## Deployment

Please refer to `DEPLOYMENT.md` for a comprehensive, production-grade deployment guide on DigitalOcean using Nginx, Certbot, and Fish.
