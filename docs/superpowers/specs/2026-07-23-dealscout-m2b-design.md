# DealScout M2b 设计文档 —— 自然语言下单（NL Watch Setup）

日期：2026-07-23
状态：已与项目所有者确认设计，待审阅书面稿

## 1. 背景与定位

M1 + M1.5 + M2a 已上线：DealScout 是一只"有记忆、会主动通知、能用 LLM 判断好价"的盯价狗。

M2b 给它装上**自然语言入口**：用户不用记 `--max-price` / `--min-cut` 这些参数，直接一句话——"帮我盯艾尔登法环，降到 RM120 叫我"——LLM 把它解析成一条 `WatchRule` 建好。

**为什么这是差异化而非"套壳"**：解析本身不是护城河，但它降低了这只盯价狗的使用门槛，让"持续盯价 + 主动通知"这条真正的护城河更好用。一个附带的真实价值点：LLM 顺手把中文游戏名（"艾尔登法环"）翻成 ITAD 能查的英文名（"Elden Ring"），比字符串直传更稳。

## 2. 范围

**做（范围 A）**：解析"具体游戏 + 条件"。一个新 CLI 命令 `dealscout watch "<一句话>"`：LLM 解析 → 校验 → ITAD 查游戏 → 货币换算 → 建 `WatchRule` 入库 → 回显确认。LLM 解析层做成可替换（协议 + 一个 Gemini 实现），复用 M2a 的 provider 套路。

**明确不做（YAGNI）**：
- **范围 B：发现/推荐**（"帮我找个好玩的恐怖游戏"）——需要 DealScout 没有的发现数据源、且撞聊天式 AI 强项、回报低。以后单独议，很可能不做。
- 交互式 Telegram bot（那是被否掉的多用户方向）。
- 改动 runner/notify/tick 主干——建 watch 是一次性的本地/手动动作，不进每小时云端 tick。

## 3. 详细设计

### 3.1 数据模型 `WatchRequest`（新增 `dealscout/intake.py` 内）

LLM 从一句话抽出的"原始意图"，**字段全可选**——"没听出游戏名 / 没听出条件"是要友好报错的正常结果，不能让它在模型层抛异常，校验交给 `resolve_watch`（§3.3）。

```python
class WatchRequest(BaseModel):
    title: str | None = None        # 具体游戏名（英文，便于 ITAD 查）；听不出具体游戏 → None
    max_price: float | None = None  # 绝对价格阈值，单位见 currency
    currency: str | None = None     # max_price 的币种 ISO 码，如 "USD"/"MYR"；无价则 None
    min_cut: int | None = None      # 折扣百分比阈值
```

### 3.2 LLM 解析层 `dealscout/intake.py`

与 `dealscout/verdict.py` 同构（`VerdictLLM` / `GeminiVerdictLLM` 那套）：

```python
class ParseError(RuntimeError): ...

class WatchParser(Protocol):
    def parse(self, text: str) -> WatchRequest:
        """把一句话解析成 WatchRequest。失败抛 ParseError。"""
        ...

def build_prompt(text: str) -> str: ...

class GeminiWatchParser:
    def __init__(self, api_key: str, model: str, client=None) -> None: ...
    def parse(self, text: str) -> WatchRequest: ...
```

- **Gemini 实现**：完全照搬 M2a 已验证的调用形态——
  - 构造：`genai.Client(api_key=..., http_options=genai.types.HttpOptions(timeout=30_000))`（毫秒超时，防止无界挂死；lazy import 让离线测试不需要 SDK）。
  - 调用：`client.models.generate_content(model, contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=WatchRequest))`。
  - 解析：`WatchRequest.model_validate_json(resp.text)`。
  - SDK/网络异常、空响应、解析失败一律包成 `ParseError`。
  - `client` 可注入，测试传 fake，全程离线。
- **Prompt 内容（`build_prompt`）**：中文指令，要求模型从用户输入抽取：
  - `title`：**游戏的英文名**（把"艾尔登法环"这类翻成"Elden Ring"）；若听不出是哪个具体游戏（如"找个恐怖游戏"），留 `null`。
  - `max_price` + `currency`：绝对价格阈值及其币种 ISO 码。**用户没明说币种时默认 `MYR`**（用户是马来西亚人，"降到 30"大概率指 RM30；且确认消息会回显换算后的 USD 让用户核对）。无价格阈值则两者留 `null`。
  - `min_cut`：折扣百分比阈值（"打七折" → 30、"降三成" → 30），无则 `null`。

### 3.3 纯逻辑函数 `resolve_watch`（`dealscout/intake.py` 内）

把"校验 + 查游戏 + 换算"从 CLI 抽成一个可离线单测的纯函数（注入 source/fx 的 fake 即可测，不碰网络、不碰 typer）：

```python
def resolve_watch(req: WatchRequest, source, fx, country: str = "MY") -> WatchRule:
    """把 WatchRequest 落成一条可入库的 WatchRule。

    source: 需有 lookup_game(title) -> (game_id, canonical)
    fx:     需有 convert(amount, from_ccy, to_ccy) -> float
    抛出：ParseError（无游戏/无条件）、GameNotFoundError/SourceError（查游戏）、FxError（换算）
    """
```

行为：
1. `req.title` 为空/空白 → `ParseError("没听出具体游戏名，请说清是哪个游戏")`。
2. `req.max_price is None and req.min_cut is None` → `ParseError("没听出价格或折扣条件")`。
3. `source.lookup_game(req.title)` → `(game_id, canonical)`；查不到由其抛 `GameNotFoundError`（`SourceError` 子类）冒泡。
4. 货币换算得 `max_price_usd`：
   - `req.max_price is None` → `None`（纯 min_cut 规则，不换算）。
   - 否则 `ccy = (req.currency or "MYR").upper()`——兜底 `MYR` 与 §3.2 的产品默认一致（LLM 正常会填币种；万一漏填，也按"马来用户默认 RM"处理，不会被误当 USD 而放宽 4 倍阈值）。`ccy == "USD"` → 原值；否则 `round(fx.convert(req.max_price, ccy, "USD"), 2)`（`FxError` 冒泡）。
5. 返回 `WatchRule(title=canonical, game_id=game_id, max_price=max_price_usd, min_cut=req.min_cut, country=country)`。

**货币两个决定**（划清与 M1.5 的区别）：
- **存 USD**：`WatchRule.max_price` 一律存源货币 USD（country=MY 源头是 USD，与 M1.5 一致）。
- **这里 fx 是"承重"的，失败必须硬报错**：M1.5 通知里的 ≈MYR 只是显示，失败静默降级无所谓；M2b 换算出的是**阈值本身**，换错/换不出会建出一条坏 watch，所以 `FxError` 直接冒泡 → CLI Exit(1)，不静默降级。

### 3.4 CLI 命令 `dealscout watch`（`dealscout/cli.py`）

薄壳，与现有 `add` 同构；`GeminiWatchParser` / `ItadClient` / `FxConverter` / `Store` 均以模块级名字引用，便于测试 monkeypatch：

```python
@app.command()
def watch(sentence: str) -> None:
    """用一句话描述想盯的游戏和条件，自动建 watch。"""
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

- `_fmt_conds(req, rule)`：拼确认串，**同时显示换算后的 USD 阈值与原始输入币种**，让用户一眼核对 LLM 有没有听错。例：`price<=$28.54 (≈RM120) or cut>=20%`。当输入本就是 USD 或无 max_price 时，省掉 `(≈...)` 段。
- 需要 `from dealscout.fx import FxError` 与 `from dealscout.intake import GeminiWatchParser, ParseError, resolve_watch`（`SourceError` 已导入，`GameNotFoundError` 是其子类，被同一 except 捕获）。

用法：
```
$ dealscout watch "帮我盯艾尔登法环，降到RM120叫我"
watching #4: Elden Ring [price<=$28.54 (≈RM120)] country=MY
```

### 3.5 配置

**无新增**。`GEMINI_API_KEY`（必填）与 `DEALSCOUT_LLM_MODEL`（默认 gemini-2.5-flash）M2a 已加。云端 `watch.yml` 不改——建 watch 不进每小时 tick。

## 4. 错误处理

全部落到 CLI 的 `except` → `Exit(1)` + stderr 友好消息：

| 情况 | 抛出 | 消息（示例） |
|---|---|---|
| LLM 听不出具体游戏 | `ParseError` | 没听出具体游戏名，请说清是哪个游戏 |
| 听不出价格/折扣条件 | `ParseError` | 没听出价格或折扣条件 |
| LLM 调用失败/空响应/不可解析 | `ParseError` | gemini parse failed: ... |
| 游戏 ITAD 查不到 | `GameNotFoundError` | game not found on ITAD: '...' |
| ITAD/网络故障 | `SourceError` | ITAD lookup failed: HTTP ... |
| 汇率换算失败 | `FxError` | fx rate failed: ... |

## 5. 测试策略（全离线，进 CI）

**`tests/test_intake.py`（新增）**：
- `build_prompt` 组装：含用户原句，指令里含"英文游戏名/价格+币种/折扣/听不出留空"要点。
- `GeminiWatchParser.parse`：注入 fake client（返回固定 JSON），解析出预期 `WatchRequest`；空响应 → `ParseError`；坏 JSON → `ParseError`。
- `resolve_watch`（用 `FakeSource` + `FakeFx`）：
  - 正常 USD 阈值：`currency="USD"` 不经换算原值入 `max_price`。
  - MYR→USD 换算：`currency="MYR", max_price=120` → 调 `fx.convert(120,"MYR","USD")`、结果两位小数入 `max_price`。
  - 无 currency 兜底：`max_price` 有值但 `currency=None` → 按 `"MYR"` 换算（与 §3.2 产品默认一致），测这条兜底路径确实走了 `fx.convert(..., "MYR", "USD")`。
  - 无 `title` → `ParseError`；`max_price` 与 `min_cut` 皆空 → `ParseError`。
  - `GameNotFoundError` 从 `lookup_game` 冒泡。
  - 纯 `min_cut`（`max_price=None`）：不调用 `fx.convert`，`max_price` 落 `None`。

**`tests/test_cli.py`（追加）**：新增 `FakeWatchParser`（返回固定 `WatchRequest`），在 `fake_env` fixture 里 `monkeypatch.setattr(cli, "GeminiWatchParser", FakeWatchParser)`；`FakeFx.convert` 返回可预期值。
- `watch` 成功：`exit_code == 0`，回显含规范游戏名与阈值。
- `watch` 失败：parser 返回无 title 的 `WatchRequest` → `Exit(1)` + stderr 友好消息（用 `_strip_ansi` 处理 rich 颜色，与现有 `test_add_requires_a_condition` 一致）。

**真实冒烟（用户侧手动，需 Gemini+ITAD key，联网）**：`dealscout watch "盯艾尔登法环，降到RM120"`，人工看解析是否正确（英文名、RM→USD 阈值、确认回显）。

## 6. 里程碑标志

用户在终端敲 `dealscout watch "一句话"`，DealScout 听懂游戏与条件、建好 watch 并回显核对；从此不必再记 `--max-price` / `--min-cut` 参数。

## 7. 不变量（回归保证）

- 不改 `WatchRule` / `Store` / `runner` / `notify` / `tick` 的现有行为；M1/M1.5/M2a 的 68 个测试全部保持绿。
- `intake.py` 与 `verdict.py` 各自独立，仅共享"结构化输出"这一模式；若 M2c 出现第三个 Gemini 调用点，再考虑把两处 client 构造抽成公共 provider（现在两处不抽，YAGNI）。
