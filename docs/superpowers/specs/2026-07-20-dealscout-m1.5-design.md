# DealScout M1.5 设计文档 —— 汇率换算 + 时区门控调度 + 云端定时

日期：2026-07-20
状态：已与项目所有者确认设计，待审阅书面稿

## 1. 背景与动机

M1 已上线（https://github.com/ShroudyJF/dealscout），本地 Windows 计划任务每日 9:00 运行。实际使用暴露两个问题：

1. **本地定时不可靠**：Windows 任务"仅交互"模式，笔记本 9 点若睡眠/关机/未登录则跳过；catch-up 运行还返回启动错误码。
2. **两个体验缺口**：
   - **货币**：马来西亚游戏商店（Steam/Epic/微软商店）源头就用 **USD** 定价（已实测：country=MY/SG 返回 USD，GB 返回 GBP，DE 返回 EUR，证明 ITAD 正确本地化、MY 真实币种即 USD）。用户想看 RM 需**汇率换算**，源头无 MYR 价可直接取。
   - **时间/时区**：用户想要"早上 9 点通知"，但不同国家用户时区不同；GitHub Actions cron **只认 UTC**，无法直接表达"用户本地 9 点"。

M1.5 目标：把定时运行搬到 **GitHub Actions 云端**，并顺带补齐**按用户时区触发**与**RM 汇率换算**两个体验点。

## 2. 范围

三个相对独立的小件，合为一份 spec（彼此关联：都服务于"云端按我本地 9 点跑、给我看 RM"）：

1. 汇率换算（≈ RM 一行）
2. 时区门控调度（`should_run_now` + `dealscout tick`）
3. GitHub Actions 云端工作流 + 数据库持久化

**明确不做（YAGNI）**：多币种偏好切换、汇率历史留存、Web 界面、多用户。仍是单人个人工具。

## 3. 详细设计

### 3.1 汇率换算

**新模块 `dealscout/fx.py`**：

```
FxConverter(base_url: str = FRANKFURTER_API, client: httpx.Client | None = None)
    convert(amount: float, from_ccy: str, to_ccy: str) -> float
FxError(RuntimeError)
FRANKFURTER_API = "https://api.frankfurter.app"
```

- 数据源：**Frankfurter**（免费、无需 key、ECB 官方数据，货币列表含 MYR）。请求 `/latest?from=<from>&to=<to>`，取 `rates[to]` 乘以 amount。
- `from_ccy == to_ccy` 时直接返回原值，不发请求。
- 沿用项目 `client` 注入模式；测试全程 `httpx.MockTransport` 离线。
- 每次进程内对同一 `(from, to)` 只请求一次（内存缓存 dict）。
- HTTP 非 200 或响应缺字段 → 抛 `FxError`。

**配置**：新增 `DEALSCOUT_DISPLAY_CURRENCY`（默认 `MYR`）。加入 `Settings`。

**接入点**：`format_deal` 保持纯函数，签名扩展为
```
format_deal(deal: Deal, display: tuple[str, float] | None = None) -> str
```
`display` 为 `(currency, amount)`；非空时在原价行下追加一行 `≈ {currency} {amount:.2f}`，为空时不加该行。

`runner.run_once` 负责换算：拿到 `deal` 后，用 `FxConverter` 把 `deal.best.price`（源货币 `deal.best.currency`）换算到 `settings.display_currency`，成功则 `display=(display_currency, converted)`，**任何 `FxError` 被捕获并记录**（不影响通知，`display=None`）。为此 `run_once` 需要新增可选参数 `fx: FxConverter | None = None` 和 `display_currency: str | None = None`；两者缺省时不换算，保持 M1 行为与现有测试不变。

**通知效果**：
```
🎯 DealScout: Hades
Steam: USD 12.49 (regular 24.99, -50%)
≈ MYR 58.70
why: cut 50% >= 30%
<url>
```

**测试**：
- `convert` 正常换算、同币种短路、非 200 抛 FxError（MockTransport）。
- `format_deal` 带/不带 display 两种输出。
- `run_once`：注入 FakeFx 验证通知含换算行；注入抛 FxError 的 FakeFx 验证通知照发但无换算行、`RunResult` 不受影响。

### 3.2 时区门控调度

**新模块 `dealscout/schedule.py`**：

```
should_run_now(tz_name: str, run_hour: int, now_utc: datetime) -> bool
```
- 用 `zoneinfo.ZoneInfo(tz_name)` 将 `now_utc`（tz-aware UTC）转为本地时间，返回 `local.hour == run_hour`。
- 夏令时由 tz 数据库自动处理。
- `now_utc` 作为参数注入，保证纯函数可测（不在函数内部读时钟）。

**配置**：新增 `DEALSCOUT_TZ`（默认 `Asia/Kuala_Lumpur`）、`DEALSCOUT_RUN_HOUR`（默认 `9`，`int`）。加入 `Settings`。

**新 CLI 命令 `dealscout tick`**（小时心跳）：
- 读 settings；`now_utc = datetime.now(timezone.utc)`。
- `should_run_now(tz, run_hour, now_utc)` 为真 → 执行与 `run` 相同的监控逻辑（复用同一段 wiring）；为假 → `typer.echo("skipped: not {run_hour}:00 in {tz}")` 并正常退出 0，**不请求 ITAD**。
- `dealscout run` 保持无条件执行（手动使用）。为避免 `run` 与 `tick` 重复 wiring，把"构建 store/source/notifier/fx 并调用 `run_once`、打印结果、按需 Exit(1)"抽成一个内部辅助函数 `_execute_run(settings)`，`run` 与 `tick`（门控通过时）都调用它。

**依赖**：新增 `tzdata`（Windows 上 `zoneinfo` 无系统时区库，必须依赖；Linux 无害，保证可移植）。

**测试**：
- `should_run_now`：固定 `now_utc`，对多个 tz 断言（UTC+8 的 9 点、UTC 的同一时刻不为 9 点）；专测一个夏令时时区（`America/New_York`）在夏/冬令时同一 UTC 时刻的不同判定。
- `tick`：门控读取的"当前时间"来自 `cli` 里的 `datetime.now(timezone.utc)`；测试 monkeypatch `cli` 的 now 源（或把 now 抽成可注入），使门控为真→触发 run 逻辑；为假→输出 skipped 且不构建/调用 source（monkeypatch `cli.ItadClient` 为断言未被实例化的 fake）。

### 3.3 GitHub Actions 云端 + 数据库持久化

**新工作流 `.github/workflows/watch.yml`**（独立于 ci.yml）：

```yaml
name: watch
on:
  schedule:
    - cron: "0 * * * *"      # 每小时；tick 内部门控只在本地 9 点真跑
  workflow_dispatch: {}        # 手动触发，便于测试
concurrency:
  group: watch
  cancel-in-progress: false
permissions:
  contents: write              # 允许把更新后的 db 提交回仓库
jobs:
  tick:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e .
      - run: dealscout tick
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
          if ! git diff --quiet -- data/dealscout.db; then
            git config user.name "github-actions[bot]"
            git config user.email "github-actions[bot]@users.noreply.github.com"
            git add data/dealscout.db
            git commit -m "chore: update price history [skip ci]"
            git push
          else
            echo "no db changes"
          fi
```

**密钥**：`ITAD_API_KEY`/`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` 存 GitHub Actions Secrets（用户在仓库 Settings → Secrets 手动添加，属用户侧动作）。非敏感项（TZ、RUN_HOUR、DISPLAY_CURRENCY、DB 路径）明文写在 workflow env。

**数据库持久化**：
- 把 db 移到受版本管理路径 `data/dealscout.db`。改 `.gitignore`：保留 `*.db` 忽略，但加放行例外 `!data/dealscout.db`（db 无密钥，公开无妨）。
- 门控保证只有本地 9 点那次 `tick` 真跑、真改 db；工作流据 `git diff --quiet` 判断有变化才提交，commit message 带 `[skip ci]` 不触发 ci.yml。其余 23 小时空跑、db 不变、无 commit，不刷屏历史。
- **单一数据源模型**：本地 `.env` 也把 `DEALSCOUT_DB` 指向 `data/dealscout.db`。用户本地 `dealscout add` 后 `git add data/dealscout.db && commit && push`；云端每天更新后自动 commit 回来；用户下次本地改动前先 `git pull`。单人工具，轻量 git 同步。

**上线收尾（用户侧动作，写入 README/交付清单）**：
1. 在 GitHub 仓库添加三个 Secrets。
2. 把现有 watch 迁到 `data/dealscout.db` 并提交（或重新 `dealscout add`）。
3. `workflow_dispatch` 手动触发一次验证云端跑通、收到 Telegram、db 被提交回来。
4. 删除本地 Windows 计划任务 `DealScout`，避免两边重复通知、db 分叉。

## 4. 文件影响一览

| 文件 | 动作 | 责任 |
|------|------|------|
| `dealscout/fx.py` | 新增 | 汇率换算 |
| `dealscout/schedule.py` | 新增 | 时区门控纯函数 |
| `dealscout/config.py` | 改 | 加 display_currency / tz / run_hour |
| `dealscout/notify.py` | 改 | `format_deal` 加 display 参数 |
| `dealscout/runner.py` | 改 | run_once 可选 fx 换算 |
| `dealscout/cli.py` | 改 | 抽 `_execute_run`，加 `tick` 命令 |
| `pyproject.toml` | 改 | 加 tzdata 依赖 |
| `.gitignore` | 改 | 放行 data/dealscout.db |
| `.github/workflows/watch.yml` | 新增 | 云端每小时 tick + db 提交 |
| `.env.example` / `README.md` | 改 | 新增配置项与云端说明 |
| 对应 `tests/test_*.py` | 新增/改 | 覆盖上述 |

## 5. 风险与对策

| 风险 | 对策 |
|------|------|
| Frankfurter 宕机/无该货币 | 捕获 FxError，通知照发只是少 ≈ 行；base_url 可注入便于换源 |
| 云端 db 与本地分叉 | 单一数据源 data/dealscout.db + pull-before-edit 约定；单人可控 |
| 每小时空跑浪费额度 | 公开仓库 Actions 免费无限分钟；门控前置，空跑仅查时钟秒级退出 |
| 忘删本地任务导致双通知 | 上线清单明确要求删除本地 Windows 任务 |
| zoneinfo 在 Windows 无时区库 | 依赖 tzdata |

## 6. 测试策略

- 沿用 M1 约定：Pydantic 模型边界、全离线测试（HTTP 用 MockTransport）、每模块独立单测。
- FX、schedule 为纯逻辑/可注入，重点单测；tick 与 run 复用 `_execute_run`，测 tick 的门控分支。
- 工作流无法单测，靠 `workflow_dispatch` 手动验证（用户侧）。
- 全量 pytest + ruff 进 CI，维持绿。
