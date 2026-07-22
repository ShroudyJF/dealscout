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


class _FakeResp:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, text=None, exc=None):
        self._text = text
        self._exc = exc
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return _FakeResp(self._text)


class _FakeGenaiClient:
    def __init__(self, text=None, exc=None):
        self.models = _FakeModels(text=text, exc=exc)


def test_gemini_judge_parses_structured_json():
    from dealscout.verdict import GeminiVerdictLLM

    fake = _FakeGenaiClient(text='{"rating": "good", "reason": "接近史低", "wait_target": 6.24}')
    llm = GeminiVerdictLLM(api_key="k", model="gemini-x", client=fake)
    v = llm.judge(_overview(), WatchRule(id=1, title="Hades", game_id="g", min_cut=30))
    assert v.rating == "good"
    assert v.wait_target == 6.24
    assert fake.models.calls  # 确实调用了模型


def test_gemini_judge_wraps_errors():
    from dealscout.verdict import GeminiVerdictLLM, VerdictError

    fake = _FakeGenaiClient(exc=RuntimeError("api down"))
    llm = GeminiVerdictLLM(api_key="k", model="gemini-x", client=fake)
    with pytest.raises(VerdictError):
        llm.judge(_overview(), WatchRule(id=1, title="Hades", game_id="g", min_cut=30))


def test_gemini_judge_empty_response_raises():
    from dealscout.verdict import GeminiVerdictLLM, VerdictError

    fake = _FakeGenaiClient(text="")
    llm = GeminiVerdictLLM(api_key="k", model="gemini-x", client=fake)
    with pytest.raises(VerdictError):
        llm.judge(_overview(), WatchRule(id=1, title="Hades", game_id="g", min_cut=30))


def test_gemini_judge_bad_json_raises():
    from dealscout.verdict import GeminiVerdictLLM, VerdictError

    fake = _FakeGenaiClient(text="not json at all")
    llm = GeminiVerdictLLM(api_key="k", model="gemini-x", client=fake)
    with pytest.raises(VerdictError):
        llm.judge(_overview(), WatchRule(id=1, title="Hades", game_id="g", min_cut=30))
