"""SQLite persistence: watches, price history, notification log."""

import sqlite3
from pathlib import Path

from dealscout.models import PricePoint, WatchRule

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    game_id TEXT NOT NULL,
    max_price REAL,
    min_cut INTEGER,
    country TEXT NOT NULL DEFAULT 'MY',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER NOT NULL REFERENCES watches(id),
    shop TEXT NOT NULL,
    price REAL NOT NULL,
    regular REAL NOT NULL,
    cut INTEGER NOT NULL,
    currency TEXT NOT NULL,
    url TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER NOT NULL REFERENCES watches(id),
    price REAL NOT NULL,
    message TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Store:
    def __init__(self, db_path: str | Path) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def add_watch(self, rule: WatchRule) -> WatchRule:
        cur = self._conn.execute(
            "INSERT INTO watches (title, game_id, max_price, min_cut, country)"
            " VALUES (?, ?, ?, ?, ?)",
            (rule.title, rule.game_id, rule.max_price, rule.min_cut, rule.country),
        )
        self._conn.commit()
        return rule.model_copy(update={"id": cur.lastrowid})

    def list_watches(self) -> list[WatchRule]:
        rows = self._conn.execute("SELECT * FROM watches ORDER BY id").fetchall()
        return [
            WatchRule(
                id=r["id"],
                title=r["title"],
                game_id=r["game_id"],
                max_price=r["max_price"],
                min_cut=r["min_cut"],
                country=r["country"],
            )
            for r in rows
        ]

    def record_prices(self, watch_id: int, points: list[PricePoint]) -> None:
        self._conn.executemany(
            "INSERT INTO price_history (watch_id, shop, price, regular, cut, currency, url)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(watch_id, p.shop, p.price, p.regular, p.cut, p.currency, p.url) for p in points],
        )
        self._conn.commit()

    def price_history(self, watch_id: int, limit: int = 50) -> list[tuple[str, PricePoint]]:
        rows = self._conn.execute(
            "SELECT * FROM price_history WHERE watch_id = ? ORDER BY id DESC LIMIT ?",
            (watch_id, limit),
        ).fetchall()
        return [
            (
                r["fetched_at"],
                PricePoint(
                    shop=r["shop"],
                    price=r["price"],
                    regular=r["regular"],
                    cut=r["cut"],
                    currency=r["currency"],
                    url=r["url"],
                ),
            )
            for r in rows
        ]

    def last_notified_price(self, watch_id: int) -> float | None:
        row = self._conn.execute(
            "SELECT price FROM notifications WHERE watch_id = ? ORDER BY id DESC LIMIT 1",
            (watch_id,),
        ).fetchone()
        return None if row is None else row["price"]

    def record_notification(self, watch_id: int, price: float, message: str) -> None:
        self._conn.execute(
            "INSERT INTO notifications (watch_id, price, message) VALUES (?, ?, ?)",
            (watch_id, price, message),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
