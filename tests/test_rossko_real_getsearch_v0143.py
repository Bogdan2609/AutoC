"""Regression: ROSSKO real SOAP uses lowercase success, not Success."""
from decimal import Decimal
import pytest
from app.modules.offer_engine.providers import parse_rossko_search_result, Unavailable


def fixture():
    return {
        "success": True,
        "text": "Trialli CF 705",
        "message": "Вы используете API в режиме тестирования",
        "PartsList": {"Part": [
            {"guid": "PART-CF705", "brand": "Trialli", "partnumber": "CF 705",
             "stocks": {"stock": [
                 {"id": "WH1", "price": "492.27", "count": 4, "delivery": 0, "extra": 1, "multiplicity": 1},
                 {"id": "WH2", "price": "492.27", "count": 107, "delivery": 1, "extra": 0, "multiplicity": 1},
                 {"id": "WH3", "price": "492.27", "count": 971, "delivery": 1, "extra": 0, "multiplicity": 1},
                 {"id": "WH4", "price": "492.27", "count": 288, "delivery": 2, "extra": 0, "multiplicity": 1},
             ]}},
            {"guid": "PART-CC705", "brand": "Trialli", "partnumber": "CC 705",
             "stocks": {"stock": [{"id": "CROSS", "price": "3765", "count": 1, "delivery": 1}]}},
        ]}
    }


def test_lowercase_success_real_shape_four_offers():
    found = parse_rossko_search_result(fixture(), "TRIALLI", "CF705")
    assert len(found) == 4
    assert [x.key for x in found] == [f"PART-CF705:WH{i}" for i in range(1, 5)]
    assert [x.qty for x in found] == [4, 107, 971, 288]
    assert [x.days for x in found] == [0, 1, 1, 2]
    assert all(x.price == Decimal("492.27") for x in found)
    assert found[0].public_meta["extra_days"] == 1
    assert all(x.article == "CF 705" for x in found)


def test_uppercase_success_and_singleton_stock_backward_compatible():
    data = fixture()
    data["Success"] = "true"
    del data["success"]
    data["PartsList"]["Part"] = data["PartsList"]["Part"][0]
    data["PartsList"]["Part"]["stocks"]["stock"] = data["PartsList"]["Part"]["stocks"]["stock"][0]
    assert len(parse_rossko_search_result({"SearchResult": data}, "Trialli", "CF 705")) == 1


def test_negative_result_is_not_a_positive_offer():
    data = fixture()
    data["success"] = False
    with pytest.raises(Unavailable, match="non-success"):
        parse_rossko_search_result(data,"Trialli","CF705")


def test_invalid_envelope_fails_closed():
    with pytest.raises(Unavailable):
        parse_rossko_search_result({"success": True,"PartsList": []},"Trialli","CF705")
