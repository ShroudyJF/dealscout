# DealScout M2a（LLM 好价判断）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 盯的游戏触发提醒时，用 ITAD 现价 + 史上最低价，调 LLM（Gemini）给出结构化"好价判断"，best-effort 地拼进 Telegram 通知。

**Architecture:** 新增 `PriceOverview` 模型与 `ItadClient.fetch_overview`（`/games/overview/v2` 一次拿现价+史低）；新增 `verdict.py`（`DealVerdict` 结构化结果 + `VerdictLLM` 协议 + `GeminiVerdictLLM` 实现）；LLM 藏在协议后面，测试注入 fake，全程离线；`runner`/`notify` best-effort 接线——LLM 或 ITAD 出错绝不阻断通知。

**Tech Stack:** Python 3.11+、Pydantic v2、httpx、google-genai、Typer、pytest、ruff、GitHub Actions。

**Scope note:** 覆盖 spec `docs/superpowers/specs/2026-07-22-dealscout-m2a-design.md` 全部。不做 Shopee/Lazada 抓取、跨平台比价、自采长期历史。

## Global Constraints

- Python ≥ 3.11（开发机 3.11.9，CI 用 3.12）。
- 模块间传递数据用 Pydantic 模型或明确类型，不传裸 dict。
- pytest 全程离线：HTTP 一律 `httpx.MockTransport`；LLM 一律注入 fake，不访问真实网络。
- LLM 与 ITAD-overview 是 best-effort：失败绝不能中断真实 deal 的通知（与 M1.5 汇率同一原则）。
- LLM 藏在 `VerdictLLM` 协议后面，`GeminiVerdictLLM` 是唯一实现，`client` 可注入。
- 新增参数一律向后兼容：现有 `run_once`、`format_deal` 调用不破，M1/M1.5 现有 47 测试保持绿。
- 每个任务收尾：`ruff check .` 通过 + `pytest -q` 全绿 + 单独 commit，message 用 `feat:`/`test:`/`docs:`/`chore:` 前缀。
- 工具用 venv 直连路径调用：`.venv\Scripts\python.exe -m pytest -q`、`.venv\Scripts\python.exe -m ruff check .`。

## 前置说明

当前 `master` 全绿（47 tests，ruff clean）。现有模型：`WatchRule`、`PricePoint(shop, price, regular, cut, currency, url)`、`Deal(watch_id, title, best, reason)`。`ItadClient` 有 `lookup_game`、`fetch_prices`。`format_deal(deal, display=None)`（M1.5 已加 display）。`run_once(store, source, notifier, fx=None, display_currency=None)`。

---

### Task 1: PriceOverview 模型 + PricePoint.seen_at

**Files:**
- Modify: `dealscout/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: 现有 `PricePoint`。
- Produces:
  - `PricePoint` 新增 `seen_at: str | None = None`（承载史低日期，向后兼容）。
  - `PriceOverview(current: PricePoint, historical_low: PricePoint | None = None)`。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_models.py`）

```python
def test_pricepoint_seen_at_optional_defaults_none():
    p = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    assert p.seen_at is None


def test_price_overview_holds_current_and_low():
    from dealscout.models import PriceOverview

    cur = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    low = PricePoint(
        shop="Steam", price=6.24, regular=24.99, cut=75, currency="USD", url="https://x",
        seen_at="2025-09-17",
    )
    ov = PriceOverview(current=cur, historical_low=low)
    assert ov.current.price == 7.49
    assert ov.historical_low.price == 6.24
    assert ov.historical_low.seen_at == "2025-09-17"


def test_price_overview_low_optional():
    from dealscout.models import PriceOverview

    cur = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    assert PriceOverview(current=cur).historical_low is None
```

- [ ] **Step 2: 确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_models.py -q`
Expected: FAIL（`PricePoint` 无 `seen_at` / `ImportError: cannot import name 'PriceOverview'`）

- [ ] **Step 3: 实现**——修改 `dealscout/models.py`

`PricePoint` 加一行字段：

```python
class PricePoint(BaseModel):
    shop: str
    price: float
    regular: float
    cut: int
    currency: str
    url: str
    seen_at: str | None = None
```

文件末尾追加：

```python
class PriceOverview(BaseModel):
    current: PricePoint
    historical_low: PricePoint | None = None
```

- [ ] **Step 4: 确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_models.py -q` → Expected: `6 passed`（原 3 + 新 3）
Run: `.venv\Scripts\python.exe -m ruff check .` → Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add dealscout/models.py tests/test_models.py
git commit -m "feat: add PriceOverview model and PricePoint.seen_at"
```

---

### Task 2: ItadClient.fetch_overview（取现价 + 史低）

**Files:**
- Modify: `dealscout/sources/itad.py`
- Test: `tests/test_itad.py`

**Interfaces:**
- Consumes: `WatchRule`、`PricePoint`、`PriceOverview`（Task 1）、现有 `SourceError`。
- Produces: `ItadClient.fetch_overview(rule: WatchRule) -> PriceOverview`——`POST /games/overview/v2`，从 `prices[0].current`/`prices[0].lowest` 解析。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_itad.py`）

```python
OVERVIEW_OK = {
    "prices": [
        {
            "id": "g-123",
            "current": {
                "shop": {"id": 61, "name": "Steam"},
                "price": {"amount": 7.49, "currency": "USD"},
                "regular": {"amount": 24.99, "currency": "USD"},
                "cut": 70,
                "url": "https://itad.link/cur",
            },
            "lowest": {
                "shop": {"id": 61, "name": "Steam"},
                "price": {"amount": 6.24, "currency": "USD"},
                "regular": {"amount": 24.99, "currency": "USD"},
                "cut": 75,
                "timestamp": "2025-09-17T19:20:27+02:00",
            },
        }
    ],
    "bundles": [],
}


def test_fetch_overview_parses_current_and_low():
    def handler(request):
        assert request.url.path == "/games/overview/v2"
        assert request.url.params["country"] == "MY"
        return httpx.Response(200, json=OVERVIEW_OK)

    itad = ItadClient("k", client=make_client(handler))
    rule = WatchRule(id=1, title="Hades", game_id="g-123", max_price=15.0)
    ov = itad.fetch_overview(rule)
    assert ov.current.price == 7.49
    assert ov.current.shop == "Steam"
    assert ov.historical_low.price == 6.24
    assert ov.historical_low.cut == 75
    assert ov.historical_low.seen_at == "2025-09-17T19:20:27+02:00"


def test_fetch_overview_missing_lowest():
    body = {"prices": [{"id": "g-123", "current": OVERVIEW_OK["prices"][0]["current"]}], "bundles": []}
    itad = ItadClient("k", client=make_client(lambda r: httpx.Response(200, json=body)))
    ov = itad.fetch_overview(WatchRule(id=1, title="Hades", game_id="g-123", max_price=15.0))
    assert ov.historical_low is None


def test_fetch_overview_http_error_raises():
    itad = ItadClient("k", client=make_client(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(SourceError):
        itad.fetch_overview(WatchRule(id=1, title="Hades", game_id="g-123", max_price=15.0))
```

（`make_client`、`ItadClient`、`SourceError`、`WatchRule`、`httpx`、`pytest` 已在该文件顶部导入——沿用 M1 测试的既有导入。）

- [ ] **Step 2: 确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_itad.py -q`
Expected: FAIL（`ItadClient` 无 `fetch_overview`）

- [ ] **Step 3: 实现**——在 `dealscout/sources/itad.py` 的 `ItadClient` 里加方法（放在 `fetch_prices` 之后）：

```python
    def _point_from(self, block: dict, seen_at: str | None = None) -> PricePoint:
        return PricePoint(
            shop=block["shop"]["name"],
            price=block["price"]["amount"],
            regular=block["regular"]["amount"],
            cut=block["cut"],
            currency=block["price"]["currency"],
            url=block.get("url", ""),
            seen_at=seen_at,
        )

    def fetch_overview(self, rule: WatchRule) -> PriceOverview:
        resp = self._client.post(
            "/games/overview/v2",
            params={"key": self._api_key, "country": rule.country},
            json=[rule.game_id],
        )
        if resp.status_code != 200:
            raise SourceError(f"ITAD overview failed: HTTP {resp.status_code}")
        data = resp.json()
        prices = data.get("prices") or []
        if not prices or "current" not in prices[0]:
            raise SourceError("ITAD overview: no current price")
        entry = prices[0]
        current = self._point_from(entry["current"])
        low = None
        if entry.get("lowest"):
            low = self._point_from(entry["lowest"], seen_at=entry["lowest"].get("timestamp"))
        return PriceOverview(current=current, historical_low=low)
```

文件顶部导入补 `PriceOverview`：把 `from dealscout.models import PricePoint, WatchRule` 改为 `from dealscout.models import PricePoint, PriceOverview, WatchRule`。

- [ ] **Step 4: 确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_itad.py -q` → Expected: 全绿（原 3 + 新 3 = 6）
Run: `.venv\Scripts\python.exe -m ruff check .` → Expected: 通过

- [ ] **Step 5: 手动验证真实 overview 结构（需 ITAD_API_KEY，联网）**

Run（PowerShell，替换 KEY）:
`$env:ITAD_API_KEY="<key>"; .venv\Scripts\python.exe -c "import os; from dealscout.sources.itad import ItadClient; from dealscout.models import WatchRule; c=ItadClient(os.environ['ITAD_API_KEY']); gid,t=c.lookup_game('Hades'); print(c.fetch_overview(WatchRule(id=1,title=t,game_id=gid,max_price=999)))"`
Expected: 打印含 current 与 historical_low 的 PriceOverview。若字段与 fixture 不符，以真实响应为准修正并重跑 Step 4。

- [ ] **Step 6: Commit**

```bash
git add dealscout/sources/itad.py tests/test_itad.py
git commit -m "feat: add ItadClient.fetch_overview (current + historical low)"
```

---

### Task 3: verdict 核心（DealVerdict + VerdictLLM 协议 + prompt 构建）

**Files:**
- Create: `dealscout/verdict.py`
- Test: `tests/test_verdict.py`

**Interfaces:**
- Consumes: `PriceOverview`（Task 1）、`WatchRule`。
- Produces:
  - `DealVerdict(rating: Literal["buy_now","good","wait","skip"], reason: str, wait_target: float | None = None)`
  - `VerdictLLM`（Protocol）：`judge(overview: PriceOverview, rule: WatchRule) -> DealVerdict`
  - `VerdictError(RuntimeError)`
  - `build_prompt(overview: PriceOverview, rule: WatchRule) -> str`（把现价/原价/折扣/史低及其日期/用户阈值拼成给模型的中文指令）

- [ ] **Step 1: 写失败测试** `tests/test_verdict.py`

```python
import pytest

from dealscout.models import PricePoint, PriceOverview, WatchRule
from dealscout.verdict import DealVerdict, build_prompt


def _overview():
    cur = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    low = PricePoint(
        shop="Steam", price=6.24, regular=24.99, cut=75, currency="USD", url="https://x",
        seen_at="2025-09-17",
    )
    return PriceOverview(current=cur, historical_low=low)


def test_deal_verdict_rejects_bad_rating():
    with pytest.raises(Exception):
        DealVerdict(rating="amazing", reason="r")


def test_deal_verdict_accepts_valid():
    v = DealVerdict(rating="good", reason="接近史低", wait_target=6.24)
    assert v.rating == "good"
    assert v.wait_target == 6.24


def test_build_prompt_includes_key_numbers():
    rule = WatchRule(id=1, title="Hades", game_id="g", min_cut=30)
    prompt = build_prompt(_overview(), rule)
    assert "Hades" in prompt
    assert "7.49" in prompt
    assert "6.24" in prompt          # 史低价
    assert "2025-09-17" in prompt    # 史低日期


def test_build_prompt_handles_no_low():
    cur = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    rule = WatchRule(id=1, title="Hades", game_id="g", min_cut=30)
    prompt = build_prompt(PriceOverview(current=cur), rule)
    assert "Hades" in prompt  # 不含史低也能生成
```

- [ ] **Step 2: 确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_verdict.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'dealscout.verdict'`）

- [ ] **Step 3: 实现** `dealscout/verdict.py`

```python
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
```

- [ ] **Step 4: 确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_verdict.py -q` → Expected: `4 passed`
Run: `.venv\Scripts\python.exe -m ruff check .` → Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add dealscout/verdict.py tests/test_verdict.py
git commit -m "feat: add DealVerdict, VerdictLLM protocol, and prompt builder"
```

---

### Task 4: GeminiVerdictLLM 实现 + google-genai 依赖

**Files:**
- Modify: `dealscout/verdict.py`, `pyproject.toml`
- Test: `tests/test_verdict.py`

**Interfaces:**
- Consumes: `DealVerdict`、`VerdictError`、`build_prompt`（Task 3）。
- Produces: `GeminiVerdictLLM(api_key: str, model: str, client=None)` 实现 `judge(overview, rule) -> DealVerdict`。

**⚠️ 实现者必读——google-genai 的确切调用要对照官方文档确认，不要凭记忆：**
本任务的离线测试**不依赖任何真实 SDK 行为**（注入 fake client），所以单测能先绿。但真实 Gemini 调用的字段名（`response_schema` vs `response_json_schema`）、以及可用的 Flash 型号 ID，各 SDK 版本有差异。实现 `judge` 里那一行真实调用前，**必须 WebFetch `https://ai.google.dev/gemini-api/docs/structured-output` 与 `https://ai.google.dev/gemini-api/docs/models` 核对**，并在 `pyproject.toml` 里**钉住一个能跑通的 `google-genai` 版本**。下方给的是结构骨架 + 版本鲁棒的解析方式（`DealVerdict.model_validate_json(resp.text)`，不依赖 `.parsed` 是否存在）。

- [ ] **Step 1: 加依赖**——`pyproject.toml` 的 `dependencies` 加一行 `"google-genai>=1.0",`（放在 `python-dotenv>=1.0` 之后；实现 Step 5 冒烟时若该版本调不通，改成实测可用的版本号并说明）。

- [ ] **Step 2: 安装**

Run: `.venv\Scripts\python.exe -m pip install -e ".[dev]"`
Expected: 安装 google-genai 成功。

- [ ] **Step 3: 写失败测试**（追加到 `tests/test_verdict.py`）

```python
class _FakeResp:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, text=None, exc=None):
        self._text = text
        self._exc = exc
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return _FakeResp(self._text)


class _FakeGenaiClient:
    def __init__(self, text=None, exc=None):
        self.models = _FakeModels(text=text, exc=exc)


def test_gemini_judge_parses_structured_json():
    from dealscout.verdict import GeminiVerdictLLM

    fake = _FakeGenaiClient(text='{"rating": "good", "reason": "接近史低", "wait_target": 6.24}')
    llm = GeminiVerdictLLM(api_key="k", model="gemini-x", client=fake)
    v = llm.judge(_overview(), WatchRule(id=1, title="Hades", game_id="g", min_cut=30))
    assert v.rating == "good"
    assert v.wait_target == 6.24
    assert fake.models.calls  # 确实调用了模型


def test_gemini_judge_wraps_errors():
    from dealscout.verdict import GeminiVerdictLLM, VerdictError

    fake = _FakeGenaiClient(exc=RuntimeError("api down"))
    llm = GeminiVerdictLLM(api_key="k", model="gemini-x", client=fake)
    with pytest.raises(VerdictError):
        llm.judge(_overview(), WatchRule(id=1, title="Hades", game_id="g", min_cut=30))
```

- [ ] **Step 4: 确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_verdict.py -q`
Expected: FAIL（`GeminiVerdictLLM` 未定义）

- [ ] **Step 5: 实现**——在 `dealscout/verdict.py` 追加（先按骨架写，再照官方文档核对 `generate_content` 那行）：

```python
class GeminiVerdictLLM:
    def __init__(self, api_key: str, model: str, client=None) -> None:
        self._model = model
        if client is not None:
            self._client = client
        else:
            from google import genai  # imported lazily so offline tests need no SDK network

            self._client = genai.Client(api_key=api_key)

    def judge(self, overview: PriceOverview, rule: WatchRule) -> DealVerdict:
        prompt = build_prompt(overview, rule)
        try:
            # ⚠️ 对照 https://ai.google.dev/gemini-api/docs/structured-output 核对以下调用：
            # config 里请求 JSON 输出 + DealVerdict schema；不同版本字段名可能是
            # response_schema 或 response_json_schema。
            from google.genai import types

            resp = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=DealVerdict,
                ),
            )
        except Exception as exc:  # any SDK/network error -> domain error, caller degrades gracefully
            raise VerdictError(f"gemini judge failed: {exc}") from exc
        text = getattr(resp, "text", None)
        if not text:
            raise VerdictError("gemini judge returned empty response")
        try:
            return DealVerdict.model_validate_json(text)
        except Exception as exc:
            raise VerdictError(f"gemini verdict not valid: {exc}") from exc
```

注意：注入 fake client 的测试里，`from google.genai import types` 仍会执行——因此**安装 google-genai 是本任务前置（Step 2）**，即便离线测试不发网络请求。若官方文档核对后确认字段名或调用形状不同，改这一处并保持两个离线测试断言不变（它们只验证"调用了 + 解析 text→DealVerdict + 异常包成 VerdictError"）。

- [ ] **Step 6: 确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_verdict.py -q` → Expected: 全绿（原 4 + 新 2 = 6）
Run: `.venv\Scripts\python.exe -m ruff check .` → Expected: 通过

- [ ] **Step 7: 手动真实冒烟（需 GEMINI_API_KEY，联网）**

Run（PowerShell，替换 KEY 与你核对到的可用型号）:
`$env:GEMINI_API_KEY="<key>"; .venv\Scripts\python.exe -c "from google import genai; from dealscout.verdict import GeminiVerdictLLM; from dealscout.models import *; ov=PriceOverview(current=PricePoint(shop='Steam',price=7.49,regular=24.99,cut=70,currency='USD',url='x'), historical_low=PricePoint(shop='Steam',price=6.24,regular=24.99,cut=75,currency='USD',url='x',seen_at='2025-09-17')); print(GeminiVerdictLLM('<key>','gemini-2.5-flash').judge(ov, WatchRule(id=1,title='Hades',game_id='g',min_cut=30)))"`
Expected: 打印一个合理的 DealVerdict（rating + 中文 reason）。若型号 ID 报错，换官方文档里当前可用的 Flash 型号，并把 `pyproject.toml` 里的版本/默认型号同步。

- [ ] **Step 8: Commit**

```bash
git add dealscout/verdict.py tests/test_verdict.py pyproject.toml
git commit -m "feat: add GeminiVerdictLLM (google-genai structured output)"
```

---

### Task 5: 通知拼入判断段（notify）

**Files:**
- Modify: `dealscout/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: `DealVerdict`（Task 3）、现有 `Deal`。
- Produces: `format_deal(deal: Deal, display: tuple[str, float] | None = None, verdict: DealVerdict | None = None) -> str`——verdict 非空时追加判断段。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_notify.py`）

```python
def test_format_deal_without_verdict_has_no_verdict_line():
    best = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    text = format_deal(Deal(watch_id=1, title="Hades", best=best, reason="r"))
    assert "好价判断" not in text


def test_format_deal_with_verdict_adds_section():
    from dealscout.verdict import DealVerdict

    best = PricePoint(shop="Steam", price=7.49, regular=24.99, cut=70, currency="USD", url="https://x")
    v = DealVerdict(rating="good", reason="接近史低，可入", wait_target=6.24)
    text = format_deal(Deal(watch_id=1, title="Hades", best=best, reason="r"), verdict=v)
    assert "好价判断" in text
    assert "接近史低" in text
```

- [ ] **Step 2: 确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_notify.py -q`
Expected: FAIL（`format_deal() got an unexpected keyword argument 'verdict'`）

- [ ] **Step 3: 实现**——改 `dealscout/notify.py` 的 `format_deal`（在现有 `display` 逻辑之后、`why` 行之前插入 verdict 段）：

```python
_RATING_LABEL = {"buy_now": "现在就买", "good": "不错可入", "wait": "建议再等", "skip": "别买"}


def format_deal(deal: Deal, display: tuple[str, float] | None = None, verdict=None) -> str:
    b = deal.best
    lines = [
        f"🎯 DealScout: {deal.title}",
        f"{b.shop}: {b.currency} {b.price:.2f} (regular {b.regular:.2f}, -{b.cut}%)",
    ]
    if display is not None:
        lines.append(f"≈ {display[0]} {display[1]:.2f}")
    if verdict is not None:
        label = _RATING_LABEL.get(verdict.rating, verdict.rating)
        lines.append(f"📊 好价判断：{label}")
        lines.append(verdict.reason)
        if verdict.wait_target is not None:
            lines.append(f"（目标价 {verdict.wait_target:.2f}）")
    lines.append(f"why: {deal.reason}")
    lines.append(f"{b.url}")
    return "\n".join(lines)
```

（不在 notify.py 顶部 import DealVerdict——保持 verdict 为鸭子类型可选参数，避免与 verdict.py 的循环依赖；测试里传真实 DealVerdict 即可。）

- [ ] **Step 4: 确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_notify.py -q` → Expected: 全绿（原 5 + 新 2 = 7）
Run: `.venv\Scripts\python.exe -m pytest -q` → Expected: 全绿（61 passed）
Run: `.venv\Scripts\python.exe -m ruff check .` → Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add dealscout/notify.py tests/test_notify.py
git commit -m "feat: append LLM deal verdict to notifications"
```

---

### Task 6: 配置新增 GEMINI_API_KEY / DEALSCOUT_LLM_MODEL

**Files:**
- Modify: `dealscout/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings` 新增 `gemini_api_key: str = ""`、`llm_model: str = "gemini-2.5-flash"`；`load_settings` 读 `GEMINI_API_KEY`（缺失时抛 `SettingsError`）与 `DEALSCOUT_LLM_MODEL`（默认 gemini-2.5-flash）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_config.py`）

```python
def test_gemini_settings(monkeypatch):
    monkeypatch.setattr("dealscout.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("ITAD_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.delenv("DEALSCOUT_LLM_MODEL", raising=False)
    s = load_settings()
    assert s.gemini_api_key == "g"
    assert s.llm_model == "gemini-2.5-flash"


def test_missing_gemini_key_raises(monkeypatch):
    monkeypatch.setattr("dealscout.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("ITAD_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(SettingsError, match="GEMINI_API_KEY"):
        load_settings()
```

- [ ] **Step 2: 确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -q`
Expected: FAIL（Settings 无 gemini_api_key / 未抛 SettingsError）

- [ ] **Step 3: 实现**——修改 `dealscout/config.py`

`Settings` 加两字段：

```python
class Settings(BaseModel):
    itad_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    db_path: str
    display_currency: str = "MYR"
    tz: str = "Asia/Kuala_Lumpur"
    run_hour: int = 9
    gemini_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"
```

把 `GEMINI_API_KEY` 加进必填映射：`_REQUIRED` 里新增一行 `"gemini_api_key": "GEMINI_API_KEY",`。
在 `load_settings` 里 `values["run_hour"] = run_hour` 之后、`return` 之前加：

```python
    values["llm_model"] = os.environ.get("DEALSCOUT_LLM_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
```

- [ ] **Step 4: 确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -q` → Expected: 全绿（原 5 + 新 2 = 7）
Run: `.venv\Scripts\python.exe -m pytest -q` → Expected: 全绿（63 passed）
Run: `.venv\Scripts\python.exe -m ruff check .` → Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add dealscout/config.py tests/test_config.py
git commit -m "feat: add GEMINI_API_KEY and llm_model settings"
```

---

### Task 7: runner best-effort 接线（取 overview + 判断 → 通知）

**Files:**
- Modify: `dealscout/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `VerdictLLM`（Task 3）、`format_deal(..., verdict=...)`（Task 5）、source 的 `fetch_overview`（Task 2，鸭子类型）。
- Produces: `run_once(store, source, notifier, fx=None, display_currency=None, llm=None) -> list[RunResult]`——有 deal 且 `llm` 给出时，best-effort 取 overview + 判断，把 verdict 传给 `format_deal`；任何异常吞掉、通知照发。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_runner.py`）

```python
class FakeLLM:
    def __init__(self, verdict=None, fail=False):
        self.verdict = verdict
        self.fail = fail

    def judge(self, overview, rule):
        if self.fail:
            from dealscout.verdict import VerdictError

            raise VerdictError("boom")
        return self.verdict


class FakeSourceWithOverview(FakeSource):
    def fetch_overview(self, rule):
        from dealscout.models import PricePoint, PriceOverview

        cur = PricePoint(shop="Steam", price=12.49, regular=24.99, cut=50, currency="USD", url="https://x")
        return PriceOverview(current=cur, historical_low=None)


def test_run_once_adds_verdict(store):
    from dealscout.verdict import DealVerdict

    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    llm = FakeLLM(verdict=DealVerdict(rating="good", reason="接近史低"))
    run_once(store, FakeSourceWithOverview({"g1": [_point()]}), notifier, llm=llm)
    assert "好价判断" in notifier.sent[0]


def test_run_once_verdict_failure_still_notifies(store):
    store.add_watch(WatchRule(title="Hades", game_id="g1", max_price=15.0))
    notifier = FakeNotifier()
    results = run_once(
        store, FakeSourceWithOverview({"g1": [_point()]}), notifier, llm=FakeLLM(fail=True)
    )
    assert results[0].notified is True
    assert "好价判断" not in notifier.sent[0]
```

（`FakeSource`、`FakeNotifier`、`_point`、`store` fixture、`WatchRule`、`run_once` 已在该文件——沿用现有。`FakeSourceWithOverview` 继承 M1 的 `FakeSource`。）

- [ ] **Step 2: 确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runner.py -q`
Expected: FAIL（`run_once() got an unexpected keyword argument 'llm'`）

- [ ] **Step 3: 实现**——改 `dealscout/runner.py`

在文件里加一个 best-effort 辅助，并扩展 `run_once` 签名与通知构建：

```python
def _make_verdict(source, llm, rule):
    # Best-effort: any failure (no fetch_overview, ITAD error, LLM error) -> None, never blocks notify.
    if llm is None:
        return None
    fetch = getattr(source, "fetch_overview", None)
    if fetch is None:
        return None
    try:
        overview = fetch(rule)
        return llm.judge(overview, rule)
    except Exception:
        return None


def run_once(
    store: Store,
    source: SourceAdapter,
    notifier,
    fx=None,
    display_currency: str | None = None,
    llm=None,
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
                verdict = _make_verdict(source, llm, rule)
                message = format_deal(deal, display, verdict)
                notifier.send(message)
                store.record_notification(rule.id, deal.best.price, message)
                result.notified = True
        except Exception as exc:  # per-watch isolation: one failure must not stop the pass
            result.error = f"{type(exc).__name__}: {exc}"
        results.append(result)
    return results
```

- [ ] **Step 4: 确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runner.py -q` → Expected: 全绿（原 5 + 新 2 = 7）
Run: `.venv\Scripts\python.exe -m pytest -q` → Expected: 全绿（65 passed）
Run: `.venv\Scripts\python.exe -m ruff check .` → Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add dealscout/runner.py tests/test_runner.py
git commit -m "feat: best-effort LLM verdict in monitoring pass"
```

---

### Task 8: CLI 接线 + 云端 secret + 文档

**Files:**
- Modify: `dealscout/cli.py`, `.github/workflows/watch.yml`, `.env.example`, `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `GeminiVerdictLLM`（Task 4）、`load_settings`（Task 6）、`run_once(..., llm=...)`（Task 7）。
- Produces: `_execute_run` 构建 `GeminiVerdictLLM` 并传给 `run_once`；CLI 测试注入 fake 保持离线。

- [ ] **Step 1: 写失败测试**——修改 `tests/test_cli.py`

在 `fake_env` fixture 里加 fx 桩之外，再补一个 LLM 桩，防止真实 SDK 客户端构建：

```python
class FakeVerdictLLM:
    def __init__(self, *args, **kwargs):
        pass

    def judge(self, overview, rule):
        from dealscout.verdict import DealVerdict

        return DealVerdict(rating="good", reason="ok")
```

在 `fake_env` fixture 内加一行：`monkeypatch.setattr(cli, "GeminiVerdictLLM", FakeVerdictLLM)`（与现有 `monkeypatch.setattr(cli, "FxConverter", FakeFx)` 并列）。同时给 fake Settings 补 `gemini_api_key="g"`（Settings 现有该字段默认 ""，构造时显式传更清晰）。

追加测试：

```python
def test_run_still_works_with_llm_wired(fake_env):
    runner.invoke(cli.app, ["add", "hades", "--max-price", "15"])
    result = runner.invoke(cli.app, ["run"])
    assert result.exit_code == 0, result.output
    assert "no deal" in result.output
```

- [ ] **Step 2: 确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli.py -q`
Expected: FAIL（`cli` 无 `GeminiVerdictLLM` 属性 / _execute_run 未传 llm）

- [ ] **Step 3: 实现**——修改 `dealscout/cli.py`

顶部导入补：`from dealscout.verdict import GeminiVerdictLLM`。
把 `_execute_run` 里构建 wiring 处改为也建 LLM 并传入：

```python
def _execute_run(settings) -> bool:
    store = Store(settings.db_path)
    source = ItadClient(settings.itad_api_key)
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    fx = FxConverter()
    llm = GeminiVerdictLLM(settings.gemini_api_key, settings.llm_model)
    results = run_once(
        store, source, notifier, fx=fx, display_currency=settings.display_currency, llm=llm
    )
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
```

- [ ] **Step 4: 更新 `.env.example`**——追加一行 `GEMINI_API_KEY=get-yours-free-at-aistudio.google.com`，可选 `DEALSCOUT_LLM_MODEL=gemini-2.5-flash`。

- [ ] **Step 5: 更新 `.github/workflows/watch.yml`**——在 `dealscout tick` 步骤的 `env:` 块里加一行 `GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}`（其余不变）。

- [ ] **Step 6: 更新 `README.md`**——在环境变量表加一行 `GEMINI_API_KEY`（免费，aistudio.google.com 申请，用于 LLM 好价判断），并在 Cloud scheduling 一节的 Secrets 列表补上它。

- [ ] **Step 7: 全量验证**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli.py -q` → Expected: 全绿（原 7 + 新 1 = 8）
Run: `.venv\Scripts\python.exe -m pytest -q` → Expected: 全绿（66 passed）
Run: `.venv\Scripts\python.exe -m ruff check .` → Expected: 通过

- [ ] **Step 8: Commit**

```bash
git add dealscout/cli.py .github/workflows/watch.yml .env.example README.md tests/test_cli.py
git commit -m "feat: wire GeminiVerdictLLM into CLI and cloud workflow"
```

- [ ] **Step 9: 用户侧上线动作（不在本计划自动执行，交付清单提醒）**

1. aistudio.google.com 免费申请 `GEMINI_API_KEY`，填进本地 `.env`。
2. GitHub 仓库 Settings → Secrets 加 `GEMINI_API_KEY`。
3. 本地跑一次真实冒烟（Task 4 Step 7 / Task 2 Step 5）确认 Gemini + ITAD overview 通。
4. Actions → watch → Run workflow 手动触发一次验证云端。
