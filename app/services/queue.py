"""Database-backed task queue shared by the factory and the publisher."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.domain import utcnow
from app.models import Task, TaskStatus


def enqueue_task(
    session,
    *,
    type: str,
    payload: dict,
    source: str,
    listing_id: int | None = None,
    idempotency_key: str | None = None,
    commit: bool = True,
) -> tuple[Task, bool]:
    key = (idempotency_key or "").strip() or None
    if key:
        existing = session.scalar(select(Task).where(Task.idempotency_key == key))
        if existing is not None:
            return existing, False
    task = Task(
        type=type,
        status=TaskStatus.PENDING,
        payload=payload,
        source=source,
        listing_id=listing_id,
        idempotency_key=key,
        attempts=0,
    )
    session.add(task)
    if not commit:
        session.flush()
        return task, True
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if key:
            existing = session.scalar(select(Task).where(Task.idempotency_key == key))
            if existing is not None:
                return existing, False
        raise
    session.refresh(task)
    return task, True


def claim_next_task(session, types: list[str]) -> Task | None:
    """Move the oldest pending task of the given types to processing.

    The conditional update is the lock: a second worker sees rowcount 0.
    """
    task = session.scalar(
        select(Task)
        .where(Task.status == TaskStatus.PENDING, Task.type.in_(types))
        .order_by(Task.id)
        .limit(1)
    )
    if task is None:
        return None
    now = utcnow()
    updated = session.execute(
        update(Task)
        .where(Task.id == task.id, Task.status == TaskStatus.PENDING)
        .values(status=TaskStatus.PROCESSING, started_at=now, attempts=Task.attempts + 1)
    )
    session.commit()
    if updated.rowcount != 1:
        return None
    task.status = TaskStatus.PROCESSING
    task.started_at = now
    task.attempts = (task.attempts or 0) + 1
    return task
