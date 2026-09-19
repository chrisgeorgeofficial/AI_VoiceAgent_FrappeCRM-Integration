"""Liveness endpoints, handy for checking the tunnel is up."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/")
def read_root():
    return {"status": "ok", "message": "FastAPI server is running on port 5000"}


@router.get("/health")
def health_check():
    return {"status": "healthy"}
