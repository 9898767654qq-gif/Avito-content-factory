"""Create listings, move them through approval, and open rework versions.

Public functions commit. Helpers only flush, so a queue task and the new
version land in one transaction.
"""

from __future__ import annotations

from sqlalchemy import select, update

from app.domain import DomainError, utcnow
from app.models import (
    Brief,
    Listing,
    ListingPhoto,
    ListingStatus,
    ReworkSuggestion,
    Task,
    TaskStatus,
    TaskType,
    ValidationRun,
)
from app.services.photos import process_photo_stub
from app.services.queue import claim_next_task
from app.services.status import audit, require_actor, transition
from app.services.validation import ValidationReport, validate_listing
from app.services.limits import TITLE_MAX_LENGTH

_LOCKED = {ListingStatus.PENDING, ListingStatus.APPROVED, ListingStatus.PUBLISHING}


def infer_focus(comment: str) -> str:
    text = (comment or "").casefold().replace("ё", "е")
    if any(marker in text for marker in ("заголов", "фото", "снимок", "картин")):
        return "title_and_main_photo"
    if any(marker in text for marker in ("описан", "текст", "характерист")):
        return "description"
    return ""


def _next_version(session, brief_id: int) -> int:
    from sqlalchemy import func

    current = session.scalar(select(func.max(Listing.version)).where(Listing.brief_id == brief_id))
    return int(current or 0) + 1


def _apply_draft(listing: Listing, draft, brief: Brief, *, focus: str, comment: str) -> None:
    listing.title = draft.title
    listing.alternative_titles = [item for item in draft.alternative_titles if item != draft.title][:3]
    listing.description = draft.description
    listing.characteristics = draft.characteristics
    listing.price = int(brief.price)
    listing.category = brief.category
    listing.region = brief.region
    listing.generation_source = draft.source
    listing.generation_note = draft.note
    listing.rework_focus = focus or ""
    listing.rework_comment = comment or ""
    listing.updated_at = utcnow()
    _sync_price_characteristic(listing)


def _sync_price_characteristic(listing: Listing) -> None:
    rows = []
    for row in listing.characteristics or []:
        copied = dict(row)
        if copied.get("name") == "Цена":
            copied["value"] = f"{int(listing.price)} ₽"
        rows.append(copied)
    listing.characteristics = rows


def store_validation(session, listing: Listing) -> ValidationReport:
    report = validate_listing(session, listing)
    session.add(
        ValidationRun(
            listing_id=listing.id,
            passed=report.ok,
            issues=report.as_dicts(),
        )
    )
    return report


def refresh_status_after_validation(session, listing: Listing, report: ValidationReport, actor: str) -> None:
    if listing.status == ListingStatus.DRAFT and report.ok:
        transition(session, listing, ListingStatus.VALIDATED, actor=actor, action="validate")
    elif listing.status == ListingStatus.VALIDATED and not report.ok:
        transition(
            session,
            listing,
            ListingStatus.DRAFT,
            actor=actor,
            action="validate",
            comment="Проверка нашла ошибки",
        )


def _copy_photos(listing: Listing, parent: Listing) -> None:
    for photo in list(parent.photos or []):
        clone = ListingPhoto(
            url=photo.url,
            kind=photo.kind,
            is_main=photo.is_main,
            cleanup_status="pending",
            processing_note="",
        )
        process_photo_stub(clone)
        listing.photos.append(clone)


def _write_brief(brief: Brief, facts) -> None:
    brief.category = facts.category
    brief.product_type = facts.product_type
    brief.target_buyer = facts.target_buyer
    brief.tone = facts.tone
    brief.price = int(facts.price)
    brief.region = facts.region
    brief.key_features = list(facts.key_features)
    brief.promotion_budget = facts.promotion_budget
    brief.extra = dict(facts.extra)
    brief.updated_at = utcnow()


def _new_listing(
    session,
    brief: Brief,
    generator,
    *,
    actor: str,
    parent: Listing | None,
    focus: str,
    comment: str,
    auto_submit: bool,
) -> Listing:
    draft = generator.generate(brief, focus=focus, comment=comment)
    if focus == "description" and parent is not None:
        draft.title = parent.title
        draft.alternative_titles = [item for item in draft.alternative_titles if item != parent.title][:3]
    session.execute(
        update(Listing)
        .where(Listing.brief_id == brief.id)
        .values(is_current=False)
        .execution_options(synchronize_session="fetch")
    )
    listing = Listing(
        brief_id=brief.id,
        parent_listing_id=parent.id if parent is not None else None,
        version=_next_version(session, brief.id),
        is_current=True,
        status=ListingStatus.DRAFT,
        title=draft.title or brief.product_type,
        alternative_titles=[],
        description="",
        characteristics=[],
        price=int(brief.price),
        category=brief.category,
        region=brief.region,
        generation_source=draft.source,
        generation_note="",
        rework_comment="",
        rework_focus="",
    )
    listing.brief = brief
    session.add(listing)
    session.flush()
    _apply_draft(listing, draft, brief, focus=focus, comment=comment)
    if parent is not None:
        _copy_photos(listing, parent)
        session.flush()
    audit(
        session,
        listing,
        action="generate",
        actor=actor,
        comment=draft.note[:500],
    )
    report = store_validation(session, listing)
    refresh_status_after_validation(session, listing, report, actor)
    if auto_submit and listing.status == ListingStatus.VALIDATED:
        transition(
            session,
            listing,
            ListingStatus.PENDING,
            actor=actor,
            action="submit",
            comment=comment,
        )
    return listing


def create_listing_from_facts(session, facts, generator, actor: str) -> Listing:
    if not facts.ok:
        raise DomainError(facts.errors[0])
    name = actor.strip() or "админка"
    brief = Brief(
        category=facts.category,
        product_type=facts.product_type,
        target_buyer=facts.target_buyer,
        tone=facts.tone,
        price=int(facts.price),
        region=facts.region,
        key_features=list(facts.key_features),
        promotion_budget=facts.promotion_budget,
        extra=dict(facts.extra),
    )
    session.add(brief)
    session.flush()
    listing = _new_listing(
        session,
        brief,
        generator,
        actor=name,
        parent=None,
        focus="",
        comment="",
        auto_submit=False,
    )
    session.commit()
    return listing


def current_listing(session, brief_id: int) -> Listing | None:
    return session.scalar(
        select(Listing)
        .where(Listing.brief_id == brief_id, Listing.is_current.is_(True))
        .order_by(Listing.id.desc())
        .limit(1)
    )


def update_brief(session, brief: Brief, facts, generator, actor: str) -> Listing:
    if not facts.ok:
        raise DomainError(facts.errors[0])
    name = require_actor(actor)
    current = current_listing(session, brief.id)
    if current is not None and current.status in _LOCKED:
        raise DomainError(
            "Эту версию нельзя пересобрать: она ждёт решения или уже стоит в публикации. "
            "Сначала верните её на доработку или дождитесь результата."
        )
    _write_brief(brief, facts)
    if current is not None and current.status in {ListingStatus.DRAFT, ListingStatus.VALIDATED}:
        return _regenerate_existing(session, current, generator, name)
    parent = current
    listing = _new_listing(
        session,
        brief,
        generator,
        actor=name,
        parent=parent,
        focus="",
        comment="Бриф изменён, собрана новая версия.",
        auto_submit=parent is not None
        and parent.status in {ListingStatus.PUBLISHED, ListingStatus.NEEDS_REWORK, ListingStatus.REJECTED},
    )
    session.commit()
    return listing


def _regenerate_existing(session, listing: Listing, generator, actor: str) -> Listing:
    if listing.status == ListingStatus.VALIDATED:
        transition(
            session,
            listing,
            ListingStatus.DRAFT,
            actor=actor,
            action="edit",
            comment="Текст собран заново из брифа",
        )
    elif listing.status != ListingStatus.DRAFT:
        raise DomainError("Эту версию нельзя перезаписать. Соберите новую.")
    draft = generator.generate(listing.brief, focus=listing.rework_focus or "", comment=listing.rework_comment or "")
    _apply_draft(listing, draft, listing.brief, focus=listing.rework_focus or "", comment=listing.rework_comment or "")
    audit(session, listing, action="generate", actor=actor, comment=draft.note[:500])
    report = store_validation(session, listing)
    refresh_status_after_validation(session, listing, report, actor)
    session.commit()
    return listing


def regenerate_listing(session, listing: Listing, generator, actor: str) -> Listing:
    name = require_actor(actor)
    if listing.status in {ListingStatus.DRAFT, ListingStatus.VALIDATED}:
        return _regenerate_existing(session, listing, generator, name)
    if listing.status in {ListingStatus.NEEDS_REWORK, ListingStatus.REJECTED, ListingStatus.PUBLISHED}:
        created = _new_listing(
            session,
            listing.brief,
            generator,
            actor=name,
            parent=listing,
            focus=listing.rework_focus or "",
            comment=listing.rework_comment or "Новая версия по запросу.",
            auto_submit=True,
        )
        session.commit()
        return created
    raise DomainError("Сейчас объявление нельзя пересобрать.")


def submit_for_approval(session, listing: Listing, actor: str) -> Listing:
    name = require_actor(actor)
    if listing.status != ListingStatus.VALIDATED:
        raise DomainError("Сначала нужна успешная проверка без ошибок.")
    report = store_validation(session, listing)
    if not report.ok:
        refresh_status_after_validation(session, listing, report, name)
        session.commit()
        raise DomainError("Проверка не пройдена, на согласование не отправлено.")
    transition(session, listing, ListingStatus.PENDING, actor=name, action="submit")
    session.commit()
    return listing


def approve_listing(session, listing: Listing, actor: str) -> Task:
    from app.services.queue import enqueue_task

    name = require_actor(actor)
    if listing.status != ListingStatus.PENDING:
        raise DomainError("Согласовать можно только объявление, которое ждёт решения.")
    report = store_validation(session, listing)
    if not report.ok:
        session.commit()
        raise DomainError("Согласование остановлено: проверка нашла ошибки.")
    transition(session, listing, ListingStatus.APPROVED, actor=name, action="approve")
    existing = session.scalar(
        select(Task).where(
            Task.type == TaskType.CREATE_LISTING,
            Task.listing_id == listing.id,
            Task.status.in_([TaskStatus.PENDING, TaskStatus.PROCESSING]),
        )
    )
    if existing is not None:
        session.commit()
        return existing
    task, _created = enqueue_task(
        session,
        type=TaskType.CREATE_LISTING,
        payload={"listing_id": listing.id},
        source="approval",
        listing_id=listing.id,
        commit=False,
    )
    session.commit()
    return task


def reject_listing(session, listing: Listing, actor: str, comment: str) -> Listing:
    name = require_actor(actor)
    if listing.status != ListingStatus.PENDING:
        raise DomainError("Отклонить можно только объявление, которое ждёт решения.")
    transition(
        session,
        listing,
        ListingStatus.REJECTED,
        actor=name,
        action="reject",
        comment=comment.strip(),
    )
    session.commit()
    return listing


def send_back(session, listing: Listing, generator, *, actor: str, comment: str) -> Listing:
    name = require_actor(actor)
    text = " ".join((comment or "").split())
    if len(text) < 3:
        raise DomainError("Напишите, что пересобрать: заголовок, фото или описание.")
    if listing.status != ListingStatus.PENDING:
        raise DomainError("Вернуть на доработку можно только объявление, которое ждёт решения.")
    focus = infer_focus(text)
    transition(
        session,
        listing,
        ListingStatus.NEEDS_REWORK,
        actor=name,
        action="request_rework",
        comment=text,
    )
    listing.rework_comment = text
    listing.rework_focus = focus
    created = _new_listing(
        session,
        listing.brief,
        generator,
        actor=name,
        parent=listing,
        focus=focus,
        comment=text,
        auto_submit=True,
    )
    session.commit()
    return created


def update_listing_text(
    session,
    listing: Listing,
    *,
    title: str,
    description: str,
    price: int,
    actor: str,
) -> Listing:
    name = require_actor(actor)
    if listing.status not in {ListingStatus.DRAFT, ListingStatus.VALIDATED}:
        raise DomainError("Текст можно править до отправки на согласование.")
    cleaned_title = " ".join(title.split())
    if len(cleaned_title) < 2:
        raise DomainError("Заголовок пустой.")
    if len(cleaned_title) > TITLE_MAX_LENGTH:
        raise DomainError(f"Заголовок длиннее {TITLE_MAX_LENGTH} символов.")
    if price <= 0:
        raise DomainError("Цена должна быть больше нуля.")
    if listing.status == ListingStatus.VALIDATED:
        transition(session, listing, ListingStatus.DRAFT, actor=name, action="edit", comment="Текст поправлен вручную")
    else:
        audit(session, listing, action="edit", actor=name, comment="Текст поправлен вручную")
    listing.title = cleaned_title
    listing.description = description.strip()
    listing.price = int(price)
    listing.updated_at = utcnow()
    _sync_price_characteristic(listing)
    report = store_validation(session, listing)
    refresh_status_after_validation(session, listing, report, name)
    session.commit()
    return listing


def use_alternative_title(session, listing: Listing, title: str, actor: str) -> Listing:
    name = require_actor(actor)
    if listing.status not in {ListingStatus.DRAFT, ListingStatus.VALIDATED}:
        raise DomainError("Другой заголовок можно выбрать до отправки на согласование.")
    chosen = " ".join(title.split())
    options = [str(item) for item in (listing.alternative_titles or [])]
    if chosen not in options:
        raise DomainError("Такого варианта заголовка нет.")
    if len(chosen) > TITLE_MAX_LENGTH:
        raise DomainError(f"Заголовок длиннее {TITLE_MAX_LENGTH} символов.")
    previous = listing.title
    if listing.status == ListingStatus.VALIDATED:
        transition(session, listing, ListingStatus.DRAFT, actor=name, action="edit", comment="Выбран другой заголовок")
    else:
        audit(session, listing, action="edit", actor=name, comment="Выбран другой заголовок")
    listing.title = chosen
    listing.alternative_titles = [previous, *[item for item in options if item != chosen]][:3]
    listing.updated_at = utcnow()
    report = store_validation(session, listing)
    refresh_status_after_validation(session, listing, report, name)
    session.commit()
    return listing


def add_photo(session, listing: Listing, *, url: str, kind: str, make_main: bool, actor: str) -> ListingPhoto:
    from app.models import PhotoKind

    name = actor.strip() or "админка"
    if kind not in {PhotoKind.REAL, PhotoKind.SYNTHETIC}:
        raise DomainError("Отметьте фото как живое или как синтетический рендер.")
    cleaned = url.strip()
    if len(cleaned) < 3:
        raise DomainError("Укажите адрес или имя файла.")
    if listing.status in {ListingStatus.PUBLISHING, ListingStatus.PUBLISHED}:
        raise DomainError("У опубликованной версии фото не меняем. Соберите новую версию.")
    if make_main or not list(listing.photos or []):
        for existing in list(listing.photos or []):
            existing.is_main = False
        make_main = True
    photo = ListingPhoto(
        url=cleaned[:1000],
        kind=kind,
        is_main=make_main,
        cleanup_status="pending",
        processing_note="",
    )
    process_photo_stub(photo)
    listing.photos.append(photo)
    listing.updated_at = utcnow()
    session.flush()
    if listing.status in {ListingStatus.DRAFT, ListingStatus.VALIDATED}:
        report = store_validation(session, listing)
        refresh_status_after_validation(session, listing, report, name)
    else:
        store_validation(session, listing)
    audit(session, listing, action="edit", actor=name, comment=f"Добавлено фото: {kind}")
    session.commit()
    return photo


def set_main_photo(session, listing: Listing, photo_id: int, actor: str) -> Listing:
    name = actor.strip() or "админка"
    if listing.status in {ListingStatus.PUBLISHING, ListingStatus.PUBLISHED}:
        raise DomainError("У опубликованной версии фото не меняем. Соберите новую версию.")
    target = None
    for photo in list(listing.photos or []):
        photo.is_main = photo.id == photo_id
        if photo.id == photo_id:
            target = photo
    if target is None:
        raise DomainError("Фото не найдено.")
    listing.updated_at = utcnow()
    if listing.status in {ListingStatus.DRAFT, ListingStatus.VALIDATED}:
        report = store_validation(session, listing)
        refresh_status_after_validation(session, listing, report, name)
    else:
        store_validation(session, listing)
    audit(session, listing, action="edit", actor=name, comment="Сменено главное фото")
    session.commit()
    return listing


def requeue_publish(session, listing: Listing) -> Task:
    from app.services.queue import enqueue_task

    if listing.status != ListingStatus.APPROVED:
        raise DomainError("В очередь публикации ставим только согласованное объявление.")
    existing = session.scalar(
        select(Task).where(
            Task.type == TaskType.CREATE_LISTING,
            Task.listing_id == listing.id,
            Task.status.in_([TaskStatus.PENDING, TaskStatus.PROCESSING]),
        )
    )
    if existing is not None:
        return existing
    task, _created = enqueue_task(
        session,
        type=TaskType.CREATE_LISTING,
        payload={"listing_id": listing.id},
        source="approval",
        listing_id=listing.id,
        commit=False,
    )
    session.commit()
    return task


def process_factory_queue(session, generator) -> bool:
    task = claim_next_task(session, [TaskType.REWORK])
    if task is None:
        return False
    try:
        source = session.get(Listing, int(task.payload["listing_id"]))
        if source is None:
            raise DomainError("Исходное объявление не найдено.")
        created = _new_listing(
            session,
            source.brief,
            generator,
            actor=str(task.payload.get("actor") or "правило метрик"),
            parent=source,
            focus=str(task.payload.get("focus") or ""),
            comment=str(task.payload.get("comment") or ""),
            auto_submit=bool(task.payload.get("auto_submit", True)),
        )
        task.status = TaskStatus.DONE
        task.result = {"new_listing_id": created.id, "status": created.status}
        task.error = None
        suggestion = session.scalar(select(ReworkSuggestion).where(ReworkSuggestion.task_id == task.id))
        if suggestion is not None:
            suggestion.new_listing_id = created.id
    except Exception as exc:
        task.status = TaskStatus.FAILED
        task.error = getattr(exc, "message", str(exc))[:1000]
    task.finished_at = utcnow()
    session.commit()
    return True
