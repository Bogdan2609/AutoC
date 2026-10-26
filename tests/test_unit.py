from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.catalog.normalize import normalize_article, normalize_brand
from app.modules.pricing.service import calculate_retail
from app.modules.suppliers.adapters.rossko import RosskoAdapter


def test_article_normalization_is_idempotent():
    article = "0 986-479.088"
    assert normalize_article(article) == "0986479088"
    assert normalize_article(normalize_article(article)) == "0986479088"


def test_brand_normalization():
    assert normalize_brand("  bosch   ") == "BOSCH"


def test_price_rounding_uses_decimal():
    assert calculate_retail(Decimal("2450.00"), Decimal("25")) == Decimal("3062.50")
    assert calculate_retail(Decimal("0.03"), Decimal("50")) == Decimal("0.05")


@pytest.mark.parametrize("price,markup", [(-1, 25), (5, -1), (5, 1001)])
def test_invalid_price_input(price, markup):
    with pytest.raises(ValueError):
        calculate_retail(Decimal(price), Decimal(markup))


@pytest.mark.asyncio
async def test_rossko_adapter_fails_closed():
    with pytest.raises(Exception, match="ROSSKO SOAP credentials/delivery not configured"):
        await RosskoAdapter().search("BOSCH", "0986479088")


def test_liveness_and_admin_denied_without_token():
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        response = client.post("/api/v1/backoffice/brands", json={"name": "BOSCH", "slug": "bosch"})
        assert response.status_code == 403
