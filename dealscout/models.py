"""Pydantic models shared across all DealScout layers."""

from pydantic import BaseModel, model_validator


class WatchRule(BaseModel):
    id: int | None = None
    title: str
    game_id: str
    max_price: float | None = None
    min_cut: int | None = None
    country: str = "MY"

    @model_validator(mode="after")
    def _at_least_one_condition(self) -> "WatchRule":
        if self.max_price is None and self.min_cut is None:
            raise ValueError("watch rule needs max_price or min_cut")
        return self


class PricePoint(BaseModel):
    shop: str
    price: float
    regular: float
    cut: int
    currency: str
    url: str
    seen_at: str | None = None


class Deal(BaseModel):
    watch_id: int
    title: str
    best: PricePoint
    reason: str


class PriceOverview(BaseModel):
    current: PricePoint
    historical_low: PricePoint | None = None


class TrendFeatures(BaseModel):
    window_days: int                 # stats window (nominal 90, matches ITAD's ~3-month default)
    points: int                      # number of price observations in the window
    low: float                       # lowest observed price
    median: float                    # median observed price
    times_at_or_below_current: int   # observations with price <= current price (fake-scarcity signal)
    regular_recently_raised: bool    # current list price exceeds the window's lowest list price
