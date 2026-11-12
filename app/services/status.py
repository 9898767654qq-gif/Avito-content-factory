"""Listing status machine. The publisher is not allowed to skip it."""

from __future__ import annotations

from app.domain import TransitionError, utcnow
from app.labels import status_label
from app.models import ApprovalAudit, Listing, ListingStatus

ALLOWED: dict[str, set[str]] = {
    ListingStatus.DRAFT: {ListingStatus.VALIDATED},
    ListingStatus.VALIDATED: {ListingStatus.PENDING, ListingStatus.DRAFT},
    ListingStatus.PENDING: {ListingStatus.APPROVED, ListingStatus.REJECTED, ListingStatus.NEEDS_REWORK},
    ListingStatus.APPROVED: {ListingStatus.PUBLISHING},
    ListingStatus.PUBLISHING: {ListingStatus.PUBLISHED, ListingStatus.APPROVED},
    ListingStatus.PUBLISHED: set(),
    ListingStatus.REJECTED: set(),
    ListingStatus.NEEDS_REWORK: set(),
}


def audit(
    session,
    listing: Listing,
    *,
    action: str,
    actor: str,
    comment: str = "",
    from_status: str | None = None,
    to_status: str | None = None,
) -> ApprovalAudit:
    row = ApprovalAudit(
        listing_id=listing.id,
        action=action,
        actor=actor[:200],
        comment=(comment or "")[:2000],
        from_status=from_status if from_status is not None else listing.status,
        to_status=to_status if to_status is not None else listing.status,
    )
    session.add(row)
    return row


def transition(
    session,
    listing: Listing,
    new_status: str,
    *,
    actor: str,
    action: str,
    comment: str = "",
) -> None:
    current = listing.status
    if new_status not in ALLOWED.get(current, set()):
        raise TransitionError(
            f"Нельзя перевести объявление из «{status_label(current)}» в «{status_label(new_status)}»."
        )
    listing.status = new_status
    listing.updated_at = utcnow()
    audit(
        session,
        listing,
        action=action,
        actor=actor,
        comment=comment,
        from_status=current,
        to_status=new_status,
    )


def require_actor(actor: str) -> str:
    name = " ".join((actor or "").split())
    if len(name) < 2:
        raise TransitionError("Укажите, кто принимает решение: имя нужно для журнала согласования.")
    return name[:200]
