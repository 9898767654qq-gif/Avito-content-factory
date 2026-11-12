"""Category templates for banya goods and nearby services.

Placeholders are hints for the owner. They are not copied into the listing.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain import DomainError


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    placeholder: str
    required: bool = False
    hint: str = ""


@dataclass(frozen=True)
class CategoryTemplate:
    slug: str
    title: str
    lead: str
    product_types: tuple[str, ...]
    fields: tuple[FieldSpec, ...]


CATEGORIES: tuple[CategoryTemplate, ...] = (
    CategoryTemplate(
        slug="banya",
        title="Бани",
        lead="Каркас, брус и бани под ключ.",
        product_types=("Каркасная баня", "Баня из бруса", "Баня под ключ", "Мобильная баня"),
        fields=(
            FieldSpec("material", "Материал стен", "профилированный брус, ель", True),
            FieldSpec("area_sqm", "Площадь, м²", "18", True),
            FieldSpec("capacity_people", "На сколько человек", "6"),
            FieldSpec("rooms", "Планировка", "парная, моечная, комната отдыха"),
            FieldSpec("stove_included", "Печь", "не входит"),
            FieldSpec("delivery", "Доставка", "по области, считается отдельно"),
            FieldSpec("installation", "Сборка", "на участке заказчика"),
        ),
    ),
    CategoryTemplate(
        slug="banya_barrel",
        title="Бани-бочки",
        lead="Круглые и овальные бани, обычно с печью в комплекте.",
        product_types=("Баня-бочка 3 метра", "Баня-бочка 4 метра", "Баня-бочка 6 метров", "Овальная баня-бочка"),
        fields=(
            FieldSpec("material", "Материал", "кедр", True),
            FieldSpec("length_m", "Длина, м", "4", True),
            FieldSpec("capacity_people", "Вместимость, человек", "4"),
            FieldSpec("stove_included", "Печь в комплекте", "дровяная, в комплекте"),
            FieldSpec("foundation", "Основание", "не входит"),
            FieldSpec("delivery", "Доставка", "по Ленинградской области"),
        ),
    ),
    CategoryTemplate(
        slug="kupel",
        title="Купели",
        lead="Деревянные и композитные купели, с подогревом и без.",
        product_types=("Купель круглая", "Купель овальная", "Купель с подогревом", "Купель из лиственницы"),
        fields=(
            FieldSpec("material", "Материал", "лиственница", True),
            FieldSpec("shape", "Форма", "круглая", True),
            FieldSpec("diameter_cm", "Диаметр или размер, см", "150"),
            FieldSpec("heating", "Подогрев", "без печи"),
            FieldSpec("capacity_liters", "Объём, л", "900"),
        ),
    ),
    CategoryTemplate(
        slug="stove",
        title="Печи для бани",
        lead="Дровяные и электрические печи для парной.",
        product_types=("Печь дровяная", "Печь с баком", "Печь-сетка", "Электрокаменка"),
        fields=(
            FieldSpec("fuel", "Топливо", "дрова", True),
            FieldSpec("power_kw", "Мощность, кВт", "18", True),
            FieldSpec("stone_kg", "Закладка камней, кг", "40"),
            FieldSpec("steam_room_m3", "Объём парной, м³", "12–24"),
            FieldSpec("material", "Материал топки", "сталь 4 мм"),
            FieldSpec("chimney", "Дымоход", "не входит"),
        ),
    ),
    CategoryTemplate(
        slug="chan",
        title="Банные чаны",
        lead="Уличные чаны на дровах и с внешним подогревом.",
        product_types=("Банный чан на 4", "Банный чан на 6", "Чан с внешней печью", "Чан из нержавейки"),
        fields=(
            FieldSpec("material", "Материал чана", "нержавеющая сталь", True),
            FieldSpec("heating", "Нагрев", "внешняя дровяная печь", True),
            FieldSpec("capacity_people", "Вместимость, человек", "6"),
            FieldSpec("diameter_cm", "Диаметр, см", "180"),
            FieldSpec("placement", "Установка", "на улице"),
        ),
    ),
    CategoryTemplate(
        slug="sauna",
        title="Сауны",
        lead="Домашние сауны и кабины.",
        product_types=("Сауна для дома", "Сауна-кабина", "Инфракрасная кабина", "Сауна под ключ"),
        fields=(
            FieldSpec("kind", "Тип", "сауна для дома", True),
            FieldSpec("material", "Отделка", "ольха", True),
            FieldSpec("capacity_people", "Вместимость, человек", "4"),
            FieldSpec("heater", "Печь-каменка", "электрическая, не входит"),
            FieldSpec("size", "Габариты", "200×180×210 см"),
        ),
    ),
    CategoryTemplate(
        slug="service",
        title="Услуги для бани",
        lead="Смежное: сборка, отделка, выезд на участок.",
        product_types=("Сборка бани-бочки", "Отделка парной", "Установка печи", "Выезд мастера"),
        fields=(
            FieldSpec("work", "Состав работ", "сборка бани-бочки на участке", True),
            FieldSpec("area", "География выезда", "Санкт-Петербург и область", True),
            FieldSpec("term", "Срок", "1 день"),
            FieldSpec("includes", "Что входит", "крепёж и герметизация"),
        ),
    ),
)

TONES: tuple[tuple[str, str], ...] = (
    ("business", "деловой"),
    ("friendly", "дружелюбный"),
    ("expert", "экспертный"),
)

BUYER_PRESETS: tuple[str, ...] = (
    "Частный покупатель на участок",
    "Владелец гостевого дома",
    "Подрядчик",
    "База отдыха",
)

EXAMPLES: tuple[dict, ...] = (
    {
        "id": "barrel",
        "label": "Баня-бочка 4 м",
        "category": "banya_barrel",
        "product_type": "Баня-бочка 4 метра",
        "target_buyer": "Частный покупатель на участок",
        "tone": "friendly",
        "price": 189000,
        "region": "Ленинградская область, Всеволожский район",
        "key_features": ["кедр", "печь в комплекте", "полки из липы"],
        "promotion_budget": 7000,
        "extra": {
            "material": "кедр",
            "length_m": "4",
            "capacity_people": "4",
            "stove_included": "дровяная, в комплекте",
            "delivery": "по Ленинградской области",
        },
    },
    {
        "id": "stove",
        "label": "Печь для парной",
        "category": "stove",
        "product_type": "Печь дровяная 18 кВт",
        "target_buyer": "Частный покупатель на участок",
        "tone": "expert",
        "price": 42000,
        "region": "Санкт-Петербург",
        "key_features": ["сталь 4 мм", "выносная топка"],
        "promotion_budget": 3000,
        "extra": {
            "fuel": "дрова",
            "power_kw": "18",
            "stone_kg": "40",
            "steam_room_m3": "12–24",
            "material": "сталь 4 мм",
            "chimney": "не входит",
        },
    },
    {
        "id": "kupel",
        "label": "Купель",
        "category": "kupel",
        "product_type": "Купель круглая",
        "target_buyer": "Владелец гостевого дома",
        "tone": "business",
        "price": 96000,
        "region": "Новгородская область",
        "key_features": ["диаметр 150 см", "слив в комплекте"],
        "promotion_budget": 2500,
        "extra": {
            "material": "лиственница",
            "shape": "круглая",
            "diameter_cm": "150",
            "heating": "без печи",
            "capacity_liters": "900",
        },
    },
)


def get_category(slug: str) -> CategoryTemplate:
    for category in CATEGORIES:
        if category.slug == slug:
            return category
    raise DomainError("Выберите категорию из списка банных товаров.")


def category_payload(category: CategoryTemplate) -> dict:
    return {
        "slug": category.slug,
        "title": category.title,
        "lead": category.lead,
        "product_types": list(category.product_types),
        "fields": [
            {
                "key": field.key,
                "label": field.label,
                "placeholder": field.placeholder,
                "required": field.required,
                "hint": field.hint,
            }
            for field in category.fields
        ],
    }


def tone_label(tone: str) -> str:
    for key, label in TONES:
        if key == tone:
            return label
    return tone
