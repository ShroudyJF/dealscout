"""Deterministic price-trend features from an ITAD history series. Pure, offline-testable."""

import statistics

from dealscout.models import PricePoint, TrendFeatures


def compute_trend(
    history: list[PricePoint], current: PricePoint, window_days: int = 90
) -> TrendFeatures | None:
    """Summarise a price-history window into signals for the LLM verdict.

    Returns None when there are fewer than 2 observations (too little to judge a
    trend) — callers then fall back to the M2a overview-only verdict.
    """
    if len(history) < 2:
        return None
    prices = [p.price for p in history]
    regulars = [p.regular for p in history]
    return TrendFeatures(
        window_days=window_days,
        points=len(history),
        low=min(prices),
        median=statistics.median(prices),
        times_at_or_below_current=sum(p <= current.price for p in prices),
        regular_recently_raised=current.regular > min(regulars),
    )
