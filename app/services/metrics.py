"""Metric snapshots and the weak-listing rule.

Views, favorites, and contact requests come from Avito (mocked in this
version). Dialogs come from events the external chat bot writes here.
Favorites are stored and shown; the rule below does not use them yet.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from app.domain import DomainError
from app.models import DialogEvent, Listing, ListingMetric, ListingStatus, ReworkSuggestion, Task, TaskStatus, TaskType
from app.services.queue import enqueue_task

# Tunable, deliberately simple. A listing under 30 views needs a new title
# and main photo. A listing with 80+ views and at most 1 contact request
# needs a new description.
LOW_VIEWS_THRESHOLD = 30
HIGH_VIEWS_THRESHOLD = 80
LOW_CONTACTS_THRESHOLD = 1

SCENARIOS: dict[str, dict[str, int]] = {
    "low_views": {"views": 8, "favorites": 1, "contact_requests": 0},
    "high_views_low_contacts": {"views": 140, "favorites": 11, "contact_requests": 0},
    "healthy": {"views": 90, "favorites": 7, "contact_requests": 6},
}

SCENARIO_LABELS = {
    "low_views": "Мало просмотров",
    "high_views_low_contacts": "Много просмотров, мало обращений",
    "healthy": "Нормальная карточка",
}


@dataclass(frozen=True)
class WeakListingAdvice:
    reason_code: str
    focus: str
    recommendation: str


def count_dialogs(session, listing_id: int) -> int:
    value = session.scalar(
        select(func.count(func.distinct(DialogEvent.external_dialog_id))).where(
            DialogEvent.listing_id == listing_id
        )
    )
    return int(value or 0)


def latest_metric(session, listing_id: int) -> ListingMetric | None:
    return session.scalar(
        select(ListingMetric)
        .where(ListingMetric.listing_id == listing_id)
        .order_by(ListingMetric.id.desc())
        .limit(1)
    )


def assess_metrics(metric: ListingMetric) -> WeakListingAdvice | None:
    if metric.views < LOW_VIEWS_THRESHOLD:
        return WeakListingAdvice(
            reason_code="low_views",
            focus="title_and_main_photo",
            recommendation=(
                f"Мало просмотров ({metric.views}). "
                "Нужна новая версия: другой заголовок и другое главное фото."
            ),
        )
    if metric.views >= HIGH_VIEWS_THRESHOLD and metric.contact_requests <= LOW_CONTACTS_THRESHOLD:
        return WeakListingAdvice(
            reason_code="views_without_contacts",
            focus="description",
            recommendation=(
                f"Просмотров {metric.views}, запросов контакта {metric.contact_requests}. "
                "Заголовок уже смотрят — пересоберите описание из тех же фактов."
            ),
        )
    return None


def record_metric_snapshot(
    session,
    listing: Listing,
    *,
    views: int,
    favorites: int,
    contact_requests: int,
    source: str,
    scenario: str,
) -> ListingMetric:
    metric = ListingMetric(
        listing_id=listing.id,
        views=int(views),
        favorites=int(favorites),
        contact_requests=int(contact_requests),
        dialogs=count_dialogs(session, listing.id),
        source=source,
        scenario=scenario,
    )
    session.add(metric)
    session.flush()
    return metric


def collect_metrics_task(session, task: Task, client) -> dict:
    listing_id = int(task.payload["listing_id"])
    listing = session.get(Listing, listing_id)
    if listing is None:
        raise DomainError("Объявление не найдено.")
    if listing.status != ListingStatus.PUBLISHED:
        raise DomainError("Метрики снимаем только с опубликованного объявления.")
    scenario = str(task.payload.get("scenario") or "healthy")
    if client.mode == "live":
        stats = client.fetch_stats(listing.avito_item_id or "")
        source = "avito"
        scenario_name = "live"
    else:
        if scenario not in SCENARIOS:
            raise DomainError(
                "Неизвестный сценарий метрик. Доступны: low_views, high_views_low_contacts, healthy."
            )
        stats = SCENARIOS[scenario]
        source = "mock"
        scenario_name = scenario
    metric = record_metric_snapshot(
        session,
        listing,
        views=stats["views"],
        favorites=stats["favorites"],
        contact_requests=stats["contact_requests"],
        source=source,
        scenario=scenario_name,
    )
    return {
        "metric_id": metric.id,
        "listing_id": listing.id,
        "views": metric.views,
        "favorites": metric.favorites,
        "contact_requests": metric.contact_requests,
        "dialogs": metric.dialogs,
        "source": source,
        "scenario": scenario_name,
    }


def queue_rework_if_weak(session, listing: Listing) -> Task | None:
    metric = latest_metric(session, listing.id)
    if metric is None:
        raise DomainError("Сначала снимите метрики — правила не из чего считать.")
    advice = assess_metrics(metric)
    if advice is None:
        return None
    open_task = session.scalar(
        select(Task).where(
            Task.type == TaskType.REWORK,
            Task.listing_id == listing.id,
            Task.status.in_([TaskStatus.PENDING, TaskStatus.PROCESSING]),
        )
    )
    if open_task is not None:
        return open_task
    child = session.scalar(
        select(Listing)
        .where(
            Listing.parent_listing_id == listing.id,
            Listing.status.notin_([ListingStatus.REJECTED, ListingStatus.PUBLISHED]),
        )
        .order_by(Listing.id.desc())
        .limit(1)
    )
    if child is not None:
        raise DomainError(f"Уже есть новая версия №{child.id}. Она снова проходит согласование.")
    task, _created = enqueue_task(
        session,
        type=TaskType.REWORK,
        payload={
            "listing_id": listing.id,
            "focus": advice.focus,
            "comment": advice.recommendation,
            "reason_code": advice.reason_code,
            "auto_submit": True,
            "actor": "правило метрик",
        },
        source="metrics",
        listing_id=listing.id,
        commit=False,
    )
    session.add(
        ReworkSuggestion(
            listing_id=listing.id,
            reason_code=advice.reason_code,
            recommendation=advice.recommendation,
            focus=advice.focus,
            task_id=task.id,
        )
    )
    session.commit()
    session.refresh(task)
    return task
