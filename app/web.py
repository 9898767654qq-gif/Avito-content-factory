"""Server-rendered admin: briefs, approval, queue, and metrics."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.categories import BUYER_PRESETS, CATEGORIES, EXAMPLES, TONES, category_payload, get_category
from app.domain import DomainError
from app.http import actor_from, redirect, safe_next, set_actor
from app.models import ApprovalAudit, Listing, ListingStatus, Task, TaskStatus
from app.services.briefs import parse_brief_form
from app.services.limits import TITLE_MAX_LENGTH
from app.services.listings import (
    add_photo,
    approve_listing,
    create_listing_from_facts,
    process_factory_queue,
    regenerate_listing,
    reject_listing,
    requeue_publish,
    send_back,
    set_main_photo,
    store_validation,
    submit_for_approval,
    update_brief,
    update_listing_text,
    use_alternative_title,
    refresh_status_after_validation,
)
from app.services.metrics import (
    HIGH_VIEWS_THRESHOLD,
    LOW_CONTACTS_THRESHOLD,
    LOW_VIEWS_THRESHOLD,
    SCENARIO_LABELS,
    SCENARIOS,
    queue_rework_if_weak,
)
from app.services.publisher import process_publisher_queue
from app.services.queue import enqueue_task
from app.templating import page_context

router = APIRouter()


def get_db(request: Request):
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def render(request: Request, name: str, status_code: int = 200, **context):
    templates = request.app.state.templates
    payload = page_context(request, **context)
    return templates.TemplateResponse(request, name, payload, status_code=status_code)


def _go(path: str, *, actor: str = "", flash: str = "", error_text: str = ""):
    response = redirect(path, flash=flash, error_text=error_text)
    if actor:
        set_actor(response, actor)
    return response


def _catch(listing_id: int, actor: str, action):
    try:
        return action()
    except DomainError as exc:
        return _go(f"/listings/{listing_id}", actor=actor, error_text=exc.message)


def load_listing(session, listing_id: int) -> Listing:
    listing = session.scalar(
        select(Listing)
        .where(Listing.id == listing_id)
        .options(
            selectinload(Listing.photos),
            selectinload(Listing.validations),
            selectinload(Listing.audits),
            selectinload(Listing.metrics),
            selectinload(Listing.brief),
        )
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="Объявление не найдено")
    return listing


def _json_script(data) -> str:
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def _brief_page(request, *, action: str, page_path: str, selected: str, facts=None, brief=None, error_messages=None):
    try:
        category = get_category(selected)
    except DomainError:
        category = CATEGORIES[0]
        selected = category.slug
    values = {
        "category": selected,
        "product_type": "",
        "target_buyer": "",
        "tone": "business",
        "price": "",
        "region": "",
        "key_features": "",
        "promotion_budget": "",
        "extra": {},
    }
    if facts is not None:
        values.update(
            {
                "category": facts.category or selected,
                "product_type": facts.product_type,
                "target_buyer": facts.target_buyer,
                "tone": facts.tone or "business",
                "price": facts.price or "",
                "region": facts.region,
                "key_features": "\n".join(facts.key_features),
                "promotion_budget": "" if facts.promotion_budget is None else facts.promotion_budget,
                "extra": facts.extra,
            }
        )
    elif brief is not None:
        values.update(
            {
                "category": brief.category,
                "product_type": brief.product_type,
                "target_buyer": brief.target_buyer,
                "tone": brief.tone,
                "price": brief.price,
                "region": brief.region,
                "key_features": "\n".join(brief.key_features or []),
                "promotion_budget": "" if brief.promotion_budget is None else brief.promotion_budget,
                "extra": brief.extra or {},
            }
        )
    return render(
        request,
        "brief_form.html",
        categories=CATEGORIES,
        tones=TONES,
        buyers=BUYER_PRESETS,
        examples=EXAMPLES,
        selected=category,
        values=values,
        errors=error_messages or [],
        form_action=action,
        page_path=page_path,
        categories_json=_json_script([category_payload(item) for item in CATEGORIES]),
        examples_json=_json_script(list(EXAMPLES)),
        initial_json=_json_script(values),
        editing=brief is not None,
    )


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session=Depends(get_db)):
    listings = session.scalars(
        select(Listing)
        .where(or_(Listing.is_current.is_(True), Listing.status == ListingStatus.PUBLISHED))
        .order_by(Listing.updated_at.desc())
    ).all()
    inbox = [item for item in listings if item.status == ListingStatus.PENDING]
    counts = {
        "inbox": len(inbox),
        "drafts": sum(1 for item in listings if item.status in {ListingStatus.DRAFT, ListingStatus.VALIDATED}),
        "published": sum(1 for item in listings if item.status == ListingStatus.PUBLISHED),
        "rework": sum(1 for item in listings if item.status == ListingStatus.NEEDS_REWORK),
    }
    queue_waiting = session.scalar(
        select(func.count(Task.id)).where(Task.status.in_([TaskStatus.PENDING, TaskStatus.PROCESSING]))
    )
    audits = session.scalars(select(ApprovalAudit).order_by(ApprovalAudit.id.desc()).limit(6)).all()
    return render(
        request,
        "dashboard.html",
        listings=listings,
        inbox=inbox,
        counts=counts,
        queue_waiting=queue_waiting or 0,
        audits=audits,
    )


@router.get("/briefs/new", response_class=HTMLResponse)
def new_brief(request: Request):
    selected = request.query_params.get("category") or CATEGORIES[0].slug
    return _brief_page(request, action="/briefs", page_path="/briefs/new", selected=selected)


@router.post("/briefs")
async def create_brief(request: Request, session=Depends(get_db)):
    form = await request.form()
    facts = parse_brief_form(form)
    actor = actor_from(request, str(form.get("actor") or ""))
    if not facts.ok:
        return _brief_page(
            request,
            action="/briefs",
            page_path="/briefs/new",
            selected=facts.category or CATEGORIES[0].slug,
            facts=facts,
            error_messages=facts.errors,
        )
    listing = create_listing_from_facts(session, facts, request.app.state.generator, actor or "админка")
    flash = "validated" if listing.status == ListingStatus.VALIDATED else "validation_failed"
    if listing.status == ListingStatus.DRAFT and flash == "validation_failed":
        flash = "validation_failed"
    elif listing.status == ListingStatus.VALIDATED:
        flash = "validated"
    else:
        flash = "created"
    return _go(f"/listings/{listing.id}", actor=actor, flash=flash)


@router.get("/briefs/{brief_id}/edit", response_class=HTMLResponse)
def edit_brief(request: Request, brief_id: int, session=Depends(get_db)):
    from app.models import Brief

    brief = session.get(Brief, brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="Бриф не найден")
    selected = request.query_params.get("category") or brief.category
    return _brief_page(
        request,
        action=f"/briefs/{brief_id}",
        page_path=f"/briefs/{brief_id}/edit",
        selected=selected,
        brief=brief,
    )


@router.post("/briefs/{brief_id}")
async def save_brief(request: Request, brief_id: int, session=Depends(get_db)):
    from app.models import Brief

    brief = session.get(Brief, brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="Бриф не найден")
    form = await request.form()
    facts = parse_brief_form(form)
    actor = actor_from(request, str(form.get("actor") or ""))
    if not facts.ok:
        return _brief_page(
            request,
            action=f"/briefs/{brief_id}",
            page_path=f"/briefs/{brief_id}/edit",
            selected=facts.category or brief.category,
            facts=facts,
            error_messages=facts.errors,
        )
    if len(actor) < 2:
        return _brief_page(
            request,
            action=f"/briefs/{brief_id}",
            page_path=f"/briefs/{brief_id}/edit",
            selected=facts.category,
            facts=facts,
            error_messages=["Укажите имя: правка брифа пишется в журнал."],
        )
    try:
        listing = update_brief(session, brief, facts, request.app.state.generator, actor)
    except DomainError as exc:
        return _brief_page(
            request,
            action=f"/briefs/{brief_id}",
            page_path=f"/briefs/{brief_id}/edit",
            selected=facts.category,
            facts=facts,
            error_messages=[exc.message],
        )
    return _go(f"/listings/{listing.id}", actor=actor, flash="regenerated")


@router.get("/listings/{listing_id}", response_class=HTMLResponse)
def listing_detail(request: Request, listing_id: int, session=Depends(get_db)):
    listing = load_listing(session, listing_id)
    versions = session.scalars(
        select(Listing).where(Listing.brief_id == listing.brief_id).order_by(Listing.version)
    ).all()
    tasks = session.scalars(
        select(Task).where(Task.listing_id == listing.id).order_by(Task.id.desc())
    ).all()
    validation = listing.validations[-1] if listing.validations else None
    issues = validation.issues if validation else []
    return render(
        request,
        "listing_detail.html",
        listing=listing,
        versions=versions,
        tasks=tasks,
        validation=validation,
        issues=issues,
        errors=[item for item in issues if item.get("severity") == "error"],
        warnings=[item for item in issues if item.get("severity") == "warning"],
        title_limit=TITLE_MAX_LENGTH,
        synthetic_photos=[photo for photo in listing.photos if photo.kind == "synthetic"],
        metric=listing.metrics[-1] if listing.metrics else None,
    )


@router.post("/listings/{listing_id}/validate")
async def validate_now(request: Request, listing_id: int, session=Depends(get_db)):
    listing = load_listing(session, listing_id)
    form = await request.form()
    actor = actor_from(request, str(form.get("actor") or "")) or "админка"
    report = store_validation(session, listing)
    if listing.status in {ListingStatus.DRAFT, ListingStatus.VALIDATED}:
        refresh_status_after_validation(session, listing, report, actor)
    session.commit()
    flash = "validated" if report.ok else "validation_failed"
    return _go(f"/listings/{listing.id}", actor=actor, flash=flash)


@router.post("/listings/{listing_id}/submit")
async def submit(request: Request, listing_id: int, session=Depends(get_db)):
    listing = load_listing(session, listing_id)
    form = await request.form()
    actor = actor_from(request, str(form.get("actor") or ""))
    return _catch(listing.id, actor, lambda: _submit(session, listing, actor))


def _submit(session, listing, actor: str):
    submit_for_approval(session, listing, actor)
    return _go(f"/listings/{listing.id}", actor=actor, flash="submitted")


@router.post("/listings/{listing_id}/approve")
async def approve(request: Request, listing_id: int, session=Depends(get_db)):
    listing = load_listing(session, listing_id)
    form = await request.form()
    actor = actor_from(request, str(form.get("actor") or ""))
    return _catch(listing.id, actor, lambda: _approve(session, listing, actor))


def _approve(session, listing, actor: str):
    approve_listing(session, listing, actor)
    return _go(f"/listings/{listing.id}", actor=actor, flash="approved")


@router.post("/listings/{listing_id}/reject")
async def reject(request: Request, listing_id: int, session=Depends(get_db)):
    listing = load_listing(session, listing_id)
    form = await request.form()
    actor = actor_from(request, str(form.get("actor") or ""))
    comment = str(form.get("comment") or "")
    return _catch(listing.id, actor, lambda: _reject(session, listing, actor, comment))


def _reject(session, listing, actor: str, comment: str):
    reject_listing(session, listing, actor, comment)
    return _go(f"/listings/{listing.id}", actor=actor, flash="rejected")


@router.post("/listings/{listing_id}/rework")
async def rework(request: Request, listing_id: int, session=Depends(get_db)):
    listing = load_listing(session, listing_id)
    form = await request.form()
    actor = actor_from(request, str(form.get("actor") or ""))
    comment = str(form.get("comment") or "")
    return _catch(listing.id, actor, lambda: _rework(request, session, listing, actor, comment))


def _rework(request, session, listing, actor: str, comment: str):
    created = send_back(session, listing, request.app.state.generator, actor=actor, comment=comment)
    return _go(f"/listings/{created.id}", actor=actor, flash="rework")


@router.post("/listings/{listing_id}/regenerate")
async def regenerate(request: Request, listing_id: int, session=Depends(get_db)):
    listing = load_listing(session, listing_id)
    form = await request.form()
    actor = actor_from(request, str(form.get("actor") or ""))
    return _catch(listing.id, actor, lambda: _regenerate(request, session, listing, actor))


def _regenerate(request, session, listing, actor: str):
    created = regenerate_listing(session, listing, request.app.state.generator, actor)
    return _go(f"/listings/{created.id}", actor=actor, flash="regenerated")


@router.post("/listings/{listing_id}/text")
async def save_text(request: Request, listing_id: int, session=Depends(get_db)):
    listing = load_listing(session, listing_id)
    form = await request.form()
    actor = actor_from(request, str(form.get("actor") or ""))
    title = str(form.get("title") or "")
    description = str(form.get("description") or "")
    raw_price = "".join(char for char in str(form.get("price") or "") if char.isdigit())
    price = int(raw_price) if raw_price else 0

    def action():
        update_listing_text(session, listing, title=title, description=description, price=price, actor=actor)
        return _go(f"/listings/{listing.id}", actor=actor, flash="text")

    return _catch(listing.id, actor, action)


@router.post("/listings/{listing_id}/title")
async def choose_title(request: Request, listing_id: int, session=Depends(get_db)):
    listing = load_listing(session, listing_id)
    form = await request.form()
    actor = actor_from(request, str(form.get("actor") or ""))
    title = str(form.get("title") or "")
    return _catch(
        listing.id,
        actor,
        lambda: _choose(session, listing, title, actor),
    )


def _choose(session, listing, title: str, actor: str):
    use_alternative_title(session, listing, title, actor)
    return _go(f"/listings/{listing.id}", actor=actor, flash="title")


@router.post("/listings/{listing_id}/photos")
async def create_photo(request: Request, listing_id: int, session=Depends(get_db)):
    listing = load_listing(session, listing_id)
    form = await request.form()
    actor = actor_from(request, str(form.get("actor") or ""))
    url = str(form.get("url") or "")
    kind = str(form.get("kind") or "")
    make_main = str(form.get("make_main") or "") == "1"

    def action():
        add_photo(session, listing, url=url, kind=kind, make_main=make_main, actor=actor or "админка")
        return _go(f"/listings/{listing.id}", actor=actor, flash="photo")

    return _catch(listing.id, actor, action)


@router.post("/listings/{listing_id}/photos/{photo_id}/main")
async def make_main(request: Request, listing_id: int, photo_id: int, session=Depends(get_db)):
    listing = load_listing(session, listing_id)
    form = await request.form()
    actor = actor_from(request, str(form.get("actor") or ""))

    def action():
        set_main_photo(session, listing, photo_id, actor or "админка")
        return _go(f"/listings/{listing.id}", actor=actor, flash="main_photo")

    return _catch(listing.id, actor, action)


@router.post("/listings/{listing_id}/queue-publish")
async def queue_publish(request: Request, listing_id: int, session=Depends(get_db)):
    listing = load_listing(session, listing_id)
    form = await request.form()
    actor = actor_from(request, str(form.get("actor") or ""))

    def action():
        requeue_publish(session, listing)
        return _go(f"/listings/{listing.id}", actor=actor, flash="queued")

    return _catch(listing.id, actor, action)


@router.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request, session=Depends(get_db)):
    tasks = session.scalars(select(Task).order_by(Task.id.desc()).limit(80)).all()
    return render(request, "queue.html", tasks=tasks, avito_mode=request.app.state.settings.avito_client_mode)


@router.post("/queue/step")
def queue_step(request: Request, session=Depends(get_db)):
    worked = process_publisher_queue(session, request.app.state.avito_client)
    return redirect("/queue", flash="step" if worked else "step_idle")


@router.get("/metrics", response_class=HTMLResponse)
def metrics_page(request: Request, session=Depends(get_db)):
    published = session.scalars(
        select(Listing)
        .where(Listing.status == ListingStatus.PUBLISHED)
        .options(selectinload(Listing.metrics))
        .order_by(Listing.published_at.desc())
    ).all()
    rows = []
    for listing in published:
        metric = listing.metrics[-1] if listing.metrics else None
        rows.append({"listing": listing, "metric": metric})
    return render(
        request,
        "metrics.html",
        rows=rows,
        scenarios=SCENARIOS,
        scenario_labels=SCENARIO_LABELS,
        low_views=LOW_VIEWS_THRESHOLD,
        high_views=HIGH_VIEWS_THRESHOLD,
        low_contacts=LOW_CONTACTS_THRESHOLD,
    )


@router.post("/metrics/collect")
async def collect_metrics(request: Request, session=Depends(get_db)):
    form = await request.form()
    raw_id = str(form.get("listing_id") or "").strip()
    scenario = str(form.get("scenario") or "healthy")
    if not raw_id.isdigit():
        return redirect("/metrics", error_text="Выберите объявление.")
    listing = session.get(Listing, int(raw_id))
    if listing is None or listing.status != ListingStatus.PUBLISHED:
        return redirect("/metrics", error_text="Метрики снимаем только с опубликованного объявления.")
    if scenario not in SCENARIOS and request.app.state.avito_client.mode != "live":
        return redirect("/metrics", error_text="Неизвестный сценарий.")
    enqueue_task(
        session,
        type="fetch_avito_metrics",
        payload={"listing_id": listing.id, "scenario": scenario},
        source="admin",
        listing_id=listing.id,
    )
    return redirect("/metrics", flash="metrics_queued")


@router.post("/metrics/evaluate")
async def evaluate_metrics(request: Request, session=Depends(get_db)):
    form = await request.form()
    raw_id = str(form.get("listing_id") or "").strip()
    if not raw_id.isdigit():
        return redirect("/metrics", error_text="Выберите объявление.")
    listing = session.get(Listing, int(raw_id))
    if listing is None:
        return redirect("/metrics", error_text="Объявление не найдено.")
    try:
        task = queue_rework_if_weak(session, listing)
    except DomainError as exc:
        return redirect(f"/listings/{listing.id}", error_text=exc.message)
    if task is None:
        return redirect(f"/listings/{listing.id}", flash="no_issue")
    for _ in range(8):
        session.refresh(task)
        if task.status in {TaskStatus.DONE, TaskStatus.FAILED}:
            break
        if not process_factory_queue(session, request.app.state.generator):
            break
    session.refresh(task)
    new_id = (task.result or {}).get("new_listing_id")
    if new_id:
        return redirect(f"/listings/{new_id}", flash="rework_queued")
    if task.status == TaskStatus.FAILED:
        return redirect(f"/listings/{listing.id}", error_text=task.error or "Доработка не собралась.")
    return redirect(f"/listings/{listing.id}", flash="rework_queued")


@router.post("/actor")
async def save_actor(request: Request):
    form = await request.form()
    actor = actor_from(request, str(form.get("actor") or ""))
    target = safe_next(str(form.get("next") or "/"))
    if len(actor) < 2:
        return redirect(target, error="actor_required")
    return _go(target, actor=actor, flash="actor_saved")


@router.get("/health")
def health_page():
    return redirect("/api/health")
