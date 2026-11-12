import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.factory_app import create_app
from app.services.briefs import parse_brief_fields


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path}/test.db",
        openai_api_key="",
        avito_client_mode="dry_run",
        run_factory_loop=False,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session(app):
    factory = app.state.session_factory
    with factory() as db:
        yield db


def kupel_fields(**overrides):
    payload = {
        "category": "kupel",
        "product_type": "Купель круглая",
        "target_buyer": "Частный покупатель на участок",
        "tone": "business",
        "price": 96000,
        "region": "Новгородская область",
        "key_features": ["диаметр 150 см", "слив в комплекте"],
        "promotion_budget": 654321,
        "extra": {
            "material": "лиственница",
            "shape": "круглая",
            "diameter_cm": "150",
        },
    }
    payload.update(overrides)
    return parse_brief_fields(**payload)
