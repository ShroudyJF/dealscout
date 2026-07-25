# DealScout M2c 设计文档 —— 假降价 / 价格趋势判断（Deal Authenticity）

日期：2026-07-25
状态：已与项目所有者确认设计，待审阅书面稿

## 1. 背景与定位

M2a 已给通知装上 LLM「好价判断」，用的是**现价 vs 全时史低**。M2c 把护城河里「识破假降价」那块做深：用 ITAD 的**近 3 个月价格历史序列**，识破「先涨后降」（原价被抬高让折扣显大）和「伪稀缺」（这价常年出现、并不特别），并把当前价放进近 3 月区间给出低位判断。

**为什么不用攒数据**：ITAD `GET /games/history/v2` 现成返回近 3 个月的降价日志（每次价格变动的时间戳、shop、price/regular/cut），装好当天即可判断，无需自采历史——与 M2a 用现成史低同理。

**为什么是差异化**：聊天式 AI 答不了「这个 -75% 是真降还是原价先被抬高」，因为它没有持续的价格历史上下文；DealScout 有。

## 2. 范围

**做**：触发通知时，额外拉近 3 月历史 → 纯函数算确定性趋势特征 → 喂进 M2a 的 LLM 判断，让 verdict 多一个「折扣真实性」结论，并在通知里显示。全 best-effort：历史拿不到就优雅退回 M2a 行为。

**明确不做（YAGNI）**：自采长期历史（改用 ITAD 3 月日志）；时间加权统计（用简单观测点中位数即可，交给 LLM 判断而非精确建模）；纯规则判定（判断交给已有的 LLM 层）；新 CLI 命令或用户交互面（M2c 无新用户界面，只增强既有通知）。

## 3. 详细设计

### 3.1 数据模型（`dealscout/models.py`）

历史序列复用现有 `PricePoint`（已含 shop/price/regular/cut/currency/url/seen_at；`seen_at` 承载每个历史点的时间戳）。新增：

```python
class TrendFeatures(BaseModel):
    window_days: int                 # 统计窗口（名义值 90，对应 ITAD ~3 月默认）
    points: int                      # 窗口内价格观测点数
    low: float                       # 近 3 月最低价
    median: float                    # 近 3 月观测点中位价
    times_at_or_below_current: int   # 历史上 price <= 当前价 的观测点数（伪稀缺信号）
    regular_recently_raised: bool    # 现原价高于窗口内最低原价（先涨后降信号）
```

`DealVerdict`（`dealscout/verdict.py`）增一个**向后兼容**字段（默认 `"unknown"`，M2a 行为不变）：

```python
class DealVerdict(BaseModel):
    rating: Literal["buy_now", "good", "wait", "skip"]
    reason: str
    wait_target: float | None = None
    discount_authenticity: Literal["real", "inflated", "common", "unknown"] = "unknown"
    # real=真降(接近历史低位)  inflated=原价虚高(先涨后降)  common=常年这价(伪稀缺)  unknown=无历史数据
```

### 3.2 ITAD 拉历史（`dealscout/sources/itad.py`）

新增（不动现有 `fetch_prices`/`fetch_overview`）：

```python
def fetch_history(self, rule: WatchRule) -> list[PricePoint]:
    # GET /games/history/v2  params {key, id, country}  —— ITAD 默认返回近 3 个月降价日志
    # 非 200 -> SourceError；空/无日志 -> []
```

- ITAD 每条形如 `{"timestamp":..., "shop":{"name":..}, "deal":{"price":{"amount":..,"currency":..}, "regular":{"amount":..}, "cut":..}}`——price/regular/cut 在 `deal` 下、shop 在顶层，与 `_point_from` 期望的扁平结构不同。
- 解析：把每条**摊平**成 `_point_from` 能吃的 block（`{"shop": e["shop"], "price": e["deal"]["price"], "regular": e["deal"]["regular"], "cut": e["deal"]["cut"]}`），`seen_at=e["timestamp"]`。整段摊平+解析包在一个 `try/except (KeyError, TypeError) -> SourceError`，保证 malformed 日志不裸崩（与 `_point_from` 内部同款加固一致）。
- 返回按 ITAD 给的顺序的 `list[PricePoint]`（ITAD 日志按时间升序；最后一条为最近）。

### 3.3 特征计算（新增 `dealscout/trend.py`，纯函数、离线可测）

```python
import statistics

def compute_trend(history: list[PricePoint], current: PricePoint,
                  window_days: int = 90) -> TrendFeatures | None:
    if len(history) < 2:          # 数据太少无意义 -> None（降级回 M2a）
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

- 无网络、无 LLM，确定性。`current` 来自 `overview.current`（M2a 已取）。
- `regular_recently_raised`：现原价 > 窗口内最低原价 → 参考价被抬（先涨后降信号）。
- `times_at_or_below_current` 配合 `points` 看占比 → 伪稀缺信号。
- 低位区间由 `low`/`median` + 现价交给 LLM 解读，代码不下硬结论。

### 3.4 verdict 吃特征（`dealscout/verdict.py`）

- `VerdictLLM.judge` 与 `GeminiVerdictLLM.judge` 签名加**可选** `trend: TrendFeatures | None = None`；`build_prompt(overview, rule, trend=None)`。
- Gemini 调用机制（`generate_content` + `response_schema=DealVerdict`）不变——`DealVerdict` 多了字段，schema 自动带上。
- **trend 非空**：prompt 追加「价格历史」段（window_days / points / low / median / times_at_or_below_current / regular_recently_raised），要求模型据此把 `discount_authenticity` 设为 real/inflated/common 之一、并在 reason 点明。
- **trend 为空**：prompt 不加该段，指示 `discount_authenticity="unknown"`——与 M2a 行为一致；`judge(overview, rule)` 老调用（trend 默认 None）照常工作。

### 3.5 接线 + best-effort 降级（`dealscout/runner.py`）

```python
def _make_verdict(source, llm, rule):
    if llm is None:
        return None
    fetch_ov = getattr(source, "fetch_overview", None)
    if fetch_ov is None:
        return None
    try:
        overview = fetch_ov(rule)
    except Exception:
        return None
    trend = _make_trend(source, rule, overview.current)   # best-effort，可为 None
    try:
        return llm.judge(overview, rule, trend=trend)
    except Exception:
        return None

def _make_trend(source, rule, current):
    fetch_hist = getattr(source, "fetch_history", None)
    if fetch_hist is None:
        return None
    try:
        return compute_trend(fetch_hist(rule), current)
    except Exception:
        return None
```

每层 best-effort：源不支持 `fetch_history`、拉取失败、点数 <2 → trend=None → judge 照跑（等于 M2a）→ 通知绝不被阻断。

### 3.6 通知格式（`dealscout/notify.py`）

`format_deal` 在已有「📊 好价判断」段里，当 `discount_authenticity != "unknown"` 时于 reason 之后加一行（duck-typed `getattr(verdict, "discount_authenticity", "unknown")`，字段缺失/unknown 就不加 → M2a 通知不变）：

```
📊 好价判断：{评级}
{reason}
🔎 折扣真实性：{标签}
（目标价 …）        # 若有 wait_target，仍在最后
```

标签：`real`→「真降，接近历史低位」；`inflated`→「原价虚高（先涨后降）」；`common`→「常年这价（非稀缺）」。

### 3.7 配置

**无新增**。复用 `GEMINI_API_KEY` / `ITAD_API_KEY`。云端 `watch.yml` 不改（M2c 只增强既有 tick 通知）。

## 4. 错误处理

- ITAD-history / 特征计算 / LLM 任何异常 → 捕获，退回「无 trend」的 M2a 判断，通知照发（与 M1.5 汇率、M2a verdict 同一 best-effort 原则）。
- ITAD history malformed 日志 → `SourceError`，被 `_make_trend` 的 except 吞成 trend=None。
- Gemini 限速（429）→ best-effort 失败，跳过判断，不重试轰炸。

## 5. 向后兼容 / 不变量

- `DealVerdict` 新字段默认 `"unknown"`；`judge`/`build_prompt` 新参数默认 `None` → M2a 的 `judge(overview, rule)` 调用与其单测不变。
- 现有 M2a 测试里的 fake LLM（`tests/test_runner.py` 的 `FakeLLM`、`tests/test_cli.py` 的 `FakeVerdictLLM`）的 `judge` 需加 `trend=None` 形参——协议扩展的必要改动。
- 不动 `fetch_prices`/`fetch_overview`/`judge.py`/`schedule.py`/`tick`/`cli.py` 命令行为；现有 98 测试保持绿。

## 6. 测试策略（全离线，进 CI）

- **`tests/test_trend.py`（新）**：`compute_trend` —— 原价被抬→`regular_recently_raised=True`、未抬→False；常年低价→`times_at_or_below_current` 大；`low`/`median` 数值正确；<2 点→None。
- **`tests/test_itad.py`**：`fetch_history` 解析 history/v2 序列（真实快照 fixture，含 deal 嵌套）、HTTP 错→SourceError、空日志→[]、malformed 条目→SourceError。
- **`tests/test_verdict.py`**：`DealVerdict` 接受 `discount_authenticity`；`build_prompt` 带 trend 时含历史段字段、不带时无该段；`judge` 传 trend（fake client）；不带 trend 的老调用照常。
- **`tests/test_runner.py`**：源有 `fetch_history`→trend 算出并传入 judge；源无 `fetch_history`→trend=None 仍判断（M2a 行为）；history 报错→仍通知。更新 `FakeLLM.judge` 加 `trend=None`。
- **`tests/test_notify.py`**：`discount_authenticity != "unknown"`→显示真实性行；unknown/字段缺失→不显示。
- **`tests/test_cli.py`**：`FakeVerdictLLM.judge` 加 `trend=None`（保证 `run` 不挂）。
- **真实冒烟（用户侧手动，需 Gemini+ITAD key，联网）**：对一款近期打折游戏取 history + 判断，人工看 authenticity 是否合理。

## 7. 里程碑标志

盯的游戏触发时，Telegram 通知里除了 M2a 的好价判断，多一行**基于近 3 月价格历史的折扣真实性结论**（如「原价虚高（先涨后降）」或「真降，接近历史低位」），且历史拿不到时优雅退回 M2a 的判断、通知照发。
