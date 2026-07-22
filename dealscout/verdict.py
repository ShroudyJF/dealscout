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
