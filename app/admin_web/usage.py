"""Admin vendor usage views."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func

from app.admin_web.formatting import format_user_label_with_id
from app.admin_web.templates import templates
from app.core.db import get_db
from app.core.deps import require_admin
from app.models.db import VendorUsageRecord
from app.models.db.users import User

router = APIRouter(tags=["admin"])


@router.get("/vendor-usage", response_class=HTMLResponse)
async def vendor_usage_dashboard(
    request: Request,
    _: None = Depends(require_admin),
) -> HTMLResponse:
    """Show recent persisted vendor usage rows with aggregate cost views."""
    provider = request.query_params.get("provider")
    model = request.query_params.get("model")
    feature = request.query_params.get("feature")
    user_id = request.query_params.get("user_id")
    start_date = request.query_params.get("start_date")
    end_date = request.query_params.get("end_date")
    raw_limit = request.query_params.get("limit")
    try:
        limit = max(1, min(int(raw_limit or "100"), 500))
    except ValueError:
        limit = 100
    records, totals, daily_rows, user_rows, user_day_rows = _get_vendor_usage_rows(
        provider=provider,
        model=model,
        feature=feature,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
    return templates.TemplateResponse(
        request,
        "vendor_usage_list.html",
        {
            "request": request,
            "records": records,
            "totals": totals,
            "daily_rows": daily_rows,
            "user_rows": user_rows,
            "user_day_rows": user_day_rows,
            "filters": {
                "provider": provider or "",
                "model": model or "",
                "feature": feature or "",
                "user_id": user_id or "",
                "start_date": start_date or "",
                "end_date": end_date or "",
                "limit": limit,
            },
        },
    )


@router.get("/llm-usage", response_class=HTMLResponse)
async def legacy_llm_usage_redirect(
    request: Request,
    _: None = Depends(require_admin),
) -> RedirectResponse:
    """Backwards-compatible redirect to the broader vendor usage dashboard."""
    target = "/admin/vendor-usage"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(url=target, status_code=307)


def _get_vendor_usage_rows(
    *,
    provider: str | None,
    model: str | None,
    feature: str | None,
    user_id: str | None,
    start_date: str | None,
    end_date: str | None,
    limit: int = 100,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Query persisted vendor usage records and aggregate cost views."""
    parsed_user_id = _parse_int_filter(user_id)
    start_dt = _parse_date_filter(start_date, end_of_day=False)
    end_dt = _parse_date_filter(end_date, end_of_day=True)
    with get_db() as db:
        base_query = (
            db.query(VendorUsageRecord, User)
            .outerjoin(User, User.id == VendorUsageRecord.user_id)
            .order_by(VendorUsageRecord.created_at.desc())
        )
        base_query = _apply_vendor_usage_filters(
            base_query,
            provider=provider,
            model=model,
            feature=feature,
            user_id=parsed_user_id,
            start_dt=start_dt,
            end_dt=end_dt,
        )

        rows = base_query.limit(limit).all()
        totals_query = db.query(
            func.coalesce(func.sum(VendorUsageRecord.input_tokens), 0),
            func.coalesce(func.sum(VendorUsageRecord.output_tokens), 0),
            func.coalesce(func.sum(VendorUsageRecord.total_tokens), 0),
            func.coalesce(func.sum(VendorUsageRecord.request_count), 0),
            func.coalesce(func.sum(VendorUsageRecord.resource_count), 0),
            func.coalesce(func.sum(VendorUsageRecord.cost_usd), 0.0),
            func.count(VendorUsageRecord.id),
            func.count(VendorUsageRecord.user_id),
        )
        totals_query = _apply_vendor_usage_filters(
            totals_query,
            provider=provider,
            model=model,
            feature=feature,
            user_id=parsed_user_id,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        (
            total_input_tokens,
            total_output_tokens,
            total_tokens,
            total_request_count,
            total_resource_count,
            total_cost_usd,
            total_rows,
            attributed_rows,
        ) = totals_query.one()

        usage_day = func.date(VendorUsageRecord.created_at)
        daily_query = db.query(
            usage_day.label("usage_day"),
            func.count(VendorUsageRecord.id).label("row_count"),
            func.coalesce(func.sum(VendorUsageRecord.cost_usd), 0.0).label("cost_usd"),
            func.coalesce(func.sum(VendorUsageRecord.request_count), 0).label("request_count"),
            func.coalesce(func.sum(VendorUsageRecord.resource_count), 0).label("resource_count"),
            func.coalesce(func.sum(VendorUsageRecord.total_tokens), 0).label("total_tokens"),
        )
        daily_query = _apply_vendor_usage_filters(
            daily_query,
            provider=provider,
            model=model,
            feature=feature,
            user_id=parsed_user_id,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        daily_query = daily_query.group_by(usage_day).order_by(usage_day.desc()).limit(60)

        user_totals_query = db.query(
            VendorUsageRecord.user_id.label("user_id"),
            User.email.label("email"),
            User.full_name.label("full_name"),
            func.count(VendorUsageRecord.id).label("row_count"),
            func.coalesce(func.sum(VendorUsageRecord.cost_usd), 0.0).label("cost_usd"),
            func.coalesce(func.sum(VendorUsageRecord.request_count), 0).label("request_count"),
            func.coalesce(func.sum(VendorUsageRecord.resource_count), 0).label("resource_count"),
            func.coalesce(func.sum(VendorUsageRecord.total_tokens), 0).label("total_tokens"),
        ).outerjoin(User, User.id == VendorUsageRecord.user_id)
        user_totals_query = _apply_vendor_usage_filters(
            user_totals_query,
            provider=provider,
            model=model,
            feature=feature,
            user_id=parsed_user_id,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        user_totals_query = (
            user_totals_query.group_by(
                VendorUsageRecord.user_id,
                User.email,
                User.full_name,
            )
            .order_by(
                func.coalesce(func.sum(VendorUsageRecord.cost_usd), 0.0).desc(),
                func.count(VendorUsageRecord.id).desc(),
            )
            .limit(50)
        )

        user_day_query = db.query(
            usage_day.label("usage_day"),
            VendorUsageRecord.user_id.label("user_id"),
            User.email.label("email"),
            User.full_name.label("full_name"),
            func.count(VendorUsageRecord.id).label("row_count"),
            func.coalesce(func.sum(VendorUsageRecord.cost_usd), 0.0).label("cost_usd"),
            func.coalesce(func.sum(VendorUsageRecord.request_count), 0).label("request_count"),
            func.coalesce(func.sum(VendorUsageRecord.resource_count), 0).label("resource_count"),
            func.coalesce(func.sum(VendorUsageRecord.total_tokens), 0).label("total_tokens"),
        ).outerjoin(User, User.id == VendorUsageRecord.user_id)
        user_day_query = _apply_vendor_usage_filters(
            user_day_query,
            provider=provider,
            model=model,
            feature=feature,
            user_id=parsed_user_id,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        user_day_query = (
            user_day_query.group_by(
                usage_day,
                VendorUsageRecord.user_id,
                User.email,
                User.full_name,
            )
            .order_by(
                usage_day.desc(),
                func.coalesce(func.sum(VendorUsageRecord.cost_usd), 0.0).desc(),
            )
            .limit(100)
        )

        daily_results = daily_query.all()
        user_results = user_totals_query.all()
        user_day_results = user_day_query.all()

    records = [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "provider": row.provider,
            "model": row.model,
            "feature": row.feature,
            "operation": row.operation,
            "source": row.source,
            "request_id": row.request_id,
            "task_id": row.task_id,
            "content_id": row.content_id,
            "session_id": row.session_id,
            "message_id": row.message_id,
            "user_id": row.user_id,
            "user_email": user.email if user else None,
            "user_name": user.full_name if user else None,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "total_tokens": row.total_tokens,
            "request_count": row.request_count,
            "resource_count": row.resource_count,
            "cost_usd": row.cost_usd,
            "pricing_version": row.pricing_version,
        }
        for row, user in rows
    ]
    daily_rows = [_vendor_rollup_dict(row, include_day=True) for row in daily_results]
    user_rows = [_vendor_rollup_dict(row, include_user=True) for row in user_results]
    user_day_rows = [
        _vendor_rollup_dict(row, include_day=True, include_user=True) for row in user_day_results
    ]
    return (
        records,
        {
            "row_count": int(total_rows or 0),
            "attributed_row_count": int(attributed_rows or 0),
            "input_tokens": int(total_input_tokens or 0),
            "output_tokens": int(total_output_tokens or 0),
            "total_tokens": int(total_tokens or 0),
            "request_count": int(total_request_count or 0),
            "resource_count": int(total_resource_count or 0),
            "cost_usd": round(float(total_cost_usd or 0.0), 8),
        },
        daily_rows,
        user_rows,
        user_day_rows,
    )


def _vendor_rollup_dict(
    row: Any,
    *,
    include_day: bool = False,
    include_user: bool = False,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "row_count": int(row.row_count or 0),
        "cost_usd": round(float(row.cost_usd or 0.0), 8),
        "request_count": int(row.request_count or 0),
        "resource_count": int(row.resource_count or 0),
        "total_tokens": int(row.total_tokens or 0),
    }
    if include_day:
        data["usage_day"] = str(row.usage_day)
    if include_user:
        data["user_id"] = row.user_id
        data["user_label"] = format_user_label_with_id(row.user_id, row.email, row.full_name)
    return data


def _apply_vendor_usage_filters(
    query: Any,
    *,
    provider: str | None,
    model: str | None,
    feature: str | None,
    user_id: int | None,
    start_dt: datetime | None,
    end_dt: datetime | None,
) -> Any:
    if provider:
        query = query.filter(VendorUsageRecord.provider == provider)
    if model:
        query = query.filter(VendorUsageRecord.model == model)
    if feature:
        query = query.filter(VendorUsageRecord.feature == feature)
    if user_id is not None:
        query = query.filter(VendorUsageRecord.user_id == user_id)
    if start_dt:
        query = query.filter(VendorUsageRecord.created_at >= start_dt)
    if end_dt:
        query = query.filter(VendorUsageRecord.created_at <= end_dt)
    return query


def _parse_int_filter(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_date_filter(value: str | None, *, end_of_day: bool) -> datetime | None:
    """Parse simple YYYY-MM-DD filters into UTC datetimes."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        parsed = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return parsed.astimezone(UTC)
