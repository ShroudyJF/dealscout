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


def run_once(store: Store, source: SourceAdapter, notifier) -> list[RunResult]:
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
                message = format_deal(deal)
                notifier.send(message)
                store.record_notification(rule.id, deal.best.price, message)
                result.notified = True
        except Exception as exc:  # per-watch isolation: one failure must not stop the pass
            result.error = f"{type(exc).__name__}: {exc}"
        results.append(result)
    return results
