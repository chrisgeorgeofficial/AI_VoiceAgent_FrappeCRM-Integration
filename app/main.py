"""Application entry point.

Run with:  uvicorn app.main:app --reload --port 5000
"""

from fastapi import FastAPI

from app.routers import health, voice


def create_app() -> FastAPI:
    application = FastAPI(title="AI Voice Agent - Frappe CRM Integration")
    application.include_router(health.router)
    application.include_router(voice.router)
    return application


app = create_app()
