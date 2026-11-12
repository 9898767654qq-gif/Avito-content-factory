from app.models import Brief, Listing
from app.services.listings import create_listing_from_facts
from app.services.generator import TemplateListingGenerator
from app.services.validation import validate_listing
from tests.conftest import kupel_fields


def _listing(session, **overrides) -> Listing:
    facts = kupel_fields()
    brief = Brief(
        category=facts.category,
        product_type=facts.product_type,
        target_buyer=facts.target_buyer,
        tone=facts.tone,
        price=overrides.get("price", facts.price),
        region=overrides.get("region", facts.region),
        key_features=list(facts.key_features),
        promotion_budget=facts.promotion_budget,
        extra=overrides.get("extra", dict(facts.extra)),
    )
    session.add(brief)
    session.flush()
    description = overrides.get(
        "description",
        "Купель круглая из лиственницы. Регион: Новгородская область. Для кого: частный покупатель. Цена: 96000 ₽.",
    )
    listing = Listing(
        brief_id=brief.id,
        version=1,
        is_current=True,
        status=overrides.get("status", "draft"),
        title=overrides.get("title", "Купель круглая"),
        alternative_titles=overrides.get("alternative_titles", ["Купель, лиственница"]),
        description=description,
        characteristics=overrides.get(
            "characteristics",
            [{"name": "Материал", "value": "лиственница"}],
        ),
        price=brief.price,
        category=overrides.get("category", "kupel"),
        region=brief.region,
        generation_source="template",
        generation_note="",
        rework_comment="",
        rework_focus="",
    )
    listing.brief = brief
    session.add(listing)
    session.commit()
    session.refresh(listing)
    return listing


def test_generated_listing_passes_validation(session):
    listing = create_listing_from_facts(session, kupel_fields(), TemplateListingGenerator(), "тест")
    report = validate_listing(session, listing)
    assert report.ok
    assert listing.status == "validated"


def test_phone_email_link_and_stop_word_are_errors(session):
    listing = _listing(
        session,
        description="Звоните +7 921 000-11-22, почта bath@example.ru, сайт example.com. Только сегодня скидка!",
    )
    report = validate_listing(session, listing)
    codes = {issue.code for issue in report.errors}
    assert {"phone", "email", "link", "stop_word"} <= codes
    assert report.ok is False


def test_title_length_and_required_category_field(session):
    long_title = _listing(session, title="К" * 51)
    report = validate_listing(session, long_title)
    assert any(issue.code == "title_length" for issue in report.errors)

    missing = _listing(session, extra={"shape": "круглая"}, title="Купель без материала")
    report = validate_listing(session, missing)
    assert any(issue.code == "required_field" and issue.field == "material" for issue in report.errors)


def test_exact_duplicate_is_an_error_and_similar_title_is_a_warning(session):
    _listing(session, title="Купель круглая", price=96000)
    duplicate = _listing(session, title="Купель круглая", price=96000)
    report = validate_listing(session, duplicate)
    assert any(issue.code == "duplicate" for issue in report.errors)

    _listing(session, title="Банный чан большой", price=1000, category="chan")
    similar = _listing(session, title="Банный чан большой уличный", price=2000, category="chan")
    report = validate_listing(session, similar)
    assert any(issue.code == "similar" for issue in report.warnings)
    assert not any(issue.code == "duplicate" for issue in report.errors)


def test_missing_photo_is_a_warning_not_a_block(session):
    listing = create_listing_from_facts(session, kupel_fields(), TemplateListingGenerator(), "тест")
    report = validate_listing(session, listing)
    assert report.ok
    assert any(issue.code == "no_photos" for issue in report.warnings)
