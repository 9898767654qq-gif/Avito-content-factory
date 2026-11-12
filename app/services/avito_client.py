"""Avito client boundary.

The default implementation is a dry run: it never leaves the machine.
The live client can request an OAuth token, but it refuses to publish
until the item-create method is confirmed. Endpoint details that are
not verified are marked as assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.domain import DomainError


class AvitoClientError(DomainError):
    pass


@dataclass
class PublishPayload:
    listing_id: int
    title: str
    description: str
    price: int
    category: str
    region: str
    characteristics: list
    photos: list[dict]


@dataclass
class PublishResult:
    item_id: str
    url: str
    dry_run: bool
    raw: dict = field(default_factory=dict)


class AvitoClient(Protocol):
    mode: str

    def publish(self, payload: PublishPayload) -> PublishResult: ...

    def fetch_stats(self, avito_item_id: str) -> dict: ...


def payload_from_listing(listing) -> PublishPayload:
    return PublishPayload(
        listing_id=listing.id,
        title=listing.title,
        description=listing.description,
        price=int(listing.price),
        category=listing.category,
        region=listing.region,
        characteristics=list(listing.characteristics or []),
        photos=[
            {"url": photo.url, "kind": photo.kind, "is_main": bool(photo.is_main)}
            for photo in (listing.photos or [])
        ],
    )


class DryRunAvitoClient:
    """Default publisher. Records a fake item id and does not call Avito."""

    mode = "dry_run"

    def publish(self, payload: PublishPayload) -> PublishResult:
        item_id = f"dryrun-{payload.listing_id}"
        return PublishResult(
            item_id=item_id,
            url=f"https://www.avito.ru/dry-run/items/{item_id}",
            dry_run=True,
            raw={"mode": "dry_run"},
        )

    def fetch_stats(self, avito_item_id: str) -> dict:
        raise AvitoClientError(
            "В прогоне статистика Авито не запрашивается. Используйте сценарий мок-метрик."
        )


class RealAvitoClient:
    """Skeleton of the official client.

    Token request follows the public client-credentials shape, but it is still
    an assumption until checked against the current Avito docs for this account.
    Publishing and stats are intentionally not implemented: guessing the item
    endpoint would be worse than a loud refusal.
    """

    mode = "live"

    def __init__(self, settings):
        self.settings = settings

    def request_token(self) -> dict:
        """Request an application token.

        ASSUMPTION, confirm before production. Public Avito OAuth examples use:
          POST {AVITO_TOKEN_URL}  (default https://api.avito.ru/token)
          Content-Type: application/x-www-form-urlencoded
          grant_type=client_credentials
          client_id, client_secret
        The response is expected to contain access_token.
        This method is not called by publish().
        """
        if not self.settings.avito_client_id or not self.settings.avito_client_secret:
            raise AvitoClientError("Не заданы AVITO_CLIENT_ID и AVITO_CLIENT_SECRET.")
        response = httpx.post(
            self.settings.avito_token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.settings.avito_client_id,
                "client_secret": self.settings.avito_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20.0,
        )
        response.raise_for_status()
        payload = response.json()
        if "access_token" not in payload:
            raise AvitoClientError(
                "В ответе токена нет access_token. Сверьте URL и поля с актуальной документацией Avito."
            )
        return payload

    def publish(self, payload: PublishPayload) -> PublishResult:
        # TODO: confirm the publish path for this account before writing a body.
        # It may be Autoload (XML feed or the autoload API) rather than a REST
        # "create item" call. Do not invent the path, query, or payload here.
        # When the method is confirmed, call request_token() and then that method.
        raise NotImplementedError(
            "Боевая публикация не реализована: метод создания объявления "
            "нужно подтвердить по документации Avito (автозагрузка или API объявлений)."
        )

    def fetch_stats(self, avito_item_id: str) -> dict:
        # TODO: confirm the stats method that returns views, favorites, and contacts.
        # Do not guess the path. Wire request_token() only together with that method.
        raise NotImplementedError(
            "Сбор статистики Avito не реализован: уточните актуальный метод "
            "просмотров, избранного и запросов контактов."
        )


def build_avito_client(settings) -> AvitoClient:
    if settings.avito_client_mode == "live":
        return RealAvitoClient(settings)
    return DryRunAvitoClient()
