from sqlalchemy import select

from app.models import DialogEvent, Listing, ListingStatus, Task, TaskStatus
from app.services.avito_client import DryRunAvitoClient
from app.services.generator import TemplateListingGenerator
from app.services.listings import (
    approve_listing,
    create_listing_from_facts,
    process_factory_queue,
    submit_for_approval,
)
from app.services.metrics import assess_metrics, queue_rework_if_weak, record_metric_snapshot
from app.services.publisher import process_publisher_queue
from app.services.queue import enqueue_task
from tests.conftest import kupel_fields

FORM = {
    "category": "kupel",
    "product_type": "Купель круглая",
    "target_buyer": "Частный покупатель на участок",
    "tone": "friendly",
    "price": "96000",
    "region": "Новгородская область",
    "key_features": "диаметр 150 см\nслив в комплекте",
    "promotion_budget": "654321",
    "extra__material": "лиственница",
    "extra__shape": "круглая",
    "actor": "Марина",
}


def test_http_loop_from_brief_to_dry_run_publish(client, app):
    created = client.post("/briefs", data=FORM, follow_redirects=False)
    assert created.status_code == 303
    listing_id = int(created.headers["location"].split("/listings/")[1].split("?")[0])
    page = client.get(f"/listings/{listing_id}")
    assert page.status_code == 200
    assert "лиственница" in page.text
    assert "654321" not in page.text
    assert "Отправить на согласование" in page.text

    submitted = client.post(f"/listings/{listing_id}/submit", data={"actor": "Марина"}, follow_redirects=False)
    assert submitted.status_code == 303
    approved = client.post(f"/listings/{listing_id}/approve", data={"actor": "Марина"}, follow_redirects=False)
    assert approved.status_code == 303

    with app.state.session_factory() as session:
        assert process_publisher_queue(session, DryRunAvitoClient()) is True
        listing = session.get(Listing, listing_id)
        assert listing.status == ListingStatus.PUBLISHED
        assert listing.avito_item_id == f"dryrun-{listing_id}"
        assert listing.dry_run is True

    published = client.get(f"/listings/{listing_id}")
    assert "Опубликовано в прогоне" in published.text
    assert f"dryrun-{listing_id}" in published.text


def test_bot_can_enqueue_and_write_dialogs_but_cannot_skip_approval(client, app):
    response = client.post("/api/bot/briefs", json={
        "category": "kupel",
        "product_type": "Купель круглая",
        "target_buyer": "Подрядчик",
        "tone": "business",
        "price": 96000,
        "region": "Новгородская область",
        "key_features": ["диаметр 150 см"],
        "promotion_budget": 654321,
        "extra": {"material": "лиственница", "shape": "круглая"},
        "actor": "чат-бот",
    })
    assert response.status_code == 200
    listing_id = response.json()["id"]
    assert response.json()["status"] != ListingStatus.PUBLISHED

    task = client.post("/api/bot/tasks", json={
        "type": "create_listing",
        "payload": {"listing_id": listing_id},
        "idempotency_key": "bot-create-1",
    })
    assert task.status_code == 200
    again = client.post("/api/bot/tasks", json={
        "type": "create_listing",
        "payload": {"listing_id": listing_id},
        "idempotency_key": "bot-create-1",
    })
    assert again.json()["id"] == task.json()["id"]
    assert again.json()["created"] is False

    dialog = client.post("/api/bot/dialogs", json={
        "listing_id": listing_id,
        "external_dialog_id": "dlg-1",
        "event_type": "message",
        "payload": {"text": "Когда можете привезти?"},
    })
    assert dialog.status_code == 200
    second = client.post("/api/bot/dialogs", json={
        "listing_id": listing_id,
        "external_dialog_id": "dlg-2",
        "event_type": "contact_request",
        "payload": {},
    })
    assert second.status_code == 200

    with app.state.session_factory() as session:
        assert process_publisher_queue(session, DryRunAvitoClient()) is True
        listing = session.get(Listing, listing_id)
        assert listing.status != ListingStatus.PUBLISHED
        failed = session.get(Task, task.json()["id"])
        assert failed.status == TaskStatus.FAILED
        assert "запрещена" in (failed.error or "")
        dialogs = session.scalars(select(DialogEvent).where(DialogEvent.listing_id == listing_id)).all()
        assert len(dialogs) == 2


def test_weak_metrics_create_a_rework_that_returns_to_approval(session):
    original = create_listing_from_facts(session, kupel_fields(), TemplateListingGenerator(), "Марина")
    submit_for_approval(session, original, "Марина")
    approve_listing(session, original, "Марина")
    assert process_publisher_queue(session, DryRunAvitoClient()) is True
    session.refresh(original)
    assert original.status == ListingStatus.PUBLISHED

    session.add(DialogEvent(listing_id=original.id, external_dialog_id="dlg-1", event_type="message", payload={}))
    session.add(DialogEvent(listing_id=original.id, external_dialog_id="dlg-1", event_type="message", payload={}))
    session.commit()

    record_metric_snapshot(
        session,
        original,
        views=8,
        favorites=1,
        contact_requests=0,
        source="mock",
        scenario="low_views",
    )
    session.commit()
    from app.services.metrics import latest_metric

    metric = latest_metric(session, original.id)
    assert metric.dialogs == 1
    advice = assess_metrics(metric)
    assert advice is not None
    assert advice.focus == "title_and_main_photo"

    task = queue_rework_if_weak(session, original)
    assert task is not None
    assert process_factory_queue(session, TemplateListingGenerator()) is True
    session.refresh(task)
    assert task.status == TaskStatus.DONE
    created = session.get(Listing, task.result["new_listing_id"])
    assert created.status == ListingStatus.PENDING
    assert created.title != original.title
    assert created.id != original.id
    session.refresh(original)
    assert original.status == ListingStatus.PUBLISHED

    enqueue_task(
        session,
        type="create_listing",
        payload={"listing_id": created.id},
        source="test",
        listing_id=created.id,
    )
    assert process_publisher_queue(session, DryRunAvitoClient()) is True
    session.refresh(created)
    assert created.status == ListingStatus.PENDING
    assert created.avito_item_id is None


def test_many_views_and_few_contacts_rewrite_description_only(session):
    original = create_listing_from_facts(session, kupel_fields(tone="business"), TemplateListingGenerator(), "Марина")
    submit_for_approval(session, original, "Марина")
    approve_listing(session, original, "Марина")
    process_publisher_queue(session, DryRunAvitoClient())
    session.refresh(original)
    record_metric_snapshot(
        session,
        original,
        views=140,
        favorites=11,
        contact_requests=0,
        source="mock",
        scenario="high_views_low_contacts",
    )
    session.commit()
    task = queue_rework_if_weak(session, original)
    process_factory_queue(session, TemplateListingGenerator())
    session.refresh(task)
    created = session.get(Listing, task.result["new_listing_id"])
    assert created.title == original.title
    assert created.description != original.description
    assert created.description.startswith("Особенности:")
    assert created.status == ListingStatus.PENDING
    assert "654321" not in created.description


def test_healthy_metrics_do_not_open_a_rework(session):
    original = create_listing_from_facts(session, kupel_fields(), TemplateListingGenerator(), "Марина")
    submit_for_approval(session, original, "Марина")
    approve_listing(session, original, "Марина")
    process_publisher_queue(session, DryRunAvitoClient())
    session.refresh(original)
    record_metric_snapshot(
        session,
        original,
        views=90,
        favorites=7,
        contact_requests=6,
        source="mock",
        scenario="healthy",
    )
    session.commit()
    assert queue_rework_if_weak(session, original) is None


def test_metric_thresholds():
    from app.models import ListingMetric

    low = ListingMetric(listing_id=1, views=29, favorites=0, contact_requests=5, dialogs=0, source="mock", scenario="")
    border = ListingMetric(listing_id=1, views=30, favorites=0, contact_requests=0, dialogs=0, source="mock", scenario="")
    tired = ListingMetric(listing_id=1, views=80, favorites=3, contact_requests=1, dialogs=9, source="mock", scenario="")
    fine = ListingMetric(listing_id=1, views=80, favorites=3, contact_requests=2, dialogs=0, source="mock", scenario="")
    assert assess_metrics(low).reason_code == "low_views"
    assert assess_metrics(border) is None
    assert assess_metrics(tired).focus == "description"
    assert assess_metrics(fine) is None
