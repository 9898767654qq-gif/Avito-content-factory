"""Parse a brief from the admin form or the bot API. No listing text here."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.categories import TONES, get_category
from app.domain import DomainError

_MAX_PRICE = 50_000_000
_MAX_BUDGET = 10_000_000


@dataclass
class BriefFacts:
    category: str
    product_type: str
    target_buyer: str
    tone: str
    price: int
    region: str
    key_features: list[str]
    promotion_budget: int | None
    extra: dict[str, str]
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _features(value) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        items = [line.strip() for line in str(value or "").splitlines()]
    return [item for item in items if item]


def _money(value) -> int | None:
    digits = re.sub(r"[^\d]", "", str(value or ""))
    if not digits:
        return None
    return int(digits)


def extras_from_form(form) -> dict[str, str]:
    extra: dict[str, str] = {}
    for key in form.keys():
        if not str(key).startswith("extra__"):
            continue
        extra[str(key).removeprefix("extra__")] = str(form.get(key) or "")
    return extra


def parse_brief_fields(
    *,
    category: str | None,
    product_type: str | None,
    target_buyer: str | None,
    tone: str | None,
    price,
    region: str | None,
    key_features,
    promotion_budget,
    extra: dict | None,
) -> BriefFacts:
    errors: list[str] = []
    category_slug = _clean(category)
    template = None
    try:
        template = get_category(category_slug)
    except DomainError:
        errors.append("Выберите категорию из списка банных товаров.")

    product = str(product_type or "").strip()
    if len(product) < 2:
        errors.append("Напишите, что продаёте.")
    elif len(product) > 120:
        errors.append("Название товара длиннее 120 символов.")

    buyer = _clean(target_buyer)
    if len(buyer) < 2:
        errors.append("Укажите, для кого объявление.")
    elif len(buyer) > 160:
        errors.append("Поле «для кого» длиннее 160 символов.")

    tone_key = _clean(tone)
    known_tones = {key for key, _label in TONES}
    if tone_key not in known_tones:
        errors.append("Выберите тон: деловой, дружелюбный или экспертный.")

    amount = _money(price)
    if amount is None or amount <= 0 or amount > _MAX_PRICE:
        errors.append("Укажите цену в рублях, больше нуля.")
        amount = amount or 0

    place = _clean(region)
    if len(place) < 2:
        errors.append("Укажите регион.")
    elif len(place) > 160:
        errors.append("Регион длиннее 160 символов.")

    features = _features(key_features)
    if not features:
        errors.append("Добавьте хотя бы одну особенность, каждую с новой строки.")
    elif len(features) > 8:
        errors.append("Особенностей может быть не больше восьми.")
    else:
        for feature in features:
            if len(feature) > 120:
                errors.append("Каждая особенность — не длиннее 120 символов.")
                break

    budget_raw = str(promotion_budget or "").strip()
    budget: int | None
    if not budget_raw:
        budget = None
    else:
        budget = _money(budget_raw)
        if budget is None or budget < 0 or budget > _MAX_BUDGET:
            errors.append("Бюджет продвижения укажите числом в рублях или оставьте пустым.")
            budget = None

    cleaned_extra: dict[str, str] = {}
    raw_extra = extra or {}
    if template is None:
        cleaned_extra = {str(key): str(value).strip() for key, value in raw_extra.items() if str(value).strip()}
    else:
        allowed = {spec.key: spec for spec in template.fields}
        for key, value in raw_extra.items():
            text = str(value).strip()
            if key not in allowed or not text:
                continue
            if len(text) > 200:
                errors.append(f"Поле «{allowed[key].label}» длиннее 200 символов.")
                continue
            cleaned_extra[key] = text
        for spec in template.fields:
            if spec.required and not cleaned_extra.get(spec.key):
                errors.append(f"Заполните поле «{spec.label}».")

    return BriefFacts(
        category=category_slug,
        product_type=product,
        target_buyer=buyer,
        tone=tone_key,
        price=int(amount or 0),
        region=place,
        key_features=features,
        promotion_budget=budget,
        extra=cleaned_extra,
        errors=errors,
    )


def parse_brief_form(form) -> BriefFacts:
    return parse_brief_fields(
        category=form.get("category"),
        product_type=form.get("product_type"),
        target_buyer=form.get("target_buyer"),
        tone=form.get("tone"),
        price=form.get("price"),
        region=form.get("region"),
        key_features=form.get("key_features"),
        promotion_budget=form.get("promotion_budget"),
        extra=extras_from_form(form),
    )


def require_facts(facts: BriefFacts) -> BriefFacts:
    if not facts.ok:
        raise DomainError(facts.errors[0])
    return facts
