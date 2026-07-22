"""Natural-language watch setup. Provider-agnostic: parse() is a Protocol; Gemini implements it."""

from pydantic import BaseModel


class ParseError(RuntimeError):
    pass


class WatchRequest(BaseModel):
    title: str | None = None        # specific game name (English, for ITAD lookup); None if not identifiable
    max_price: float | None = None  # absolute price threshold, in `currency`
    currency: str | None = None     # ISO code of max_price, e.g. "USD"/"MYR"; None if no price
    min_cut: int | None = None      # discount-percent threshold


def build_prompt(text: str) -> str:
    return (
        "你是 DealScout 的下单助手。用户用一句话描述想盯的游戏和触发条件，"
        "把它解析成结构化字段。\n"
        f"用户输入：{text}\n"
        "字段规则：\n"
        "- title：游戏的英文名（把中文名如\"艾尔登法环\"翻成\"Elden Ring\"）；"
        "若听不出是哪个具体游戏（如\"找个恐怖游戏\"），留 null。\n"
        "- max_price：绝对价格阈值的数字；无则 null。\n"
        "- currency：max_price 的币种 ISO 码（如 USD、MYR）；"
        "用户没明说币种时默认 MYR；无价格则 null。\n"
        "- min_cut：折扣百分比阈值的整数（\"打七折\"=30、\"降三成\"=30）；无则 null。"
    )
