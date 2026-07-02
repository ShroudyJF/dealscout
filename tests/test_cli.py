import pytest
from typer.testing import CliRunner

from dealscout import cli
from dealscout.config import Settings

runner = CliRunner()


class FakeItad:
    def __init__(self, api_key, client=None):
        pass

    def lookup_game(self, title):
        return "g-123", "Hades"

    def fetch_prices(self, rule):
        return []


class FakeNotifier:
    def __init__(self, *args, **kwargs):
        pass

    def send(self, text):
        pass


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
