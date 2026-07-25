# DealScout M2c Implementation Plan — Deal Authenticity / Price-Trend

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a deal triggers, enrich the M2a LLM verdict with ITAD's 3-month price history so the notification reports whether a discount is genuine ("real"), inflated by a raised list price ("inflated"), or merely common ("common").

**Architecture:** A new `dealscout/trend.py` computes deterministic `TrendFeatures` (pure function) from an ITAD history series fetched by a new `ItadClient.fetch_history`. `DealVerdict` gains a `discount_authenticity` field; `build_prompt`/`judge` take an optional `trend`. `runner._make_verdict` fetches history + computes features best-effort and passes them to the LLM; `notify.format_deal` shows an authenticity line. Everything degrades to exact M2a behavior when history is unavailable.

**Tech Stack:** Python ≥3.11, Pydantic, httpx (ITAD via MockTransport in tests), google-genai (verdict), pytest.

## Global Constraints

- Python ≥3.11 (dev 3.11.9, CI 3.12); no LangChain / agent framework.
- All layers exchange **Pydantic models**.
- **Tests fully offline** in CI: HTTP via `httpx.MockTransport`, the LLM via an **injected fake client** (never a real network call).
- **google-genai calls copy `dealscout/verdict.py`'s existing form verbatim** — this milestone does NOT change the `genai.Client(...)` / `generate_content(..., response_schema=DealVerdict)` / `model_validate_json` mechanics; it only adds a field to `DealVerdict` and an optional `trend` arg to `build_prompt`/`judge`.
- **Best-effort, M2c history is NOT load-bearing**: any failure fetching history, computing features, or judging → the verdict degrades (trend=None → M2a behavior; or verdict=None) and the notification still fires. Never let history block a notification.
- **Backward compatibility**: `DealVerdict.discount_authenticity` defaults `"unknown"`; `judge`/`build_prompt`'s `trend` defaults `None`. Existing `judge(overview, rule)` calls and their tests must keep working unchanged.
- Every task ends green: `ruff check .` clean AND `pytest` all pass, one `feat:`/`test:` commit.
- Do **not** modify `judge.py`, `schedule.py`, `tick`, `cli.py` command behavior, `fetch_prices`, `fetch_overview`, or `.github/workflows/watch.yml`. The existing 98 tests must stay green (two M2a test fakes get a `trend=None` param in Task 4 — that is the only change to existing tests).

---

### Task 1: `trend.py` — `TrendFeatures` model + `compute_trend`

**Files:**
- Modify: `dealscout/models.py` (add `TrendFeatures`)
- Create: `dealscout/trend.py`
- Test: `tests/test_trend.py`

**Interfaces:**
- Consumes: `dealscout.models.PricePoint` (existing).
- Produces:
  - `class TrendFeatures(BaseModel)` with `window_days: int`, `points: int`, `low: float`, `median: float`, `times_at_or_below_current: int`, `regular_recently_raised: bool`
  - `def compute_trend(history: list[PricePoint], current: PricePoint, window_days: int = 90) -> TrendFeatures | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trend.py`:

```python
from dealscout.models import PricePoint
from dealscout.trend import compute_trend


def _pt(price, regular, seen="2025-05-01"):
    return PricePoint(
        shop="Steam", price=price, regular=regular, cut=0, currency="USD", url="", seen_at=seen
    )


def test_compute_trend_basic_stats():
    history = [_pt(20, 40), _pt(10, 40), _pt(15, 40)]
    t = compute_trend(history, _pt(10, 40))
    assert t.window_days == 90
    assert t.points == 3
    assert t.low == 10
    assert t.median == 15                       # median of [10,15,20]
    assert t.times_at_or_below_current == 1     # only the 10 is <= current 10
    assert t.regular_recently_raised is False   # current regular 40 == min regular 40, not >


def test_compute_trend_detects_inflated_regular():
    history = [_pt(20, 30), _pt(18, 30)]
    t = compute_trend(history, _pt(9, 45))      # current regular 45 > min historical regular 30
    assert t.regular_recently_raised is True


def test_compute_trend_counts_common_price():
    history = [_pt(10, 40), _pt(10, 40), _pt(9, 40), _pt(20, 40)]
    t = compute_trend(history, _pt(10, 40))
    assert t.times_at_or_below_current == 3     # 10, 10, 9 are all <= 10


def test_compute_trend_too_few_points_returns_none():
    assert compute_trend([_pt(10, 40)], _pt(10, 40)) is None
    assert compute_trend([], _pt(10, 40)) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_trend.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dealscout.trend'`

- [ ] **Step 3: Add `TrendFeatures` to `dealscout/models.py`**

Append after the existing `PriceOverview` class:

```python
class TrendFeatures(BaseModel):
    window_days: int                 # stats window (nominal 90, matches ITAD's ~3-month default)
    points: int                      # number of price observations in the window
    low: float                       # lowest observed price
    median: float                    # median observed price
    times_at_or_below_current: int   # observations with price <= current price (fake-scarcity signal)
    regular_recently_raised: bool    # current list price exceeds the window's lowest list price
```

- [ ] **Step 4: Create `dealscout/trend.py`**

```python
"""Deterministic price-trend features from an ITAD history series. Pure, offline-testable."""

import statistics

from dealscout.models import PricePoint, TrendFeatures


def compute_trend(
    history: list[PricePoint], current: PricePoint, window_days: int = 90
) -> TrendFeatures | None:
    """Summarise a price-history window into signals for the LLM verdict.

    Returns None when there are fewer than 2 observations (too little to judge a
    trend) — callers then fall back to the M2a overview-only verdict.
    """
    if len(history) < 2:
        return None
    prices = [p.price for p in history]
    regulars = [p.regular for p in history]
    return TrendFeatures(
        window_days=window_days,
        points=len(history),
        low=min(prices),
        median=statistics.median(prices),
        times_at_or_below_current=sum(p <= current.price for p in prices),
        regular_recently_raised=current.regular > min(regulars),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_trend.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Lint**

Run: `ruff check dealscout/models.py dealscout/trend.py tests/test_trend.py`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add dealscout/models.py dealscout/trend.py tests/test_trend.py
git commit -m "feat: add TrendFeatures + compute_trend (price-history signals)"
```

---

### Task 2: `ItadClient.fetch_history`

**Files:**
- Modify: `dealscout/sources/itad.py`
- Test: `tests/test_itad.py`

**Interfaces:**
- Consumes: existing `ItadClient._point_from(block, seen_at=None)`, `dealscout.models.PricePoint`, `dealscout.sources.base.SourceError`.
- Produces: `def fetch_history(self, rule: WatchRule) -> list[PricePoint]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_itad.py`:

```python
HISTORY_OK = [
    {
        "timestamp": "2025-05-01T10:00:00+00:00",
        "shop": {"id": 61, "name": "Steam"},
        "deal": {"price": {"amount": 20.0, "currency": "USD"}, "regular": {"amount": 40.0}, "cut": 50},
    },
    {
        "timestamp": "2025-06-01T10:00:00+00:00",
        "shop": {"id": 61, "name": "Steam"},
        "deal": {"price": {"amount": 10.0, "currency": "USD"}, "regular": {"amount": 40.0}, "cut": 75},
    },
]


def test_fetch_history_parses_series():
    def handler(request):
        assert request.url.path == "/games/history/v2"
        assert request.url.params["id"] == "g-123"
        assert request.url.params["country"] == "MY"
        return httpx.Response(200, json=HISTORY_OK)

    itad = ItadClient("k", client=make_client(handler))
    rule = WatchRule(id=1, title="Hades", game_id="g-123", max_price=15.0)
    points = itad.fetch_history(rule)
    assert len(points) == 2
    assert points[0].price == 20.0
    assert points[1].price == 10.0
    assert points[1].regular == 40.0
    assert points[1].currency == "USD"
    assert points[1].seen_at == "2025-06-01T10:00:00+00:00"


def test_fetch_history_empty_returns_empty_list():
    itad = ItadClient("k", client=make_client(lambda r: httpx.Response(200, json=[])))
    assert itad.fetch_history(WatchRule(id=1, title="Hades", game_id="g-123", max_price=15.0)) == []


def test_fetch_history_http_error_raises():
    itad = ItadClient("k", client=make_client(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(SourceError):
        itad.fetch_history(WatchRule(id=1, title="Hades", game_id="g-123", max_price=15.0))


def test_fetch_history_malformed_entry_raises():
    body = [{"timestamp": "2025-06-01T10:00:00+00:00", "shop": {"name": "Steam"}}]  # no "deal"
    itad = ItadClient("k", client=make_client(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(SourceError):
        itad.fetch_history(WatchRule(id=1, title="Hades", game_id="g-123", max_price=15.0))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_itad.py -k fetch_history -v`
Expected: FAIL with `AttributeError: 'ItadClient' object has no attribute 'fetch_history'`

- [ ] **Step 3: Add `fetch_history` to `dealscout/sources/itad.py`**

Add this method after `fetch_overview` (it reuses the existing `_point_from`, which already wraps malformed blocks as `SourceError`):

```python
    def fetch_history(self, rule: WatchRule) -> list[PricePoint]:
        resp = self._client.get(
            "/games/history/v2",
            params={"key": self._api_key, "id": rule.game_id, "country": rule.country},
        )
        if resp.status_code != 200:
            raise SourceError(f"ITAD history failed: HTTP {resp.status_code}")
        data = resp.json() or []
        try:
            return [
                self._point_from(
                    {
                        "shop": e["shop"],
                        "price": e["deal"]["price"],
                        "regular": e["deal"]["regular"],
                        "cut": e["deal"]["cut"],
                    },
                    seen_at=e.get("timestamp"),
                )
                for e in data
            ]
        except (KeyError, TypeError) as exc:
            raise SourceError(f"ITAD malformed history entry: {exc}") from exc
```

Note: ITAD history entries nest `price`/`regular`/`cut` under `deal` and put `shop` at the top level, so each entry is flattened into the block shape `_point_from` expects before parsing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_itad.py -v`
Expected: PASS (11 tests total in the file)

- [ ] **Step 5: Lint**

Run: `ruff check dealscout/sources/itad.py tests/test_itad.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add dealscout/sources/itad.py tests/test_itad.py
git commit -m "feat: add ItadClient.fetch_history (3-month price series)"
```

---

### Task 3: `DealVerdict.discount_authenticity` + `build_prompt`/`judge` take `trend`

**Files:**
- Modify: `dealscout/verdict.py`
- Test: `tests/test_verdict.py`

**Interfaces:**
- Consumes: `dealscout.models.TrendFeatures` (Task 1), existing `PriceOverview`/`WatchRule`.
- Produces:
  - `DealVerdict` gains `discount_authenticity: Literal["real", "inflated", "common", "unknown"] = "unknown"`
  - `def build_prompt(overview: PriceOverview, rule: WatchRule, trend: TrendFeatures | None = None) -> str`
  - `VerdictLLM.judge(self, overview, rule, trend: TrendFeatures | None = None) -> DealVerdict`
  - `GeminiVerdictLLM.judge(self, overview, rule, trend=None) -> DealVerdict`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verdict.py`:

```python
def test_deal_verdict_authenticity_defaults_unknown():
    v = DealVerdict(rating="good", reason="r")
    assert v.discount_authenticity == "unknown"


def test_deal_verdict_accepts_authenticity():
    v = DealVerdict(rating="good", reason="r", discount_authenticity="inflated")
    assert v.discount_authenticity == "inflated"


def test_build_prompt_includes_trend_when_given():
    from dealscout.models import TrendFeatures

    rule = WatchRule(id=1, title="Hades", game_id="g", min_cut=30)
    trend = TrendFeatures(
        window_days=90, points=12, low=6.24, median=9.99,
        times_at_or_below_current=3, regular_recently_raised=True,
    )
    prompt = build_prompt(_overview(), rule, trend)
    assert "9.99" in prompt                     # median shown
    assert "discount_authenticity" in prompt    # asks the model to set it


def test_build_prompt_without_trend_asks_unknown():
    rule = WatchRule(id=1, title="Hades", game_id="g", min_cut=30)
    prompt = build_prompt(_overview(), rule)     # no trend
    assert "unknown" in prompt


def test_gemini_judge_passes_trend_and_reads_authenticity():
    from dealscout.models import TrendFeatures
    from dealscout.verdict import GeminiVerdictLLM

    fake = _FakeGenaiClient(
        text='{"rating": "good", "reason": "r", "discount_authenticity": "inflated"}'
    )
    llm = GeminiVerdictLLM(api_key="k", model="m", client=fake)
    trend = TrendFeatures(
        window_days=90, points=5, low=6.0, median=9.0,
        times_at_or_below_current=2, regular_recently_raised=True,
    )
    v = llm.judge(_overview(), WatchRule(id=1, title="Hades", game_id="g", min_cut=30), trend=trend)
    assert v.discount_authenticity == "inflated"
    assert "9" in fake.models.calls[0]["contents"]   # trend median reached the prompt


def test_gemini_judge_without_trend_still_works():
    from dealscout.verdict import GeminiVerdictLLM

    fake = _FakeGenaiClient(text='{"rating": "good", "reason": "r"}')
    llm = GeminiVerdictLLM(api_key="k", model="m", client=fake)
    v = llm.judge(_overview(), WatchRule(id=1, title="Hades", game_id="g", min_cut=30))
    assert v.discount_authenticity == "unknown"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_verdict.py -k "authenticity or trend" -v`
Expected: FAIL (e.g. `build_prompt()` takes 2 positional args / `DealVerdict` has no field `discount_authenticity`)

- [ ] **Step 3: Add the field, import, and prompt/judge changes in `dealscout/verdict.py`**

Add the import near the top (with the existing model import):

```python
from dealscout.models import PriceOverview, TrendFeatures, WatchRule
```

Add the field to `DealVerdict`:

```python
class DealVerdict(BaseModel):
    rating: Literal["buy_now", "good", "wait", "skip"]
    reason: str
    wait_target: float | None = None
    discount_authenticity: Literal["real", "inflated", "common", "unknown"] = "unknown"
```

Change `build_prompt`'s signature and append a trend section. Replace the current `return (...)` tail so the function reads:

```python
def build_prompt(
    overview: PriceOverview, rule: WatchRule, trend: TrendFeatures | None = None
) -> str:
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
    if trend is not None:
        trend_line = (
            f"价格历史（近 {trend.window_days} 天，{trend.points} 个观测点）："
            f"最低 {trend.low:.2f}，中位 {trend.median:.2f}，"
            f"历史上 ≤当前价 出现 {trend.times_at_or_below_current} 次；"
            f"原价{'被抬高过（先涨后降嫌疑）' if trend.regular_recently_raised else '未被抬高'}。\n"
            "据此把 discount_authenticity 设为 real(真降,接近历史低位)/"
            "inflated(原价虚高,先涨后降)/common(常年这价,非稀缺) 之一，并在 reason 点明。"
        )
    else:
        trend_line = "（无价格历史数据，discount_authenticity 请设为 unknown。）"
    return (
        "你是精明的游戏比价顾问。根据下面的数据，判断现在这个价到底值不值得买、"
        "还是再等等，给出简短的中文理由。\n"
        f"游戏：{rule.title}\n"
        f"现价：{cur.currency} {cur.price:.2f}（原价 {cur.regular:.2f}，-{cur.cut}%，{cur.shop}）\n"
        f"{low_line}\n"
        f"{trend_line}\n"
        f"用户条件：{('；'.join(cond)) or '无'}\n"
        "rating 取值：buy_now(现在就买)、good(不错可入)、wait(建议再等)、skip(别买)。"
        "若建议再等，wait_target 给一个值得设提醒的目标价（否则留空）。"
    )
```

Change the `VerdictLLM` protocol and `GeminiVerdictLLM.judge` to accept `trend`:

```python
class VerdictLLM(Protocol):
    def judge(
        self, overview: PriceOverview, rule: WatchRule, trend: TrendFeatures | None = None
    ) -> DealVerdict:
        """Return a structured deal verdict. Raises VerdictError on failure."""
        ...
```

In `GeminiVerdictLLM.judge`, change the signature and the first line only:

```python
    def judge(
        self, overview: PriceOverview, rule: WatchRule, trend: TrendFeatures | None = None
    ) -> DealVerdict:
        prompt = build_prompt(overview, rule, trend)
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
        except Exception as exc:
            raise VerdictError(f"gemini judge failed: {exc}") from exc
        text = getattr(resp, "text", None)
        if not text:
            raise VerdictError("gemini judge returned empty response")
        try:
            return DealVerdict.model_validate_json(text)
        except Exception as exc:
            raise VerdictError(f"gemini verdict not valid: {exc}") from exc
```

(The `generate_content`/`model_validate_json` body is unchanged from M2a — only the signature and the `build_prompt(overview, rule, trend)` call changed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_verdict.py -v`
Expected: PASS (all prior verdict tests + the 6 new ones; the prior `judge(overview, rule)` calls still work because `trend` defaults `None`)

- [ ] **Step 5: Lint**

Run: `ruff check dealscout/verdict.py tests/test_verdict.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add dealscout/verdict.py tests/test_verdict.py
git commit -m "feat: add discount_authenticity to verdict; build_prompt/judge take trend"
```

---

### Task 4: runner wiring (`_make_trend` + `_make_verdict`) + update M2a fakes

**Files:**
- Modify: `dealscout/runner.py`
- Test: `tests/test_runner.py` (update `FakeLLM`, add tests)
- Modify: `tests/test_cli.py` (update `FakeVerdictLLM.judge` signature for protocol consistency)

**Interfaces:**
- Consumes: `ItadClient.fetch_history` (Task 2, duck-typed via `getattr(source, "fetch_history", None)`), `compute_trend` (Task 1), `judge(overview, rule, trend=...)` (Task 3).
- Produces: `_make_trend(source, rule, current)`; updated `_make_verdict`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_runner.py`, update the existing `FakeLLM` to accept and record `trend` (it currently is `def judge(self, overview, rule)`):

```python
class FakeLLM:
    def __init__(self, verdict=None, fail=False):
        self.verdict = verdict
        self.fail = fail
        self.last_trend = "unset"

    def judge(self, overview, rule, trend=None):
        self.last_trend = trend
        if self.fail:
            from dealscout.verdict import VerdictError

            raise VerdictError("boom")
        return self.verdict
```

Add a history-capable source and new tests at the end of the file:

```python
class FakeSourceWithHistory(FakeSourceWithOverview):
    def fetch_history(self, rule):
        from dealscout.models import PricePoint

        return [
            PricePoint(shop="Steam", price=20.0, regular=40.0, cut=50, currency="USD", url="", seen_at="2025-05-01"),
            PricePoint(shop="Steam", price=12.49, regular=40.0, cut=69, currency="USD", url="", seen_at="2025-06-01"),
        ]


class FakeSourceHistoryFails(FakeSourceWithOverview):
    def fetch_history(self, rule):
        raise SourceError("history boom")


def test_run_once_passes_trend_to_llm(store):
    from dealscout.verdict import DealVerdict

    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    llm = FakeLLM(verdict=DealVerdict(rating="good", reason="r", discount_authenticity="inflated"))
    run_once(store, FakeSourceWithHistory({"g1": [_point()]}), notifier, llm=llm)
    assert llm.last_trend is not None
    assert llm.last_trend.points == 2


def test_run_once_no_fetch_history_gives_trend_none(store):
    from dealscout.verdict import DealVerdict

    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    llm = FakeLLM(verdict=DealVerdict(rating="good", reason="r"))
    # FakeSourceWithOverview has fetch_overview but no fetch_history
    run_once(store, FakeSourceWithOverview({"g1": [_point()]}), notifier, llm=llm)
    assert llm.last_trend is None


def test_run_once_history_error_still_notifies(store):
    from dealscout.verdict import DealVerdict

    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    llm = FakeLLM(verdict=DealVerdict(rating="good", reason="r"))
    results = run_once(store, FakeSourceHistoryFails({"g1": [_point()]}), notifier, llm=llm)
    assert results[0].notified is True
    assert llm.last_trend is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runner.py -k "trend or history" -v`
Expected: FAIL (`_make_trend` not wired — `last_trend` stays `"unset"` / trend never passed)

- [ ] **Step 3: Wire history + trend into `dealscout/runner.py`**

Add the import at the top (with the other `dealscout` imports):

```python
from dealscout.trend import compute_trend
```

Replace the existing `_make_verdict` with the version below and add `_make_trend` beside it:

```python
def _make_trend(source, rule, current):
    # Best-effort: source without fetch_history, ITAD error, or <2 points -> None.
    fetch = getattr(source, "fetch_history", None)
    if fetch is None:
        return None
    try:
        return compute_trend(fetch(rule), current)
    except Exception:
        return None


def _make_verdict(source, llm, rule):
    # Best-effort: any failure (no fetch_overview, ITAD error, LLM error) -> None, never blocks notify.
    if llm is None:
        return None
    fetch = getattr(source, "fetch_overview", None)
    if fetch is None:
        return None
    try:
        overview = fetch(rule)
    except Exception:
        return None
    trend = _make_trend(source, rule, overview.current)
    try:
        return llm.judge(overview, rule, trend=trend)
    except Exception:
        return None
```

- [ ] **Step 4: Update `tests/test_cli.py` `FakeVerdictLLM` for protocol consistency**

In `tests/test_cli.py`, change `FakeVerdictLLM.judge` so its signature matches the extended protocol (the runner now calls `judge(..., trend=...)`):

```python
class FakeVerdictLLM:
    def __init__(self, *args, **kwargs):
        pass

    def judge(self, overview, rule, trend=None):
        from dealscout.verdict import DealVerdict

        return DealVerdict(rating="good", reason="ok")
```

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS — all prior tests plus the 3 new runner tests; `test_run_once_adds_verdict` and `test_run_once_verdict_failure_still_notifies` still pass because `FakeLLM.judge` now accepts `trend`.

- [ ] **Step 6: Lint**

Run: `ruff check dealscout/runner.py tests/test_runner.py tests/test_cli.py`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add dealscout/runner.py tests/test_runner.py tests/test_cli.py
git commit -m "feat: wire best-effort history+trend into the verdict pass"
```

---

### Task 5: notify authenticity line

**Files:**
- Modify: `dealscout/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: `DealVerdict.discount_authenticity` (Task 3), read duck-typed so a verdict without the attribute is treated as `"unknown"`.
- Produces: an extra `🔎 折扣真实性：…` line in `format_deal` when authenticity is not `"unknown"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_notify.py`:

```python
def test_format_deal_shows_authenticity_when_known():
    from dealscout.verdict import DealVerdict

    best = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    v = DealVerdict(rating="good", reason="r", discount_authenticity="inflated")
    text = format_deal(Deal(watch_id=1, title="Hades", best=best, reason="r"), verdict=v)
    assert "折扣真实性" in text
    assert "原价虚高" in text


def test_format_deal_hides_authenticity_when_unknown():
    from dealscout.verdict import DealVerdict

    best = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    v = DealVerdict(rating="good", reason="r")   # discount_authenticity defaults "unknown"
    text = format_deal(Deal(watch_id=1, title="Hades", best=best, reason="r"), verdict=v)
    assert "折扣真实性" not in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_notify.py -k authenticity -v`
Expected: FAIL (`test_format_deal_shows_authenticity_when_known` — no "折扣真实性" in output)

- [ ] **Step 3: Add the authenticity label + line in `dealscout/notify.py`**

Add a label map near the existing `_RATING_LABEL`:

```python
_AUTHENTICITY_LABEL = {
    "real": "真降，接近历史低位",
    "inflated": "原价虚高（先涨后降）",
    "common": "常年这价（非稀缺）",
}
```

In `format_deal`, inside the `if verdict is not None:` block, add the authenticity line after `lines.append(verdict.reason)` and before the `wait_target` line:

```python
    if verdict is not None:
        label = _RATING_LABEL.get(verdict.rating, verdict.rating)
        lines.append(f"📊 好价判断：{label}")
        lines.append(verdict.reason)
        authenticity = getattr(verdict, "discount_authenticity", "unknown")
        if authenticity != "unknown":
            lines.append(f"🔎 折扣真实性：{_AUTHENTICITY_LABEL.get(authenticity, authenticity)}")
        if verdict.wait_target is not None:
            lines.append(f"（目标价 {verdict.wait_target:.2f}）")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_notify.py -v`
Expected: PASS (all prior notify tests + the 2 new ones)

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS — full suite green (98 prior + new M2c tests).

- [ ] **Step 6: Lint**

Run: `ruff check dealscout/notify.py tests/test_notify.py`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add dealscout/notify.py tests/test_notify.py
git commit -m "feat: show discount authenticity line in deal notification"
```

---

## Self-Review

**1. Spec coverage:**
- §3.1 `TrendFeatures` + `DealVerdict.discount_authenticity` → Task 1 (TrendFeatures) + Task 3 (field) ✅
- §3.2 `fetch_history` (flatten `deal`, reuse `_point_from`, malformed→SourceError) → Task 2 ✅
- §3.3 `compute_trend` (median/low/times/regular_recently_raised, <2→None) → Task 1 ✅
- §3.4 `build_prompt`/`judge` take `trend`, prompt sections, unchanged Gemini mechanics → Task 3 ✅
- §3.5 `_make_verdict`/`_make_trend` best-effort wiring → Task 4 ✅
- §3.6 notify authenticity line (duck-typed, hidden when unknown) → Task 5 ✅
- §3.7 no new config → nothing to do ✅
- §5 backward compat (defaults, update two M2a fakes) → Task 3 (defaults keep old calls working) + Task 4 (FakeLLM, FakeVerdictLLM) ✅
- §6 tests (trend, itad history, verdict, runner, notify, cli fake) → Tasks 1–5 ✅

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; every command shows expected output. ✅

**3. Type consistency:** `TrendFeatures` field names identical across Task 1 (definition), Task 3 (`build_prompt` reads `.window_days/.points/.low/.median/.times_at_or_below_current/.regular_recently_raised`), and Task 4 (`last_trend.points`). `compute_trend(history, current, window_days=90) -> TrendFeatures | None` matches its call in Task 4's `_make_trend`. `judge(overview, rule, trend=None)` signature identical across Task 3 (protocol + Gemini impl) and Task 4 (runner call `llm.judge(overview, rule, trend=trend)`, FakeLLM, FakeVerdictLLM). `discount_authenticity` literal set `{real, inflated, common, unknown}` consistent across Task 3 (field), Task 5 (`_AUTHENTICITY_LABEL` keys real/inflated/common), and the prompt text. ✅
