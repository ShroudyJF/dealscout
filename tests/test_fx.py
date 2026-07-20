import httpx
import pytest

from dealscout.fx import FRANKFURTER_API, FxConverter, FxError


def make_client(handler):
    return httpx.Client(base_url=FRANKFURTER_API, transport=httpx.MockTransport(handler))


def test_convert_same_currency_short_circuits():
    def handler(request):  # must never be called
        raise AssertionError("no HTTP for same-currency conversion")

    fx = FxConverter(client=make_client(handler))
    assert fx.convert(12.49, "USD", "USD") == 12.49


def test_convert_applies_rate():
    def handler(request):
        assert request.url.path == "/latest"
        assert request.url.params["from"] == "USD"
        assert request.url.params["to"] == "MYR"
        return httpx.Response(200, json={"amount": 1.0, "base": "USD", "rates": {"MYR": 4.7}})

    fx = FxConverter(client=make_client(handler))
    assert fx.convert(10.0, "USD", "MYR") == pytest.approx(47.0)


def test_convert_caches_rate():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"rates": {"MYR": 4.7}})

    fx = FxConverter(client=make_client(handler))
    fx.convert(10.0, "USD", "MYR")
    fx.convert(20.0, "USD", "MYR")
    assert calls["n"] == 1


def test_convert_http_error_raises():
    fx = FxConverter(client=make_client(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(FxError):
        fx.convert(10.0, "USD", "MYR")


def test_convert_missing_rate_raises():
    fx = FxConverter(client=make_client(lambda r: httpx.Response(200, json={"rates": {}})))
    with pytest.raises(FxError):
        fx.convert(10.0, "USD", "MYR")
