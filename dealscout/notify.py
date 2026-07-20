"""Telegram notification channel."""

import json

import httpx

from dealscout.models import Deal

TELEGRAM_API = "https://api.telegram.org"


class NotifyError(RuntimeError):
    pass


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, client: httpx.Client | None = None) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._client = client or httpx.Client(base_url=TELEGRAM_API, timeout=15)

    def send(self, text: str) -> None:
        resp = self._client.post(
            f"/bot{self._bot_token}/sendMessage",
            content=json.dumps({"chat_id": self._chat_id, "text": text}),
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            raise NotifyError(f"telegram send failed: HTTP {resp.status_code} {resp.text}")


def format_deal(deal: Deal, display: tuple[str, float] | None = None) -> str:
    b = deal.best
    lines = [
        f"🎯 DealScout: {deal.title}",
        f"{b.shop}: {b.currency} {b.price:.2f} (regular {b.regular:.2f}, -{b.cut}%)",
    ]
    if display is not None:
        lines.append(f"≈ {display[0]} {display[1]:.2f}")
    lines.append(f"why: {deal.reason}")
    lines.append(f"{b.url}")
    return "\n".join(lines)
