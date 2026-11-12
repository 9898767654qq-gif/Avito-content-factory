"""Jinja environment for the admin pages."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.categories import get_category, tone_label
from app.domain import DomainError
from app.http import actor_from, format_dt, format_price
from app.labels import (
    AUDIT_LABELS,
    PHOTO_KIND_LABELS,
    SOURCE_LABELS,
    STATUS_LABELS,
    TASK_LABELS,
    TASK_STATUS_LABELS,
    status_label,
)
from app.http import FLASH, FLASH_ERROR


def category_title(slug: str) -> str:
    try:
        return get_category(slug).title
    except DomainError:
        return slug or "—"


def build_templates() -> Jinja2Templates:
    directory = Path(__file__).resolve().parent / "templates"
    templates = Jinja2Templates(directory=directory)
    templates.env.filters["price"] = format_price
    templates.env.filters["dt"] = format_dt
    templates.env.globals["status_of"] = lambda listing: status_label(listing.status, dry_run=bool(listing.dry_run))
    templates.env.globals["status_name"] = status_label
    templates.env.globals["tone_label"] = tone_label
    templates.env.globals["category_title"] = category_title
    templates.env.globals["task_label"] = lambda value: TASK_LABELS.get(value, value)
    templates.env.globals["task_status_label"] = lambda value: TASK_STATUS_LABELS.get(value, value)
    templates.env.globals["audit_label"] = lambda value: AUDIT_LABELS.get(value, value)
    templates.env.globals["photo_kind_label"] = lambda value: PHOTO_KIND_LABELS.get(value, value)
    templates.env.globals["source_label"] = lambda value: SOURCE_LABELS.get(value, value)
    templates.env.globals["STATUS_LABELS"] = STATUS_LABELS
    return templates


def page_context(request, **extra):
    flash_code = request.query_params.get("flash", "")
    error_code = request.query_params.get("error", "")
    context = {
        "approver": actor_from(request),
        "flash": FLASH.get(flash_code, ""),
        "flash_error": FLASH_ERROR.get(error_code, "") or request.query_params.get("error_text", ""),
        "path": request.url.path,
    }
    context.update(extra)
    return context
