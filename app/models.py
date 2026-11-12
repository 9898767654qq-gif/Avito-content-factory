"""Shared tables for the content factory and the publisher worker."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database import Base
from app.domain import utcnow


class ListingStatus:
    DRAFT = "draft"
    VALIDATED = "validated"
    PENDING = "pending_approval"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    REJECTED = "rejected"
    NEEDS_REWORK = "needs_rework"


class TaskType:
    CREATE_LISTING = "create_listing"
    FETCH_METRICS = "fetch_avito_metrics"
    REWORK = "rework_listing"


class TaskStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class PhotoKind:
    REAL = "real"
    SYNTHETIC = "synthetic"


class Brief(Base):
    __tablename__ = "briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    product_type: Mapped[str] = mapped_column(String(200), nullable=False)
    target_buyer: Mapped[str] = mapped_column(String(200), nullable=False)
    tone: Mapped[str] = mapped_column(String(32), nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    region: Mapped[str] = mapped_column(String(200), nullable=False)
    key_features: Mapped[list] = mapped_column(JSON, nullable=False)
    promotion_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listings: Mapped[list["Listing"]] = relationship(back_populates="brief")


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("briefs.id"), nullable=False, index=True)
    parent_listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ListingStatus.DRAFT, index=True)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    alternative_titles: Mapped[list] = mapped_column(JSON, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    characteristics: Mapped[list] = mapped_column(JSON, nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    region: Mapped[str] = mapped_column(String(200), nullable=False)
    generation_source: Mapped[str] = mapped_column(String(32), nullable=False, default="template")
    generation_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rework_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rework_focus: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    avito_item_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    avito_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    brief: Mapped[Brief] = relationship(back_populates="listings")
    photos: Mapped[list["ListingPhoto"]] = relationship(
        back_populates="listing",
        cascade="all, delete-orphan",
        order_by="ListingPhoto.id",
    )
    validations: Mapped[list["ValidationRun"]] = relationship(
        back_populates="listing",
        order_by="ValidationRun.id",
    )
    audits: Mapped[list["ApprovalAudit"]] = relationship(
        back_populates="listing",
        order_by="ApprovalAudit.id",
    )
    metrics: Mapped[list["ListingMetric"]] = relationship(
        back_populates="listing",
        order_by="ListingMetric.id",
    )


class ListingPhoto(Base):
    __tablename__ = "listing_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    is_main: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cleanup_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    processing_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listing: Mapped[Listing] = relationship(back_populates="photos")


class ValidationRun(Base):
    __tablename__ = "validation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False, index=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    issues: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listing: Mapped[Listing] = relationship(back_populates="validations")


class ApprovalAudit(Base):
    __tablename__ = "approval_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    from_status: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    to_status: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listing: Mapped[Listing] = relationship(back_populates="audits")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_tasks_idempotency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=TaskStatus.PENDING, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="admin")
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DialogEvent(Base):
    __tablename__ = "dialog_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True, index=True)
    external_dialog_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ListingMetric(Base):
    __tablename__ = "listing_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False, index=True)
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    favorites: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contact_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dialogs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="mock")
    scenario: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listing: Mapped[Listing] = relationship(back_populates="metrics")


class ReworkSuggestion(Base):
    __tablename__ = "rework_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False, index=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    focus: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    new_listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
