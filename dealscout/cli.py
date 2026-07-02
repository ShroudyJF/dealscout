"""DealScout CLI: add / list / run / report."""

import typer

from dealscout.config import load_settings
from dealscout.models import WatchRule
from dealscout.notify import TelegramNotifier
from dealscout.runner import run_once
from dealscout.sources.itad import ItadClient
from dealscout.store import Store

app = typer.Typer(help="DealScout - personal price-watching agent")


@app.command()
def add(
    title: str,
    max_price: float | None = typer.Option(None, help="notify when best price <= this"),
    min_cut: int | None = typer.Option(None, help="notify when discount percent >= this"),
    country: str = typer.Option("MY", help="ITAD country code"),
) -> None:
    """Look up TITLE on IsThereAnyDeal and start watching it."""
    settings = load_settings()
    source = ItadClient(settings.itad_api_key)
    game_id, canonical = source.lookup_game(title)
    store = Store(settings.db_path)
    rule = store.add_watch(
        WatchRule(
            title=canonical, game_id=game_id, max_price=max_price, min_cut=min_cut, country=country
        )
    )
    typer.echo(f"watching #{rule.id}: {canonical} ({game_id})")


@app.command("list")
def list_() -> None:
    """Show all watches."""
    settings = load_settings()
    store = Store(settings.db_path)
    for rule in store.list_watches():
        conds = []
        if rule.max_price is not None:
            conds.append(f"price<={rule.max_price}")
        if rule.min_cut is not None:
            conds.append(f"cut>={rule.min_cut}%")
        typer.echo(f"#{rule.id} {rule.title} [{' or '.join(conds)}] country={rule.country}")


@app.command()
def run() -> None:
    """Run one monitoring pass over all watches."""
    settings = load_settings()
    store = Store(settings.db_path)
    source = ItadClient(settings.itad_api_key)
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    for r in run_once(store, source, notifier):
        if r.error:
            status = f"ERROR {r.error}"
        elif r.notified:
            status = "notified"
        elif r.deal:
            status = "deal already notified"
        else:
            status = "no deal"
        typer.echo(f"#{r.watch_id} {r.title}: {status}")


@app.command()
def report(watch_id: int, limit: int = typer.Option(10, help="history entries to show")) -> None:
    """Show recent price history for one watch."""
    settings = load_settings()
    store = Store(settings.db_path)
    for fetched_at, p in store.price_history(watch_id, limit):
        typer.echo(f"{fetched_at} {p.shop}: {p.currency} {p.price:.2f} (-{p.cut}%)")
