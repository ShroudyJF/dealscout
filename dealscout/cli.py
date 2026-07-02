"""DealScout CLI: add / list / run / report."""

import typer

from dealscout.config import SettingsError, load_settings
from dealscout.models import WatchRule
from dealscout.notify import TelegramNotifier
from dealscout.runner import run_once
from dealscout.sources.base import SourceError
from dealscout.sources.itad import ItadClient
from dealscout.store import Store

app = typer.Typer(
    help="DealScout - personal price-watching agent",
    pretty_exceptions_show_locals=False,
)


@app.command()
def add(
    title: str,
    max_price: float | None = typer.Option(None, help="notify when best price <= this"),
    min_cut: int | None = typer.Option(None, help="notify when discount percent >= this"),
    country: str = typer.Option("MY", help="ITAD country code"),
) -> None:
    """Look up TITLE on IsThereAnyDeal and start watching it."""
    if max_price is None and min_cut is None:
        raise typer.BadParameter("set at least one of --max-price / --min-cut")
    try:
        settings = load_settings()
        source = ItadClient(settings.itad_api_key)
        game_id, canonical = source.lookup_game(title)
        store = Store(settings.db_path)
        rule = store.add_watch(
            WatchRule(
                title=canonical,
                game_id=game_id,
                max_price=max_price,
                min_cut=min_cut,
                country=country,
            )
        )
    except (SettingsError, SourceError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"watching #{rule.id}: {canonical} ({game_id})")


@app.command("list")
def list_() -> None:
    """Show all watches."""
    try:
        settings = load_settings()
        store = Store(settings.db_path)
        watches = store.list_watches()
    except SettingsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    for rule in watches:
        conds = []
        if rule.max_price is not None:
            conds.append(f"price<={rule.max_price}")
        if rule.min_cut is not None:
            conds.append(f"cut>={rule.min_cut}%")
        typer.echo(f"#{rule.id} {rule.title} [{' or '.join(conds)}] country={rule.country}")


@app.command()
def run() -> None:
    """Run one monitoring pass over all watches."""
    try:
        settings = load_settings()
        store = Store(settings.db_path)
        source = ItadClient(settings.itad_api_key)
        notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        results = run_once(store, source, notifier)
    except SettingsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    has_error = False
    for r in results:
        if r.error:
            status = f"ERROR {r.error}"
            has_error = True
        elif r.notified:
            status = "notified"
        elif r.deal:
            status = "deal already notified"
        else:
            status = "no deal"
        typer.echo(f"#{r.watch_id} {r.title}: {status}")
    if has_error:
        raise typer.Exit(1)


@app.command()
def report(watch_id: int, limit: int = typer.Option(10, help="history entries to show")) -> None:
    """Show recent price history for one watch."""
    try:
        settings = load_settings()
        store = Store(settings.db_path)
        history = store.price_history(watch_id, limit)
    except SettingsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    for fetched_at, p in history:
        typer.echo(f"{fetched_at} {p.shop}: {p.currency} {p.price:.2f} (-{p.cut}%)")
