import pytest

from dealscout.models import PricePoint, WatchRule
from dealscout.runner import run_once
from dealscout.sources.base import SourceError
from dealscout.store import Store


class FakeSource:
    def __init__(self, by_game):
        self.by_game = by_game

    def fetch_prices(self, rule):
        result = self.by_game[rule.game_id]
        if isinstance(result, Exception):
            raise result
        return result


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def _point(price=12.49):
    return PricePoint(shop="Steam", price=price, regular=24.99, cut=50, currency="USD", url="https://x")


def test_deal_triggers_notification(store):
    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    results = run_once(store, FakeSource({"g1": [_point()]}), notifier)
    assert results[0].notified is True
    assert len(notifier.sent) == 1


def test_same_price_not_renotified(store):
    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    run_once(store, FakeSource({"g1": [_point()]}), notifier)
    results = run_once(store, FakeSource({"g1": [_point()]}), notifier)
    assert results[0].notified is False
    assert len(notifier.sent) == 1


def test_source_error_isolated_per_watch(store):
    store.add_watch(WatchRule(title="Broken", game_id="bad", max_price=15.0))
    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    results = run_once(store, FakeSource({"bad": SourceError("boom"), "g1": [_point()]}), notifier)
    assert results[0].error is not None
    assert results[1].notified is True
