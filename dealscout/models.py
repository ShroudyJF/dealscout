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
