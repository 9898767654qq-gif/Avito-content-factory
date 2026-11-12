import pytest

from app.domain import PublicationRefused, TransitionError
from app.models import ApprovalAudit, ListingStatus, TaskStatus
from app.services.avito_client import PublishResult, RealAvitoClient
from app.services.generator import TemplateListingGenerator
from app.services.listings import (
    approve_listing,
    create_listing_from_facts,
    reject_listing,
    send_back,
    submit_for_approval,
)
from app.services.publisher import process_publisher_queue, publish_approved_listing
from app.services.queue import enqueue_task
from app.services.status import transition
from tests.conftest import kupel_fields


class SpyClient:
    mode = "dry_run"

    def __init__(self):
        self.calls = []

    def publish(self, payload):
        self.calls.append(payload)
        return PublishResult(item_id=f"spy-{payload.listing_id}", url="https://example.test/item", dry_run=True)

    def fetch_stats(self, avito_item_id: str) -> dict:
        raise AssertionError("клиент статистики не должен вызываться")


def _validated(session):
    return create_listing_from_facts(session, kupel_fields(), TemplateListingGenerator(), "тест")


def test_worker_does_not_call_avito_unless_the_listing_is_approved(session):
    listing = _validated(session)
    client = SpyClient()
    for status in (
        ListingStatus.DRAFT,
        ListingStatus.VALIDATED,
        ListingStatus.PENDING,
        ListingStatus.REJECTED,
        ListingStatus.NEEDS_REWORK,
        ListingStatus.PUBLISHING,
    ):
        listing.status = status
        session.commit()
        enqueue_task(
            session,
            type="create_listing",
            payload={"listing_id": listing.id},
            source="test",
            listing_id=listing.id,
        )
        assert process_publisher_queue(session, client) is True
        session.refresh(listing)
        assert listing.status == status
        assert listing.avito_item_id is None
    assert client.calls == []


def test_direct_publish_refuses_before_the_client(session):
    listing = _validated(session)
    client = SpyClient()
    with pytest.raises(PublicationRefused, match="запрещена"):
        publish_approved_listing(session, listing.id, client)
    assert client.calls == []
    session.refresh(listing)
    assert listing.status == ListingStatus.VALIDATED


def test_approved_listing_is_published_and_the_audit_remembers_who(session):
    listing = _validated(session)
    submit_for_approval(session, listing, "Марина")
    approve_listing(session, listing, "Марина")
    session.refresh(listing)
    assert listing.status == ListingStatus.APPROVED
    client = SpyClient()
    assert process_publisher_queue(session, client) is True
    session.refresh(listing)
    assert len(client.calls) == 1
    assert client.calls[0].listing_id == listing.id
    assert listing.status == ListingStatus.PUBLISHED
    assert listing.dry_run is True
    assert listing.avito_item_id == f"spy-{listing.id}"
    audit = session.query(ApprovalAudit).filter_by(listing_id=listing.id, action="approve").one()
    assert audit.actor == "Марина"
    assert audit.created_at is not None
    assert audit.from_status == ListingStatus.PENDING
    assert audit.to_status == ListingStatus.APPROVED


def test_live_client_refuses_and_returns_the_listing_to_approved(session, settings):
    listing = _validated(session)
    submit_for_approval(session, listing, "Марина")
    approve_listing(session, listing, "Марина")
    client = RealAvitoClient(settings)
    with pytest.raises(NotImplementedError):
        publish_approved_listing(session, listing.id, client)
    session.refresh(listing)
    assert listing.status == ListingStatus.APPROVED
    assert listing.avito_item_id is None


def test_send_back_builds_a_new_version_that_still_needs_approval(session):
    listing = _validated(session)
    submit_for_approval(session, listing, "Марина")
    created = send_back(session, listing, TemplateListingGenerator(), actor="Марина", comment="Короче заголовок и другое фото")
    session.refresh(listing)
    assert listing.status == ListingStatus.NEEDS_REWORK
    assert created.id != listing.id
    assert created.status == ListingStatus.PENDING
    assert created.version == 2
    client = SpyClient()
    enqueue_task(
        session,
        type="create_listing",
        payload={"listing_id": created.id},
        source="test",
        listing_id=created.id,
    )
    process_publisher_queue(session, client)
    session.refresh(created)
    assert created.status == ListingStatus.PENDING
    assert client.calls == []


def test_rejected_listing_stays_unpublished(session):
    listing = _validated(session)
    submit_for_approval(session, listing, "Марина")
    reject_listing(session, listing, "Марина", "Не тот размер")
    client = SpyClient()
    enqueue_task(
        session,
        type="create_listing",
        payload={"listing_id": listing.id},
        source="test",
        listing_id=listing.id,
    )
    process_publisher_queue(session, client)
    session.refresh(listing)
    assert listing.status == ListingStatus.REJECTED
    assert client.calls == []
    from sqlalchemy import select

    from app.models import Task

    failed = session.scalars(select(Task).where(Task.listing_id == listing.id)).all()[-1]
    assert failed.status == TaskStatus.FAILED


def test_status_machine_blocks_skipping_approval(session):
    listing = _validated(session)
    with pytest.raises(TransitionError):
        transition(session, listing, ListingStatus.APPROVED, actor="Марина", action="approve")
    with pytest.raises(TransitionError):
        transition(session, listing, ListingStatus.PUBLISHED, actor="Марина", action="publish_succeeded")
