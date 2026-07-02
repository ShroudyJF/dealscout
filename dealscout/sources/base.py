"""SourceAdapter protocol — every price source implements this."""

from typing import Protocol

from dealscout.models import PricePoint, WatchRule


class SourceError(RuntimeError):
    pass


class GameNotFoundError(SourceError):
    pass


class SourceAdapter(Protocol):
    def fetch_prices(self, rule: WatchRule) -> list[PricePoint]:
        """Return current prices for the watched item. Raises SourceError on failure."""
        ...
