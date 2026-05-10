"""Admin evaluation views and endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.admin_web.templates import templates
from app.core.db import get_readonly_db_session
from app.core.deps import require_admin
from app.models.internal.admin_eval import (
    EVAL_MODEL_LABELS,
    EVAL_MODEL_SPECS,
    LONGFORM_TEMPLATE_LABELS,
    AdminEvalRunRequest,
)
from app.services.admin_eval import get_default_pricing, run_admin_eval

router = APIRouter(tags=["admin"])


@router.get("/evals/summaries", response_class=HTMLResponse)
def admin_eval_summaries_page(
    request: Request,
    _: None = Depends(require_admin),
) -> HTMLResponse:
    """Render admin summary eval UI."""
    return templates.TemplateResponse(
        request,
        "eval_summaries.html",
        {
            "request": request,
            "model_specs": EVAL_MODEL_SPECS,
            "model_labels": EVAL_MODEL_LABELS,
            "template_labels": LONGFORM_TEMPLATE_LABELS,
            "default_pricing": get_default_pricing(),
        },
    )


@router.post("/evals/summaries/run")
def admin_eval_summaries_run(
    payload: AdminEvalRunRequest,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    _: None = Depends(require_admin),
) -> dict[str, Any]:
    """Run summary/title eval against selected models and content samples."""
    return run_admin_eval(db, payload)
