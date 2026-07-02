"""IsThereAnyDeal API v2 adapter. Docs: https://docs.isthereanydeal.com/"""

import httpx

from dealscout.models import PricePoint, WatchRule
from dealscout.sources.base import GameNotFoundError, SourceError

BASE_URL = "https://api.isthereanydeal.com"


class ItadClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=15)

    def lookup_game(self, title: str) -> tuple[str, str]:
        resp = self._client.get("/games/lookup/v1", params={"key": self._api_key, "title": title})
        if resp.status_code != 200:
            raise SourceError(f"ITAD lookup failed: HTTP {resp.status_code}")
        data = resp.json()
        if not data.get("found") or not data.get("game"):
            raise GameNotFoundError(f"game not found on ITAD: {title!r}")
        return data["game"]["id"], data["game"]["title"]

    def fetch_prices(self, rule: WatchRule) -> list[PricePoint]:
        resp = self._client.post(
            "/games/prices/v3",
            params={"key": self._api_key, "country": rule.country},
            json=[rule.game_id],
        )
        if resp.status_code != 200:
            raise SourceError(f"ITAD prices failed: HTTP {resp.status_code}")
        data = resp.json()
        if not data:
            return []
        return [
            PricePoint(
                shop=deal["shop"]["name"],
                price=deal["price"]["amount"],
                regular=deal["regular"]["amount"],
                cut=deal["cut"],
                currency=deal["price"]["currency"],
                url=deal["url"],
            )
            for deal in data[0].get("deals", [])
        ]
