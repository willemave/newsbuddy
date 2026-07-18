"""Aggregate router for the admin web UI."""

from fastapi import APIRouter

from app.admin_web import (
    api_keys,
    dashboard,
    evals,
    feedback,
    logs,
    onboarding,
    usage,
)

router = APIRouter(prefix="/admin", tags=["admin"])
router.include_router(dashboard.router)
router.include_router(evals.router)
router.include_router(onboarding.router)
router.include_router(api_keys.router)
router.include_router(feedback.router)
router.include_router(logs.router)
router.include_router(usage.router)
