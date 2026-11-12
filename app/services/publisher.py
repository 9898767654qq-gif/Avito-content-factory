"""Publisher worker logic.

The Avito client is called only after the listing status is exactly approved.
Anything else fails the queue task and leaves the listing unpublished.
"""

from __future__ import annotations

from app.domain import DomainError, PublicationRefused, utcnow
from app.labels import status_label
from app.models import Listing, ListingStatus, TaskStatus, TaskType
from app.services.avito_client import payload_from_listing
from app.services.metrics import collect_metrics_task
from app.services.queue import claim_next_task
from app.services.status import transition

PUBLISHER_TASKS = [TaskType.CREATE_LISTING, TaskType.FETCH_METRICS]


def publish_approved_listing(session, listing_id: int, client, *, actor: str = "служба публикации") -> dict:
    listing = session.get(Listing, listing_id)
    if listing is None:
        raise PublicationRefused("Объявление не найдено.")
    if listing.status == ListingStatus.PUBLISHED:
        raise PublicationRefused("Объявление уже опубликовано.")
    if listing.status != ListingStatus.APPROVED:
        raise PublicationRefused(
            "Публикация запрещена: объявление не согласовано. "
            f"Сейчас статус «{status_label(listing.status)}», нужен статус «Согласовано»."
        )
    transition(
        session,
        listing,
        ListingStatus.PUBLISHING,
        actor=actor,
        action="publish_started",
    )
    session.commit()
    try:
        result = client.publish(payload_from_listing(listing))
    except Exception as exc:
        failed = session.get(Listing, listing_id)
        if failed is not None and failed.status == ListingStatus.PUBLISHING:
            transition(
                session,
                failed,
                ListingStatus.APPROVED,
                actor=actor,
                action="publish_failed",
                comment=str(exc)[:500],
            )
            session.commit()
        raise
    listing.avito_item_id = result.item_id
    listing.avito_url = result.url
    listing.dry_run = bool(result.dry_run)
    listing.published_at = utcnow()
    transition(
        session,
        listing,
        ListingStatus.PUBLISHED,
        actor=actor,
        action="publish_succeeded",
        comment=result.item_id,
    )
    session.commit()
    return {
        "listing_id": listing.id,
        "avito_item_id": result.item_id,
        "url": result.url,
        "dry_run": bool(result.dry_run),
    }


def process_publisher_queue(session, client) -> bool:
    task = claim_next_task(session, PUBLISHER_TASKS)
    if task is None:
        return False
    try:
        if task.type == TaskType.CREATE_LISTING:
            result = publish_approved_listing(session, int(task.payload["listing_id"]), client)
        elif task.type == TaskType.FETCH_METRICS:
            result = collect_metrics_task(session, task, client)
        else:
            raise DomainError(f"Служба публикации не берёт задачу «{task.type}».")
        task.status = TaskStatus.DONE
        task.result = result
        task.error = None
    except (PublicationRefused, DomainError) as exc:
        task.status = TaskStatus.FAILED
        task.error = exc.message
        task.result = {"refused": isinstance(exc, PublicationRefused)}
    except Exception as exc:
        task.status = TaskStatus.FAILED
        task.error = str(exc)[:1000]
        task.result = {"refused": False}
    task.finished_at = utcnow()
    session.commit()
    return True
