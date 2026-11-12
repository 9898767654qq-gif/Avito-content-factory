"""HTTP contract for the external Avito chat bot.

The bot can leave a brief, enqueue a task, and write dialog events.
It cannot publish: create_listing still dies in the worker unless a person
has approved that exact version.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.domain import DomainError
from app.models import DialogEvent, Listing, Task, TaskType
from app.services.briefs import parse_brief_fields
from app.services.listings import create_listing_from_facts
from app.services.queue import enqueue_task

router = APIRouter(prefix="/api")

BOT_TASKS = {TaskType.CREATE_LISTING, TaskType.FETCH_METRICS, TaskType.REWORK}


def get_db(request: Request):
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


class BriefIn(BaseModel):
    category: str
    product_type: str
    target_buyer: str
    tone: str
    price: int
    region: str
    key_features: list[str]
    promotion_budget: int | None = None
    extra: dict[str, str] = Field(default_factory=dict)
    actor: str = "чат-бот"


class TaskIn(BaseModel):
    type: str
    payload: dict = Field(default_factory=dict)
    idempotency_key: str | None = None


class DialogIn(BaseModel):
    external_dialog_id: str
    event_type: str
    listing_id: int | None = None
    payload: dict = Field(default_factory=dict)


def listing_payload(listing: Listing, issues: list | None = None) -> dict:
    latest = listing.validations[-1] if listing.validations else None
    return {
        "id": listing.id,
        "brief_id": listing.brief_id,
        "version": listing.version,
        "status": listing.status,
        "title": listing.title,
        "alternative_titles": listing.alternative_titles,
        "description": listing.description,
        "characteristics": listing.characteristics,
        "price": listing.price,
        "category": listing.category,
        "region": listing.region,
        "generation_source": listing.generation_source,
        "avito_item_id": listing.avito_item_id,
        "avito_url": listing.avito_url,
        "dry_run": listing.dry_run,
        "validation_passed": None if latest is None else latest.passed,
        "issues": latest.issues if latest is not None else (issues or []),
    }


def task_payload(task: Task, created: bool | None = None) -> dict:
    body = {
        "id": task.id,
        "type": task.type,
        "status": task.status,
        "source": task.source,
        "listing_id": task.listing_id,
        "payload": task.payload,
        "result": task.result,
        "error": task.error,
        "attempts": task.attempts,
    }
    if created is not None:
        body["created"] = created
    return body


@router.get("/health")
def health(request: Request):
    return {
        "status": "ok",
        "service": "parnaya",
        "avito_mode": request.app.state.settings.avito_client_mode,
    }


@router.post("/bot/briefs")
def create_brief(body: BriefIn, request: Request, session=Depends(get_db)):
    facts = parse_brief_fields(
        category=body.category,
        product_type=body.product_type,
        target_buyer=body.target_buyer,
        tone=body.tone,
        price=body.price,
        region=body.region,
        key_features=body.key_features,
        promotion_budget=body.promotion_budget,
        extra=body.extra,
    )
    if not facts.ok:
        raise DomainError(" ".join(facts.errors))
    listing = create_listing_from_facts(
        session,
        facts,
        request.app.state.generator,
        body.actor.strip() or "чат-бот",
    )
    session.refresh(listing)
    return listing_payload(listing)


@router.post("/bot/tasks")
def create_task(body: TaskIn, session=Depends(get_db)):
    if body.type not in BOT_TASKS:
        raise DomainError("Неизвестный тип задачи. Доступны create_listing, fetch_avito_metrics, rework_listing.")
    listing_id = body.payload.get("listing_id")
    if listing_id is None:
        raise DomainError("В payload нужен listing_id.")
    try:
        listing_key = int(listing_id)
    except (TypeError, ValueError) as exc:
        raise DomainError("listing_id должен быть числом.") from exc
    listing = session.get(Listing, listing_key)
    if listing is None:
        raise DomainError("Объявление не найдено.")
    task, created = enqueue_task(
        session,
        type=body.type,
        payload=body.payload,
        source="chat-bot",
        listing_id=listing.id,
        idempotency_key=body.idempotency_key,
    )
    return task_payload(task, created=created)


@router.post("/bot/dialogs")
def create_dialog(body: DialogIn, session=Depends(get_db)):
    external_id = body.external_dialog_id.strip()
    event_type = body.event_type.strip()
    if not external_id or not event_type:
        raise DomainError("Нужны external_dialog_id и event_type.")
    if len(event_type) > 64 or len(external_id) > 200:
        raise DomainError("Слишком длинный идентификатор диалога или тип события.")
    if body.listing_id is not None and session.get(Listing, body.listing_id) is None:
        raise DomainError("Объявление для диалога не найдено.")
    event = DialogEvent(
        listing_id=body.listing_id,
        external_dialog_id=external_id,
        event_type=event_type,
        payload=body.payload or {},
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return {
        "id": event.id,
        "listing_id": event.listing_id,
        "external_dialog_id": event.external_dialog_id,
        "event_type": event.event_type,
    }


@router.get("/tasks/{task_id}")
def read_task(task_id: int, session=Depends(get_db)):
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return task_payload(task)


@router.get("/listings/{listing_id}")
def read_listing(listing_id: int, session=Depends(get_db)):
    listing = session.scalar(
        select(Listing).where(Listing.id == listing_id).options()
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="Объявление не найдено")
    return listing_payload(listing)
