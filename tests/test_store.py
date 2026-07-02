import pytest

from dealscout.models import PricePoint, WatchRule
from dealscout.store import Store


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def _point(price=12.49, cut=50):
    return PricePoint(shop="Steam", price=price, regular=24.99, cut=cut, currency="USD", url="https://x")


def test_add_and_list_watch_roundtrip(store):
    added = store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    assert added.id == 1
    assert store.list_watches() == [added]


def test_price_history_returns_latest_first(store):
    w = store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    store.record_prices(w.id, [_point(price=20.0)])
    store.record_prices(w.id, [_point(price=12.49)])
    history = store.price_history(w.id)
    assert [p.price for _, p in history] == [12.49, 20.0]


def test_notification_price_tracking(store):
    w = store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    assert store.last_notified_price(w.id) is None
    store.record_notification(w.id, 12.49, "msg")
    assert store.last_notified_price(w.id) == 12.49
