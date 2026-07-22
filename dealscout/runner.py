"""One monitoring pass over all watches: fetch -> store -> judge -> notify."""

from pydantic import BaseModel

from dealscout import judge
from dealscout.models import Deal
from dealscout.notify import format_deal
from dealscout.sources.base import SourceAdapter
from dealscout.store import Store


class RunResult(BaseModel):
    watch_id: int
    title: str
    deal: Deal | None = None
    notified: bool = False
    error: str | None = None


def _display_price(fx, display_currency, deal):
    # FX is best-effort: never let conversion break a real deal notification.
    if fx is None or display_currency is None:
        return None
    try:
        amount = fx.convert(deal.best.price, deal.best.currency, display_currency)
    except Exception:
        return None
    return (display_currency, amount)


def _make_verdict(source, llm, rule):
    # Best-effort: any failure (no fetch_overview, ITAD error, LLM error) -> None, never blocks notify.
    if llm is None:
        return None
    fetch = getattr(source, "fetch_overview", None)
    if fetch is None:
        return None
    try:
        overview = fetch(rule)
        return llm.judge(overview, rule)
    except Exception:
        return None


def run_once(
    store: Store,
    source: SourceAdapter,
    notifier,
    fx=None,
    display_currency: str | None = None,
    llm=None,
) -> list[RunResult]:
    results: list[RunResult] = []
    for rule in store.list_watches():
        assert rule.id is not None
        result = RunResult(watch_id=rule.id, title=rule.title)
        try:
            points = source.fetch_prices(rule)
            store.record_prices(rule.id, points)
            deal = judge.evaluate(rule, points)
            result.deal = deal
            if deal is not None and store.last_notified_price(rule.id) != deal.best.price:
                display = _display_price(fx, display_currency, deal)
                verdict = _make_verdict(source, llm, rule)
                message = format_deal(deal, display, verdict)
                notifier.send(message)
                store.record_notification(rule.id, deal.best.price, message)
                result.notified = True
        except Exception as exc:  # per-watch isolation: one failure must not stop the pass
            result.error = f"{type(exc).__name__}: {exc}"
        results.append(result)
    return results
