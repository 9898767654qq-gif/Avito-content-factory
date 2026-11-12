"""Checks that run before a person is asked to approve a listing."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from sqlalchemy import select

from app.categories import get_category
from app.models import Listing, ListingStatus, PhotoKind
from app.services.limits import DESCRIPTION_MAX_LENGTH, DESCRIPTION_MIN_WARNING, TITLE_MAX_LENGTH
from app.services.photos import main_photo

_STOP_PATTERNS = [
    r"только сегодня",
    r"успей(?:те)?",
    r"звоните",
    r"пишите",
    r"whats?\s*app",
    r"ватсап",
    r"вотсап",
    r"телеграм",
    r"telegram",
    r"бесплатн\w*",
    r"дешев\w*",
    r"\bакци[яи]\b",
    r"скидк\w*",
    r"гарантия\s*100",
    r"лучш\w*\s+цен\w*",
    r"без обмана",
    r"\bсрочно\b",
    r"номер\s+один",
]
_STOP_RE = [re.compile(pattern, re.IGNORECASE) for pattern in _STOP_PATTERNS]
_PHONE_RE = re.compile(
    r"(?:(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2})"
    r"|(?:\b\d{3}[\s\-]\d{3}[\s\-]\d{2}[\s\-]\d{2}\b)"
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}")
_LINK_RE = re.compile(r"(https?://|www\.|t\.me/|wa\.me/|telegram\.me/)", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"\b[\w.-]+\.(?:ru|com|net|org|su|рф)\b", re.IGNORECASE)

_FIELD_LABELS = {
    "title": "заголовок",
    "description": "описание",
    "price": "цена",
    "region": "регион",
    "category": "категория",
}


@dataclass
class Issue:
    code: str
    severity: str
    field: str
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationReport:
    issues: list[Issue]

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    def as_dicts(self) -> list[dict]:
        return [issue.as_dict() for issue in self.issues]


def _fold(text: str) -> str:
    return (text or "").replace("ё", "е").replace("Ё", "Е")


def normalize_text(value: str) -> str:
    folded = _fold(value).casefold()
    folded = re.sub(r"[^\w\s]", " ", folded, flags=re.UNICODE)
    return " ".join(folded.split())


def _tokens(value: str) -> set[str]:
    return {token for token in normalize_text(value).split() if len(token) > 1}


def collected_text(listing: Listing) -> dict[str, str]:
    chunks = {
        "title": listing.title or "",
        "description": listing.description or "",
    }
    for index, alternative in enumerate(listing.alternative_titles or []):
        chunks[f"alternative_{index}"] = str(alternative)
    for index, row in enumerate(listing.characteristics or []):
        name = str(row.get("name", ""))
        value = str(row.get("value", ""))
        chunks[f"characteristic_{index}"] = f"{name}: {value}"
    return chunks


def _field_label(field: str) -> str:
    if field in _FIELD_LABELS:
        return _FIELD_LABELS[field]
    if field.startswith("alternative"):
        return "альтернативный заголовок"
    if field.startswith("characteristic"):
        return "характеристика"
    return field


def validate_listing(session, listing: Listing) -> ValidationReport:
    issues: list[Issue] = []

    def error(code: str, field: str, message: str) -> None:
        issues.append(Issue(code=code, severity="error", field=field, message=message))

    def warning(code: str, field: str, message: str) -> None:
        issues.append(Issue(code=code, severity="warning", field=field, message=message))

    title = (listing.title or "").strip()
    description = (listing.description or "").strip()
    if not title:
        error("title_required", "title", "Нужен заголовок.")
    elif len(title) > TITLE_MAX_LENGTH:
        error(
            "title_length",
            "title",
            f"Заголовок длиннее {TITLE_MAX_LENGTH} символов ({len(title)}). Так Авито его обычно не принимает.",
        )
    if not description:
        error("description_required", "description", "Нужно описание.")
    elif len(description) > DESCRIPTION_MAX_LENGTH:
        error(
            "description_length",
            "description",
            f"Описание длиннее {DESCRIPTION_MAX_LENGTH} символов.",
        )
    elif len(description) < DESCRIPTION_MIN_WARNING:
        warning("description_short", "description", "Описание совсем короткое, покупателю может не хватить фактов.")

    for index, alternative in enumerate(listing.alternative_titles or []):
        if len(str(alternative)) > TITLE_MAX_LENGTH:
            error(
                "title_length",
                f"alternative_{index}",
                f"Альтернативный заголовок длиннее {TITLE_MAX_LENGTH} символов.",
            )

    if not listing.region or not str(listing.region).strip():
        error("region_required", "region", "Укажите регион.")
    if not listing.category:
        error("category_required", "category", "Укажите категорию.")
    if listing.price is None or int(listing.price) <= 0:
        error("price_required", "price", "Цена должна быть больше нуля.")

    try:
        category = get_category(listing.category)
    except Exception:
        category = None
        error("category_unknown", "category", "Категория не из банного списка.")
    if category is not None and listing.brief is not None:
        extra = listing.brief.extra or {}
        for spec in category.fields:
            if spec.required and not str(extra.get(spec.key, "")).strip():
                error(
                    "required_field",
                    spec.key,
                    f"Для категории «{category.title}» заполните «{spec.label}».",
                )

    for field, text in collected_text(listing).items():
        folded = _fold(text)
        label = _field_label(field)
        if _PHONE_RE.search(text):
            error("phone", field, f"В поле «{label}» найден телефон. В текст объявления его не кладём.")
        if _EMAIL_RE.search(text):
            error("email", field, f"В поле «{label}» найдена почта. Уберите контакт из текста.")
        if _LINK_RE.search(text) or _DOMAIN_RE.search(text):
            error("link", field, f"В поле «{label}» найдена ссылка или домен.")
        for pattern in _STOP_RE:
            if pattern.search(folded):
                error(
                    "stop_word",
                    field,
                    f"В поле «{label}» есть рекламный шаблон. Оставьте факты, без призывов, скидок и «срочно».",
                )
                break
        if text.count("!") >= 3:
            error("spam_punctuation", field, f"В поле «{label}» слишком много восклицательных знаков.")
        letters = [char for char in text if char.isalpha()]
        uppers = [char for char in letters if char.isupper()]
        if len(letters) > 20 and len(uppers) / len(letters) > 0.6:
            warning("caps", field, f"Поле «{label}» почти целиком из заглавных букв.")

    _duplicate_issues(session, listing, error, warning)
    _photo_issues(session, listing, warning)
    return ValidationReport(issues=issues)


def _duplicate_issues(session, listing: Listing, error, warning) -> None:
    if not listing.title or not listing.category:
        return
    related: set[int] = set()
    if listing.id:
        related.add(listing.id)
    if listing.brief_id:
        sibling_ids = session.scalars(select(Listing.id).where(Listing.brief_id == listing.brief_id)).all()
        related.update(sibling_ids)
    others = session.scalars(
        select(Listing).where(
            Listing.category == listing.category,
            Listing.status != ListingStatus.REJECTED,
        )
    ).all()
    own = normalize_text(listing.title)
    own_tokens = _tokens(listing.title)
    for other in others:
        if other.id in related:
            continue
        if normalize_text(other.title) == own and int(other.price) == int(listing.price):
            error(
                "duplicate",
                "title",
                f"Такое объявление уже есть: «{other.title}», версия {other.version}, №{other.id}.",
            )
            return
        other_tokens = _tokens(other.title)
        if not own_tokens or not other_tokens:
            continue
        overlap = len(own_tokens & other_tokens) / len(own_tokens | other_tokens)
        if overlap >= 0.75:
            warning(
                "similar",
                "title",
                f"Заголовок похож на объявление №{other.id} «{other.title}». Проверьте, что это не повтор.",
            )
            return


def _photo_issues(session, listing: Listing, warning) -> None:
    photos = list(listing.photos or [])
    if not photos:
        warning("no_photos", "photos", "Фотографий нет. На Авито без кадра объявление слабее.")
        return
    if all(photo.kind == PhotoKind.SYNTHETIC for photo in photos):
        warning(
            "synthetic_only",
            "photos",
            "В объявлении только синтетические рендеры. Живого фото нет, согласующий должен это видеть.",
        )
    main = main_photo(photos)
    if main is not None and main.kind == PhotoKind.SYNTHETIC:
        warning(
            "synthetic_main",
            "photos",
            "Главное фото помечено как синтетический рендер. Его нельзя выдавать за снятый товар.",
        )
    if listing.rework_focus == "title_and_main_photo" and listing.parent_listing_id and main is not None:
        parent = session.get(Listing, listing.parent_listing_id)
        if parent is not None:
            previous = main_photo(list(parent.photos or []))
            if previous is not None and previous.url == main.url:
                warning(
                    "main_photo_unchanged",
                    "photos",
                    "Главное фото не менялось. Для этой доработки нужен другой первый кадр.",
                )
