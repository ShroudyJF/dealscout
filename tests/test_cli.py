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
        return "g-123", "Hades"

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


@pytest.fixture()
def fake_env(tmp_path, monkeypatch):
    settings = Settings(
        itad_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="c",
        db_path=str(tmp_path / "cli.db"),
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "ItadClient", FakeItad)
    monkeypatch.setattr(cli, "TelegramNotifier", FakeNotifier)
    monkeypatch.setattr(cli, "FxConverter", FakeFx)
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
