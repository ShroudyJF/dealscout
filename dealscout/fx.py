"""Currency conversion via Frankfurter (free, no key, ECB reference rates)."""

import httpx

FRANKFURTER_API = "https://api.frankfurter.dev"


class FxError(RuntimeError):
    pass


class FxConverter:
    def __init__(self, base_url: str = FRANKFURTER_API, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=15, follow_redirects=True)
        self._cache: dict[tuple[str, str], float] = {}

    def _rate(self, from_ccy: str, to_ccy: str) -> float:
        key = (from_ccy, to_ccy)
        if key in self._cache:
            return self._cache[key]
        resp = self._client.get("/v1/latest", params={"from": from_ccy, "to": to_ccy})
        if resp.status_code != 200:
            raise FxError(f"fx rate failed: HTTP {resp.status_code}")
        rates = resp.json().get("rates", {})
        if to_ccy not in rates:
            raise FxError(f"fx rate missing {to_ccy} in response")
        self._cache[key] = rates[to_ccy]
        return self._cache[key]

    def convert(self, amount: float, from_ccy: str, to_ccy: str) -> float:
        if from_ccy == to_ccy:
            return amount
        return amount * self._rate(from_ccy, to_ccy)
