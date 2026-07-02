# DealScout 设计文档 —— GitHub 求职作品集旗舰项目

日期：2026-07-02
状态：已与项目所有者确认设计，待审阅书面稿

## 1. 背景与目标

项目所有者是刚毕业的 CS (AI) 学生，目标岗位为 **AI 应用 / LLM 应用开发**，目前 GitHub 为空，可投入 **3 个月以上**系统建设。

作品集总体策略采用「**一个旗舰 + 若干配角**」：

- **旗舰**：DealScout（本文档主体）——一个生产级深度的个人盯价 Agent。
- **配角**：整理发布已有的旧项目；从旗舰中孵化出独立小库与 MCP server。
- **补充**：使用过程中给依赖的开源库顺手提 issue/PR。

选题依据：项目所有者会每天真实使用该工具（盯 Shopee/Lazada 数码产品与游戏/软件折扣），保证需求真实、迭代反馈快、面试叙事有血有肉。

## 2. 产品概念

**一句话定位**：传统比价网站只会记录价格；DealScout 用 LLM 理解用户需求、读懂商品页面、结合历史价格判断折扣真假，然后带着理由主动汇报。

### 核心功能（按面试亮点排序）

1. **自然语言下达监控任务**：用户用自然语言描述需求（商品、平台、价格/折扣触发条件），Agent 解析为结构化 WatchRule。
2. **LLM 驱动的页面理解**（技术差异化核心）：Shopee/Lazada 无公开 API，传统 CSS 选择器爬虫脆弱。DealScout 用 Playwright 渲染页面后由 LLM 提取价格/库存/规格，输出经 Pydantic schema 校验，对页面改版与多规格变体天然鲁棒。
3. **折扣真假判断**：价格历史入库；触发时 Agent 分析是真降价还是「先涨后降」、跨平台比价，生成带推理过程的通知。
4. **Telegram 通知 + 人工确认**：即时警报 + 每日摘要；一切「行动」类操作走 human-in-the-loop 确认。
5. **游戏/软件优惠第二数据源**：经 IsThereAnyDeal/Steam 干净 API 接入，验证多源可插拔架构。

### 明确不做（YAGNI）

自动下单、多用户 SaaS、手机 App、覆盖所有电商平台。定位为做深的个人工具。

## 3. 技术架构

**技术栈**：Python 3.12、Playwright、Pydantic、SQLite、Claude API（以 Haiku 为主控制成本）、Telegram Bot API、Typer CLI。**不使用 LangChain 等重框架**——自行实现 Agent 循环，作为「懂原理」的面试信号。

### 模块划分（单一职责、可独立测试）

```
dealscout/
├── watch/      # 监控任务：NL 请求 → 结构化 WatchRule（商品、条件、平台）
├── sources/    # 数据源适配器（核心抽象层）
│   ├── base.py         # SourceAdapter 接口：search(query) / fetch(url) → RawListing
│   ├── itad.py         # 游戏：IsThereAnyDeal API（干净数据，先跑通）
│   ├── shopee.py       # Playwright 渲染 → 交给 extract 层
│   └── lazada.py
├── extract/    # LLM 页面理解：渲染后页面内容 → Pydantic 校验的 Listing
├── store/      # SQLite：价格历史、监控任务、运行日志
├── judge/      # 折扣判断：规则触发 + LLM 分析 → 带推理的结论
├── notify/     # Telegram：即时警报 + 每日摘要
├── runner/     # 调度循环：定时执行、失败重试、限速、成本预算
└── cli.py      # dealscout add/list/run/report
```

**数据流**：`WatchRule → runner 定时触发 → sources 抓原始页面 → extract 提取结构化数据 → store 存历史 → judge 评估 → notify 推送`。各环节输入输出均为明确的 Pydantic 模型，逐环可独立测试。

### 工程亮点层（面试区分度核心）

1. **Eval 体系**：真实页面快照 golden set 评测提取准确率；judge 判断质量建小型标注集评测；全部进 CI，README 公布准确率数字与回归流程。
2. **成本与可观测性**：每次运行记录 token 消耗与 API 成本，CLI 可查成本报表。
3. **失败韧性**：反爬拦截、页面超时、LLM 输出不合 schema 时的重试/降级/告警策略，运行日志可回溯。

### 反爬预案

带登录态的 Playwright 本地运行、低频（每天数次）、随机间隔，属个人使用范畴。若某平台无法稳定抓取，降级为「手动粘贴页面内容/分享链接 → Agent 照常提取分析」，项目核心价值不受影响。

## 4. 三个月里程碑

| 阶段 | 时间 | 内容 | 完成标志 |
|------|------|------|----------|
| M0 | 第 1-2 周 | 整理发布旧项目（README/架构图/license）；完善 GitHub 个人资料；建 DealScout 仓库骨架 | GitHub 不再空白 |
| M1 | 第 3-4 周 | 最小闭环：ITAD API → SQLite → 规则触发 → Telegram（不碰 LLM 与爬虫） | 自己每天收到通知 |
| M2 | 第 5-8 周 | NL→WatchRule；Playwright + LLM 提取 Shopee/Lazada；judge 模块 | 盯住第一个真实想买的商品 |
| M3 | 第 9-12 周 | Eval 进 CI、成本报表、README 打磨（demo GIF、架构图、设计决策）；孵化配角项目 | 可写进简历投递 |

**配角项目孵化**：将 extract 层抽为独立库 `llm-page-extract`（任意 HTML + schema → 校验过的结构化数据）单独发布；有余力再加 MCP server 封装价格库查询。

**贯穿要求**：每周 3-4 天以上真实 commit；顺手给依赖库提 issue/PR。

## 5. 风险与对策

| 风险 | 对策 |
|------|------|
| 平台反爬升级 | 登录态低频抓取 → 手动粘贴模式降级，价值不塌 |
| LLM 成本失控 | Haiku 为主 + 缓存 + 成本追踪，预期每月数美元内 |
| 中途弃坑 | M1 后自己即日活用户，真实需求驱动 |

## 6. 测试策略

- 每个模块独立单元测试（Pydantic 模型边界清晰）。
- extract 层用离线页面快照测试，不依赖网络。
- Eval 体系（M3）作为回归防线进 CI。
- 端到端：以「自己每天真实使用」为最终验收。
