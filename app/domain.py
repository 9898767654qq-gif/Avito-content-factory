"""Shared errors. User-facing messages are Russian."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DomainError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TransitionError(DomainError):
    pass


class PublicationRefused(DomainError):
    """The publisher worker must raise this instead of calling Avito."""
