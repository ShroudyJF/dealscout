# DealScout M2b Implementation Plan — Natural-Language Watch Setup

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `dealscout watch "<sentence>"` command that uses an LLM to parse a natural-language request ("盯艾尔登法环，降到RM120叫我") into a `WatchRule` and stores it.

**Architecture:** A new `dealscout/intake.py` module mirrors `dealscout/verdict.py`: a `WatchRequest` Pydantic model, a `WatchParser` Protocol with a `GeminiWatchParser` implementation (same google-genai structured-output form verified in M2a), plus a pure, offline-testable `resolve_watch(req, source, fx)` that validates the request, looks the game up on ITAD, and converts the price threshold to USD. `cli.py` gains a thin `watch` command wiring parser → resolve_watch → store.

**Tech Stack:** Python ≥3.11, Pydantic, Typer, google-genai (`GeminiWatchParser`), httpx (via existing ITAD/FX clients), pytest.

## Global Constraints

- Python ≥3.11 (dev 3.11.9, CI 3.12); no LangChain or any agent framework.
- All layers exchange **Pydantic models**; validation lives in models where it belongs.
- **Tests are fully offline** and run in CI: HTTP via `httpx.MockTransport`, the LLM via an **injected fake client** (never a real network call). Mirror the fake-genai-client pattern already in `tests/test_verdict.py`.
- **google-genai calls copy the exact form in `dealscout/verdict.py`**: lazy import; `genai.Client(api_key=..., http_options=genai.types.HttpOptions(timeout=30_000))`; `client.models.generate_content(model=..., contents=..., config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=<PydanticModel>))`; parse with `<Model>.model_validate_json(resp.text)`; wrap SDK/network/empty/unparseable failures as the module's own error type.
- **Currency default = MYR** when the user does not state one (user is in Malaysia). Threshold is stored in source currency **USD** on `WatchRule.max_price` (consistent with M1.5).
- **In M2b, FX is load-bearing, not best-effort**: an FX failure must abort the command (Exit 1), unlike the M1.5 display-only `≈MYR` line which degrades silently. Do not swallow `FxError` in `watch`.
- Every task ends green: `ruff check .` clean, `pytest` all pass, one `feat:`/`test:`/`docs:` commit.
- Do **not** modify `runner.py`, `notify.py`, `judge.py`, `schedule.py`, `tick`, or `.github/workflows/watch.yml`. The existing 68 tests must stay green.

---

### Task 1: `intake.py` foundation — `WatchRequest`, `ParseError`, `build_prompt`

**Files:**
- Create: `dealscout/intake.py`
- Test: `tests/test_intake.py`

**Interfaces:**
- Consumes: `dealscout.models.WatchRule` (imported for later tasks in this file; not used yet in Task 1).
- Produces:
  - `class ParseError(RuntimeError)`
  - `class WatchRequest(BaseModel)` with fields `title: str | None = None`, `max_price: float | None = None`, `currency: str | None = None`, `min_cut: int | None = None`
  - `def build_prompt(text: str) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_intake.py`:

```python
import pytest

from dealscout.intake import ParseError, WatchRequest, build_prompt


def test_watch_request_defaults_all_none():
    req = WatchRequest()
    assert req.title is None
    assert req.max_price is None
    assert req.currency is None
    assert req.min_cut is None


def test_watch_request_accepts_partial():
    req = WatchRequest(title="Elden Ring", min_cut=20)
    assert req.title == "Elden Ring"
    assert req.min_cut == 20
    assert req.max_price is None


def test_parse_error_is_runtime_error():
    assert issubclass(ParseError, RuntimeError)


def test_build_prompt_includes_user_text_and_field_rules():
    prompt = build_prompt("盯艾尔登法环 降到RM120")
    assert "盯艾尔登法环 降到RM120" in prompt   # 原句喂给模型
    assert "title" in prompt
    assert "英文" in prompt                      # 要求翻成英文名
    assert "MYR" in prompt                       # 没说币种默认 MYR
    assert "max_price" in prompt
    assert "min_cut" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_intake.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dealscout.intake'`

- [ ] **Step 3: Write minimal implementation**

Create `dealscout/intake.py`:

```python
"""Natural-language watch setup. Provider-agnostic: parse() is a Protocol; Gemini implements it."""

from pydantic import BaseModel

from dealscout.models import WatchRule  # noqa: F401  (used by resolve_watch in a later task)


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
        "- title：游戏的英文名（把中文名如“艾尔登法环”翻成“Elden Ring”）；"
        "若听不出是哪个具体游戏（如“找个恐怖游戏”），留 null。\n"
        "- max_price：绝对价格阈值的数字；无则 null。\n"
        "- currency：max_price 的币种 ISO 码（如 USD、MYR）；"
        "用户没明说币种时默认 MYR；无价格则 null。\n"
        "- min_cut：折扣百分比阈值的整数（“打七折”=30、“降三成”=30）；无则 null。"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_intake.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Lint**

Run: `ruff check dealscout/intake.py tests/test_intake.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add dealscout/intake.py tests/test_intake.py
git commit -m "feat: add intake WatchRequest model and build_prompt"
```

---

### Task 2: `resolve_watch` — validate + lookup + currency conversion

**Files:**
- Modify: `dealscout/intake.py` (append `resolve_watch`)
- Test: `tests/test_intake.py` (append)

**Interfaces:**
- Consumes: `WatchRequest`, `ParseError` (Task 1); `dealscout.models.WatchRule`; `dealscout.sources.base.GameNotFoundError` (raised by real `lookup_game`); `dealscout.fx.FxError` (raised by real `convert`). `source` is any object with `lookup_game(title) -> tuple[str, str]`; `fx` is any object with `convert(amount, from_ccy, to_ccy) -> float` (duck-typed, mirrors `runner.py`).
- Produces: `def resolve_watch(req: WatchRequest, source, fx, country: str = "MY") -> WatchRule`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_intake.py`:

```python
from dealscout.intake import resolve_watch
from dealscout.sources.base import GameNotFoundError


class FakeSource:
    def __init__(self, game_id="g-1", canonical="Elden Ring"):
        self._game_id = game_id
        self._canonical = canonical
        self.looked_up = None

    def lookup_game(self, title):
        self.looked_up = title
        return self._game_id, self._canonical


class RecordingFx:
    def __init__(self, rate=0.2377):
        self.rate = rate
        self.calls = []

    def convert(self, amount, from_ccy, to_ccy):
        self.calls.append((amount, from_ccy, to_ccy))
        return amount * self.rate


def test_resolve_watch_usd_passes_through_without_fx():
    fx = RecordingFx()
    rule = resolve_watch(
        WatchRequest(title="Elden Ring", max_price=30, currency="USD"), FakeSource(), fx
    )
    assert rule.title == "Elden Ring"
    assert rule.game_id == "g-1"
    assert rule.max_price == 30
    assert rule.country == "MY"
    assert fx.calls == []            # USD needs no conversion


def test_resolve_watch_myr_converted_to_usd():
    fx = RecordingFx(rate=0.2377)
    rule = resolve_watch(
        WatchRequest(title="Elden Ring", max_price=120, currency="MYR"), FakeSource(), fx
    )
    assert fx.calls == [(120, "MYR", "USD")]
    assert rule.max_price == round(120 * 0.2377, 2)   # 28.52


def test_resolve_watch_missing_currency_defaults_to_myr():
    fx = RecordingFx()
    resolve_watch(
        WatchRequest(title="Elden Ring", max_price=100, currency=None), FakeSource(), fx
    )
    assert fx.calls == [(100, "MYR", "USD")]           # None currency -> MYR (product default)


def test_resolve_watch_pure_min_cut_skips_fx():
    fx = RecordingFx()
    rule = resolve_watch(WatchRequest(title="Elden Ring", min_cut=25), FakeSource(), fx)
    assert rule.max_price is None
    assert rule.min_cut == 25
    assert fx.calls == []


def test_resolve_watch_uses_canonical_title_from_lookup():
    src = FakeSource(game_id="g-elden", canonical="ELDEN RING")
    rule = resolve_watch(
        WatchRequest(title="elden ring", max_price=30, currency="USD"), src, RecordingFx()
    )
    assert src.looked_up == "elden ring"
    assert rule.title == "ELDEN RING"
    assert rule.game_id == "g-elden"


def test_resolve_watch_no_title_raises_parse_error():
    with pytest.raises(ParseError, match="游戏"):
        resolve_watch(
            WatchRequest(title=None, max_price=30, currency="USD"), FakeSource(), RecordingFx()
        )


def test_resolve_watch_blank_title_raises_parse_error():
    with pytest.raises(ParseError, match="游戏"):
        resolve_watch(
            WatchRequest(title="   ", max_price=30, currency="USD"), FakeSource(), RecordingFx()
        )


def test_resolve_watch_no_condition_raises_parse_error():
    with pytest.raises(ParseError, match="条件"):
        resolve_watch(WatchRequest(title="Elden Ring"), FakeSource(), RecordingFx())


def test_resolve_watch_propagates_game_not_found():
    class MissingSource:
        def lookup_game(self, title):
            raise GameNotFoundError("game not found on ITAD: 'zzz'")

    with pytest.raises(GameNotFoundError):
        resolve_watch(
            WatchRequest(title="zzz", max_price=30, currency="USD"), MissingSource(), RecordingFx()
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_intake.py -k resolve_watch -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_watch'`

- [ ] **Step 3: Write minimal implementation**

Append to `dealscout/intake.py`:

```python
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
```

Then remove the now-unnecessary `# noqa: F401` on the `WatchRule` import (it is used now):

```python
from dealscout.models import WatchRule
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_intake.py -v`
Expected: PASS (13 tests total in the file)

- [ ] **Step 5: Lint**

Run: `ruff check dealscout/intake.py tests/test_intake.py`
Expected: no errors (confirm the `WatchRule` import no longer needs `# noqa`)

- [ ] **Step 6: Commit**

```bash
git add dealscout/intake.py tests/test_intake.py
git commit -m "feat: add resolve_watch (validate + lookup + fx to USD)"
```

---

### Task 3: `WatchParser` protocol + `GeminiWatchParser`

**Files:**
- Modify: `dealscout/intake.py` (append protocol + Gemini implementation)
- Test: `tests/test_intake.py` (append)

**Interfaces:**
- Consumes: `WatchRequest`, `build_prompt`, `ParseError` (Task 1). google-genai SDK (lazy import).
- Produces:
  - `class WatchParser(Protocol)` with `def parse(self, text: str) -> WatchRequest`
  - `class GeminiWatchParser` with `__init__(self, api_key: str, model: str, client=None)` and `parse(self, text: str) -> WatchRequest`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_intake.py` (mirrors the fake-genai-client pattern in `tests/test_verdict.py`):

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


def test_gemini_parser_parses_structured_json():
    from dealscout.intake import GeminiWatchParser

    fake = _FakeGenaiClient(text='{"title": "Elden Ring", "max_price": 120, "currency": "MYR", "min_cut": null}')
    parser = GeminiWatchParser(api_key="k", model="gemini-x", client=fake)
    req = parser.parse("盯艾尔登法环 降到RM120")
    assert req.title == "Elden Ring"
    assert req.max_price == 120
    assert req.currency == "MYR"
    assert req.min_cut is None
    assert fake.models.calls                       # 确实调用了模型


def test_gemini_parser_sends_json_schema_config():
    from dealscout.intake import GeminiWatchParser, WatchRequest

    fake = _FakeGenaiClient(text='{"title": "Hades"}')
    parser = GeminiWatchParser(api_key="k", model="gemini-2.5-flash", client=fake)
    parser.parse("盯 Hades")
    kwargs = fake.models.calls[0]
    assert kwargs["model"] == "gemini-2.5-flash"
    assert kwargs["config"].response_mime_type == "application/json"
    assert kwargs["config"].response_schema is WatchRequest


def test_gemini_parser_wraps_errors():
    from dealscout.intake import GeminiWatchParser

    fake = _FakeGenaiClient(exc=RuntimeError("api down"))
    parser = GeminiWatchParser(api_key="k", model="gemini-x", client=fake)
    with pytest.raises(ParseError):
        parser.parse("x")


def test_gemini_parser_empty_response_raises():
    from dealscout.intake import GeminiWatchParser

    fake = _FakeGenaiClient(text="")
    parser = GeminiWatchParser(api_key="k", model="gemini-x", client=fake)
    with pytest.raises(ParseError, match="empty"):
        parser.parse("x")


def test_gemini_parser_bad_json_raises():
    from dealscout.intake import GeminiWatchParser

    fake = _FakeGenaiClient(text="not json at all")
    parser = GeminiWatchParser(api_key="k", model="gemini-x", client=fake)
    with pytest.raises(ParseError):
        parser.parse("x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_intake.py -k gemini_parser -v`
Expected: FAIL with `ImportError: cannot import name 'GeminiWatchParser'`

- [ ] **Step 3: Write minimal implementation**

Add the `Protocol` import at the top of `dealscout/intake.py` (change the typing import line):

```python
from typing import Protocol
```

Append to `dealscout/intake.py`:

```python
class WatchParser(Protocol):
    def parse(self, text: str) -> WatchRequest:
        """Parse a sentence into a WatchRequest. Raises ParseError on failure."""
        ...


class GeminiWatchParser:
    """WatchParser backed by Gemini structured output (google-genai SDK).

    Same call form as GeminiVerdictLLM (M2a): passing a bare Pydantic model
    class as response_schema is a supported, tested google-genai usage.
    """

    def __init__(self, api_key: str, model: str, client=None) -> None:
        self._model = model
        if client is not None:
            self._client = client
        else:
            from google import genai  # lazy import so offline tests need no SDK network

            self._client = genai.Client(
                api_key=api_key,
                # google-genai's default httpx timeout is unbounded; cap it (ms).
                http_options=genai.types.HttpOptions(timeout=30_000),
            )

    def parse(self, text: str) -> WatchRequest:
        prompt = build_prompt(text)
        try:
            from google.genai import types

            resp = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=WatchRequest,
                ),
            )
        except Exception as exc:  # any SDK/network error -> domain error
            raise ParseError(f"gemini parse failed: {exc}") from exc
        out = getattr(resp, "text", None)
        if not out:
            raise ParseError("gemini parse returned empty response")
        try:
            return WatchRequest.model_validate_json(out)
        except Exception as exc:
            raise ParseError(f"gemini parse not valid: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_intake.py -v`
Expected: PASS (18 tests total in the file)

- [ ] **Step 5: Lint**

Run: `ruff check dealscout/intake.py tests/test_intake.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add dealscout/intake.py tests/test_intake.py
git commit -m "feat: add GeminiWatchParser (structured NL parsing)"
```

---

### Task 4: CLI `watch` command + `_fmt_conds` + README

**Files:**
- Modify: `dealscout/cli.py` (add imports, `_fmt_conds`, `watch` command)
- Modify: `tests/test_cli.py` (add `FakeWatchParser`, wire into `fake_env`, add tests)
- Modify: `README.md` (document the `watch` command)

**Interfaces:**
- Consumes: `GeminiWatchParser`, `ParseError`, `resolve_watch` (Task 2/3); `dealscout.fx.FxConverter`, `dealscout.fx.FxError`; existing `ItadClient`, `Store`, `load_settings`, `SettingsError`, `SourceError`.
- Produces: `dealscout watch "<sentence>"` CLI command; `_fmt_conds(req, rule) -> str` helper.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, add a fake parser near the other fakes (after `FakeVerdictLLM`):

```python
class FakeWatchParser:
    def __init__(self, *args, **kwargs):
        pass

    def parse(self, text):
        from dealscout.intake import WatchRequest

        return WatchRequest(title="Elden Ring", max_price=120, currency="MYR", min_cut=None)
```

Wire it into the `fake_env` fixture by adding one line alongside the other `monkeypatch.setattr` calls:

```python
    monkeypatch.setattr(cli, "GeminiWatchParser", FakeWatchParser)
```

Then add these tests at the end of `tests/test_cli.py`:

```python
def test_watch_creates_and_confirms(fake_env):
    result = runner.invoke(cli.app, ["watch", "盯艾尔登法环 降到RM120"])
    assert result.exit_code == 0, result.output
    output = _strip_ansi(result.output)
    assert "Elden Ring" in output
    assert "price<=$120" in output      # FakeFx.convert returns amount unchanged (120)
    assert "MYR120" in output           # confirm echoes original currency+amount


def test_watch_persists_watch(fake_env):
    runner.invoke(cli.app, ["watch", "盯艾尔登法环 降到RM120"])
    result = runner.invoke(cli.app, ["list"])
    assert "Elden Ring" in result.output


def test_watch_errors_when_no_game(fake_env, monkeypatch):
    class NoGameParser:
        def __init__(self, *a, **k):
            pass

        def parse(self, text):
            from dealscout.intake import WatchRequest

            return WatchRequest(title=None, max_price=30, currency="USD")

    monkeypatch.setattr(cli, "GeminiWatchParser", NoGameParser)
    result = runner.invoke(cli.app, ["watch", "找个恐怖游戏"])
    assert result.exit_code == 1
    assert "游戏" in _strip_ansi(result.output)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -k watch -v`
Expected: FAIL — `watch` command not registered (Typer exits non-zero with usage error / `No such command`)

- [ ] **Step 3: Write minimal implementation**

In `dealscout/cli.py`, extend the imports. Change:

```python
from dealscout.fx import FxConverter
```
to:
```python
from dealscout.fx import FxConverter, FxError
```

And add after the existing `from dealscout.verdict import GeminiVerdictLLM` line:

```python
from dealscout.intake import GeminiWatchParser, ParseError, resolve_watch
```

Add the helper and command (place after the `add` command, before `list_`):

```python
def _fmt_conds(req, rule) -> str:
    conds = []
    if rule.max_price is not None:
        src_ccy = (req.currency or "MYR").upper()
        if src_ccy == "USD":
            conds.append(f"price<=${rule.max_price}")
        else:
            conds.append(f"price<=${rule.max_price} (≈{src_ccy}{req.max_price:g})")
    if rule.min_cut is not None:
        conds.append(f"cut>={rule.min_cut}%")
    return " or ".join(conds)


@app.command()
def watch(sentence: str) -> None:
    """Parse a natural-language request and start watching a game."""
    try:
        settings = load_settings()
        parser = GeminiWatchParser(settings.gemini_api_key, settings.llm_model)
        req = parser.parse(sentence)
        source = ItadClient(settings.itad_api_key)
        fx = FxConverter()
        rule = resolve_watch(req, source, fx)
        store = Store(settings.db_path)
        rule = store.add_watch(rule)
    except (SettingsError, ParseError, SourceError, FxError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"watching #{rule.id}: {rule.title} [{_fmt_conds(req, rule)}] country={rule.country}")
```

Note: `rule.max_price` is only non-`None` when `req.max_price` was provided (resolve_watch invariant), so `req.max_price:g` in `_fmt_conds` is always safe.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (all cli tests, including the 3 new `watch` tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS — 68 prior + new intake/cli tests, all green.

- [ ] **Step 6: Update README**

In `README.md`, add a `watch` bullet to the `### Commands` list, right after the `add` bullet:

```markdown
- `dealscout watch "<一句话>"` — describe a game and trigger condition in natural language (中文/English) and DealScout parses it into a watch via the LLM (e.g. `dealscout watch "盯艾尔登法环，降到RM120叫我"`). Non-USD amounts like `RM120` are converted to the USD threshold; the confirmation line echoes both so you can verify the parse.
```

- [ ] **Step 7: Lint**

Run: `ruff check dealscout/cli.py tests/test_cli.py`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add dealscout/cli.py tests/test_cli.py README.md
git commit -m "feat: add dealscout watch command (natural-language setup)"
```

---

## Self-Review

**1. Spec coverage:**
- §3.1 `WatchRequest` → Task 1 ✅
- §3.2 `WatchParser` / `GeminiWatchParser` / `build_prompt` → Task 1 (`build_prompt`) + Task 3 ✅
- §3.3 `resolve_watch` (validate, lookup, MYR→USD, USD passthrough, pure min_cut, default MYR) → Task 2 ✅
- §3.4 CLI `watch` + `_fmt_conds` → Task 4 ✅
- §3.5 no new config → nothing to do; README documents the command (Task 4) ✅
- §4 error table (ParseError no-game / no-condition / LLM fail; GameNotFound; SourceError; FxError) → Task 2 (ParseError, GameNotFound propagation), Task 3 (LLM fail → ParseError), Task 4 (single except tuple → Exit 1) ✅
- §5 tests (build_prompt, GeminiWatchParser parse/empty/bad, resolve_watch matrix, CLI success + no-game) → Tasks 1–4 ✅
- §7 invariants (don't touch runner/notify/tick; 68 tests stay green) → Global Constraints + Task 4 Step 5 full-suite run ✅

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; every command shows expected output. ✅

**3. Type consistency:** `WatchRequest` fields (`title/max_price/currency/min_cut`) are identical across Tasks 1–4; `resolve_watch(req, source, fx, country="MY") -> WatchRule` signature matches its call in Task 4; `GeminiWatchParser(api_key, model, client=None).parse(text) -> WatchRequest` matches Task 4 usage and the M2a `GeminiVerdictLLM` shape; `_fmt_conds(req, rule)` signature matches its call site. `ParseError`/`FxError`/`SourceError` all `RuntimeError` subclasses caught by the Task 4 except tuple. ✅
