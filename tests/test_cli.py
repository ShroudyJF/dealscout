import re

import pytest
from typer.testing import CliRunner

from dealscout import cli
from dealscout.config import Settings
from dealscout.models import PricePoint
from dealscout.sources.base import SourceError
from dealscout.store import Store

runner = CliRunner()


class FakeItad:
    def __init__(self, api_key, client=None):
        pass

    def lookup_game(self, title):
        return "g-123", title.title()

    def fetch_prices(self, rule):
        return []


class FailingFetchItad(FakeItad):
    def fetch_prices(self, rule):
        raise SourceError("boom")


class FakeNotifier:
    def __init__(self, *args, **kwargs):
        pass

    def send(self, text):
        pass


class FakeFx:
    def __init__(self, *args, **kwargs):
        pass

    def convert(self, amount, from_ccy, to_ccy):
        return amount


class FakeVerdictLLM:
    def __init__(self, *args, **kwargs):
        pass

    def judge(self, overview, rule):
        from dealscout.verdict import DealVerdict

        return DealVerdict(rating="good", reason="ok")


class FakeWatchParser:
    def __init__(self, *args, **kwargs):
        pass

    def parse(self, text):
        from dealscout.intake import WatchRequest

        return WatchRequest(title="Elden Ring", max_price=120, currency="MYR", min_cut=None)


@pytest.fixture()
def fake_env(tmp_path, monkeypatch):
    settings = Settings(
        itad_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="c",
        db_path=str(tmp_path / "cli.db"),
        gemini_api_key="g",
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "ItadClient", FakeItad)
    monkeypatch.setattr(cli, "TelegramNotifier", FakeNotifier)
    monkeypatch.setattr(cli, "FxConverter", FakeFx)
    monkeypatch.setattr(cli, "GeminiVerdictLLM", FakeVerdictLLM)
    monkeypatch.setattr(cli, "GeminiWatchParser", FakeWatchParser)
    return settings


def test_add_then_list(fake_env):
    result = runner.invoke(cli.app, ["add", "hades", "--max-price", "15"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli.app, ["list"])
    assert "Hades" in result.output


def test_run_reports_no_deal(fake_env):
    runner.invoke(cli.app, ["add", "hades", "--max-price", "15"])
    result = runner.invoke(cli.app, ["run"])
    assert result.exit_code == 0, result.output
    assert "no deal" in result.output


def _strip_ansi(text):
    # rich colors error output on CI terminals and may split words with style codes
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_add_requires_a_condition(fake_env):
    result = runner.invoke(cli.app, ["add", "hades"])
    output = _strip_ansi(result.output)
    assert result.exit_code != 0
    assert "ValidationError" not in output
    assert "max-price" in output
    assert "min-cut" in output


def test_report_shows_history(fake_env):
    result = runner.invoke(cli.app, ["add", "hades", "--max-price", "15"])
    assert result.exit_code == 0, result.output

    store = Store(fake_env.db_path)
    store.record_prices(
        1,
        [
            PricePoint(
                shop="Steam",
                price=9.99,
                regular=19.99,
                cut=50,
                currency="USD",
                url="https://example.com/hades",
            )
        ],
    )

    result = runner.invoke(cli.app, ["report", "1"])
    assert result.exit_code == 0, result.output
    assert "Steam" in result.output


def test_run_exits_nonzero_on_watch_error(fake_env, monkeypatch):
    monkeypatch.setattr(cli, "ItadClient", FailingFetchItad)
    runner.invoke(cli.app, ["add", "hades", "--max-price", "15"])
    result = runner.invoke(cli.app, ["run"])
    assert result.exit_code == 1, result.output
    assert "ERROR" in result.output


def test_tick_skips_off_hour(fake_env, monkeypatch):
    monkeypatch.setattr(cli, "should_run_now", lambda tz, hour, now: False)
    calls = {"itad": 0}
    real_fake = cli.ItadClient

    class CountingItad(real_fake):
        def __init__(self, *a, **kw):
            calls["itad"] += 1
            super().__init__(*a, **kw)

    monkeypatch.setattr(cli, "ItadClient", CountingItad)
    result = runner.invoke(cli.app, ["tick"])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    assert calls["itad"] == 0


def test_tick_runs_on_hour(fake_env, monkeypatch):
    monkeypatch.setattr(cli, "should_run_now", lambda tz, hour, now: True)
    runner.invoke(cli.app, ["add", "hades", "--max-price", "15"])
    result = runner.invoke(cli.app, ["tick"])
    assert result.exit_code == 0, result.output
    assert "Hades" in result.output


def test_run_still_works_with_llm_wired(fake_env):
    runner.invoke(cli.app, ["add", "hades", "--max-price", "15"])
    result = runner.invoke(cli.app, ["run"])
    assert result.exit_code == 0, result.output
    assert "no deal" in result.output


def test_watch_creates_and_confirms(fake_env):
    result = runner.invoke(cli.app, ["watch", "盯艾尔登法环 降到RM120"])
    assert result.exit_code == 0, result.output
    output = _strip_ansi(result.output)
    assert "Elden Ring" in output
    assert "price<=$120" in output      # FakeFx.convert returns amount unchanged (120)
    assert "MYR120" in output           # confirm echoes original currency+amount


def test_watch_persists_watch(fake_env):
    runner.invoke(cli.app, ["watch", "盯艾尔登法环 降到RM120"])
    result = runner.invoke(cli.app, ["list"])
    assert "Elden Ring" in result.output


def test_watch_errors_when_no_game(fake_env, monkeypatch):
    class NoGameParser:
        def __init__(self, *a, **k):
            pass

        def parse(self, text):
            from dealscout.intake import WatchRequest

            return WatchRequest(title=None, max_price=30, currency="USD")

    monkeypatch.setattr(cli, "GeminiWatchParser", NoGameParser)
    result = runner.invoke(cli.app, ["watch", "找个恐怖游戏"])
    assert result.exit_code == 1
    assert "游戏" in _strip_ansi(result.output)


def test_watch_exits_when_fx_fails(fake_env, monkeypatch):
    from dealscout.fx import FxError

    class FailingFx:
        def __init__(self, *a, **k):
            pass

        def convert(self, amount, from_ccy, to_ccy):
            raise FxError("fx rate failed: HTTP 500")

    monkeypatch.setattr(cli, "FxConverter", FailingFx)
    result = runner.invoke(cli.app, ["watch", "盯艾尔登法环 降到RM120"])
    assert result.exit_code == 1
    assert "fx" in _strip_ansi(result.output).lower()


def test_watch_blank_input_fails_fast(fake_env, monkeypatch):
    class BoomParser:
        def __init__(self, *a, **k):
            pass

        def parse(self, text):
            raise AssertionError("parser must not be called for blank input")

    monkeypatch.setattr(cli, "GeminiWatchParser", BoomParser)
    result = runner.invoke(cli.app, ["watch", "   "])
    assert result.exit_code == 1
    assert "游戏" in _strip_ansi(result.output)   # friendly usage message, not an LLM call
