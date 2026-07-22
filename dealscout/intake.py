"""Natural-language watch setup. Provider-agnostic: parse() is a Protocol; Gemini implements it."""

from pydantic import BaseModel

from dealscout.models import WatchRule


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


def resolve_watch(req: WatchRequest, source, fx, country: str = "MY") -> WatchRule:
    """Turn a parsed WatchRequest into a storable WatchRule.

    source: object with lookup_game(title) -> (game_id, canonical)
    fx:     object with convert(amount, from_ccy, to_ccy) -> float
    Raises ParseError (no game / no condition), GameNotFoundError/SourceError
    (lookup), FxError (conversion). FX is load-bearing here — failures propagate.
    """
    if not req.title or not req.title.strip():
        raise ParseError("没听出具体游戏名，请说清是哪个游戏")
    if req.max_price is None and req.min_cut is None:
        raise ParseError("没听出价格或折扣条件")

    game_id, canonical = source.lookup_game(req.title)

    max_price_usd = None
    if req.max_price is not None:
        ccy = (req.currency or "MYR").upper()   # default MYR matches build_prompt
        if ccy == "USD":
            max_price_usd = req.max_price
        else:
            max_price_usd = round(fx.convert(req.max_price, ccy, "USD"), 2)

    return WatchRule(
        title=canonical,
        game_id=game_id,
        max_price=max_price_usd,
        min_cut=req.min_cut,
        country=country,
    )
