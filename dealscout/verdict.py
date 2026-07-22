"""LLM deal-quality verdict. Provider-agnostic: judge() is a Protocol; Gemini implements it."""

from typing import Literal, Protocol

from pydantic import BaseModel

from dealscout.models import PriceOverview, WatchRule


class VerdictError(RuntimeError):
    pass


class DealVerdict(BaseModel):
    rating: Literal["buy_now", "good", "wait", "skip"]
    reason: str
    wait_target: float | None = None


class VerdictLLM(Protocol):
    def judge(self, overview: PriceOverview, rule: WatchRule) -> DealVerdict:
        """Return a structured deal verdict. Raises VerdictError on failure."""
        ...


def build_prompt(overview: PriceOverview, rule: WatchRule) -> str:
    cur = overview.current
    low = overview.historical_low
    cond = []
    if rule.max_price is not None:
        cond.append(f"目标价 <= {rule.max_price}")
    if rule.min_cut is not None:
        cond.append(f"折扣 >= {rule.min_cut}%")
    low_line = (
        f"史上最低：{low.currency} {low.price:.2f}（-{low.cut}%，{low.seen_at}）"
        if low is not None
        else "史上最低：未知"
    )
    return (
        "你是精明的游戏比价顾问。根据下面的数据，判断现在这个价到底值不值得买、"
        "还是再等等，给出简短的中文理由。\n"
        f"游戏：{rule.title}\n"
        f"现价：{cur.currency} {cur.price:.2f}（原价 {cur.regular:.2f}，-{cur.cut}%，{cur.shop}）\n"
        f"{low_line}\n"
        f"用户条件：{('；'.join(cond)) or '无'}\n"
        "rating 取值：buy_now(现在就买)、good(不错可入)、wait(建议再等)、skip(别买)。"
        "若建议再等，wait_target 给一个值得设提醒的目标价（否则留空）。"
    )


class GeminiVerdictLLM:
    """VerdictLLM backed by Gemini structured output (google-genai SDK).

    Confirmed against the installed google-genai==2.13.0 SDK's own test suite
    (site-packages/google/genai/tests/models/test_generate_content.py): passing
    a bare Pydantic model class as ``response_schema`` (e.g.
    ``response_schema=CountryInfo``) is a directly tested, supported usage.
    ``response_json_schema`` also exists as an alternative for schemas that
    ``response_schema`` can't process, but is not needed here.
    """

    def __init__(self, api_key: str, model: str, client=None) -> None:
        self._model = model
        if client is not None:
            self._client = client
        else:
            from google import genai  # imported lazily so offline tests need no SDK network

            self._client = genai.Client(api_key=api_key)

    def judge(self, overview: PriceOverview, rule: WatchRule) -> DealVerdict:
        prompt = build_prompt(overview, rule)
        try:
            from google.genai import types

            resp = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=DealVerdict,
                ),
            )
        except Exception as exc:  # any SDK/network error -> domain error, caller degrades gracefully
            raise VerdictError(f"gemini judge failed: {exc}") from exc
        text = getattr(resp, "text", None)
        if not text:
            raise VerdictError("gemini judge returned empty response")
        try:
            return DealVerdict.model_validate_json(text)
        except Exception as exc:
            raise VerdictError(f"gemini verdict not valid: {exc}") from exc
