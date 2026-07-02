import pytest
from pydantic import ValidationError

from dealscout.models import Deal, PricePoint, WatchRule


def test_watch_rule_accepts_single_condition():
    rule = WatchRule(title="Hades", game_id="abc", max_price=15.0)
    assert rule.min_cut is None
    assert rule.country == "MY"


def test_watch_rule_rejects_no_conditions():
    with pytest.raises(ValidationError):
        WatchRule(title="Hades", game_id="abc")


def test_deal_holds_best_point():
    best = PricePoint(shop="Steam", price=12.49, regular=24.99, cut=50, currency="USD", url="https://x")
    deal = Deal(watch_id=1, title="Hades", best=best, reason="cut 50% >= 40%")
    assert deal.best.shop == "Steam"
