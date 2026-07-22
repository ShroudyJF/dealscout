import httpx
import pytest

from dealscout.models import Deal, PricePoint
from dealscout.notify import TELEGRAM_API, NotifyError, TelegramNotifier, format_deal


def test_send_posts_to_bot_endpoint():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(base_url=TELEGRAM_API, transport=httpx.MockTransport(handler))
    TelegramNotifier("TOKEN", "42", client=client).send("hello")
    assert seen["path"] == "/botTOKEN/sendMessage"
    assert '"chat_id": "42"' in seen["body"]
    assert '"text": "hello"' in seen["body"]


def test_send_raises_on_http_error():
    client = httpx.Client(
        base_url=TELEGRAM_API,
        transport=httpx.MockTransport(lambda r: httpx.Response(401, text="unauthorized")),
    )
    with pytest.raises(NotifyError):
        TelegramNotifier("TOKEN", "42", client=client).send("hello")


def test_format_deal_mentions_price_and_reason():
    best = PricePoint(shop="Steam", price=12.49, regular=24.99, cut=50, currency="USD", url="https://x")
    text = format_deal(Deal(watch_id=1, title="Hades", best=best, reason="cut 50% >= 40%"))
    assert "Hades" in text
    assert "12.49" in text
    assert "cut 50% >= 40%" in text


def test_format_deal_without_display_has_no_convert_line():
    best = PricePoint(shop="Steam", price=12.49, regular=24.99, cut=50, currency="USD", url="https://x")
    text = format_deal(Deal(watch_id=1, title="Hades", best=best, reason="r"))
    assert "≈" not in text


def test_format_deal_with_display_adds_convert_line():
    best = PricePoint(shop="Steam", price=12.49, regular=24.99, cut=50, currency="USD", url="https://x")
    text = format_deal(Deal(watch_id=1, title="Hades", best=best, reason="r"), display=("MYR", 58.7))
    assert "≈ MYR 58.70" in text


def test_format_deal_without_verdict_has_no_verdict_line():
    best = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    text = format_deal(Deal(watch_id=1, title="Hades", best=best, reason="r"))
    assert "好价判断" not in text


def test_format_deal_with_verdict_adds_section():
    from dealscout.verdict import DealVerdict

    best = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    v = DealVerdict(rating="good", reason="接近史低，可入", wait_target=6.24)
    text = format_deal(Deal(watch_id=1, title="Hades", best=best, reason="r"), verdict=v)
    assert "好价判断" in text
    assert "接近史低" in text
