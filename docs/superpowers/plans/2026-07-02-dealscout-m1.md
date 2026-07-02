# DealScout M1（最小闭环）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 跑通 DealScout 最小闭环：ITAD API 抓游戏价格 → SQLite 存历史 → 规则触发判断 → Telegram 通知，全程不碰 LLM 与爬虫。

**Architecture:** 单向数据流 `WatchRule → source 抓取 → store 入库 → judge 评估 → notify 推送`，每层输入输出都是 Pydantic 模型，可独立测试。数据源走 SourceAdapter 协议（M2 加 Shopee/Lazada 时零改动接入）。HTTP 客户端全部支持注入，测试用 `httpx.MockTransport` 离线跑。

**Tech Stack:** Python 3.12+、Pydantic v2、httpx、Typer、SQLite（stdlib sqlite3）、pytest、ruff。

**Scope note:** 本计划只覆盖设计文档中的 M1。M2（NL 解析、Playwright+LLM 提取、LLM judge）与 M3（Eval 进 CI、成本追踪、llm-page-extract 孵化）在 M1 交付后各出独立计划。

## Global Constraints

- Python ≥ 3.12（先运行 `python --version` 确认；不足则从 python.org 安装）。
- 不使用 LangChain 等 Agent 框架（设计文档明确要求，自研循环是面试信号）。
- 模块间传递的数据一律是 Pydantic 模型，不传裸 dict。
- pytest 全程离线：任何测试不得访问真实网络，HTTP 一律 `httpx.MockTransport`。
- 每个任务收尾必须：`ruff check .` 通过 + `pytest -q` 全绿 + 单独 commit。
- commit message 用 `feat:` / `test:` / `docs:` / `chore:` 前缀。

## 前置准备（执行者需要用户提供 / 自行注册）

1. **ITAD API key**：在 https://isthereanydeal.com 注册 → https://isthereanydeal.com/apps/ 新建 app 拿 key。
2. **Telegram bot**：和 @BotFather 对话 `/newbot` 拿 `TELEGRAM_BOT_TOKEN`；给 bot 发一条消息后访问 `https://api.telegram.org/bot<TOKEN>/getUpdates` 从 `chat.id` 拿 `TELEGRAM_CHAT_ID`。
3. 两者只在手动验证步骤和真实运行时需要；纯开发（pytest）不需要。

---

### Task 1: 项目脚手架

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `dealscout/__init__.py`, `dealscout/sources/__init__.py`, `tests/__init__.py`, `tests/test_smoke.py`

**Interfaces:**
- Produces: 可 `pip install -e ".[dev]"` 的包骨架；`dealscout.__version__ == "0.1.0"`。

- [ ] **Step 1: 写 pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "dealscout"
version = "0.1.0"
description = "Personal price-watching agent: watches game and e-commerce prices, judges real discounts, notifies via Telegram"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
    "httpx>=0.27",
    "typer>=0.12",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.4"]

[project.scripts]
dealscout = "dealscout.cli:app"

[tool.setuptools.packages.find]
include = ["dealscout*"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

- [ ] **Step 2: 写 .gitignore**

```
.venv/
__pycache__/
*.pyc
*.egg-info/
dist/
.env
*.db
.pytest_cache/
.ruff_cache/
```

- [ ] **Step 3: 写 .env.example**

```
ITAD_API_KEY=get-yours-at-isthereanydeal.com/apps
TELEGRAM_BOT_TOKEN=123456:ABC-from-BotFather
TELEGRAM_CHAT_ID=123456789
DEALSCOUT_DB=dealscout.db
```

- [ ] **Step 4: 建包骨架**

`dealscout/__init__.py`：

```python
__version__ = "0.1.0"
```

`dealscout/sources/__init__.py` 与 `tests/__init__.py`：空文件。

- [ ] **Step 5: 写冒烟测试** `tests/test_smoke.py`

```python
def test_package_importable():
    import dealscout

    assert dealscout.__version__ == "0.1.0"
```

- [ ] **Step 6: 建虚拟环境并安装**

Run（PowerShell）: `python -m venv .venv; .venv\Scripts\Activate.ps1; pip install -e ".[dev]"`
Expected: 安装成功无报错。

- [ ] **Step 7: 跑测试和 lint**

Run: `pytest -q` → Expected: `1 passed`
Run: `ruff check .` → Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore .env.example dealscout tests
git commit -m "chore: scaffold dealscout package"
```

---

### Task 2: 数据模型 models.py

**Files:**
- Create: `dealscout/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `WatchRule(id: int | None, title: str, game_id: str, max_price: float | None, min_cut: int | None, country: str = "MY")`——max_price/min_cut 至少一个非空，否则 ValidationError。
  - `PricePoint(shop: str, price: float, regular: float, cut: int, currency: str, url: str)`
  - `Deal(watch_id: int, title: str, best: PricePoint, reason: str)`

- [ ] **Step 1: 写失败测试** `tests/test_models.py`

```python
import pytest
from pydantic import ValidationError

from dealscout.models import Deal, PricePoint, WatchRule


def test_watch_rule_accepts_single_condition():
    rule = WatchRule(title="Hades", game_id="abc", max_price=15.0)
    assert rule.min_cut is None
    assert rule.country == "MY"


def test_watch_rule_rejects_no_conditions():
    with pytest.raises(ValidationError):
        WatchRule(title="Hades", game_id="abc")


def test_deal_holds_best_point():
    best = PricePoint(shop="Steam", price=12.49, regular=24.99, cut=50, currency="USD", url="https://x")
    deal = Deal(watch_id=1, title="Hades", best=best, reason="cut 50% >= 40%")
    assert deal.best.shop == "Steam"
```

- [ ] **Step 2: 确认失败**

Run: `pytest tests/test_models.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'dealscout.models'`

- [ ] **Step 3: 实现** `dealscout/models.py`

```python
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


class Deal(BaseModel):
    watch_id: int
    title: str
    best: PricePoint
    reason: str
```

- [ ] **Step 4: 确认通过**

Run: `pytest tests/test_models.py -q` → Expected: `3 passed`
Run: `ruff check .` → Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add dealscout/models.py tests/test_models.py
git commit -m "feat: add WatchRule/PricePoint/Deal models"
```

---

### Task 3: 存储层 store.py

**Files:**
- Create: `dealscout/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `WatchRule`, `PricePoint`（Task 2）。
- Produces: `Store` 类——
  - `__init__(db_path: str | Path)`（建表幂等）
  - `add_watch(rule: WatchRule) -> WatchRule`（返回带 id 的副本）
  - `list_watches() -> list[WatchRule]`
  - `record_prices(watch_id: int, points: list[PricePoint]) -> None`
  - `price_history(watch_id: int, limit: int = 50) -> list[tuple[str, PricePoint]]`（最新在前，tuple[0] 为 fetched_at 文本）
  - `last_notified_price(watch_id: int) -> float | None`
  - `record_notification(watch_id: int, price: float, message: str) -> None`
  - `close() -> None`

- [ ] **Step 1: 写失败测试** `tests/test_store.py`

```python
import pytest

from dealscout.models import PricePoint, WatchRule
from dealscout.store import Store


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def _point(price=12.49, cut=50):
    return PricePoint(shop="Steam", price=price, regular=24.99, cut=cut, currency="USD", url="https://x")


def test_add_and_list_watch_roundtrip(store):
    added = store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    assert added.id == 1
    assert store.list_watches() == [added]


def test_price_history_returns_latest_first(store):
    w = store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    store.record_prices(w.id, [_point(price=20.0)])
    store.record_prices(w.id, [_point(price=12.49)])
    history = store.price_history(w.id)
    assert [p.price for _, p in history] == [12.49, 20.0]


def test_notification_price_tracking(store):
    w = store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    assert store.last_notified_price(w.id) is None
    store.record_notification(w.id, 12.49, "msg")
    assert store.last_notified_price(w.id) == 12.49
```

- [ ] **Step 2: 确认失败**

Run: `pytest tests/test_store.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'dealscout.store'`

- [ ] **Step 3: 实现** `dealscout/store.py`

```python
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
```

- [ ] **Step 4: 确认通过**

Run: `pytest tests/test_store.py -q` → Expected: `3 passed`
Run: `ruff check .` → Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add dealscout/store.py tests/test_store.py
git commit -m "feat: add SQLite store for watches, price history, notifications"
```

---

### Task 4: 配置 config.py

**Files:**
- Create: `dealscout/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Settings(itad_api_key: str, telegram_bot_token: str, telegram_chat_id: str, db_path: str)`
  - `load_settings() -> Settings`（读 env，先 `load_dotenv()`；缺必填项抛 `SettingsError`）
  - `SettingsError(RuntimeError)`

- [ ] **Step 1: 写失败测试** `tests/test_config.py`

```python
import pytest

from dealscout.config import SettingsError, load_settings


def test_load_settings_from_env(monkeypatch):
    monkeypatch.setenv("ITAD_API_KEY", "k1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c1")
    monkeypatch.setenv("DEALSCOUT_DB", "custom.db")
    s = load_settings()
    assert (s.itad_api_key, s.telegram_bot_token, s.telegram_chat_id) == ("k1", "t1", "c1")
    assert s.db_path == "custom.db"


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("ITAD_API_KEY", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c1")
    with pytest.raises(SettingsError, match="ITAD_API_KEY"):
        load_settings()
```

- [ ] **Step 2: 确认失败**

Run: `pytest tests/test_config.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'dealscout.config'`

- [ ] **Step 3: 实现** `dealscout/config.py`

```python
"""Environment-based settings. Copy .env.example to .env and fill in keys."""

import os

from dotenv import load_dotenv
from pydantic import BaseModel


class SettingsError(RuntimeError):
    pass


class Settings(BaseModel):
    itad_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    db_path: str


_REQUIRED = {
    "itad_api_key": "ITAD_API_KEY",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
}


def load_settings() -> Settings:
    load_dotenv()
    values: dict[str, str] = {}
    for field, env in _REQUIRED.items():
        value = os.environ.get(env, "").strip()
        if not value:
            raise SettingsError(f"missing environment variable {env}, see .env.example")
        values[field] = value
    values["db_path"] = os.environ.get("DEALSCOUT_DB", "dealscout.db")
    return Settings(**values)
```

注意：`test_missing_key_raises` 里若本机 `.env` 已含 ITAD_API_KEY 会导致误通过/误失败——开发机 M1 阶段先不建 `.env`，或测试前 `monkeypatch.chdir(tmp_path)` 隔离。若遇到该问题，在两个测试函数首行加 `monkeypatch.chdir(tmp_path)`（fixture 参数加 `tmp_path`），使 `load_dotenv()` 找不到 `.env`。

- [ ] **Step 4: 确认通过**

Run: `pytest tests/test_config.py -q` → Expected: `2 passed`
Run: `ruff check .` → Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add dealscout/config.py tests/test_config.py
git commit -m "feat: add env-based settings loader"
```

---

### Task 5: 数据源 sources/base.py + sources/itad.py

**Files:**
- Create: `dealscout/sources/base.py`, `dealscout/sources/itad.py`
- Test: `tests/test_itad.py`

**Interfaces:**
- Consumes: `WatchRule`, `PricePoint`（Task 2）。
- Produces:
  - `SourceAdapter`（Protocol）：`fetch_prices(rule: WatchRule) -> list[PricePoint]`
  - `SourceError(RuntimeError)`、`GameNotFoundError(SourceError)`
  - `ItadClient(api_key: str, client: httpx.Client | None = None)`，方法 `lookup_game(title: str) -> tuple[str, str]`（返回 (game_id, canonical_title)）和 `fetch_prices(rule) -> list[PricePoint]`
  - 常量 `BASE_URL = "https://api.isthereanydeal.com"`

- [ ] **Step 1: 写失败测试** `tests/test_itad.py`

```python
import httpx
import pytest

from dealscout.models import WatchRule
from dealscout.sources.base import GameNotFoundError
from dealscout.sources.itad import BASE_URL, ItadClient

LOOKUP_OK = {"found": True, "game": {"id": "g-123", "slug": "hades", "title": "Hades"}}
PRICES_OK = [
    {
        "id": "g-123",
        "deals": [
            {
                "shop": {"id": 61, "name": "Steam"},
                "price": {"amount": 12.49, "currency": "USD"},
                "regular": {"amount": 24.99, "currency": "USD"},
                "cut": 50,
                "url": "https://store.steampowered.com/app/1145360/",
            }
        ],
    }
]


def make_client(handler):
    return httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))


def test_lookup_game_returns_id_and_title():
    def handler(request):
        assert request.url.path == "/games/lookup/v1"
        assert request.url.params["title"] == "hades"
        return httpx.Response(200, json=LOOKUP_OK)

    itad = ItadClient("k", client=make_client(handler))
    assert itad.lookup_game("hades") == ("g-123", "Hades")


def test_lookup_game_not_found():
    itad = ItadClient("k", client=make_client(lambda r: httpx.Response(200, json={"found": False})))
    with pytest.raises(GameNotFoundError):
        itad.lookup_game("no-such-game")


def test_fetch_prices_parses_deals():
    def handler(request):
        assert request.url.path == "/games/prices/v3"
        assert request.url.params["country"] == "MY"
        return httpx.Response(200, json=PRICES_OK)

    itad = ItadClient("k", client=make_client(handler))
    rule = WatchRule(id=1, title="Hades", game_id="g-123", max_price=15.0)
    points = itad.fetch_prices(rule)
    assert len(points) == 1
    assert (points[0].shop, points[0].price, points[0].cut) == ("Steam", 12.49, 50)
```

- [ ] **Step 2: 确认失败**

Run: `pytest tests/test_itad.py -q`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现** `dealscout/sources/base.py`

```python
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
```

- [ ] **Step 4: 实现** `dealscout/sources/itad.py`

```python
"""IsThereAnyDeal API v2 adapter. Docs: https://docs.isthereanydeal.com/"""

import httpx

from dealscout.models import PricePoint, WatchRule
from dealscout.sources.base import GameNotFoundError, SourceError

BASE_URL = "https://api.isthereanydeal.com"


class ItadClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=15)

    def lookup_game(self, title: str) -> tuple[str, str]:
        resp = self._client.get("/games/lookup/v1", params={"key": self._api_key, "title": title})
        if resp.status_code != 200:
            raise SourceError(f"ITAD lookup failed: HTTP {resp.status_code}")
        data = resp.json()
        if not data.get("found") or not data.get("game"):
            raise GameNotFoundError(f"game not found on ITAD: {title!r}")
        return data["game"]["id"], data["game"]["title"]

    def fetch_prices(self, rule: WatchRule) -> list[PricePoint]:
        resp = self._client.post(
            "/games/prices/v3",
            params={"key": self._api_key, "country": rule.country},
            json=[rule.game_id],
        )
        if resp.status_code != 200:
            raise SourceError(f"ITAD prices failed: HTTP {resp.status_code}")
        data = resp.json()
        if not data:
            return []
        return [
            PricePoint(
                shop=deal["shop"]["name"],
                price=deal["price"]["amount"],
                regular=deal["regular"]["amount"],
                cut=deal["cut"],
                currency=deal["price"]["currency"],
                url=deal["url"],
            )
            for deal in data[0].get("deals", [])
        ]
```

- [ ] **Step 5: 确认通过**

Run: `pytest tests/test_itad.py -q` → Expected: `3 passed`
Run: `ruff check .` → Expected: 通过

- [ ] **Step 6: 手动验证真实 API（需要 ITAD_API_KEY，联网）**

fixture 的响应结构来自 ITAD v2 文档记忆，必须对照真实 API 验证一次：

Run（PowerShell，替换 KEY）:
`$env:ITAD_API_KEY="<你的key>"; python -c "import os; from dealscout.sources.itad import ItadClient; from dealscout.models import WatchRule; c = ItadClient(os.environ['ITAD_API_KEY']); gid, t = c.lookup_game('Hades'); print(gid, t); print(c.fetch_prices(WatchRule(id=1, title=t, game_id=gid, max_price=999))[:3])"`

Expected: 打印 game id、"Hades" 和若干 PricePoint。若真实响应字段与 fixture 不符（例如 lookup 返回结构不同），以真实响应为准修正 `itad.py` 与测试 fixture，重跑 Step 5，并在 commit message 里注明。

- [ ] **Step 7: Commit**

```bash
git add dealscout/sources tests/test_itad.py
git commit -m "feat: add ITAD price source behind SourceAdapter protocol"
```

---

### Task 6: 规则判断 judge.py

**Files:**
- Create: `dealscout/judge.py`
- Test: `tests/test_judge.py`

**Interfaces:**
- Consumes: `WatchRule`, `PricePoint`, `Deal`（Task 2）。
- Produces: `evaluate(rule: WatchRule, points: list[PricePoint]) -> Deal | None`——取最低价 point；`price <= max_price` 或 `cut >= min_cut` 任一满足（OR 语义）即返回 Deal，否则 None；空列表返回 None。

- [ ] **Step 1: 写失败测试** `tests/test_judge.py`

```python
from dealscout import judge
from dealscout.models import PricePoint, WatchRule


def _point(price, cut, shop="Steam"):
    return PricePoint(shop=shop, price=price, regular=24.99, cut=cut, currency="USD", url="https://x")


def _rule(**kw):
    return WatchRule(id=1, title="Hades", game_id="g", **kw)


def test_triggers_on_max_price():
    deal = judge.evaluate(_rule(max_price=15.0), [_point(12.49, 50)])
    assert deal is not None
    assert deal.best.price == 12.49


def test_triggers_on_min_cut():
    assert judge.evaluate(_rule(min_cut=40), [_point(18.0, 40)]) is not None


def test_no_trigger_when_conditions_unmet():
    assert judge.evaluate(_rule(max_price=10.0), [_point(12.49, 20)]) is None


def test_picks_cheapest_shop():
    deal = judge.evaluate(_rule(max_price=15.0), [_point(14.0, 40, shop="GOG"), _point(12.49, 50)])
    assert deal.best.shop == "Steam"


def test_empty_points_returns_none():
    assert judge.evaluate(_rule(max_price=15.0), []) is None
```

- [ ] **Step 2: 确认失败**

Run: `pytest tests/test_judge.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'dealscout.judge'`

- [ ] **Step 3: 实现** `dealscout/judge.py`

```python
"""Rule-based deal evaluation. M2 layers LLM analysis on top of this."""

from dealscout.models import Deal, PricePoint, WatchRule


def evaluate(rule: WatchRule, points: list[PricePoint]) -> Deal | None:
    if not points:
        return None
    best = min(points, key=lambda p: p.price)
    reasons = []
    if rule.max_price is not None and best.price <= rule.max_price:
        reasons.append(f"price {best.price:.2f} <= target {rule.max_price:.2f}")
    if rule.min_cut is not None and best.cut >= rule.min_cut:
        reasons.append(f"cut {best.cut}% >= target {rule.min_cut}%")
    if not reasons:
        return None
    assert rule.id is not None, "rule must be persisted (have an id) before evaluation"
    return Deal(watch_id=rule.id, title=rule.title, best=best, reason="; ".join(reasons))
```

- [ ] **Step 4: 确认通过**

Run: `pytest tests/test_judge.py -q` → Expected: `5 passed`
Run: `ruff check .` → Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add dealscout/judge.py tests/test_judge.py
git commit -m "feat: add rule-based deal evaluation"
```

---

### Task 7: 通知 notify.py

**Files:**
- Create: `dealscout/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: `Deal`（Task 2）。
- Produces:
  - `TelegramNotifier(bot_token: str, chat_id: str, client: httpx.Client | None = None)`，方法 `send(text: str) -> None`（非 200 抛 `NotifyError`）
  - `format_deal(deal: Deal) -> str`
  - `NotifyError(RuntimeError)`、常量 `TELEGRAM_API = "https://api.telegram.org"`

- [ ] **Step 1: 写失败测试** `tests/test_notify.py`

```python
import httpx
import pytest

from dealscout.models import Deal, PricePoint
from dealscout.notify import TELEGRAM_API, NotifyError, TelegramNotifier, format_deal


def test_send_posts_to_bot_endpoint():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(base_url=TELEGRAM_API, transport=httpx.MockTransport(handler))
    TelegramNotifier("TOKEN", "42", client=client).send("hello")
    assert seen["path"] == "/botTOKEN/sendMessage"
    assert '"chat_id": "42"' in seen["body"]
    assert '"text": "hello"' in seen["body"]


def test_send_raises_on_http_error():
    client = httpx.Client(
        base_url=TELEGRAM_API,
        transport=httpx.MockTransport(lambda r: httpx.Response(401, text="unauthorized")),
    )
    with pytest.raises(NotifyError):
        TelegramNotifier("TOKEN", "42", client=client).send("hello")


def test_format_deal_mentions_price_and_reason():
    best = PricePoint(shop="Steam", price=12.49, regular=24.99, cut=50, currency="USD", url="https://x")
    text = format_deal(Deal(watch_id=1, title="Hades", best=best, reason="cut 50% >= 40%"))
    assert "Hades" in text
    assert "12.49" in text
    assert "cut 50% >= 40%" in text
```

- [ ] **Step 2: 确认失败**

Run: `pytest tests/test_notify.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'dealscout.notify'`

- [ ] **Step 3: 实现** `dealscout/notify.py`

```python
"""Telegram notification channel."""

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
            json={"chat_id": self._chat_id, "text": text},
        )
        if resp.status_code != 200:
            raise NotifyError(f"telegram send failed: HTTP {resp.status_code} {resp.text}")


def format_deal(deal: Deal) -> str:
    b = deal.best
    return (
        f"🎯 DealScout: {deal.title}\n"
        f"{b.shop}: {b.currency} {b.price:.2f} (regular {b.regular:.2f}, -{b.cut}%)\n"
        f"why: {deal.reason}\n"
        f"{b.url}"
    )
```

- [ ] **Step 4: 确认通过**

Run: `pytest tests/test_notify.py -q` → Expected: `3 passed`
Run: `ruff check .` → Expected: 通过

- [ ] **Step 5: 手动验证真实 Telegram（需要 token/chat id，联网）**

Run（PowerShell，替换两个值）:
`$env:TELEGRAM_BOT_TOKEN="<token>"; $env:TELEGRAM_CHAT_ID="<chat_id>"; python -c "import os; from dealscout.notify import TelegramNotifier; TelegramNotifier(os.environ['TELEGRAM_BOT_TOKEN'], os.environ['TELEGRAM_CHAT_ID']).send('DealScout test message')"`

Expected: 手机 Telegram 收到 "DealScout test message"。

- [ ] **Step 6: Commit**

```bash
git add dealscout/notify.py tests/test_notify.py
git commit -m "feat: add Telegram notifier and deal formatter"
```

---

### Task 8: 调度 runner.py

**Files:**
- Create: `dealscout/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `Store`（Task 3）、`SourceAdapter`（Task 5）、`judge.evaluate`（Task 6）、`format_deal` + notifier 鸭子类型 `send(text)`（Task 7）。
- Produces:
  - `RunResult(watch_id: int, title: str, deal: Deal | None = None, notified: bool = False, error: str | None = None)`
  - `run_once(store, source, notifier) -> list[RunResult]`——逐 watch：抓取→入库→评估→（有 deal 且价格 != 上次已通知价格）发送并记录；单个 watch 抛异常只记入该结果的 error，不中断整轮。

- [ ] **Step 1: 写失败测试** `tests/test_runner.py`

```python
import pytest

from dealscout.models import PricePoint, WatchRule
from dealscout.runner import run_once
from dealscout.sources.base import SourceError
from dealscout.store import Store


class FakeSource:
    def __init__(self, by_game):
        self.by_game = by_game

    def fetch_prices(self, rule):
        result = self.by_game[rule.game_id]
        if isinstance(result, Exception):
            raise result
        return result


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def _point(price=12.49):
    return PricePoint(shop="Steam", price=price, regular=24.99, cut=50, currency="USD", url="https://x")


def test_deal_triggers_notification(store):
    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    results = run_once(store, FakeSource({"g1": [_point()]}), notifier)
    assert results[0].notified is True
    assert len(notifier.sent) == 1


def test_same_price_not_renotified(store):
    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    run_once(store, FakeSource({"g1": [_point()]}), notifier)
    results = run_once(store, FakeSource({"g1": [_point()]}), notifier)
    assert results[0].notified is False
    assert len(notifier.sent) == 1


def test_source_error_isolated_per_watch(store):
    store.add_watch(WatchRule(title="Broken", game_id="bad", max_price=15.0))
    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    results = run_once(store, FakeSource({"bad": SourceError("boom"), "g1": [_point()]}), notifier)
    assert results[0].error is not None
    assert results[1].notified is True
```

- [ ] **Step 2: 确认失败**

Run: `pytest tests/test_runner.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'dealscout.runner'`

- [ ] **Step 3: 实现** `dealscout/runner.py`

```python
"""One monitoring pass over all watches: fetch -> store -> judge -> notify."""

from pydantic import BaseModel

from dealscout import judge
from dealscout.models import Deal
from dealscout.notify import format_deal
from dealscout.sources.base import SourceAdapter
from dealscout.store import Store


class RunResult(BaseModel):
    watch_id: int
    title: str
    deal: Deal | None = None
    notified: bool = False
    error: str | None = None


def run_once(store: Store, source: SourceAdapter, notifier) -> list[RunResult]:
    results: list[RunResult] = []
    for rule in store.list_watches():
        assert rule.id is not None
        result = RunResult(watch_id=rule.id, title=rule.title)
        try:
            points = source.fetch_prices(rule)
            store.record_prices(rule.id, points)
            deal = judge.evaluate(rule, points)
            result.deal = deal
            if deal is not None and store.last_notified_price(rule.id) != deal.best.price:
                message = format_deal(deal)
                notifier.send(message)
                store.record_notification(rule.id, deal.best.price, message)
                result.notified = True
        except Exception as exc:  # per-watch isolation: one failure must not stop the pass
            result.error = f"{type(exc).__name__}: {exc}"
        results.append(result)
    return results
```

- [ ] **Step 4: 确认通过**

Run: `pytest tests/test_runner.py -q` → Expected: `3 passed`
Run: `ruff check .` → Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add dealscout/runner.py tests/test_runner.py
git commit -m "feat: add per-watch monitoring pass with notification dedup"
```

---

### Task 9: CLI cli.py + 端到端手动验证

**Files:**
- Create: `dealscout/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: 前面全部模块。
- Produces: Typer app `dealscout.cli:app`，命令 `add TITLE [--max-price F] [--min-cut N] [--country MY]`、`list`、`run`、`report WATCH_ID [--limit N]`。

- [ ] **Step 1: 写失败测试** `tests/test_cli.py`

```python
import pytest
from typer.testing import CliRunner

from dealscout import cli
from dealscout.config import Settings

runner = CliRunner()


class FakeItad:
    def __init__(self, api_key, client=None):
        pass

    def lookup_game(self, title):
        return "g-123", "Hades"

    def fetch_prices(self, rule):
        return []


class FakeNotifier:
    def __init__(self, *args, **kwargs):
        pass

    def send(self, text):
        pass


@pytest.fixture()
def fake_env(tmp_path, monkeypatch):
    settings = Settings(
        itad_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="c",
        db_path=str(tmp_path / "cli.db"),
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "ItadClient", FakeItad)
    monkeypatch.setattr(cli, "TelegramNotifier", FakeNotifier)
    return settings


def test_add_then_list(fake_env):
    result = runner.invoke(cli.app, ["add", "hades", "--max-price", "15"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli.app, ["list"])
    assert "Hades" in result.output


def test_run_reports_no_deal(fake_env):
    runner.invoke(cli.app, ["add", "hades", "--max-price", "15"])
    result = runner.invoke(cli.app, ["run"])
    assert result.exit_code == 0, result.output
    assert "no deal" in result.output
```

- [ ] **Step 2: 确认失败**

Run: `pytest tests/test_cli.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'dealscout.cli'`

- [ ] **Step 3: 实现** `dealscout/cli.py`

```python
"""DealScout CLI: add / list / run / report."""

import typer

from dealscout.config import load_settings
from dealscout.models import WatchRule
from dealscout.notify import TelegramNotifier
from dealscout.runner import run_once
from dealscout.sources.itad import ItadClient
from dealscout.store import Store

app = typer.Typer(help="DealScout - personal price-watching agent")


@app.command()
def add(
    title: str,
    max_price: float | None = typer.Option(None, help="notify when best price <= this"),
    min_cut: int | None = typer.Option(None, help="notify when discount percent >= this"),
    country: str = typer.Option("MY", help="ITAD country code"),
) -> None:
    """Look up TITLE on IsThereAnyDeal and start watching it."""
    settings = load_settings()
    source = ItadClient(settings.itad_api_key)
    game_id, canonical = source.lookup_game(title)
    store = Store(settings.db_path)
    rule = store.add_watch(
        WatchRule(
            title=canonical, game_id=game_id, max_price=max_price, min_cut=min_cut, country=country
        )
    )
    typer.echo(f"watching #{rule.id}: {canonical} ({game_id})")


@app.command("list")
def list_() -> None:
    """Show all watches."""
    settings = load_settings()
    store = Store(settings.db_path)
    for rule in store.list_watches():
        conds = []
        if rule.max_price is not None:
            conds.append(f"price<={rule.max_price}")
        if rule.min_cut is not None:
            conds.append(f"cut>={rule.min_cut}%")
        typer.echo(f"#{rule.id} {rule.title} [{' or '.join(conds)}] country={rule.country}")


@app.command()
def run() -> None:
    """Run one monitoring pass over all watches."""
    settings = load_settings()
    store = Store(settings.db_path)
    source = ItadClient(settings.itad_api_key)
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    for r in run_once(store, source, notifier):
        if r.error:
            status = f"ERROR {r.error}"
        elif r.notified:
            status = "notified"
        elif r.deal:
            status = "deal already notified"
        else:
            status = "no deal"
        typer.echo(f"#{r.watch_id} {r.title}: {status}")


@app.command()
def report(watch_id: int, limit: int = typer.Option(10, help="history entries to show")) -> None:
    """Show recent price history for one watch."""
    settings = load_settings()
    store = Store(settings.db_path)
    for fetched_at, p in store.price_history(watch_id, limit):
        typer.echo(f"{fetched_at} {p.shop}: {p.currency} {p.price:.2f} (-{p.cut}%)")
```

- [ ] **Step 4: 确认通过**

Run: `pytest -q`（全量） → Expected: 全部通过（25 个）
Run: `ruff check .` → Expected: 通过

- [ ] **Step 5: 端到端手动验证（真实 key，联网）**

把 `.env.example` 复制为 `.env` 填入真实值，然后：

Run: `dealscout add "Hades" --min-cut 30`
Expected: 输出 `watching #1: Hades (<game_id>)`

Run: `dealscout run`
Expected: 若 Hades 正在打折 ≥30%，手机 Telegram 收到带价格和理由的消息，CLI 显示 `notified`；否则显示 `no deal`（可改用 `--min-cut 1` 提高触发概率验证通知链路）。

Run: `dealscout report 1`
Expected: 打印刚抓到的价格记录。

- [ ] **Step 6: Commit**

```bash
git add dealscout/cli.py tests/test_cli.py
git commit -m "feat: add CLI (add/list/run/report) wiring full M1 loop"
```

---

### Task 10: CI、README 与定时运行

**Files:**
- Create: `.github/workflows/ci.yml`, `README.md`

**Interfaces:**
- Consumes: 全部已完成模块；CI 跑 `ruff check .` + `pytest -q`（全离线，无需任何 secret）。

- [ ] **Step 1: 写 CI workflow** `.github/workflows/ci.yml`

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: pytest -q
```

- [ ] **Step 2: 写 README.md**

内容骨架（用真实中英文写全，不留 TODO）：

```markdown
# DealScout 🎯

Personal price-watching agent. Watches game deals (and soon Shopee/Lazada),
stores price history, judges whether a "deal" is real, and pings you on
Telegram with the reasoning.

## Why

Price trackers record prices. DealScout is built to *reason* about them —
M2 adds LLM-driven page understanding for stores without APIs and
fake-discount detection from price history.

## Quick start

​```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env   # fill in ITAD + Telegram keys
dealscout add "Hades" --min-cut 30
dealscout run            # one monitoring pass
dealscout report 1       # price history
​```

## Architecture

​```mermaid
flowchart LR
    CLI[cli.py] --> Runner[runner.py]
    Runner --> Sources[sources/ SourceAdapter]
    Sources --> ITAD[itad.py]
    Runner --> Store[(SQLite store.py)]
    Runner --> Judge[judge.py]
    Judge --> Notify[notify.py Telegram]
​```

Every layer exchanges Pydantic models and is tested offline with
httpx.MockTransport. No agent framework — the loop is ~40 lines you can read.

## Scheduled runs

Windows Task Scheduler:
`schtasks /create /tn DealScout /tr "C:\path\to\.venv\Scripts\dealscout.exe run" /sc daily /st 09:00`
Linux/macOS cron: `0 9 * * * cd /path/to/dealscout && .venv/bin/dealscout run`

## Roadmap

- [x] M1: ITAD source -> SQLite -> rule trigger -> Telegram
- [ ] M2: natural-language watch rules; Shopee/Lazada via Playwright + LLM extraction; fake-discount judge
- [ ] M3: eval harness in CI, cost tracking, spin off `llm-page-extract`
​```
```

（README 中 mermaid/代码块按上述内容落地，去掉引用用的零宽标记。）

- [ ] **Step 3: 本地全量验证**

Run: `pytest -q` → Expected: 全绿
Run: `ruff check .` → Expected: 通过

- [ ] **Step 4: Commit**

```bash
git add .github README.md
git commit -m "docs: add README and CI workflow"
```

- [ ] **Step 5: 发布到 GitHub（需要 gh 已登录）**

Run: `gh repo create dealscout --public --source . --push`
Expected: 仓库创建并推送；Actions 页面 ci 工作流跑绿。若未安装/未登录 gh，改为在 github.com 手动建空仓库后 `git remote add origin <url>; git push -u origin master`。

- [ ] **Step 6: 验证 M1 完成标志**

用 Task Scheduler 配好每日运行后，观察 1-2 天：手机每天收到通知或 CLI 日志正常。此时 M1 交付完成，可开始写 M2 计划。
