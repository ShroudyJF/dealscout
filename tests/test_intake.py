import pytest
from pydantic import ValidationError

from dealscout.intake import ParseError, WatchRequest, build_prompt, resolve_watch
from dealscout.sources.base import GameNotFoundError


def test_watch_request_defaults_all_none():
    req = WatchRequest()
    assert req.title is None
    assert req.max_price is None
    assert req.currency is None
    assert req.min_cut is None


def test_watch_request_accepts_partial():
    req = WatchRequest(title="Elden Ring", min_cut=20)
    assert req.title == "Elden Ring"
    assert req.min_cut == 20
    assert req.max_price is None


def test_watch_request_rejects_out_of_range_min_cut():
    with pytest.raises(ValidationError):
        WatchRequest(title="Elden Ring", min_cut=130)   # >100% discount is impossible


def test_watch_request_rejects_nonpositive_max_price():
    with pytest.raises(ValidationError):
        WatchRequest(title="Elden Ring", max_price=0)    # a price threshold must be > 0


def test_parse_error_is_runtime_error():
    assert issubclass(ParseError, RuntimeError)


def test_build_prompt_includes_user_text_and_field_rules():
    prompt = build_prompt("盯艾尔登法环 降到RM120")
    assert "盯艾尔登法环 降到RM120" in prompt   # 原句喂给模型
    assert "title" in prompt
    assert "英文" in prompt                      # 要求翻成英文名
    assert "MYR" in prompt                       # 没说币种默认 MYR
    assert "max_price" in prompt
    assert "min_cut" in prompt


class FakeSource:
    def __init__(self, game_id="g-1", canonical="Elden Ring"):
        self._game_id = game_id
        self._canonical = canonical
        self.looked_up = None

    def lookup_game(self, title):
        self.looked_up = title
        return self._game_id, self._canonical


class RecordingFx:
    def __init__(self, rate=0.2377):
        self.rate = rate
        self.calls = []

    def convert(self, amount, from_ccy, to_ccy):
        self.calls.append((amount, from_ccy, to_ccy))
        return amount * self.rate


def test_resolve_watch_usd_passes_through_without_fx():
    fx = RecordingFx()
    rule = resolve_watch(
        WatchRequest(title="Elden Ring", max_price=30, currency="USD"), FakeSource(), fx
    )
    assert rule.title == "Elden Ring"
    assert rule.game_id == "g-1"
    assert rule.max_price == 30
    assert rule.country == "MY"
    assert fx.calls == []            # USD needs no conversion


def test_resolve_watch_myr_converted_to_usd():
    fx = RecordingFx(rate=0.2377)
    rule = resolve_watch(
        WatchRequest(title="Elden Ring", max_price=120, currency="MYR"), FakeSource(), fx
    )
    assert fx.calls == [(120, "MYR", "USD")]
    assert rule.max_price == round(120 * 0.2377, 2)   # 28.52


def test_resolve_watch_missing_currency_defaults_to_myr():
    fx = RecordingFx()
    resolve_watch(
        WatchRequest(title="Elden Ring", max_price=100, currency=None), FakeSource(), fx
    )
    assert fx.calls == [(100, "MYR", "USD")]           # None currency -> MYR (product default)


def test_resolve_watch_pure_min_cut_skips_fx():
    fx = RecordingFx()
    rule = resolve_watch(WatchRequest(title="Elden Ring", min_cut=25), FakeSource(), fx)
    assert rule.max_price is None
    assert rule.min_cut == 25
    assert fx.calls == []


def test_resolve_watch_uses_canonical_title_from_lookup():
    src = FakeSource(game_id="g-elden", canonical="ELDEN RING")
    rule = resolve_watch(
        WatchRequest(title="elden ring", max_price=30, currency="USD"), src, RecordingFx()
    )
    assert src.looked_up == "elden ring"
    assert rule.title == "ELDEN RING"
    assert rule.game_id == "g-elden"


def test_resolve_watch_no_title_raises_parse_error():
    with pytest.raises(ParseError, match="游戏"):
        resolve_watch(
            WatchRequest(title=None, max_price=30, currency="USD"), FakeSource(), RecordingFx()
        )


def test_resolve_watch_blank_title_raises_parse_error():
    with pytest.raises(ParseError, match="游戏"):
        resolve_watch(
            WatchRequest(title="   ", max_price=30, currency="USD"), FakeSource(), RecordingFx()
        )


def test_resolve_watch_no_condition_raises_parse_error():
    with pytest.raises(ParseError, match="条件"):
        resolve_watch(WatchRequest(title="Elden Ring"), FakeSource(), RecordingFx())


def test_resolve_watch_propagates_game_not_found():
    class MissingSource:
        def lookup_game(self, title):
            raise GameNotFoundError("game not found on ITAD: 'zzz'")

    with pytest.raises(GameNotFoundError):
        resolve_watch(
            WatchRequest(title="zzz", max_price=30, currency="USD"), MissingSource(), RecordingFx()
        )


def test_resolve_watch_propagates_fx_error():
    from dealscout.fx import FxError

    class FailingFx:
        def convert(self, amount, from_ccy, to_ccy):
            raise FxError("fx rate failed: HTTP 500")

    with pytest.raises(FxError):
        resolve_watch(
            WatchRequest(title="Elden Ring", max_price=120, currency="MYR"), FakeSource(), FailingFx()
        )


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


def test_gemini_parser_parses_structured_json():
    from dealscout.intake import GeminiWatchParser

    fake = _FakeGenaiClient(text='{"title": "Elden Ring", "max_price": 120, "currency": "MYR", "min_cut": null}')
    parser = GeminiWatchParser(api_key="k", model="gemini-x", client=fake)
    req = parser.parse("盯艾尔登法环 降到RM120")
    assert req.title == "Elden Ring"
    assert req.max_price == 120
    assert req.currency == "MYR"
    assert req.min_cut is None
    assert fake.models.calls                       # 确实调用了模型


def test_gemini_parser_sends_json_schema_config():
    from dealscout.intake import GeminiWatchParser, WatchRequest

    fake = _FakeGenaiClient(text='{"title": "Hades"}')
    parser = GeminiWatchParser(api_key="k", model="gemini-2.5-flash", client=fake)
    parser.parse("盯 Hades")
    kwargs = fake.models.calls[0]
    assert kwargs["model"] == "gemini-2.5-flash"
    assert kwargs["config"].response_mime_type == "application/json"
    assert kwargs["config"].response_schema is WatchRequest


def test_gemini_parser_wraps_errors():
    from dealscout.intake import GeminiWatchParser

    fake = _FakeGenaiClient(exc=RuntimeError("api down"))
    parser = GeminiWatchParser(api_key="k", model="gemini-x", client=fake)
    with pytest.raises(ParseError):
        parser.parse("x")


def test_gemini_parser_empty_response_raises():
    from dealscout.intake import GeminiWatchParser

    fake = _FakeGenaiClient(text="")
    parser = GeminiWatchParser(api_key="k", model="gemini-x", client=fake)
    with pytest.raises(ParseError, match="empty"):
        parser.parse("x")


def test_gemini_parser_bad_json_raises():
    from dealscout.intake import GeminiWatchParser

    fake = _FakeGenaiClient(text="not json at all")
    parser = GeminiWatchParser(api_key="k", model="gemini-x", client=fake)
    with pytest.raises(ParseError):
        parser.parse("x")


def test_gemini_parser_out_of_range_raises_parse_error():
    # an LLM misparse (min_cut=130) fails WatchRequest bounds -> surfaces as ParseError, not a bad watch
    from dealscout.intake import GeminiWatchParser

    fake = _FakeGenaiClient(text='{"title": "Elden Ring", "min_cut": 130}')
    parser = GeminiWatchParser(api_key="k", model="gemini-x", client=fake)
    with pytest.raises(ParseError):
        parser.parse("x")
