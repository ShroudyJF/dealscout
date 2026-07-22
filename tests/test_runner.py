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


class FakeFx:
    def __init__(self, rate=4.7, fail=False):
        self.rate = rate
        self.fail = fail

    def convert(self, amount, from_ccy, to_ccy):
        if self.fail:
            from dealscout.fx import FxError

            raise FxError("boom")
        return amount * self.rate


def test_run_once_adds_converted_line(store):
    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    run_once(store, FakeSource({"g1": [_point()]}), notifier, fx=FakeFx(), display_currency="MYR")
    assert "≈ MYR" in notifier.sent[0]


def test_run_once_fx_failure_still_notifies(store):
    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    results = run_once(
        store, FakeSource({"g1": [_point()]}), notifier, fx=FakeFx(fail=True), display_currency="MYR"
    )
    assert results[0].notified is True
    assert "≈" not in notifier.sent[0]


class FakeLLM:
    def __init__(self, verdict=None, fail=False):
        self.verdict = verdict
        self.fail = fail

    def judge(self, overview, rule):
        if self.fail:
            from dealscout.verdict import VerdictError

            raise VerdictError("boom")
        return self.verdict


class FakeSourceWithOverview(FakeSource):
    def fetch_overview(self, rule):
        from dealscout.models import PricePoint, PriceOverview

        cur = PricePoint(shop="Steam", price=12.49, regular=24.99, cut=50, currency="USD", url="https://x")
        return PriceOverview(current=cur, historical_low=None)


def test_run_once_adds_verdict(store):
    from dealscout.verdict import DealVerdict

    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    llm = FakeLLM(verdict=DealVerdict(rating="good", reason="接近史低"))
    run_once(store, FakeSourceWithOverview({"g1": [_point()]}), notifier, llm=llm)
    assert "好价判断" in notifier.sent[0]


def test_run_once_verdict_failure_still_notifies(store):
    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    results = run_once(
        store, FakeSourceWithOverview({"g1": [_point()]}), notifier, llm=FakeLLM(fail=True)
    )
    assert results[0].notified is True
    assert "好价判断" not in notifier.sent[0]


def test_run_once_llm_without_overview_source_skips_verdict(store):
    from dealscout.verdict import DealVerdict

    # plain FakeSource has no fetch_overview -> _make_verdict returns None, notify still fires
    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    llm = FakeLLM(verdict=DealVerdict(rating="good", reason="接近史低"))
    results = run_once(store, FakeSource({"g1": [_point()]}), notifier, llm=llm)
    assert results[0].notified is True
    assert "好价判断" not in notifier.sent[0]
