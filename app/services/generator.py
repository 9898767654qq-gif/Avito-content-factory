"""Draft generator.

The template path is deterministic and uses only brief facts. The LLM path
may suggest titles and a description, but every line is checked against the
brief. Characteristics are always rebuilt from the brief, never from the model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.categories import CategoryTemplate, get_category
from app.services.limits import TITLE_MAX_LENGTH

# Connective words the factory itself is allowed to add. They carry no product facts.
_GLUE_WORDS = {
    "регион",
    "особенности",
    "характеристики",
    "подойдет",
    "покупатель",
    "размещения",
    "можно",
    "посмотреть",
    "цена",
    "указана",
    "версия",
    "факты",
    "смотрите",
    "кому",
    "кого",
    "этот",
    "эта",
    "это",
    "только",
    "что",
    "как",
    "или",
    "при",
    "без",
    "под",
    "над",
    "для",
    "товар",
}


@dataclass
class GeneratedDraft:
    title: str
    alternative_titles: list[str]
    description: str
    characteristics: list[dict[str, str]]
    source: str
    note: str = ""
    warnings: list[str] = field(default_factory=list)


class DraftGenerator(Protocol):
    def generate(self, brief, *, focus: str = "", comment: str = "") -> GeneratedDraft: ...


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


def clip_title(text: str, limit: int = TITLE_MAX_LENGTH) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed[:limit].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    cut = cut.rstrip(" ,.—–-:;/|·")
    if not cut:
        cut = collapsed[:limit].rstrip()
    return cut


def brief_corpus(brief, category: CategoryTemplate) -> str:
    parts = [
        brief.product_type,
        brief.region,
        brief.target_buyer,
        str(brief.price),
        category.title,
        category.lead,
        *[item.label for item in category.fields],
        *[str(value) for value in (brief.extra or {}).values()],
        *[str(item) for item in brief.key_features or []],
    ]
    return " ".join(parts).casefold().replace("ё", "е")


def brief_numbers(brief) -> set[str]:
    parts = [
        brief.product_type,
        brief.region,
        brief.target_buyer,
        str(brief.price),
        *[str(value) for value in (brief.extra or {}).values()],
        *[str(item) for item in brief.key_features or []],
    ]
    found: set[str] = set()
    for part in parts:
        found.update(re.findall(r"\d+", str(part)))
    return found


def ungrounded_words(text: str, corpus: str) -> list[str]:
    bad: list[str] = []
    for word in re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", text or ""):
        token = word.casefold().replace("ё", "е")
        if token in _GLUE_WORDS:
            continue
        if token not in corpus:
            bad.append(word)
    return bad


def text_is_grounded(text: str, corpus: str, allowed_numbers: set[str]) -> bool:
    if ungrounded_words(text, corpus):
        return False
    return all(number in allowed_numbers for number in re.findall(r"\d+", text or ""))


def characteristics_from_brief(brief, category: CategoryTemplate) -> list[dict[str, str]]:
    rows = [
        {"name": "Товар", "value": brief.product_type.strip()},
        {"name": "Регион", "value": brief.region.strip()},
        {"name": "Для кого", "value": brief.target_buyer.strip()},
        {"name": "Цена", "value": f"{int(brief.price)} ₽"},
    ]
    extra = brief.extra or {}
    for spec in category.fields:
        value = str(extra.get(spec.key, "")).strip()
        if value:
            rows.append({"name": spec.label, "value": value})
    features = [str(item).strip() for item in (brief.key_features or []) if str(item).strip()]
    if features:
        rows.append({"name": "Особенности", "value": "; ".join(features)})
    return rows


def _material_hint(brief) -> str:
    extra = brief.extra or {}
    for key in ("material", "fuel", "kind", "work"):
        value = str(extra.get(key, "")).strip()
        if value:
            return value
    return ""


def build_titles(brief, category: CategoryTemplate, focus: str = "") -> tuple[str, list[str]]:
    product = brief.product_type.strip()
    features = [str(item).strip() for item in (brief.key_features or []) if str(item).strip()]
    region_short = brief.region.split(",")[0].strip()
    material = _material_hint(brief)
    ordered: list[str] = []

    def add(text: str) -> None:
        clipped = clip_title(text)
        if clipped and clipped not in ordered:
            ordered.append(clipped)

    if focus == "title_and_main_photo" and features:
        add(f"{features[0]} — {product}")
        add(f"{product}: {features[0]}")
    add(product)
    if features:
        add(f"{product}, {features[0]}")
    if material and material.casefold() not in product.casefold():
        add(f"{product}, {material}")
    if region_short:
        add(f"{product} — {region_short}")
    if len(features) >= 2:
        add(f"{features[0]}, {features[1]}")
    if features and region_short:
        add(f"{features[0]}, {region_short}")
    add(f"{category.title}: {product}")
    if features:
        add(f"{category.title}, {features[0]}")
        add(features[0])
    if region_short:
        add(region_short)
    if not ordered:
        add(category.title)

    main = ordered[0]
    alternatives = [item for item in ordered[1:] if item != main][:3]
    return main, alternatives


def _lead_lines(brief) -> list[str]:
    product = brief.product_type.strip()
    region = brief.region.strip()
    buyer = brief.target_buyer.strip()
    if brief.tone == "friendly":
        return [
            f"{product}.",
            f"Регион: {region}.",
            f"Подойдёт: {buyer}.",
        ]
    if brief.tone == "expert":
        return [
            product,
            f"Покупатель: {buyer}.",
            f"Регион размещения: {region}.",
        ]
    return [
        f"{product}.",
        f"Регион: {region}.",
        f"Для кого: {buyer}.",
    ]


def render_description(brief, category: CategoryTemplate, focus: str = "") -> str:
    features = [str(item).strip() for item in (brief.key_features or []) if str(item).strip()]
    extra = brief.extra or {}
    fact_lines = []
    for spec in category.fields:
        value = str(extra.get(spec.key, "")).strip()
        if value:
            fact_lines.append(f"— {spec.label}: {value}")
    feature_block = ["Особенности:", *[f"— {item}" for item in features]] if features else []
    fact_block = ["Характеристики:", *fact_lines] if fact_lines else []
    lead = _lead_lines(brief)
    price_line = [f"Цена: {int(brief.price)} ₽."]
    if focus == "description":
        blocks = [feature_block, fact_block, lead, price_line]
    else:
        blocks = [lead, feature_block, fact_block, price_line]
    parts: list[str] = []
    for block in blocks:
        if not block:
            continue
        if parts:
            parts.append("")
        parts.extend(block)
    return "\n".join(parts).strip()


def _note_for_focus(focus: str, comment: str) -> str:
    notes = []
    if focus == "title_and_main_photo":
        notes.append("Доработка из-за слабых просмотров: нужен другой заголовок и другое главное фото.")
    elif focus == "description":
        notes.append("Доработка из-за просмотров без обращений: описание пересобрано из тех же фактов.")
    if comment.strip():
        notes.append("Комментарий к доработке хранится в журнале и в текст объявления не подставлен.")
    return " ".join(notes)


class TemplateListingGenerator:
    source = "template"

    def generate(self, brief, *, focus: str = "", comment: str = "") -> GeneratedDraft:
        category = get_category(brief.category)
        title, alternatives = build_titles(brief, category, focus=focus)
        description = render_description(brief, category, focus=focus)
        note = "Текст собран шаблоном только из заполненных полей брифа. Бюджет продвижения в объявление не входит."
        extra = _note_for_focus(focus, comment)
        if extra:
            note = f"{note} {extra}"
        return GeneratedDraft(
            title=title,
            alternative_titles=alternatives,
            description=description,
            characteristics=characteristics_from_brief(brief, category),
            source=self.source,
            note=note,
        )


_SYSTEM_PROMPT = """Ты готовишь текст объявления для Авито о банном товаре.
Жёсткие правила:
- Бери только факты из JSON брифа. Не добавляй размеры, материалы, гарантию, комплектацию, сроки, скидки и оценки.
- Значения копируй как в брифе, без новых окончаний и синонимов.
- Не пиши телефон, почту, ссылки и призывы «звоните», «пишите», «скидка».
- Бюджет продвижения в текст не включай.
- Заголовок и каждый альтернативный заголовок — не длиннее 50 символов.
- Дай ровно 3 альтернативных заголовка.
- Ответ — только JSON без пояснений:
  {"title": "", "alternative_titles": ["", "", ""], "description": ""}
"""


class OpenAICompatibleClient:
    """Chat-completions client. The base URL can point at any compatible host."""

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 40.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


def parse_json_object(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("В ответе модели нет JSON")
    payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Модель вернула не объект")
    return payload


def _prompt_payload(brief) -> str:
    extra = {key: value for key, value in (brief.extra or {}).items() if str(value).strip()}
    payload = {
        "category": brief.category,
        "product_type": brief.product_type,
        "target_buyer": brief.target_buyer,
        "tone": brief.tone,
        "price_rub": int(brief.price),
        "region": brief.region,
        "key_features": list(brief.key_features or []),
        "fields": extra,
    }
    return json.dumps(payload, ensure_ascii=False)


def sanitize_model_draft(payload: dict, brief, template: GeneratedDraft, *, focus: str) -> GeneratedDraft:
    """Keep model phrasing only where every word and number is already in the brief."""
    category = get_category(brief.category)
    corpus = brief_corpus(brief, category)
    numbers = brief_numbers(brief)
    candidates = [str(payload.get("title") or "")]
    raw_alts = payload.get("alternative_titles") or []
    if isinstance(raw_alts, list):
        candidates.extend(str(item) for item in raw_alts)
    grounded_titles = []
    for candidate in candidates:
        clipped = clip_title(candidate)
        if clipped and text_is_grounded(clipped, corpus, numbers) and clipped not in grounded_titles:
            grounded_titles.append(clipped)

    title = template.title
    alternatives = list(template.alternative_titles)
    used_model_title = False
    if grounded_titles and focus != "description":
        candidate_title = grounded_titles[0]
        candidate_alts = [item for item in grounded_titles[1:] if item != candidate_title]
        for fallback in template.alternative_titles:
            if len(candidate_alts) >= 2:
                break
            if fallback != candidate_title and fallback not in candidate_alts:
                candidate_alts.append(fallback)
        if candidate_title and len(candidate_alts) >= 2:
            title = candidate_title
            alternatives = candidate_alts[:3]
            used_model_title = True

    description = template.description
    used_model_description = False
    model_description = str(payload.get("description") or "")
    if focus != "title_and_main_photo" and model_description.strip():
        kept: list[str] = []
        for line in model_description.splitlines():
            stripped = line.strip()
            if not stripped:
                if kept and kept[-1] != "":
                    kept.append("")
                continue
            if text_is_grounded(stripped, corpus, numbers):
                kept.append(stripped)
        cleaned = "\n".join(kept).strip()
        product_cf = brief.product_type.casefold().replace("ё", "е")
        price_token = str(int(brief.price))
        if (
            len(cleaned) >= 80
            and product_cf in cleaned.casefold().replace("ё", "е")
            and price_token in cleaned.replace(" ", "")
        ):
            description = cleaned
            used_model_description = True

    if used_model_title or used_model_description:
        note = (
            "Модель предложила формулировки, в текст попали только строки, "
            "где каждое слово и число есть в брифе. Характеристики собраны из брифа, не из ответа модели."
        )
        source = "llm"
    else:
        note = template.note + " Ответ модели не прошёл сверку с брифом, оставлен шаблон."
        source = "template"
        title = template.title
        alternatives = list(template.alternative_titles)
        description = template.description
    extra = _note_for_focus(focus, "")
    if extra:
        note = f"{note} {extra}"
    return GeneratedDraft(
        title=title,
        alternative_titles=alternatives[:3],
        description=description,
        characteristics=characteristics_from_brief(brief, category),
        source=source,
        note=note,
    )


class LLMListingGenerator:
    def __init__(self, client: LLMClient, fallback: TemplateListingGenerator | None = None):
        self.client = client
        self.fallback = fallback or TemplateListingGenerator()

    def generate(self, brief, *, focus: str = "", comment: str = "") -> GeneratedDraft:
        template = self.fallback.generate(brief, focus=focus, comment=comment)
        instruction = _prompt_payload(brief)
        if comment.strip():
            instruction += (
                "\nКомментарий редактора (это указание, не факт для текста): "
                + comment.strip()
            )
        if focus == "title_and_main_photo":
            instruction += "\nСделай упор на другой фактический заголовок. Описание оставь сухим."
        elif focus == "description":
            instruction += "\nЗаголовок не меняй по смыслу. Перестрой описание, не добавляя фактов."
        try:
            raw = self.client.complete(_SYSTEM_PROMPT, instruction)
            payload = parse_json_object(raw)
        except Exception as exc:  # network, parse, schema — template already has the facts
            template.note = (
                f"{template.note} Модель не использована ({exc.__class__.__name__})."
            )
            template.warnings.append(exc.__class__.__name__)
            return template
        return sanitize_model_draft(payload, brief, template, focus=focus)


def build_generator(settings) -> DraftGenerator:
    if settings.openai_api_key.strip():
        client = OpenAICompatibleClient(
            api_key=settings.openai_api_key.strip(),
            base_url=settings.openai_base_url,
            model=settings.openai_model,
        )
        return LLMListingGenerator(client)
    return TemplateListingGenerator()
