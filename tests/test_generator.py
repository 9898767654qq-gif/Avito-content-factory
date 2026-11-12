import json
import re

from app.services.generator import (
    LLMListingGenerator,
    TemplateListingGenerator,
    build_generator,
    clip_title,
    sanitize_model_draft,
)
from tests.conftest import kupel_fields


def test_template_uses_only_brief_facts():
    facts = kupel_fields()
    draft = TemplateListingGenerator().generate(facts)
    blob = "\n".join(
        [
            draft.title,
            *draft.alternative_titles,
            draft.description,
            json.dumps(draft.characteristics, ensure_ascii=False),
        ]
    )
    assert "лиственница" in blob
    assert "диаметр 150 см" in blob
    assert "круглая" in blob
    assert "654321" not in blob
    assert "гарантия" not in blob.casefold()
    assert "кедр" not in blob.casefold()
    names = [row["name"] for row in draft.characteristics]
    assert "Объём, л" not in names
    assert "Материал" in names
    assert all(len(title) <= 50 for title in [draft.title, *draft.alternative_titles])
    assert 2 <= len(draft.alternative_titles) <= 3
    assert draft.source == "template"


def test_tones_change_wording_without_new_facts():
    business = TemplateListingGenerator().generate(kupel_fields(tone="business"))
    friendly = TemplateListingGenerator().generate(kupel_fields(tone="friendly"))
    expert = TemplateListingGenerator().generate(kupel_fields(tone="expert"))
    assert "Для кого:" in business.description
    assert "Подойдёт:" in friendly.description
    assert "Покупатель:" in expert.description
    assert business.description != friendly.description != expert.description
    for draft in (business, friendly, expert):
        assert "96000" in draft.description
        assert "654321" not in draft.description


def test_long_title_is_clipped_and_alternatives_remain():
    facts = kupel_fields(product_type=("Купель из лиственницы " * 6).strip())
    draft = TemplateListingGenerator().generate(facts)
    assert len(draft.title) <= 50
    assert len(draft.alternative_titles) >= 2
    assert all(len(item) <= 50 for item in draft.alternative_titles)
    assert clip_title("а" * 80) == "а" * 50 or len(clip_title("а" * 80)) <= 50


def test_title_focus_reorders_existing_facts_only():
    facts = kupel_fields()
    plain = TemplateListingGenerator().generate(facts)
    focused = TemplateListingGenerator().generate(facts, focus="title_and_main_photo", comment="паровоз не факт")
    assert focused.title != plain.title
    assert "диаметр" in focused.title
    assert "паровоз" not in focused.title
    assert "паровоз" not in focused.description


def test_sanitizer_drops_invented_characteristics_and_numbers():
    facts = kupel_fields()
    template = TemplateListingGenerator().generate(facts)
    dirty = {
        "title": "Купель с гарантией 10 лет",
        "alternative_titles": ["Купель из мрамора", "Купель круглая", "Купель, лиственница"],
        "description": "Купель круглая.\nМощность 20 кВт и мрамор.\n" + template.description,
        "characteristics": [
            {"name": "Гарантия", "value": "10 лет"},
            {"name": "Материал", "value": "мрамор"},
        ],
    }
    clean = sanitize_model_draft(dirty, facts, template, focus="")
    blob = clean.title + "\n" + clean.description + "\n" + json.dumps(clean.characteristics, ensure_ascii=False)
    assert "мрамор" not in blob.casefold()
    assert "10 лет" not in blob
    assert "20" not in re.findall(r"\d+", blob)
    assert "лиственница" in blob
    assert any(row["value"] == "лиственница" for row in clean.characteristics)
    assert all(row["name"] != "Гарантия" for row in clean.characteristics)


def test_llm_generator_falls_back_when_the_model_fails():
    class Broken:
        def complete(self, system: str, user: str) -> str:
            raise RuntimeError("нет сети")

    draft = LLMListingGenerator(Broken()).generate(kupel_fields())
    assert draft.source == "template"
    assert "лиственница" in draft.description
    assert "RuntimeError" in draft.note


def test_missing_api_key_selects_template(settings):
    generator = build_generator(settings)
    assert isinstance(generator, TemplateListingGenerator)
