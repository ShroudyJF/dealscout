# DealScout M2a 设计文档 —— LLM 好价判断（Deal Verdict）

日期：2026-07-22
状态：已与项目所有者确认设计，待审阅书面稿

## 1. 背景与定位

M1 + M1.5 已上线：DealScout 是一只"有记忆、会主动通知"的盯价狗，护城河是**聊天式 AI 做不到的三件事**——自主持续盯价、积累历史、主动推送。

M2a 给这条护城河装上第一层真正的 LLM 能力：**不只告诉你"降价了"，而是结合 ITAD 的史上最低价，用大模型给出带理由的"这价到底值不值 / 要不要等"判断**，一起推送。

**为什么这是差异化而非"套壳搜索"**：聊天式 AI 是"拉"（你问一次），DealScout 是"推"（替你守着、逼近史低时主动喊你），且判断基于持续跟踪。用户痛点是"这折扣是真降还是先涨后降 / 现在买还是再等"，M2a 直接回答它。

**为什么先做游戏、不做电商**（关键决策，已充分讨论）：
- Shopee/Lazada 自动盯价 = 规模化反爬 + 需长期自采数据，是公司级数据工程，单人不可行；且纯"现价比价"打不过聊天式 AI。
- 游戏走 ITAD：**现价与史上最低价现成可取**（已实测 `/games/overview/v2` 同时返回 `current` 与 `lowest`），装好当天即可判断真假好坏，**无需攒数据**。

## 2. 范围

**做**：对已触发的游戏监控，取 ITAD 现价 + 史低，调 LLM 产出结构化"好价判断"，拼进 Telegram 通知。LLM 层做成可替换（协议 + 一个 Gemini 实现）。

**明确不做（YAGNI）**：Shopee/Lazada 抓取、跨平台比价、自采长期历史（改用 ITAD 史低）、多模型编排（先一个 Gemini 实现）。

## 3. 详细设计

### 3.1 数据模型（`dealscout/models.py`）

新增：
```
PriceOverview(current: PricePoint, historical_low: PricePoint | None)
```
`current`/`historical_low` 复用现有 `PricePoint`（史低的 timestamp 需保留，用于"多久以前"——`PricePoint` 已有 currency/price/regular/cut/url，新增可选 `seen_at: str | None = None` 承载史低日期）。

### 3.2 ITAD 取史低（`dealscout/sources/itad.py`）

新增方法（不动现有 `fetch_prices`，M1 主干不变）：
```
ItadClient.fetch_overview(rule: WatchRule) -> PriceOverview
```
- 请求 `POST /games/overview/v2`，params `{key, country}`，body `[game_id]`。
- 从响应 `prices[0].current` 取现价 PricePoint；从 `prices[0].lowest` 取史低 PricePoint（含其 `timestamp` → `seen_at`）。
- 无 `lowest` 时 `historical_low=None`。
- 非 200 / 结构异常抛 `SourceError`（复用现有异常）。

### 3.3 LLM 好价判断（新增 `dealscout/verdict.py`）

```
DealVerdict(BaseModel):
    rating: Literal["buy_now", "good", "wait", "skip"]
    reason: str          # 一句话人话理由（中文）
    wait_target: float | None = None   # 若建议再等，给个可设提醒的目标价

class VerdictLLM(Protocol):
    def judge(self, overview: PriceOverview, rule: WatchRule) -> DealVerdict: ...

class GeminiVerdictLLM:
    def __init__(self, api_key: str, model: str, client=None): ...
    def judge(self, overview, rule) -> DealVerdict: ...

class VerdictError(RuntimeError): ...
```

- **Gemini 实现**：用 Google 官方 `google-genai` SDK；结构化输出（`response_schema=DealVerdict` / `response_mime_type=application/json`），取解析后的对象。`client` 可注入，测试时传 fake，全程离线。
- **Prompt 内容**：喂现价、原价、折扣%、史上最低价及其日期（多久以前）、用户阈值；要求模型判断"这价相对历史值不值、要不要等"，输出上面四个字段。实现时对照 Gemini 官方文档写结构化输出，不凭记忆。
- **模型**：默认 `DEALSCOUT_LLM_MODEL`（默认某个 Gemini Flash，实现时取当前可用型号），免费额度覆盖低频使用。

### 3.4 配置（`dealscout/config.py`）

新增：
- `GEMINI_API_KEY`（必填，Google AI Studio 免费申请，不用绑卡）
- `DEALSCOUT_LLM_MODEL`（可选，默认 Gemini Flash 型号）

### 3.5 接线（`dealscout/runner.py`）

触发通知前，best-effort 地取 overview + 判断：
```
verdict = None
try:
    overview = source.fetch_overview(rule)   # 若 source 支持
    verdict = llm.judge(overview, rule)
except Exception:
    verdict = None   # best-effort：LLM/ITAD 出错绝不阻断通知
message = format_deal(deal, display, verdict)
```
`run_once` 新增可选参数 `llm: VerdictLLM | None = None`；缺省时不判断，保持 M1/M1.5 行为与现有测试不变。

### 3.6 通知格式（`dealscout/notify.py`）

`format_deal` 增加可选 `verdict: DealVerdict | None`，非空时追加一段：
```
📊 好价判断：{中文评级} {stars}
{reason}
（史上最低 {史低价}，{史低日期}）
```
`verdict=None` 时不加该段（best-effort 降级）。

## 4. 错误处理

- LLM/ITAD-overview 任何异常 → 捕获，通知照发、只是没有判断段（与 M1.5 汇率"best-effort"同一原则）。
- Gemini 免费额度限速（429）→ 视为 best-effort 失败，跳过判断；不重试轰炸。

## 5. 前置（用户侧）

- **Gemini API key**：aistudio.google.com 免费申请（不绑卡），填入 `.env` 与 GitHub Secrets。
- 云端：`GEMINI_API_KEY` 加进 `watch.yml` env（来自 Secrets）；上线前手动 `workflow_dispatch` 验证 GitHub Actions 里 Gemini 可调通。

## 6. 测试策略

- **models / itad.fetch_overview**：离线，存真实 overview 快照当 fixture，MockTransport 断言请求与解析（含 `lowest` 与无 `lowest` 两种）。
- **verdict**：注入返回固定 `DealVerdict` 的 FakeVerdictLLM，测 prompt 组装与 best-effort（judge 抛错时 runner 仍发通知、无判断段）。
- **notify**：`format_deal` 带/不带 verdict 两种输出。
- **真实冒烟**（需 Gemini key + ITAD key，联网）：对 Hades 取 overview + 真实 Gemini 判断，人工看输出合理性。
- 全离线单测进 CI，维持绿；真实冒烟为用户侧手动步骤。

## 7. 里程碑标志

盯的游戏触发时，你收到的 Telegram 通知里出现一段**基于史上最低价、带理由的中文好价判断**（如"值得买，接近史低"或"再等等，去年更便宜"）。
