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


def test_pricepoint_seen_at_optional_defaults_none():
    p = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    assert p.seen_at is None


def test_price_overview_holds_current_and_low():
    from dealscout.models import PriceOverview

    cur = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    low = PricePoint(
        shop="Steam", price=6.24, regular=24.99, cut=75, currency="USD", url="https://x",
        seen_at="2025-09-17",
    )
    ov = PriceOverview(current=cur, historical_low=low)
    assert ov.current.price == 7.49
    assert ov.historical_low.price == 6.24
    assert ov.historical_low.seen_at == "2025-09-17"


def test_price_overview_low_optional():
    from dealscout.models import PriceOverview

    cur = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    assert PriceOverview(current=cur).historical_low is None
