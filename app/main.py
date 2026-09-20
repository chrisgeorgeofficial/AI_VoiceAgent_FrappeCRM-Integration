"""Application entry point.

Run with:  uvicorn app.main:app --reload --port 5000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CORS_ORIGINS
from app.routers import health, voice


def create_app() -> FastAPI:
    application = FastAPI(title="AI Voice Agent - Frappe CRM Integration")

    # The CRM desk is served from another port, so its "Call with AI Agent"
    # button is a cross-origin request and the browser blocks it otherwise.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["POST"],
        allow_headers=["*"],
    )
    application.include_router(health.router)
    application.include_router(voice.router)
    return application


app = create_app()
