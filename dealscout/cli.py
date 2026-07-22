"""DealScout CLI: add / list / run / report."""

from datetime import datetime, timezone

import typer

from dealscout.config import SettingsError, load_settings
from dealscout.fx import FxConverter, FxError
from dealscout.intake import GeminiWatchParser, ParseError, resolve_watch
from dealscout.models import WatchRule
from dealscout.notify import TelegramNotifier
from dealscout.runner import run_once
from dealscout.schedule import should_run_now
from dealscout.sources.base import SourceError
from dealscout.sources.itad import ItadClient
from dealscout.store import Store
from dealscout.verdict import GeminiVerdictLLM

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


def _fmt_conds(req, rule) -> str:
    conds = []
    if rule.max_price is not None:
        src_ccy = (req.currency or "MYR").upper()
        if src_ccy == "USD":
            conds.append(f"price<=${rule.max_price}")
        else:
            conds.append(f"price<=${rule.max_price} (≈{src_ccy}{req.max_price:g})")
    if rule.min_cut is not None:
        conds.append(f"cut>={rule.min_cut}%")
    return " or ".join(conds)


@app.command()
def watch(sentence: str) -> None:
    """Parse a natural-language request and start watching a game."""
    try:
        settings = load_settings()
        parser = GeminiWatchParser(settings.gemini_api_key, settings.llm_model)
        req = parser.parse(sentence)
        source = ItadClient(settings.itad_api_key)
        fx = FxConverter()
        rule = resolve_watch(req, source, fx)
        store = Store(settings.db_path)
        rule = store.add_watch(rule)
    except (SettingsError, ParseError, SourceError, FxError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"watching #{rule.id}: {rule.title} [{_fmt_conds(req, rule)}] country={rule.country}")


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


def _execute_run(settings) -> bool:
    """Build wiring, run one pass, print statuses; return True if any watch errored."""
    store = Store(settings.db_path)
    source = ItadClient(settings.itad_api_key)
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    fx = FxConverter()
    llm = GeminiVerdictLLM(settings.gemini_api_key, settings.llm_model)
    results = run_once(
        store, source, notifier, fx=fx, display_currency=settings.display_currency, llm=llm
    )
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
    return has_error


@app.command()
def run() -> None:
    """Run one monitoring pass over all watches."""
    try:
        settings = load_settings()
        has_error = _execute_run(settings)
    except SettingsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if has_error:
        raise typer.Exit(1)


@app.command()
def tick() -> None:
    """Cron heartbeat: run only when it is the configured hour in the configured timezone."""
    try:
        settings = load_settings()
        now_utc = datetime.now(timezone.utc)
        if not should_run_now(settings.tz, settings.run_hour, now_utc):
            typer.echo(f"skipped: not {settings.run_hour:02d}:00 in {settings.tz}")
            return
        has_error = _execute_run(settings)
    except SettingsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
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
