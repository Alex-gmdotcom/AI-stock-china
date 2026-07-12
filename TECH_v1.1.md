# TECH.md — AI Hedge Fund 中国版 v1 技术规格

> **SDD 第 ② 步**：技术规格 / 施工图纸
> **版本**：v1.1（对账修订固化版，对齐 PRODUCT.md v1.1，2026-07-02）
> **核心变化 vs v1.0**：数据层整体 baostock 化（DataSourceRouter + Tushare + pytdx，附录 A）；D4 v2（WACC 经 MarketConfig 路由，§8.1 重写）；§10.3 修正为配对迁移 API（对齐已验证实现）；新增 §6.4 agent 花名册与注册表（D8）、研究回路 `src/eval/`（D5）、坑 17-22、反向测试 R8/R9；不变量 35→41
> **基础**：现有实现的 markets / tools / agents / strategy.three_categories / eval 保留
> **配套**：openclaw_hk_news_prompt.md；**附录 A** = data_layer_architecture.md（数据层规格，编号 DL-N，防与本文 Phase 1-5 撞号）；**附录 B** = 修②设计草案（ADR-07：维度加权聚合已否决未 ship，AGENT 注册表登记制保留）

---

## 1. 整体架构

### 1.1 进程模型

```
┌─────────────────────────────────────────────────────────┐
│  Web 控制台 (web_app.py, FastAPI, localhost:8000)        │
│  4 个页面 + 1 个全局 footer                                │
│  A: 早晚报粘贴入口                                          │
│  B: 个股深度分析（核心）                                     │
│  C: 三分法池总览                                            │
│  D: 历史报告浏览                                            │
└──────────────────┬──────────────────────────────────────┘
                   │ HTTP
                   ▼
┌─────────────────────────────────────────────────────────┐
│ 启动前置（必须按顺序，违反则 fail-fast）                       │
│ 1. inject_no_proxy()                                     │
│ 2. import tools.api_bridge                               │
│ 3. boot_print_versions()                                 │
│ 4. load_pool_state()                                     │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│ 数据层（v1.1，规格详见附录 A，编号 DL-N）                     │
│ ├─ DataSourceRouter（字段组路由+熔断+健康）tools/datasource/ │
│ ├─ baostock 主源（PRICE/VALUATION/RATIO_FIN，反推链受保护）  │
│ ├─ Tushare（ABS_CASHFLOW/ABS_BALANCE 真值，_ttm_from_ytd）  │
│ ├─ pytdx / 腾讯·新浪（PRICE/VALUATION 机制冗余）             │
│ ├─ markets/market_config.py（资本成本市场路由，I10.2）       │
│ ├─ API Bridge monkey-patch (tools/api_bridge.py)         │
│ └─ openclaw 港股新闻消费 (hk_news/ingest.py)              │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ 分析层                                                     │
│ ├─ Agents（9-agent 推荐集，花名册见 §6.4）                  │
│ ├─ DCF (analysis/dcf.py)                                 │
│ ├─ 财务舞弊 (analysis/fraud_detector.py)                  │
│ ├─ 解禁雷达 (analysis/unlock_radar.py)                    │
│ ├─ 同业对比 (analysis/peer_compare.py)                    │
│ └─ 标的抽取 (analysis/ticker_extractor.py)                │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ LLM 层 (llm_text.py)                                     │
│ 仅服务三种调用：                                            │
│ ├─ 标的抽取 (briefings_archive/ingest.py)                 │
│ ├─ 多 agent 决议 (agents/*)                               │
│ └─ 舞弊检测 (analysis/fraud_detector.py)                  │
│ ★ 不再有"早晚报生成"路径                                    │
└─────────────────────────────────────────────────────────┘
```

### 1.2 三种主要数据流

**数据流 A：早晚报粘贴 → 标的抽取 → 跳深度页**

```
用户粘贴文本 (markdown / 纯文本)
   │
   ▼
[Step 1] briefings_archive/ingest.py
   ├─ 按日期 + 类型识别（用户在 UI 选）
   ├─ 写入 briefings_archive/{date}-{type}.md
   ├─ 重名时滚动 .bak.N
   └─ 追加到 _all_briefings.md
   │
[Step 2] analysis/ticker_extractor.py（LLM 调用）
   ├─ 输入：报告全文
   ├─ Prompt：抽取 ticker + 角色（重点/风险/路过）
   ├─ 输出：JSON 列表
   └─ 失败 → fail-loud + 降级到"手动多选池里的票"
   │
[Step 3] UI 展示卡片列表 + "深度看看"按钮
```

**数据流 B：个股深度分析（最重要，10 个数据维度并行采集）**

```
Ticker 输入 (URL 参数 / 池跳转 / 早报标的)
   │
   ▼
[Step 0] ticker_resolve → MarketInfo
   │
[Step 1] 并行采集 10 个数据维度 (asyncio.gather)
   ├─ K线 + MA            ← AKShare stock_zh_a_hist (A) / stock_hk_hist (HK)
   ├─ 估值 8 卡            ← AKShare stock_financial_em + stock_a_lg_indicator
   ├─ 同业对比             ← AKShare stock_board_industry_cons_em
   ├─ 同业指数叠加         ← AKShare stock_zh_a_hist + 行业指数 hist
   ├─ 资金面 sparkline     ← AKShare stock_hsgt_hold_stock_em + margin + main flow
   ├─ 限售解禁雷达         ← AKShare stock_restricted_release_queue_em
   ├─ 公告 / 新闻 / 研报   ← AKShare (A) / hk_news cache (HK)
   ├─ 8 agent 决议         ← LLM 并行调用 (含原版 + 中国特色)
   ├─ DCF 估值             ← analysis/dcf.py（纯计算，含默认假设）
   └─ 舞弊检测             ← analysis/fraud_detector.py (LLM 调用)
   │
[Step 2] 渲染页面 (server-side template + 前端 JS 交互)
[Step 3] DCF / K 线时间维度切换走前端 JS，不重刷
```

**数据流 C：港股 openclaw 新闻消费**

```
用户在深度页点"导入 openclaw 新闻"按钮
   │
   ▼
弹出粘贴框，用户粘贴 JSON
   │
[Step 1] hk_news/schema.py 校验
   ├─ JSON parse 失败 → 红底报错（指明 line/col）
   ├─ schema_version 不识别 → 提示 Alex
   ├─ 必填字段缺失（ticker / snapshot_at）→ 整体拒绝
   └─ 选填字段缺失 → 降级 + footer 标注"数据不完整"
   │
[Step 2] hk_news/ingest.py 持久化
   └─ ~/.ai-hedge-fund/hk_news/{ticker}_{YYYYMMDD}.json
   │
[Step 3] 触发 china_public_opinion agent 重新决议
   └─ 用真实新闻数据 + 风险事件 + 同业事件
   │
[Step 4] 页面动态刷新：公告时间线 + 舆情 agent 信号 + 风险红黄灯
```

---

## 2. 文件结构（精简后）

```
ai-hedge-fund/                          # 原版仓库根（零修改）
├── src/
│   ├── ...                             # 原版结构保留
│   │
│   ├── markets/                        # 市场抽象层
│   │   ├── ticker.py                   # Ticker 解析 + market detection
│   │   ├── config.py                   # 交易规则配置
│   │   ├── market_config.py            # ★ 资本成本市场路由（D4 v2 / I10.2）
│   │   └── proxy.py                    # NO_PROXY 注入
│   │
│   ├── data/
│   │   └── models_china.py             # 数据模型（14 个）
│   │
│   ├── tools/
│   │   ├── api_china.py                # 数据接口薄壳（内部经 Router）
│   │   ├── api_factory.py
│   │   ├── api_bridge.py               # monkey-patch（+datasource_v1/mootdx_v3 override）
│   │   ├── data_fallback.py            # fallback chain（链尾兜底）
│   │   ├── baostock_data.py            # ★ DL-0 A股主源（平衡表反推链——受保护禁改）
│   │   ├── mootdx_data.py              # ★ DL-3 pytdx 直连（needs_cn_ip）
│   │   ├── line_items_china.py         # ★ 含 _ttm_from_ytd（修①，I10.1）
│   │   └── datasource/                 # ★ DL-1 路由层
│   │       ├── base.py                 # Provider 抽象 + 能力矩阵
│   │       ├── breaker.py              # 熔断器
│   │       ├── health.py               # 健康上报（飞书注入点）
│   │       └── router.py               # 字段组路由 + 失效转移
│   │
│   ├── agents/                         # 花名册见 §6.4（9-agent 推荐集，D8）
│   │   ├── china_public_opinion.py     # 消费 openclaw 输入或新闻源（I3.3）
│   │   ├── china_policy.py / china_capital_flow.py / china_sector_rotation.py
│   │   ├── valuation / fundamentals / warren_buffett / technicals / nassim_taleb（上游沿用）
│   │   └── portfolio_manager / risk_manager（上游沿用；聚合层现状与 ADR 见附录 B）
│   │
│   ├── analysis/                       # ★ 新增分析模块
│   │   ├── dcf.py                      # 粗 DCF + 假设可调
│   │   ├── fraud_detector.py           # 财务舞弊检测 agent
│   │   ├── unlock_radar.py             # 限售解禁雷达
│   │   ├── peer_compare.py             # 同业对比 + 指数叠加
│   │   └── ticker_extractor.py         # 早晚报标的抽取
│   │
│   ├── briefings_archive/              # ★ 新增：早晚报粘贴消费
│   │   ├── ingest.py
│   │   ├── parser.py                   # openclaw 早晚报格式解析
│   │   └── search.py                   # 全文搜索
│   │
│   ├── hk_news/                        # ★ 新增：港股新闻消费
│   │   ├── schema.py                   # openclaw JSON schema v1.0
│   │   └── ingest.py
│   │
│   ├── eval/                           # ★ 研究回路（D5 / I10.4-10.5）
│   │   ├── metrics.py                  # RankIC/ICIR/命中率（33 项确定性验证）
│   │   ├── fees.py                     # A股/港股费用模型
│   │   ├── signals.py                  # 批跑 log 直读解析（PORTFOLIO SUMMARY）
│   │   ├── data.py                     # baostock 取价/日历/沪深300（可 mock）
│   │   └── harness.py                  # 编排 + CLI（多截面累积）
│   │
│   ├── strategy/
│   │   └── three_categories.py         # 三分法池（人工决策版）
│   │
│   ├── llm_text.py                     # 统一 LLM 入口
│   ├── boot.py                         # 启动前置 + 版本横幅
│   ├── main_china.py                   # CLI 入口
│   └── web_app.py                      # FastAPI Web 控制台
│   
│   ★ 已删除：strategy/briefing_generator.py（1500 行）
│
├── .env                                # API Keys
├── pyproject.toml                      # numpy >=1.26,<3.0
└── ~/.ai-hedge-fund/                   # 运行时状态
    ├── three_categories.json           # 三分法池状态
    ├── three_categories.json.bak.{YYYY-MM-DD}
    ├── briefings_archive/              # ★ 早晚报存档（粘贴的）
    │   ├── 2026-06-13-morning.md
    │   ├── 2026-06-13-evening.md
    │   ├── _all_briefings.md
    │   └── extracted_tickers/
    │       └── 2026-06-13-morning.json   # LLM 抽取结果缓存
    ├── hk_news/                        # ★ 港股新闻 JSON 归档
    │   ├── 00700.HK_20260613.json
    │   └── 09880.HK_20260612.json
    └── logs/
        └── {YYYY-MM-DD}.log
```

---

## 3. 数据模型

### 3.1 Ticker / MarketInfo（同 v0.1）

```python
class Market(Enum):
    CN_SH_MAIN = "sh_main"      # 60xxxx ±10%
    CN_SZ_MAIN = "sz_main"      # 000xxx/001xxx ±10%
    CN_GEM     = "gem"          # 300xxx ±20%
    CN_STAR    = "star"         # 688xxx ±20%
    CN_BSE     = "bse"          # 8xxxxx ±30%
    HK         = "hk"
    UNKNOWN    = "unknown"

@dataclass
class MarketInfo:
    ticker: str
    raw_input: str
    market: Market
    daily_limit_up: float
    daily_limit_down: float
    settlement: str
    lot_size: int
```

### 3.2 StockSnapshot（深度页核心，新增）

```python
@dataclass
class StockSnapshot:
    ticker: str
    market_info: MarketInfo
    captured_at: datetime
    
    # K 线 + 技术
    kline_daily: list[Candle]    # 近 250 个交易日
    ma_periods: dict[int, list[float]]  # {5: [...], 10: [...], 20: [...], 60: [...]}
    volume: list[int]
    
    # 估值 + 财务
    pe_ttm: float | None
    pb: float | None
    roe: float | None
    dividend_yield: float | None
    revenue_yoy: float | None
    net_profit_yoy: float | None
    debt_ratio: float | None
    institutional_holding: float | None
    pe_5y_percentile: float | None
    industry_median_pe: float | None
    
    # 同业
    industry_sw_l2: str          # 申万二级行业名
    peers: list[PeerRow]         # 同业对比表行
    industry_index_hist: list[float]   # 行业指数 K 线（叠加图用）
    
    # 资金面
    northbound_holding_history: list[tuple[date, float]]
    margin_balance_history: list[tuple[date, float]]
    main_capital_flow_history: list[tuple[date, float]]
    
    # 限售解禁
    unlock_events: list[UnlockEvent]   # 未来 12 月所有解禁
    historical_unlock_perf: list[float]   # 历史解禁后 30 日相对涨跌
    
    # 公告新闻研报
    announcements: list[Announcement]   # A 股：AKShare；港股：openclaw 缓存
    news: list[NewsItem]
    analyst_reports: list[AnalystReport]
    
    # Agent 决议
    agent_signals: dict[str, AgentSignal]
    consensus: dict           # {bullish_pct, neutral_pct, bearish_pct}
    
    # DCF
    dcf_result: DCFResult     # 含 assumptions + intrinsic_value
    
    # 舞弊检测
    fraud_check: FraudCheckResult
    
    # 元信息
    data_gaps: list[str]
    fallback_chain_used: dict[str, str]
    llm_calls: list[LLMCallLog]
```

### 3.3 BriefingArchive

```python
@dataclass
class BriefingArchive:
    date: str                    # "2026-06-13"
    type: Literal["morning", "evening", "weekly"]
    source: Literal["openclaw_v3", "manual", "other"]
    
    raw_text: str
    archived_at: datetime
    archive_path: str            # briefings_archive/2026-06-13-morning.md
    
    extracted_tickers: list[ExtractedTicker]   # LLM 抽取结果
    extraction_method: Literal["llm", "manual_fallback"]
    extraction_failed: bool      # 若 True 表示 LLM 失败，下面 list 是手动填的

@dataclass
class ExtractedTicker:
    ticker: str
    name: str
    role: Literal["focus", "risk", "passing"]   # 重点 / 风险 / 路过
    raw_mention: str             # 早报中原文片段
```

### 3.4 HKNewsBundle（openclaw 输入对应）

```python
@dataclass
class HKNewsBundle:
    schema_version: str
    ticker: str
    company_name_zh: str
    company_name_en: str | None
    market: str
    snapshot_at: datetime
    data_window_days: int
    
    news: list[NewsItem]
    announcements: list[Announcement]
    analyst_reports: list[AnalystReport]
    sentiment_signals: SentimentSignals
    risk_events: list[RiskEvent]
    peer_events: list[PeerEvent]
    data_gaps: list[str]
    
    ingested_at: datetime
    source_path: str             # hk_news/{ticker}_{YYYYMMDD}.json
```

### 3.5 DCFAssumptions（用户可调）

```python
@dataclass
class DCFAssumptions:
    perpetual_growth_rate: float         # 永续 g，默认看行业（消费 3% / 科技 4% / 周期 2%）
    wacc: float                           # 折现率，默认看行业
    five_year_growth_rate: float          # 5 年增速假设
    fcf_base: float                       # 基期自由现金流（自动从财报取）
    
@dataclass
class DCFResult:
    assumptions: DCFAssumptions
    intrinsic_value_per_share: float
    intrinsic_value_low: float           # 假设 -1σ
    intrinsic_value_high: float          # 假设 +1σ
    current_price: float
    margin_of_safety_pct: float          # (intrinsic - current) / intrinsic
    confidence_note: str                 # "假设敏感性高，参考用"
```

### 3.6 FraudCheckResult

```python
@dataclass
class FraudCheckResult:
    level: Literal["healthy", "watch", "alert"]
    findings: list[FraudFinding]         # 警示档必须非空
    summary: str
    checked_at: datetime

@dataclass
class FraudFinding:
    indicator: str                       # "应收账款增速 vs 营收"
    observed: str                        # "应收增速 45% > 营收增速 12%"
    threshold: str                       # ">营收增速 20pp 视为警示"
    severity: Literal["info", "watch", "alert"]
```

### 3.7 PoolState（三分法，简化版）

对比 v0.1：删除 `migrations_this_month` 字段中的 `decided_by: "weekly_review"` 约束（因为不再有周复盘自动确认）；改为：

```python
@dataclass
class MigrationRecord:
    record_id: str
    pair_id: str                 # ★ v1.1：配对迁移共享 ID（月配额按 pair 去重，I4.2）
    ticker: str
    from_category: Literal["V", "T", "N", "OUT"]   # OUT = 池外（出池/入池外部腿）
    to_category: Literal["V", "T", "N", "OUT"]
    signal: str                  # 系统检测到的信号代码
    evidence: list[dict]         # 用户提交时必须非空
    user_rationale: str          # 人工补充的判断理由
    decided_at: datetime
```

> **v1.1 修正（对账硬伤 2）**：迁移 API 为配对原子 `execute_migration_pair`（见 §10.3）；v1.0 的单腿 API 在 Phase 1 沙箱 T6 已被证明违反 I4.1。

---

## 4. 网络层与代理（markets/proxy.py，同 v0.1）

参考 v0.1 §4。关键点：

- `inject_no_proxy()` 必须是进程第一行调用
- 白名单含东方财富全子域 + 腾讯财经 + 新浪财经 + AKShare 域
- 不依赖 `requests.Session(trust_env=True)`，因 TUN 模式不走环境变量

---

## 5. API Bridge（tools/api_bridge.py，同 v0.1）

参考 v0.1 §5。关键点：

- monkey-patch 5 个原版数据函数：`get_prices` / `get_financial_metrics` / `search_line_items` / `get_insider_trades` / `get_company_news`
- CN/HK 路由到 `api_china`，美股保留原版
- 必须在任何 agent import 前 import 本模块
- **v1.1 扩展**：api_china 内部经 DataSourceRouter（`datasource_v1` override）；PRICE 链含 pytdx（`mootdx_v3` override）；所有 override 以幂等 marker 部署（重复部署安全）

---

## 6. LLM 调用层（llm_text.py，大幅简化）

### 6.1 三种调用类型

```python
__version__ = "v1.0.0"

class LLMCallType(Enum):
    TICKER_EXTRACTION = "ticker_extraction"   # 标的抽取
    AGENT_DECISION = "agent_decision"          # 8 agent 决议
    FRAUD_CHECK = "fraud_check"                # 舞弊检测

def llm_text(
    call_type: LLMCallType,
    provider: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    temperature: float = 0.3,
    pydantic_model: type | None = None,   # 可选，用于结构化输出
) -> tuple[str | object, dict]:
    """
    统一 LLM 调用入口。
    返回 (text_or_parsed, metadata)。
    
    关键：失败必须抛异常（fail-loud），调用方捕获并显式报错给用户。
    禁止任何静默回退。
    """
    # 1. provider resolution + DeepSeek thinking disabled
    # 2. token 上限保护（I6.3）
    # 3. 调用 + 重试 1 次
    # 4. 解析（含 pydantic_model 校验）
    # 5. 日志 + 控制台打印（I8.1）
    # 6. 失败抛 LLM*Error 子类
```

### 6.2 三种调用的实现要点

| 调用类型 | Prompt 长度估算 | Provider 偏好 | 失败处理 |
|---------|--------------|------------|---------|
| 标的抽取 | ~4-8K tokens（早报全文 + 抽取指令） | DeepSeek（快+便宜） | fail-loud → 降级"手动多选" |
| Agent 决议 | ~2-4K tokens（个股快照 + 视角指令） | DeepSeek 默认 / Claude 可选 | fail-loud → agent 信号显示"调用失败" |
| 舞弊检测 | ~3-6K tokens（5 年财报数据 + 异常指标指令） | Claude 优先（推理质量） | fail-loud → 卡片显示"检测失败" |

### 6.3 Provider 配置（同 v0.1）

参考 v0.1 §6.2。6 家 provider，DeepSeek 默认 + .env 有 Claude 自动推荐。

**模型名注记（v1.1）**：`deepseek-chat` / `deepseek-reasoner` 于 2026-07-24 退役；canonical = `deepseek-v4-flash`（批跑自动选择已确认）；JSON 任务强制 `"thinking": {"type": "disabled"}`。

### 6.4 Agent 花名册与注册表（D8，v1.1 新增）

**推荐集（A 股，9 agent）**：

| agent | 维度 | 角色 |
|---|---|---|
| valuation_analyst | V | 估值（DCF 资本成本经 MarketConfig，D4 v2） |
| fundamentals_analyst | V | 基本面 |
| warren_buffett | V | 质量 / 护城河 |
| technical_analyst | T | 技术 / 趋势 |
| china_capital_flow | T | 资金 / 北向（I10.3 重点对象） |
| china_sector_rotation | T | 板块动量 |
| china_public_opinion | N | 舆情（港股必须消费 openclaw JSON，I3.3） |
| china_policy | N | 政策 |
| nassim_taleb | 风险叠加 | 尾部风险（不属方向维度） |

**港股集**：peter_lynch 替换部分 V 类视角。

**注册表约束**：新增 agent 必须在 `AGENT_REGISTRY` 登记维度（V/T/N/风险叠加）与关键数据依赖；未登记不得进入决议图。聚合层维度加权（附录 B）已否决未 ship，登记制独立保留。

---

## 7. 4 个页面的实现（web_app.py）

### 7.1 路由总览

```
GET  /                                  主页（4 个入口卡片）

# 入口 A：早晚报粘贴
GET  /briefing/paste                    粘贴页
POST /briefing/paste                    body: {type, date, raw_text}
GET  /briefing/{date}/{type}/tickers    抽取结果（卡片列表）

# 入口 B：个股深度分析
GET  /stock/{ticker}                    深度页（HTML）
GET  /stock/{ticker}/snapshot           JSON 快照（前端 fetch 用）
POST /stock/{ticker}/hk-news/import     港股新闻 JSON 粘贴
POST /stock/{ticker}/dcf                DCF 假设调整后重算

# 入口 C：三分法池
GET  /pool                              池总览页
POST /pool/migrate                      迁移操作（body: {ticker, from, to, rationale}）
POST /pool/add                          手动加池
POST /pool/remove                       手动减池

# 入口 D：历史报告
GET  /history                           历史浏览页
GET  /history/search?q=...              全文搜索
GET  /history/{date}/{type}             单份报告 + 标的卡片

# 基础设施
GET  /llm/providers                     可用 provider 列表（基于 .env）
GET  /healthz                           启动横幅 + 版本信息（便于调试 Ghost Version）
```

### 7.2 启动前置（必须按顺序）

```python
# web_app.py 第 1 行起：

from markets.proxy import inject_no_proxy
inject_no_proxy()

import tools.api_bridge   # 触发 monkey-patch

from boot import boot_print_versions
boot_print_versions()

from strategy.three_categories import load_pool_state
_POOL_STATE = load_pool_state()

# 然后才能 import 其他业务模块
from fastapi import FastAPI
from analysis import dcf, fraud_detector, unlock_radar, peer_compare, ticker_extractor
from briefings_archive import ingest as briefing_ingest
from hk_news import ingest as hk_news_ingest
...
```

### 7.3 页面 B 深度页的并行采集

```python
async def build_stock_snapshot(ticker: str) -> StockSnapshot:
    market_info = ticker_resolve(ticker)
    
    # 10 个数据维度并行
    tasks = {
        "kline":      asyncio.create_task(_fetch_kline(market_info)),
        "valuation":  asyncio.create_task(_fetch_valuation(market_info)),
        "peers":      asyncio.create_task(peer_compare.fetch_peers(market_info)),
        "capital":    asyncio.create_task(_fetch_capital_flow(market_info)),
        "unlock":     asyncio.create_task(unlock_radar.fetch(market_info)),
        "news":       asyncio.create_task(_fetch_news_or_cache(market_info)),
        "agents":     asyncio.create_task(_run_all_agents(market_info)),
        "dcf":        asyncio.create_task(dcf.compute(market_info)),
        "fraud":      asyncio.create_task(fraud_detector.check(market_info)),
        "industry_index": asyncio.create_task(peer_compare.industry_index(market_info)),
    }
    
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    
    # 单维度失败标【数据缺口】，不中断整页（I6.2）
    # 但 ≥50% 失败暂停整页
    failures = sum(1 for r in results if isinstance(r, Exception))
    if failures / len(tasks) >= 0.5:
        raise MajorPageDataFailure(...)
    
    return _assemble_snapshot(market_info, dict(zip(tasks.keys(), results)))
```

---

## 8. 新增分析模块

> **v1.1 数据获取总则**：本节各模块数据一律经 DataSourceRouter（附录 A）获取；文中 AKShare 接口名保留为字段语义参考，实际链路以能力矩阵路由为准。

### 8.1 DCF 估值（analysis/dcf.py）— D4 v2

> **v1.1 核心修正（对账硬伤 1）**：v1.0 的 31 行业绝对 WACC 表（8%-13%）与 MarketConfig 实测 CN 资本成本（WACC≈7.3%）冲突——若照 v1.0 建 dcf.py，将在新模块里重新引入"valuation 系统性偏空"（利率 patch 已修复的同类 bug），并与 agents 侧形成两套并行 DCF 真相源。v1.1 起：**基准 WACC 一律路由自 MarketConfig，行业只提供风险溢价调整（I10.2）**。

```python
from markets.market_config import MarketConfig

# 行业调整表（D4 v2）：(风险溢价调整 pp, 永续 g, 5 年增速)
# 换算规则（机械，零判断）：调整 pp = v1.0 原表 WACC − 10%（原表隐含基线）；g / 5y_g 原值保留
INDUSTRY_ADJUSTMENTS = {
    # === 大消费（稳健消费 / 高质量） ===
    "食品饮料": (+0.000, 0.030, 0.12),
    "家用电器": (+0.000, 0.030, 0.10),
    "纺织服饰": (+0.010, 0.025, 0.07),
    "商贸零售": (+0.010, 0.025, 0.07),
    "美容护理": (+0.000, 0.035, 0.15),
    "社会服务": (+0.010, 0.035, 0.15),
    "农林牧渔": (+0.010, 0.025, 0.08),
    # === 科技 / 高成长 ===
    "电子":     (+0.010, 0.040, 0.25),
    "计算机":   (+0.020, 0.040, 0.25),
    "通信":     (+0.000, 0.030, 0.12),
    "传媒":     (+0.020, 0.035, 0.15),
    "医药生物": (+0.000, 0.040, 0.18),
    # === 新能源 / 政策驱动 ===
    "电力设备": (+0.010, 0.040, 0.22),
    "汽车":     (+0.010, 0.035, 0.18),
    "国防军工": (+0.000, 0.035, 0.18),
    "环保":     (+0.010, 0.030, 0.12),
    "机械设备": (+0.010, 0.030, 0.12),
    # === 周期 / 大宗 ===
    "钢铁":     (+0.020, 0.020, 0.05),
    "有色金属": (+0.020, 0.025, 0.08),
    "基础化工": (+0.010, 0.025, 0.07),
    "煤炭":     (+0.020, 0.015, 0.03),
    "石油石化": (+0.010, 0.020, 0.05),
    "轻工制造": (+0.010, 0.025, 0.08),
    "建筑材料": (+0.020, 0.020, 0.05),
    "建筑装饰": (+0.020, 0.020, 0.05),
    # === 公用 / 金融（低风险低增长） ===
    "公用事业": (-0.020, 0.020, 0.05),
    "交通运输": (-0.010, 0.025, 0.06),
    "银行":     (-0.020, 0.020, 0.05),
    "非银金融": (+0.000, 0.025, 0.08),
    # === 房地产 / 综合 ===
    "房地产":   (+0.030, 0.015, 0.03),
    "综合":     (+0.010, 0.025, 0.08),
}
# 共 31 行，覆盖申万一级全部。港股按主业映射申万一级（东财映射）；判定不到回退 "综合"。

def wacc_for(market_info: MarketInfo, industry: str) -> float:
    base = MarketConfig.for_market(market_info.market).wacc   # CN 批跑实测 ≈7.3%；HK/US 各自路由
    adj = INDUSTRY_ADJUSTMENTS.get(industry, INDUSTRY_ADJUSTMENTS["综合"])[0]
    return base + adj

def compute(market_info: MarketInfo, assumptions: DCFAssumptions | None = None) -> DCFResult:
    """
    两段式 DCF：
    - Stage 1: 未来 5 年 FCF 按 five_year_growth_rate 增长
    - Stage 2: 永续按 perpetual_growth_rate
    - 折现率：wacc_for(market, industry)（禁止绕过 MarketConfig，I10.2）

    敏感性分析：对 wacc / perpetual_g 各 ±1pp 跑 4 组，输出 low / high 区间。
    基期 FCF：经营现金流 − capex，经 DataSourceRouter 取 Tushare 真值（DL-2），TTM 口径（I10.1）。
    """
    ...
```

**高 capex 成长股护栏（安集教训）**：当 capex/营收 > 行业 90 分位或 TTM FCF < 0 时，当期 FCF-DCF 结构性低估——`confidence_note` 强制降级为"当期 FCF 被再投资压制，DCF 仅作下界参考"，且该情形下 valuation 不得以 DCF 单方法给出高置信方向信号（关联 I10.3）。

**UI 交互**：三个 slider（perpetual_g / 溢价调整 / 5y_g）围绕路由基准调整；拖动时 `POST /stock/{ticker}/dcf` 重算（纯计算 < 50ms），卡片显示"基准 WACC 来源：MarketConfig({market}) = X.X%"（I1.4：假设可见含来源可见）。

### 8.2 财务舞弊检测（analysis/fraud_detector.py）

**指标清单**（D3 已固化，5 项核心指标，可在 config.json 调）：

```python
FRAUD_INDICATORS = [
    {
        "name": "经营现金流 / 净利润",
        "rule": "5 年累计 OCF / 累计净利 < 0.7 → watch；< 0.4 → alert",
        "source": "AKShare 现金流量表 + 利润表",
    },
    {
        "name": "应收账款增速 vs 营收增速",
        "rule": "应收增速 - 营收增速 > 20pp → watch；> 50pp → alert",
        "source": "AKShare 资产负债表 + 利润表",
    },
    {
        "name": "存货增速 vs 营收增速",
        "rule": "存货增速 - 营收增速 > 30pp → watch；> 60pp → alert",
        "source": "同上",
    },
    {
        "name": "商誉占净资产",
        "rule": "商誉 / 净资产 > 30% → watch；> 50% → alert",
        "source": "AKShare 资产负债表",
    },
    {
        "name": "关联交易占比",
        "rule": "关联交易营收 / 总营收 > 30% → watch；> 50% → alert",
        "source": "AKShare 财报附注（可能数据缺失）",
    },
]

def check(market_info: MarketInfo) -> FraudCheckResult:
    """
    1. 拉 5 年财报数据
    2. 对每个指标计算 + 比对阈值
    3. 累积 findings
    4. LLM 调用：把 raw findings 给 LLM，让它输出自然语言 summary（不改变档位结论）
    5. 返回 FraudCheckResult，level = max(severity for f in findings)
    """
```

**警示档强制**（I1.5）：alert 档必须列具体 findings，UI 红底显示每条异常指标 + 实际数值 + 阈值。

### 8.3 限售解禁雷达（analysis/unlock_radar.py）

```python
def fetch(market_info: MarketInfo) -> UnlockRadarResult:
    """
    A 股：AKShare stock_restricted_release_queue_em
    港股：v1 无（标注【数据缺口】或 v2 接 HKEX）
    
    输出：
    - 未来 3 / 6 / 12 月解禁时间表
    - 每次解禁规模占当前流通股比例
    - 历史解禁后 30 日股价相对涨跌（中位 + 区间）
    
    UI：雷达图（4 象限）或时间线（推荐时间线，更直观）
    """
```

### 8.4 同业对比 + 指数叠加（analysis/peer_compare.py）

```python
def fetch_peers(market_info: MarketInfo) -> list[PeerRow]:
    """
    1. 取 market_info 对应的申万二级行业（D1 已固化）
    2. 取行业全部成员 ticker
    3. 对每个成员拉 PE / PB / ROE / 市值 / 今日涨跌
    4. 计算行业中位 / 均值
    5. 返回 PeerRow 列表（含原标的高亮）
    """

def industry_index(market_info: MarketInfo) -> IndustryIndexData:
    """
    1. 取行业指数 ticker（如申万白酒 801120）
    2. 拉行业指数 K 线
    3. 同时拉沪深 300 / 中证 500 K 线（D2 已固化：三条对照线）
    4. 归一化到同一起点 → 叠加图数据
    """
```

### 8.5 标的抽取（analysis/ticker_extractor.py）

```python
def extract_tickers(briefing_text: str, briefing_type: str) -> list[ExtractedTicker]:
    """
    LLM 调用：
    - System prompt: "你是金融文本解析助手，从早晚报中识别股票代码 + 名称 + 角色"
    - User prompt: 报告全文 + 输出 JSON schema 要求
    - 输出: list of {ticker, name, role, raw_mention}
    
    Role 判定规则（写在 prompt 里）：
    - focus（重点）：标题级讨论、独立段落、含买卖意见
    - risk（风险）：负面提及、风险标的列表
    - passing（路过）：一笔带过、对比中提到、行业全景中列出
    
    失败处理（I2.2）：
    - LLM 调用异常 → 抛 TickerExtractionFailed
    - 调用方（briefings_archive/ingest.py）捕获 → 标记 extraction_failed=True
    - UI 弹出"AI 抽取失败，请手动选择" + 显示用户的池内 30 只票多选框
    """
```

---

## 9. 港股新闻消费（hk_news/）

### 9.1 JSON Schema 校验（hk_news/schema.py）

```python
from pydantic import BaseModel, ValidationError

class HKNewsSchemaV1_0(BaseModel):
    schema_version: Literal["1.0"]
    ticker: str
    company_name_zh: str
    company_name_en: str | None = None
    market: Literal["CN_SH", "CN_SZ", "CN_BJ", "HK"]
    snapshot_at: datetime
    data_window_days: int
    
    news: list[NewsItem]
    announcements: list[Announcement]
    analyst_reports: list[AnalystReport]
    sentiment_signals: SentimentSignals
    risk_events: list[RiskEvent]
    peer_events: list[PeerEvent]
    data_gaps: list[str]

def validate_openclaw_json(raw_json_str: str) -> tuple[HKNewsBundle | None, list[str]]:
    """
    返回 (parsed_bundle, error_messages)。
    errors 非空时 parsed_bundle = None（整体拒绝）。
    """
    try:
        data = json.loads(raw_json_str)
    except json.JSONDecodeError as e:
        return None, [f"JSON 解析失败：第 {e.lineno} 行 第 {e.colno} 列：{e.msg}"]
    
    # schema_version 检查
    if data.get("schema_version") != "1.0":
        return None, [f"schema_version 必须是 '1.0'，得到 {data.get('schema_version')}"]
    
    try:
        validated = HKNewsSchemaV1_0(**data)
    except ValidationError as e:
        errors = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
        return None, errors
    
    return _to_bundle(validated), []
```

### 9.2 持久化与缓存（hk_news/ingest.py）

```python
def ingest_openclaw_json(ticker: str, raw_json_str: str) -> tuple[HKNewsBundle | None, list[str]]:
    bundle, errors = validate_openclaw_json(raw_json_str)
    if errors:
        return None, errors   # UI 红底显示
    
    # 持久化
    today = datetime.now().strftime("%Y%m%d")
    path = Path.home() / ".ai-hedge-fund" / "hk_news" / f"{ticker}_{today}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw_json_str, encoding="utf-8")
    
    # 触发 china_public_opinion 重新决议
    _invalidate_agent_cache(ticker)
    
    return bundle, []

def load_cached(ticker: str, max_age_hours: int = 24) -> HKNewsBundle | None:
    """24 小时内自动加载（I3.2）。"""
    path_pattern = f"{ticker}_*.json"
    base = Path.home() / ".ai-hedge-fund" / "hk_news"
    if not base.exists():
        return None
    
    candidates = sorted(base.glob(path_pattern), reverse=True)
    if not candidates:
        return None
    
    newest = candidates[0]
    ingested_at = datetime.fromtimestamp(newest.stat().st_mtime)
    if datetime.now() - ingested_at > timedelta(hours=max_age_hours):
        return None  # 过期，提示用户重跑
    
    bundle, _ = validate_openclaw_json(newest.read_text(encoding="utf-8"))
    return bundle
```

---

## 10. 三分法池（strategy/three_categories.py，简化版）

### 10.1 关键变化 vs v0.1

| 项 | v0.1 | v0.2 |
|----|------|------|
| 迁移建议 | 晚报自动生成 | UI 实时计算红黄灯（无 LLM） |
| 迁移确认 | 周报独占 + LLM | UI 手动按钮 + 必填理由 |
| 月度计数 | `migrations_this_month` | 同左，保留 |
| 池规模约束 | 5+5+5 | 同左 |

### 10.2 迁移信号检测（无 LLM，纯数据规则）

```python
def detect_migration_signals(pool: PoolState) -> list[MigrationSignal]:
    """
    返回信号列表（不强制人工操作），UI 用红黄灯显示。
    """
    signals = []
    
    # T → V: 景气连续 3 日走弱（资金流出 + 板块排名下行）
    for entry in pool.t_pool:
        capital_flow_3d = _get_capital_flow(entry.ticker, days=3)
        sector_rank_change = _get_sector_rank_change(entry.ticker, days=3)
        if all(f < 0 for f in capital_flow_3d) and sector_rank_change > 5:
            signals.append(MigrationSignal(
                ticker=entry.ticker,
                from_cat="T",
                to_cat="V",
                strength="yellow",   # red / yellow / none
                evidence={"capital_flow_3d": capital_flow_3d, "rank_change": sector_rank_change},
            ))
    
    # V → T: 5 日累涨 15%
    for entry in pool.v_pool:
        ret_5d = _cum_return(entry.ticker, days=5)
        if ret_5d >= 0.15:
            signals.append(MigrationSignal(
                ticker=entry.ticker, from_cat="V", to_cat="T",
                strength="red" if ret_5d >= 0.20 else "yellow",
                evidence={"cum_return_5d": ret_5d},
            ))
    
    # N → T: 叙事兑现（业绩超预期 + 股价确认）
    # 用 AKShare stock_yjbb_em + ret_20d 联合判定
    ...
    
    return signals
```

### 10.3 人工迁移操作（v1.1 修正：配对原子 API）

> **对账硬伤 2**：v1.0 原文的单腿 `execute_migration()` 在 Phase 1 沙箱 T6 中被证明违反 I4.1——
> 单腿出 / 入必然经过 4-5-6 的中间态。已验证实现为**配对原子迁移**，规格随之对齐。

```python
@dataclass
class MigrationLeg:
    ticker: str
    from_category: Literal["V", "T", "N", "OUT"]   # OUT = 池外
    to_category: Literal["V", "T", "N", "OUT"]

def execute_migration_pair(
    pool: PoolState,
    exit_leg: MigrationLeg,       # 出腿
    enter_leg: MigrationLeg,      # 入腿
    user_rationale: str,          # 必填（I4.5）
    signal_evidence: dict | None = None,
) -> tuple[PoolState, list[str]]:
    """
    两腿原子执行：任一腿校验失败 → 整体拒绝，不产生半迁移状态。
    - 共享 pair_id；两条 MigrationRecord 以 pair_id 关联
    - 月度配额按 pair_id 去重计数（I4.2：2 进 2 出）
    - 执行后强制断言池规模 5+5+5（I4.1），违反 → 回滚（R5d）
    - 原子写盘 .tmp + rename + 7 天 backup（I4.4）
    - user_rationale 非空校验（I4.5）
    """
```

**UI 牵连（Step 18）**：迁移面板必须同时选择两只票（出腿 + 入腿）后才允许提交；单票操作仅限"加池 / 减池"且同样受配额与规模断言约束。

---

## 11. 错误处理总览

### 11.1 错误分类

| 错误类型 | 来源 | 用户提示 | 系统行为 |
|---------|------|---------|---------|
| `LLMConfigError` | llm_text | "{provider} API Key 未配置" | 终止当前调用 |
| `LLMEmptyResponseError` | llm_text | "Provider 返回空；DeepSeek 检查 thinking 模式" | 重试 1 次 → 失败抛 |
| `LLMPromptTooLongError` | llm_text | "Prompt 超 token 上限" | 终止 + 提示降级 |
| `TickerExtractionFailed` | ticker_extractor | "AI 抽取失败，请手动选择" | 降级到 UI 手动多选 |
| `AgentDecisionFailed` | agents | "Agent {name} 调用失败" | 单 agent 标失败，其他继续 |
| `FraudCheckFailed` | fraud_detector | "舞弊检测失败" | 卡片显示失败状态 |
| `OpenclawJSONInvalid` | hk_news.schema | "Schema 校验失败：{details}" | 红底报错 + 不污染缓存 |
| `OpenclawJSONStale` | hk_news.ingest | "缓存已超 24 小时，建议重跑 openclaw" | 软提示，仍加载旧数据 |
| `DataSourceExhaustedError` | data_fallback | "{ticker} 所有数据源失败" | 单标的标【数据缺口】 |
| `MajorPageDataFailure` | snapshot | "≥50% 数据失败" | **暂停页面渲染**，提示 |
| `PoolStateLoadError` | three_categories | "池状态文件损坏 + 7 天备份均不可用" | 终止，需人工修复 |
| `MonthlyMigrationLimitExceeded` | three_categories | "本月迁移已达上限" | 终止本次确认 |
| `PoolSizeInvariantViolated` | three_categories | "迁移后池规模 ≠ 5+5+5" | 终止 + 回滚 |
| `DataSourceBreakerOpen`（状态非异常） | datasource.router | （健康面板可见） | 静默绕行链上下一源，run 不中断（I10.6） |
| `_TdxUnavailable` | mootdx_data | — | 记熔断，PRICE 链回落 akshare |
| baostock 疑似会话错误 | baostock_data | — | 仅此情形重登 + 30s 冷却；数据级"无数据"按空结果处理（登录风暴规则） |

### 11.2 三种 LLM 调用的 fail-loud 实现（I6.1）

```python
# briefings_archive/ingest.py
def ingest_with_extraction(text: str, type_: str, date_: str) -> BriefingArchive:
    archive = _save_to_disk(text, type_, date_)
    
    try:
        tickers = ticker_extractor.extract_tickers(text, type_)
        archive.extracted_tickers = tickers
        archive.extraction_method = "llm"
    except LLMConfigError as e:
        # 显式 fail-loud：UI 弹错 + 降级到手动
        archive.extraction_failed = True
        archive.extraction_method = "manual_fallback"
        raise UIWarning(f"AI 抽取失败：{e}。请手动选择池内标的。") from e
    
    return archive
```

---

## 12. 已踩坑速查表（精简 + 更新）

| # | 坑 | 不变量 | 防御机制 |
|---|----|-------|---------|
| 1 | python 跳微软商店 | — | 文档 |
| 2 | numpy Meson 编译失败 | — | pyproject.toml 锁版本 |
| 3 | `.env` 文件名错 | I7.1 | 启动检查文件名格式 |
| 4 | 观察池存档崩溃 (Pydantic v2) | I4.4 | `json.dumps(model.model_dump(), ensure_ascii=False)` |
| 5 | 个股全员中性（原版 agent 调美股 API） | I5.3 | api_bridge monkey-patch |
| 6 | `get_prices() unexpected keyword 'api_key'` | I5.3 | 路由前剥离 api_key |
| 7 | ~~AI 增强版从未生效~~（已删除报告生成功能） | I6.1 | 三种 LLM 调用全部 fail-loud |
| 8 | 数据空当 0 处理 | I1.1 | 强制 None + UI 渲染【数据缺口】 |
| 9 | AKShare 接口改名 / 参数大小写 | I1.2 | fallback chain + try/except |
| 10 | 系统级代理拦截 AKShare | I6.4 | NO_PROXY 进程级注入 |
| 11 | Ghost Version（patch 没生效） | I5.1-5.3 | 启动横幅打印模块路径 |
| 12 | DeepSeek thinking mode JSON 空响应 | I6.1 (内部) | endpoint 含 "deepseek" 时强制 disabled |
| **13** | **openclaw JSON 格式不匹配静默吃掉** | **I3.1** | **schema 严格校验 + 整体拒绝 + 红底报错** |
| **14** | **标的抽取失败 UI 卡住** | **I2.2** | **fail-loud + 降级到手动多选** |
| **15** | **DCF 假设硬编码看不见** | **I1.4** | **UI 三个 slider 可见可调** |
| **16** | **舞弊检测只给结论不给依据** | **I1.5** | **alert 档必须列具体异常项** |

| **17** | **Tushare YTD 累计覆盖 TTM（流量科目单季失真）** | **I10.1** | **`_ttm_from_ytd` 三条 overlay 规则（修①）** |
| **18** | **美股贴现率 / 参数残留在 CN/HK 路径（价值系统性低估 70-85%）** | **I10.2** | **MarketConfig 市场路由；禁模块自带 WACC 表** |
| **19** | **「结论」价格锚点取 live price，回填时泄漏未来价** | **I10.4** | **PIT 探针前置；锚点改用窗口末收盘（待修，backlog）** |
| **20** | **baostock 数据级"无数据"误判会话失效 → 并发登录风暴** | — | **仅疑似会话错误重登 + 30s 冷却** |
| **21** | **mootdx wrapper 拖入 httpx<0.26 + py_mini_racer（V8 回流）** | — | **pytdx 直连（附录 A ADR-06）** |
| **22** | **港股决策置信恒 70.0% 疑似封顶 artifact** | **I10.3** | **待查（backlog）** |

后 4 个（13-16）是 v0.2 新增防御；坑 17-22 为 v1.1 对账新增（2026-06 数据层 / 信号层实战）。

---

## 13. 实现/补强顺序（v1.1 重排）

```
█ Phase 1: 基础设施补强 ✅ 完成（2026-06-17/18，5 模块真机冒烟）
█ Phase 2: 删除旧逻辑 ✅ 完成（2026-06-19，含 hotfix 链：ThreeCategoryPool 兼容壳 /
   api_china v1.0.2 补函数 / line_items_china v1.0.0）

█ 数据层重建（规格外转向，已收编为附录 A；编号 DL-N，防与本表撞号）
   DL-0 baostock 主源 ✅（2026-06-22 真机验收）
   DL-1 Router + 熔断 + 健康 ✅（2026-06-23 真机验收，单点消除成立）
   DL-2 Tushare 真值 ✅（激活；TTM 经 _ttm_from_ytd 保护）
   DL-3 pytdx PRICE 冗余 ✅（2026-06-23 真机验收）
   DL-4 浏览器兜底 ⬜（可选，不进热路径）

█ Phase 3/4 剩余（D6：工作台四页面全量保留 v1.1；宿主 = 国内协作机）

□ Step 0: 文件盘点（30 秒命令）落定 analysis/ 五模块与 web_app 页面现状——一切排期以此为准
□ Step 9-13: 分析模块（最小集优先）：peer_compare → dcf（D4 v2，§8.1）→ ticker_extractor
   → unlock_radar → fraud_detector
□ Step 14-15: briefings_archive / hk_news 路由 + UI 接线（模块已建已测）
□ Step 16: web_app 4 页面路由 + 模板
□ Step 17: 前端 K 线 + sparkline + 热力图（接 DataSourceRouter 真数据）
□ Step 18: 池总览页 + 红黄灯 + 配对迁移面板（两腿选择，§10.3）

█ 研究回路（与 Phase 3/4 并行，零阻塞，D5）
□ 每周批跑 → run log 喂 src/eval/harness 累积截面（I10.5）
□ 下次批跑顺手：Step 0 盘点 + 港股深度页实测（定谳 I1.3/I3.3）
□ I1.1 双实锤修复（backlog 首位）：capflow data_quality 透传（I10.3 理想版）；
   technical 退化值改【数据缺口】不入信号

█ Phase 5: 端到端验证
□ Step 19-22: 场景 1-4（PRODUCT §7）
□ Step 22b: 场景 5（批跑 + outcome 评估，D5）
□ Step 23: 反向测试 R1-R9（§14）
□ Step 24: 运行 SDD ④ /validate-changes-match-specs（对 v1.1）
□ Step 25: Alex 人工对照 41 条不变量签字
```

工作量估算：待 Step 0 盘点落定现状后重定基线（v1.0 估算表已随现实失效，删除）。

---

## 14. 反向测试（v0.1 §15.3 升级版）

```
反向测试 R1：三种 LLM 调用都必须 fail-loud
   R1a: 临时改错 DEEPSEEK_API_KEY → 早晚报粘贴时标的抽取应弹错 + 降级手动
   R1b: 同上 → 个股深度页 8 agent 应显示部分失败但其他正常
   R1c: 同上 → 舞弊检测卡片应显示"检测失败"，不是"健康"档

反向测试 R2：数据缺口必须显式标注
   操作：临时断网或拒绝东方财富域名
   预期：深度页至少 5 处出现【数据缺口】
   反例：北向资金显示 "0.0 亿" = 违反 I1.1

反向测试 R3：Ghost Version 必须立即识破
   操作：故意改一个模块 __version__ 不重启
   预期：下次启动横幅显示新版本

反向测试 R4：openclaw JSON 校验严格
   R4a: 粘贴 broken JSON → 红底报错 + 不写缓存
   R4b: 粘贴缺 schema_version 字段 → 整体拒绝
   R4c: 粘贴非 1.0 版本 → 提示版本不识别
   R4d: 粘贴 sentiment 字段类型错（数字而非枚举字符串）→ 整体拒绝

反向测试 R5：三分法约束
   R5a: 月度已 2 进 2 出后再迁移 → 拒绝
   R5b: 不填 rationale 提交迁移 → 拒绝
   R5c: 池满员（V=5）再加入 → 拒绝
   R5d: 迁移后池规模 ≠ 5+5+5 → 应自动回滚

反向测试 R6：DCF 假设 UI 可见
   操作：检查深度页 DCF 卡
   预期：三个 slider 当前值显式可见，且拖动后内在价值实时更新
   反例：硬编码默认值不显示 = 违反 I1.4

反向测试 R7：舞弊检测警示档必须有依据
   操作：构造一只财报异常股的快照（应收增速远超营收）
   预期：检测结果为 alert + findings 列表非空 + 每条含 observed / threshold
   反例：alert 档 findings 为空 = 违反 I1.5

反向测试 R8：信号数据质量（I10.3，v1.1 新增）
   操作：人为断北向数据源
   预期：capflow 低置信 + 【数据缺口】标注 + data_quality 透传
   反例：输出 82% 高置信方向信号 = 违反（2026-06 实锤原型）

反向测试 R9：PIT 纪律（I10.4，v1.1 新增）
   操作：--end-date 设过去日期，跑价格敏感 agent（technical / valuation）双日期比对
   预期：价格类指标随窗口变化（ADX / RSI / market cap 不同；内在值可同——TTM 未变属正常）
   反例：价格类指标与"今日窗口"完全一致 = 泄漏；「结论」价格锚点 live-price 为已知未修项（坑 19）
```

---

## 15. 验证（SDD 第 ④ ⑤ 步）

### 15.1 SDD ④ — 规格一致性校验

跑完所有 Phase 1-4 后用 `/validate-changes-match-specs`：
- 输入：PRODUCT.md v1.1 + TECH.md v1.1 + 实现的代码
- 输出：不一致项清单
- 重点：41 条不变量 + 9 个反向测试是否都有对应代码实现
- 基线：《SDD对账报告_v1.0对现实》§3 核查表（2026-07-02，✓19 / ❓7 / ⊘6 / ✗1 / 嫌疑✗2）

### 15.2 SDD ⑤ — 端到端验证

按 PRODUCT.md v1.1 §7 五个场景跑 + §14 九组反向测试。

每个场景跑完后 Alex 对照 PRODUCT.md §8 Checklist 人工签字。

---

## 16. 已固化决策（v1.0，与 PRODUCT.md §9 一致）

| # | 决策项 | 固化值 | 位置 |
|---|---|---|---|
| ✅ D1 | 同业对比行业层级 | 申万二级 | §8.4 |
| ✅ D2 | 同业指数叠加 | 行业指数 + 沪深 300 + 中证 500 三条对照线 | §8.4 |
| ✅ D3 | 舞弊检测指标 | 5 项（OCF/净利、应收增速 vs 营收、存货增速 vs 营收、商誉占净资产、关联交易占比） | §8.2 |
| ✅ D4 (v2) | DCF 资本成本 | 基准 WACC 经 MarketConfig 路由；行业改风险溢价调整 ±pp；禁模块自带绝对 WACC 表（I10.2） | §8.1 |
| ✅ D5 | 研究回路入规格 | 批跑 CLI + eval harness + PIT 探针（F33-F35 / 场景 5 / I10 域） | PRODUCT §3H |
| ✅ D6 | 工作台与 UI 宿主 | 四页面全量保留 v1.1；宿主 = 国内协作机；Alex 远程消费导出；远程直连 = v2 | §13 |
| ✅ D7 | D4(v2) 批准 | 同 D4 行 | §8.1 |
| ✅ D8 | agent 花名册规格化 | 9-agent 推荐集 + 港股替换 + AGENT_REGISTRY 登记制 | §6.4 |

D5-D8 于 2026-07-02 对账后固化。后续新决策项作为 v1.2 增量处理。

### 16.1 v1.2 增量决策

| # | 决策项 | 固化值 | 位置 |
|---|---|---|---|
| ✅ 裁决⑤ | 研究回路评估宇宙冻结 | eval 宇宙 = **冻结票单**（跨周 IC 累积唯一真相源）；三分法池标签仅作 `by_class` 切片、不参与截面构成；0707 的 8 票 `stock_class` 空记录不重跑。自下次批跑起，`src/eval/signals.py` 解析时**票单来源与池状态解耦**：截面成员固定读冻结票单，池标签仅回填 `by_class` 维度（缺失记 null，不阻塞 IC 计算）。挂靠 I10.5，不新增不变量。 | §13 研究回路 / PRODUCT §9 v1.2 |

**附录 A**：data_layer_architecture.md（数据层规格全文收编，Phase 编号统一为 DL-0…DL-4）。
**附录 B**：修②设计草案（ADR-07：portfolio_manager 维度加权聚合——设计完成、离线验证否决未 ship；AGENT 注册表登记制独立保留）。

---

**TECH.md v1.1 结束（对账修订固化版）。** 下一步 → §13 Step 0 盘点 → Phase 3/4 剩余 + 研究回路并行 → Phase 5 → SDD ④。
