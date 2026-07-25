from dealscout.models import PricePoint
from dealscout.trend import compute_trend


def _pt(price, regular, seen="2025-05-01"):
    return PricePoint(
        shop="Steam", price=price, regular=regular, cut=0, currency="USD", url="", seen_at=seen
    )


def test_compute_trend_basic_stats():
    history = [_pt(20, 40), _pt(10, 40), _pt(15, 40)]
    t = compute_trend(history, _pt(10, 40))
    assert t.window_days == 90
    assert t.points == 3
    assert t.low == 10
    assert t.median == 15                       # median of [10,15,20]
    assert t.times_at_or_below_current == 1     # only the 10 is <= current 10
    assert t.regular_recently_raised is False   # current regular 40 == min regular 40, not >


def test_compute_trend_detects_inflated_regular():
    history = [_pt(20, 30), _pt(18, 30)]
    t = compute_trend(history, _pt(9, 45))      # current regular 45 > min historical regular 30
    assert t.regular_recently_raised is True


def test_compute_trend_counts_common_price():
    history = [_pt(10, 40), _pt(10, 40), _pt(9, 40), _pt(20, 40)]
    t = compute_trend(history, _pt(10, 40))
    assert t.times_at_or_below_current == 3     # 10, 10, 9 are all <= 10


def test_compute_trend_too_few_points_returns_none():
    assert compute_trend([_pt(10, 40)], _pt(10, 40)) is None
    assert compute_trend([], _pt(10, 40)) is None
