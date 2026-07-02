from dealscout import judge
from dealscout.models import PricePoint, WatchRule


def _point(price, cut, shop="Steam"):
    return PricePoint(shop=shop, price=price, regular=24.99, cut=cut, currency="USD", url="https://x")


def _rule(**kw):
    return WatchRule(id=1, title="Hades", game_id="g", **kw)


def test_triggers_on_max_price():
    deal = judge.evaluate(_rule(max_price=15.0), [_point(12.49, 50)])
    assert deal is not None
    assert deal.best.price == 12.49


def test_triggers_on_min_cut():
    assert judge.evaluate(_rule(min_cut=40), [_point(18.0, 40)]) is not None


def test_no_trigger_when_conditions_unmet():
    assert judge.evaluate(_rule(max_price=10.0), [_point(12.49, 20)]) is None


def test_picks_cheapest_shop():
    deal = judge.evaluate(_rule(max_price=15.0), [_point(14.0, 40, shop="GOG"), _point(12.49, 50)])
    assert deal.best.shop == "Steam"


def test_empty_points_returns_none():
    assert judge.evaluate(_rule(max_price=15.0), []) is None
