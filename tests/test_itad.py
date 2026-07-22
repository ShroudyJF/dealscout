import httpx
import pytest

from dealscout.models import WatchRule
from dealscout.sources.base import GameNotFoundError, SourceError
from dealscout.sources.itad import BASE_URL, ItadClient

LOOKUP_OK = {"found": True, "game": {"id": "g-123", "slug": "hades", "title": "Hades"}}
PRICES_OK = [
    {
        "id": "g-123",
        "deals": [
            {
                "shop": {"id": 61, "name": "Steam"},
                "price": {"amount": 12.49, "currency": "USD"},
                "regular": {"amount": 24.99, "currency": "USD"},
                "cut": 50,
                "url": "https://store.steampowered.com/app/1145360/",
            }
        ],
    }
]


def make_client(handler):
    return httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))


def test_lookup_game_returns_id_and_title():
    def handler(request):
        assert request.url.path == "/games/lookup/v1"
        assert request.url.params["title"] == "hades"
        return httpx.Response(200, json=LOOKUP_OK)

    itad = ItadClient("k", client=make_client(handler))
    assert itad.lookup_game("hades") == ("g-123", "Hades")


def test_lookup_game_not_found():
    itad = ItadClient("k", client=make_client(lambda r: httpx.Response(200, json={"found": False})))
    with pytest.raises(GameNotFoundError):
        itad.lookup_game("no-such-game")


def test_fetch_prices_parses_deals():
    def handler(request):
        assert request.url.path == "/games/prices/v3"
        assert request.url.params["country"] == "MY"
        return httpx.Response(200, json=PRICES_OK)

    itad = ItadClient("k", client=make_client(handler))
    rule = WatchRule(id=1, title="Hades", game_id="g-123", max_price=15.0)
    points = itad.fetch_prices(rule)
    assert len(points) == 1
    assert (points[0].shop, points[0].price, points[0].cut) == ("Steam", 12.49, 50)


OVERVIEW_OK = {
    "prices": [
        {
            "id": "g-123",
            "current": {
                "shop": {"id": 61, "name": "Steam"},
                "price": {"amount": 7.49, "currency": "USD"},
                "regular": {"amount": 24.99, "currency": "USD"},
                "cut": 70,
                "url": "https://itad.link/cur",
            },
            "lowest": {
                "shop": {"id": 61, "name": "Steam"},
                "price": {"amount": 6.24, "currency": "USD"},
                "regular": {"amount": 24.99, "currency": "USD"},
                "cut": 75,
                "timestamp": "2025-09-17T19:20:27+02:00",
            },
        }
    ],
    "bundles": [],
}


def test_fetch_overview_parses_current_and_low():
    def handler(request):
        assert request.url.path == "/games/overview/v2"
        assert request.url.params["country"] == "MY"
        return httpx.Response(200, json=OVERVIEW_OK)

    itad = ItadClient("k", client=make_client(handler))
    rule = WatchRule(id=1, title="Hades", game_id="g-123", max_price=15.0)
    ov = itad.fetch_overview(rule)
    assert ov.current.price == 7.49
    assert ov.current.shop == "Steam"
    assert ov.historical_low.price == 6.24
    assert ov.historical_low.cut == 75
    assert ov.historical_low.seen_at == "2025-09-17T19:20:27+02:00"


def test_fetch_overview_missing_lowest():
    body = {"prices": [{"id": "g-123", "current": OVERVIEW_OK["prices"][0]["current"]}], "bundles": []}
    itad = ItadClient("k", client=make_client(lambda r: httpx.Response(200, json=body)))
    ov = itad.fetch_overview(WatchRule(id=1, title="Hades", game_id="g-123", max_price=15.0))
    assert ov.historical_low is None


def test_fetch_overview_http_error_raises():
    itad = ItadClient("k", client=make_client(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(SourceError):
        itad.fetch_overview(WatchRule(id=1, title="Hades", game_id="g-123", max_price=15.0))
