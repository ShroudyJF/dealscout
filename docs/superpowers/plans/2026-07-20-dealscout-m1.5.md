# DealScout M1.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 DealScout 加汇率换算（通知里追加 ≈ RM 一行）、按用户时区触发的调度门控（`dealscout tick`），并把定时运行搬到 GitHub Actions 云端 + 数据库提交回仓库持久化。

**Architecture:** 两个新纯逻辑模块（`fx.py` 换算、`schedule.py` 时区门控），各自可离线单测；`format_deal`/`run_once` 扩展可选参数向后兼容；CLI 抽出 `_execute_run` 并新增 `tick` 命令；新增每小时 `watch.yml` 工作流，门控保证只有本地 9 点真跑并把 `data/dealscout.db` 提交回仓库。

**Tech Stack:** Python 3.11+、Pydantic v2、httpx、Typer、zoneinfo + tzdata、pytest、ruff、GitHub Actions。

**Scope note:** 覆盖 spec `docs/superpowers/specs/2026-07-20-dealscout-m1.5-design.md` 全部三节。

## Global Constraints

- Python ≥ 3.11（开发机 3.11.9，CI 用 3.12）。
- 不使用 LangChain 等 Agent 框架；自研逻辑。
- 模块间传递数据用 Pydantic 模型或明确类型，不传裸 dict。
- pytest 全程离线：HTTP 一律 `httpx.MockTransport`，不访问真实网络。
- 汇率是 best-effort：换算失败绝不能中断真实 deal 的通知。
- 数据库单一数据源 = `data/dealscout.db`（纳入版本管理，无密钥）。
- 汇率源 Frankfurter：`https://api.frankfurter.app`，免费无 key，`base_url` 可注入。
- 时区门控用 `zoneinfo`，依赖 `tzdata`（Windows 无系统时区库）。
- 每个任务收尾：`ruff check .` 通过 + `pytest -q` 全绿 + 单独 commit，message 用 `feat:`/`test:`/`docs:`/`chore:` 前缀。
- 所有工具用 venv 直连路径调用：`.venv\Scripts\python.exe -m pytest -q`、`.venv\Scripts\python.exe -m ruff check .`。

## 前置说明

当前 `master` 全绿（28 tests，ruff clean）。`Settings` 现有字段：`itad_api_key, telegram_bot_token, telegram_chat_id, db_path`。`run_once(store, source, notifier)` 现为三参。`format_deal(deal)` 现为单参。本计划新增字段一律带默认值，保证已有直接构造 `Settings(...)` 的测试不破。

---

### Task 1: 汇率换算模块 fx.py

**Files:**
- Create: `dealscout/fx.py`
- Test: `tests/test_fx.py`

**Interfaces:**
- Produces:
  - `FRANKFURTER_API = "https://api.frankfurter.app"`
  - `FxError(RuntimeError)`
  - `FxConverter(base_url: str = FRANKFURTER_API, client: httpx.Client | None = None)`，方法 `convert(amount: float, from_ccy: str, to_ccy: str) -> float`（同币种短路；非 200 或缺目标币种抛 `FxError`；同一 (from,to) 进程内缓存）

- [ ] **Step 1: 写失败测试** `tests/test_fx.py`

```python
import httpx
import pytest

from dealscout.fx import FRANKFURTER_API, FxConverter, FxError


def make_client(handler):
    return httpx.Client(base_url=FRANKFURTER_API, transport=httpx.MockTransport(handler))


def test_convert_same_currency_short_circuits():
    def handler(request):  # must never be called
        raise AssertionError("no HTTP for same-currency conversion")

    fx = FxConverter(client=make_client(handler))
    assert fx.convert(12.49, "USD", "USD") == 12.49


def test_convert_applies_rate():
    def handler(request):
        assert request.url.path == "/latest"
        assert request.url.params["from"] == "USD"
        assert request.url.params["to"] == "MYR"
        return httpx.Response(200, json={"amount": 1.0, "base": "USD", "rates": {"MYR": 4.7}})

    fx = FxConverter(client=make_client(handler))
    assert fx.convert(10.0, "USD", "MYR") == pytest.approx(47.0)


def test_convert_caches_rate():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"rates": {"MYR": 4.7}})

    fx = FxConverter(client=make_client(handler))
    fx.convert(10.0, "USD", "MYR")
    fx.convert(20.0, "USD", "MYR")
    assert calls["n"] == 1


def test_convert_http_error_raises():
    fx = FxConverter(client=make_client(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(FxError):
        fx.convert(10.0, "USD", "MYR")


def test_convert_missing_rate_raises():
    fx = FxConverter(client=make_client(lambda r: httpx.Response(200, json={"rates": {}})))
    with pytest.raises(FxError):
        fx.convert(10.0, "USD", "MYR")
```

- [ ] **Step 2: 确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_fx.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'dealscout.fx'`

- [ ] **Step 3: 实现** `dealscout/fx.py`

```python
"""Currency conversion via Frankfurter (free, no key, ECB reference rates)."""

import httpx

FRANKFURTER_API = "https://api.frankfurter.app"


class FxError(RuntimeError):
    pass


class FxConverter:
    def __init__(self, base_url: str = FRANKFURTER_API, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=15)
        self._cache: dict[tuple[str, str], float] = {}

    def _rate(self, from_ccy: str, to_ccy: str) -> float:
        key = (from_ccy, to_ccy)
        if key in self._cache:
            return self._cache[key]
        resp = self._client.get("/latest", params={"from": from_ccy, "to": to_ccy})
        if resp.status_code != 200:
            raise FxError(f"fx rate failed: HTTP {resp.status_code}")
        rates = resp.json().get("rates", {})
        if to_ccy not in rates:
            raise FxError(f"fx rate missing {to_ccy} in response")
        self._cache[key] = rates[to_ccy]
        return self._cache[key]

    def convert(self, amount: float, from_ccy: str, to_ccy: str) -> float:
        if from_ccy == to_ccy:
            return amount
        return amount * self._rate(from_ccy, to_ccy)
```

- [ ] **Step 4: 确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_fx.py -q` → Expected: `5 passed`
Run: `.venv\Scripts\python.exe -m ruff check .` → Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add dealscout/fx.py tests/test_fx.py
git commit -m "feat: add FxConverter (Frankfurter) for currency conversion"
```

---

### Task 2: 配置新增 display_currency / tz / run_hour

**Files:**
- Modify: `dealscout/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 现有 `Settings`、`load_settings`、`SettingsError`。
- Produces: `Settings` 新增带默认值字段 `display_currency: str = "MYR"`、`tz: str = "Asia/Kuala_Lumpur"`、`run_hour: int = 9`；`load_settings` 读 `DEALSCOUT_DISPLAY_CURRENCY`/`DEALSCOUT_TZ`/`DEALSCOUT_RUN_HOUR`（非法 run_hour 抛 `SettingsError`）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_config.py` 末尾）

```python
def test_locale_defaults(monkeypatch):
    monkeypatch.setattr("dealscout.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("ITAD_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    for var in ("DEALSCOUT_DISPLAY_CURRENCY", "DEALSCOUT_TZ", "DEALSCOUT_RUN_HOUR"):
        monkeypatch.delenv(var, raising=False)
    s = load_settings()
    assert s.display_currency == "MYR"
    assert s.tz == "Asia/Kuala_Lumpur"
    assert s.run_hour == 9


def test_locale_overrides(monkeypatch):
    monkeypatch.setattr("dealscout.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("ITAD_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("DEALSCOUT_DISPLAY_CURRENCY", "SGD")
    monkeypatch.setenv("DEALSCOUT_TZ", "America/New_York")
    monkeypatch.setenv("DEALSCOUT_RUN_HOUR", "7")
    s = load_settings()
    assert (s.display_currency, s.tz, s.run_hour) == ("SGD", "America/New_York", 7)


def test_invalid_run_hour_raises(monkeypatch):
    monkeypatch.setattr("dealscout.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("ITAD_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("DEALSCOUT_RUN_HOUR", "not-a-number")
    with pytest.raises(SettingsError, match="DEALSCOUT_RUN_HOUR"):
        load_settings()
```

- [ ] **Step 2: 确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -q`
Expected: FAIL（`AttributeError: 'Settings' object has no attribute 'display_currency'` 等）

- [ ] **Step 3: 实现**——修改 `dealscout/config.py`

把 `Settings` 改为：

```python
class Settings(BaseModel):
    itad_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    db_path: str
    display_currency: str = "MYR"
    tz: str = "Asia/Kuala_Lumpur"
    run_hour: int = 9
```

把 `load_settings` 的 `values` 类型与结尾改为（替换现有 `values: dict[str, str] = {}` 一行为 `object`，并在 `values["db_path"] = ...` 之后、`return` 之前插入 locale 解析）：

```python
def load_settings() -> Settings:
    load_dotenv()
    values: dict[str, object] = {}
    for field, env in _REQUIRED.items():
        value = os.environ.get(env, "").strip()
        if not value:
            raise SettingsError(f"missing environment variable {env}, see .env.example")
        values[field] = value
    values["db_path"] = os.environ.get("DEALSCOUT_DB", "dealscout.db")
    values["display_currency"] = os.environ.get("DEALSCOUT_DISPLAY_CURRENCY", "MYR").strip() or "MYR"
    values["tz"] = os.environ.get("DEALSCOUT_TZ", "Asia/Kuala_Lumpur").strip() or "Asia/Kuala_Lumpur"
    run_hour_raw = os.environ.get("DEALSCOUT_RUN_HOUR", "9").strip() or "9"
    try:
        run_hour = int(run_hour_raw)
    except ValueError as exc:
        raise SettingsError(
            f"DEALSCOUT_RUN_HOUR must be an integer 0-23, got {run_hour_raw!r}"
        ) from exc
    if not 0 <= run_hour <= 23:
        raise SettingsError(f"DEALSCOUT_RUN_HOUR must be 0-23, got {run_hour}")
    values["run_hour"] = run_hour
    return Settings(**values)
```

- [ ] **Step 4: 确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -q` → Expected: `5 passed`（原 2 + 新 3）
Run: `.venv\Scripts\python.exe -m pytest -q` → Expected: 全绿（36 passed）
Run: `.venv\Scripts\python.exe -m ruff check .` → Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add dealscout/config.py tests/test_config.py
git commit -m "feat: add display_currency/tz/run_hour settings"
```

---

### Task 3: format_deal 换算行 + runner 集成 fx

**Files:**
- Modify: `dealscout/notify.py`, `dealscout/runner.py`
- Test: `tests/test_notify.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes: `FxConverter`/`FxError`（Task 1）、`Deal`。
- Produces:
  - `format_deal(deal: Deal, display: tuple[str, float] | None = None) -> str`（display 非空时在原价行下加 `≈ {ccy} {amount:.2f}`）
  - `run_once(store, source, notifier, fx=None, display_currency: str | None = None) -> list[RunResult]`（有 deal 且 fx+display_currency 均给出时换算；换算异常被吞，通知照发无 ≈ 行）

- [ ] **Step 1: 写失败测试**——`tests/test_notify.py` 追加：

```python
def test_format_deal_without_display_has_no_convert_line():
    best = PricePoint(shop="Steam", price=12.49, regular=24.99, cut=50, currency="USD", url="https://x")
    text = format_deal(Deal(watch_id=1, title="Hades", best=best, reason="r"))
    assert "≈" not in text


def test_format_deal_with_display_adds_convert_line():
    best = PricePoint(shop="Steam", price=12.49, regular=24.99, cut=50, currency="USD", url="https://x")
    text = format_deal(Deal(watch_id=1, title="Hades", best=best, reason="r"), display=("MYR", 58.7))
    assert "≈ MYR 58.70" in text
```

`tests/test_runner.py` 追加（文件顶部已有 `PricePoint`/`WatchRule`/`run_once`/`Store` 导入与 `store` fixture、`FakeSource`/`FakeNotifier`/`_point`）：

```python
class FakeFx:
    def __init__(self, rate=4.7, fail=False):
        self.rate = rate
        self.fail = fail

    def convert(self, amount, from_ccy, to_ccy):
        if self.fail:
            from dealscout.fx import FxError

            raise FxError("boom")
        return amount * self.rate


def test_run_once_adds_converted_line(store):
    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    run_once(store, FakeSource({"g1": [_point()]}), notifier, fx=FakeFx(), display_currency="MYR")
    assert "≈ MYR" in notifier.sent[0]


def test_run_once_fx_failure_still_notifies(store):
    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    results = run_once(
        store, FakeSource({"g1": [_point()]}), notifier, fx=FakeFx(fail=True), display_currency="MYR"
    )
    assert results[0].notified is True
    assert "≈" not in notifier.sent[0]
```

- [ ] **Step 2: 确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_notify.py tests/test_runner.py -q`
Expected: FAIL（`format_deal() got an unexpected keyword argument 'display'` / `run_once() got an unexpected keyword argument 'fx'`）

- [ ] **Step 3: 实现**

改 `dealscout/notify.py` 的 `format_deal`：

```python
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
```

改 `dealscout/runner.py`——扩展签名并在通知前算 display：

```python
def _display_price(fx, display_currency, deal):
    # FX is best-effort: never let conversion break a real deal notification.
    if fx is None or display_currency is None:
        return None
    try:
        amount = fx.convert(deal.best.price, deal.best.currency, display_currency)
    except Exception:
        return None
    return (display_currency, amount)


def run_once(
    store: Store,
    source: SourceAdapter,
    notifier,
    fx=None,
    display_currency: str | None = None,
) -> list[RunResult]:
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
                display = _display_price(fx, display_currency, deal)
                message = format_deal(deal, display)
                notifier.send(message)
                store.record_notification(rule.id, deal.best.price, message)
                result.notified = True
        except Exception as exc:  # per-watch isolation: one failure must not stop the pass
            result.error = f"{type(exc).__name__}: {exc}"
        results.append(result)
    return results
```

- [ ] **Step 4: 确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_notify.py tests/test_runner.py -q` → Expected: 全绿（notify 5、runner 5）
Run: `.venv\Scripts\python.exe -m pytest -q` → Expected: 全绿（40 passed）
Run: `.venv\Scripts\python.exe -m ruff check .` → Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add dealscout/notify.py dealscout/runner.py tests/test_notify.py tests/test_runner.py
git commit -m "feat: append converted-currency line to deal notifications"
```

---

### Task 4: 时区门控 schedule.py + tzdata 依赖

**Files:**
- Create: `dealscout/schedule.py`
- Modify: `pyproject.toml`
- Test: `tests/test_schedule.py`

**Interfaces:**
- Produces: `should_run_now(tz_name: str, run_hour: int, now_utc: datetime) -> bool`（把 tz-aware 的 `now_utc` 转到 `tz_name` 本地时区，返回 `local.hour == run_hour`；夏令时由 tz 数据库处理）。

- [ ] **Step 1: 加依赖**——`pyproject.toml` 的 `dependencies` 列表加一行 `"tzdata>=2024.1",`（放在 `python-dotenv>=1.0` 之后）。

- [ ] **Step 2: 重新安装以拉取 tzdata**

Run: `.venv\Scripts\python.exe -m pip install -e ".[dev]"`
Expected: 安装 tzdata 成功。

- [ ] **Step 3: 写失败测试** `tests/test_schedule.py`

```python
from datetime import datetime, timezone

from dealscout.schedule import should_run_now


def _utc(y, m, d, h):
    return datetime(y, m, d, h, 0, tzinfo=timezone.utc)


def test_true_at_local_run_hour():
    # 01:00 UTC == 09:00 in Asia/Kuala_Lumpur (UTC+8, no DST)
    assert should_run_now("Asia/Kuala_Lumpur", 9, _utc(2026, 7, 20, 1)) is True


def test_false_off_hour():
    assert should_run_now("Asia/Kuala_Lumpur", 10, _utc(2026, 7, 20, 1)) is False


def test_dst_aware_new_york():
    # Same 13:00 UTC is 09:00 EDT in summer but 08:00 EST in winter.
    assert should_run_now("America/New_York", 9, _utc(2026, 7, 1, 13)) is True
    assert should_run_now("America/New_York", 9, _utc(2026, 1, 1, 13)) is False
```

- [ ] **Step 4: 确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_schedule.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'dealscout.schedule'`

- [ ] **Step 5: 实现** `dealscout/schedule.py`

```python
"""Timezone-aware run gate. GitHub Actions cron is UTC-only, so we gate here."""

from datetime import datetime
from zoneinfo import ZoneInfo


def should_run_now(tz_name: str, run_hour: int, now_utc: datetime) -> bool:
    local = now_utc.astimezone(ZoneInfo(tz_name))
    return local.hour == run_hour
```

- [ ] **Step 6: 确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_schedule.py -q` → Expected: `3 passed`
Run: `.venv\Scripts\python.exe -m ruff check .` → Expected: 通过

- [ ] **Step 7: Commit**

```bash
git add dealscout/schedule.py tests/test_schedule.py pyproject.toml
git commit -m "feat: add timezone-aware should_run_now gate (tzdata)"
```

---

### Task 5: CLI 抽 _execute_run + tick 命令

**Files:**
- Modify: `dealscout/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `load_settings`/`Settings`（Task 2）、`FxConverter`（Task 1）、`should_run_now`（Task 4）、`run_once`（Task 3）。
- Produces: 内部 `_execute_run(settings) -> bool`（返回 has_error）；新命令 `dealscout tick`（门控不通过则打印 `skipped: not HH:00 in TZ` 并退出 0，不构建 source）。

- [ ] **Step 1: 写失败测试**——修改 `tests/test_cli.py` 的 `fake_env` fixture 并追加两个测试。

在 `fake_env` fixture 里（现在 monkeypatch 了 `cli.load_settings`/`cli.ItadClient`/`cli.TelegramNotifier`）追加一个 fx 桩，防止真实网络客户端构建：

```python
class FakeFx:
    def __init__(self, *args, **kwargs):
        pass

    def convert(self, amount, from_ccy, to_ccy):
        return amount
```

在 `fake_env` fixture 内加一行：`monkeypatch.setattr(cli, "FxConverter", FakeFx)`（与现有 `monkeypatch.setattr(cli, "ItadClient", FakeItad)` 并列）。

追加测试：

```python
def test_tick_skips_off_hour(fake_env, monkeypatch):
    monkeypatch.setattr(cli, "should_run_now", lambda tz, hour, now: False)
    calls = {"itad": 0}
    real_fake = cli.ItadClient

    class CountingItad(real_fake):
        def __init__(self, *a, **kw):
            calls["itad"] += 1
            super().__init__(*a, **kw)

    monkeypatch.setattr(cli, "ItadClient", CountingItad)
    result = runner.invoke(cli.app, ["tick"])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    assert calls["itad"] == 0


def test_tick_runs_on_hour(fake_env, monkeypatch):
    monkeypatch.setattr(cli, "should_run_now", lambda tz, hour, now: True)
    runner.invoke(cli.app, ["add", "hades", "--max-price", "15"])
    result = runner.invoke(cli.app, ["tick"])
    assert result.exit_code == 0, result.output
    assert "Hades" in result.output
```

- [ ] **Step 2: 确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli.py -q`
Expected: FAIL（`tick` 命令不存在 / `cli` 无 `should_run_now`/`FxConverter` 属性）

- [ ] **Step 3: 实现**——修改 `dealscout/cli.py`

顶部导入区补充：

```python
from datetime import datetime, timezone

from dealscout.config import SettingsError, load_settings
from dealscout.fx import FxConverter
from dealscout.models import WatchRule
from dealscout.notify import TelegramNotifier
from dealscout.runner import run_once
from dealscout.schedule import should_run_now
from dealscout.sources.base import SourceError
from dealscout.sources.itad import ItadClient
from dealscout.store import Store
```

在 `run` 之前加内部辅助，并把 `run` 改为调用它；再加 `tick`：

```python
def _execute_run(settings) -> bool:
    """Build wiring, run one pass, print statuses; return True if any watch errored."""
    store = Store(settings.db_path)
    source = ItadClient(settings.itad_api_key)
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    fx = FxConverter()
    results = run_once(store, source, notifier, fx=fx, display_currency=settings.display_currency)
    has_error = False
    for r in results:
        if r.error:
            status = f"ERROR {r.error}"
            has_error = True
        elif r.notified:
            status = "notified"
        elif r.deal:
            status = "deal already notified"
        else:
            status = "no deal"
        typer.echo(f"#{r.watch_id} {r.title}: {status}")
    return has_error


@app.command()
def run() -> None:
    """Run one monitoring pass over all watches."""
    try:
        settings = load_settings()
        has_error = _execute_run(settings)
    except SettingsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if has_error:
        raise typer.Exit(1)


@app.command()
def tick() -> None:
    """Cron heartbeat: run only when it is the configured hour in the configured timezone."""
    try:
        settings = load_settings()
        now_utc = datetime.now(timezone.utc)
        if not should_run_now(settings.tz, settings.run_hour, now_utc):
            typer.echo(f"skipped: not {settings.run_hour:02d}:00 in {settings.tz}")
            return
        has_error = _execute_run(settings)
    except SettingsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if has_error:
        raise typer.Exit(1)
```

（删除原 `run` 函数体里重复的 wiring/循环，全部移入 `_execute_run`。）

- [ ] **Step 4: 确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli.py -q` → Expected: 全绿（原 5 + 新 2 = 7）
Run: `.venv\Scripts\python.exe -m pytest -q` → Expected: 全绿（45 passed）
Run: `.venv\Scripts\python.exe -m ruff check .` → Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add dealscout/cli.py tests/test_cli.py
git commit -m "feat: add tick command with timezone gate; extract _execute_run"
```

---

### Task 6: 云端工作流 + db 持久化 + 文档

**Files:**
- Create: `.github/workflows/watch.yml`
- Modify: `.gitignore`, `.env.example`, `README.md`

**Interfaces:**
- Consumes: `dealscout tick`（Task 5）。CI 无 secret（纯离线测试）；watch.yml 用 repo Secrets。

- [ ] **Step 1: 放行受管 db**——`.gitignore` 在 `*.db` 之后加一行 `!data/dealscout.db`。

- [ ] **Step 2: 更新 `.env.example`**——追加：

```
DEALSCOUT_DB=data/dealscout.db
DEALSCOUT_DISPLAY_CURRENCY=MYR
DEALSCOUT_TZ=Asia/Kuala_Lumpur
DEALSCOUT_RUN_HOUR=9
```

（保留原有三个密钥行；把原 `DEALSCOUT_DB=dealscout.db` 一行替换为上面的 `data/dealscout.db`。）

- [ ] **Step 3: 创建** `.github/workflows/watch.yml`

```yaml
name: watch
on:
  schedule:
    - cron: "0 * * * *"
  workflow_dispatch: {}
concurrency:
  group: watch
  cancel-in-progress: false
permissions:
  contents: write
jobs:
  tick:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e .
      - name: Run tick
        run: dealscout tick
        env:
          ITAD_API_KEY: ${{ secrets.ITAD_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          DEALSCOUT_DB: data/dealscout.db
          DEALSCOUT_TZ: Asia/Kuala_Lumpur
          DEALSCOUT_RUN_HOUR: "9"
          DEALSCOUT_DISPLAY_CURRENCY: MYR
      - name: Commit updated db if changed
        run: |
          git add data/dealscout.db 2>/dev/null || true
          if git diff --cached --quiet -- data/dealscout.db; then
            echo "no db changes"
          else
            git config user.name "github-actions[bot]"
            git config user.email "github-actions[bot]@users.noreply.github.com"
            git commit -m "chore: update price history [skip ci]"
            git push
          fi
```

- [ ] **Step 4: 更新 `README.md`**——在 "Scheduled runs" 之后加一节（用真实内容，无占位）：

```markdown
## Cloud scheduling (GitHub Actions)

`dealscout run` fires whenever it runs; `dealscout tick` only runs at your
local hour. The `watch.yml` workflow triggers hourly (cron is UTC-only) and
`tick` gates on your timezone, so real work happens once a day at your local
`DEALSCOUT_RUN_HOUR`, DST-safe via `zoneinfo`.

Setup:
1. Repo **Settings → Secrets and variables → Actions**: add `ITAD_API_KEY`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
2. Add a watch locally with `DEALSCOUT_DB=data/dealscout.db`, then
   `git add data/dealscout.db && git commit && git push`.
3. Trigger the workflow once via **Actions → watch → Run workflow** to verify.
4. Delete the local Windows Task Scheduler job to avoid double notifications:
   `schtasks /delete /tn DealScout /f`.

State (watches + price history) lives in the committed `data/dealscout.db`;
the workflow commits it back after each real run. Pull before editing locally.
```

同时把 Roadmap 里 M1.5 相关项标记完成（在 M1 行后加一行 `- [x] M1.5: FX line, timezone-gated cloud cron`）。

- [ ] **Step 5: 全量验证**

Run: `.venv\Scripts\python.exe -m pytest -q` → Expected: 全绿（45 passed）
Run: `.venv\Scripts\python.exe -m ruff check .` → Expected: 通过

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/watch.yml .gitignore .env.example README.md
git commit -m "feat: add hourly cloud watch workflow with db persistence"
```

- [ ] **Step 7: 用户侧上线动作（不在本计划自动执行，交付清单提醒）**

1. `git push` 到 GitHub。
2. 仓库加三个 Secrets。
3. 本地用 `DEALSCOUT_DB=data/dealscout.db` 重新 `dealscout add` 想监控的游戏并提交。
4. Actions → watch → Run workflow 手动触发一次验证（收 Telegram、db 被提交回来）。
5. `schtasks /delete /tn DealScout /f` 删除本地任务。
