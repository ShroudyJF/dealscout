import pytest

from dealscout.models import PricePoint, PriceOverview, WatchRule
from dealscout.verdict import DealVerdict, build_prompt


def _overview():
    cur = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    low = PricePoint(
        shop="Steam", price=6.24, regular=24.99, cut=75, currency="USD", url="https://x",
        seen_at="2025-09-17",
    )
    return PriceOverview(current=cur, historical_low=low)


def test_deal_verdict_rejects_bad_rating():
    with pytest.raises(Exception):
        DealVerdict(rating="amazing", reason="r")


def test_deal_verdict_accepts_valid():
    v = DealVerdict(rating="good", reason="接近史低", wait_target=6.24)
    assert v.rating == "good"
    assert v.wait_target == 6.24


def test_build_prompt_includes_key_numbers():
    rule = WatchRule(id=1, title="Hades", game_id="g", min_cut=30)
    prompt = build_prompt(_overview(), rule)
    assert "Hades" in prompt
    assert "7.49" in prompt
    assert "6.24" in prompt          # 史低价
    assert "2025-09-17" in prompt    # 史低日期


def test_build_prompt_handles_no_low():
    cur = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    rule = WatchRule(id=1, title="Hades", game_id="g", min_cut=30)
    prompt = build_prompt(PriceOverview(current=cur), rule)
    assert "Hades" in prompt  # 不含史低也能生成
