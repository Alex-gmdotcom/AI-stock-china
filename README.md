# AI Hedge Fund — 中国 A 股 / 港股版 (China / Hong Kong Edition)

一个面向中国 A 股（主板 / 创业板 / 科创板）与香港港股的 AI 多智能体投研系统，基于 [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) 框架深度适配。

> ⚠️ **本项目仅供学习与研究，不构成任何投资建议。** AI 输出仅为参考，过往表现不代表未来收益，真实交易风险自担。

---

## 与原版的区别

| 维度 | 原版 (US) | 本版本 (CN/HK) |
|------|----------|---------------|
| 数据源 | financialdatasets.ai（付费） | **Baostock 主源 + pytdx(通达信 TCP) + AKShare**，多源失效转移 + 熔断 |
| Ticker 格式 | AAPL, NVDA | 600519.SH, 00700.HK |
| 投资者 Agent | 13 位西方名人 | 保留适用的 + **4 个中国特色 Agent** |
| 舆情分析 | 简单新闻分类 | 多源舆情 + **黑天鹅检测** |
| 政策分析 | 无 | 央行 / 证监会 / 国务院政策解读 |
| 资金流向 | Insider trading | 北向资金 / 融资融券 / 主力资金 |
| 板块分析 | 无 | 板块轮动 + 概念热度 |
| 交易规则 | T+0，无涨跌停 | T+1，±10% / ±20% 涨跌停适配 |

---

## 数据层（本版本核心改造）

A 股数据层从单源 AKShare 重建为「**Baostock 干净主源 + 按字段组多源失效转移 + 熔断 + 健康观测**」的韧性子系统：

- **价格 (PRICE)**：`baostock → pytdx(通达信 TCP) → akshare` 三机制冗余，任一源挂掉自动绕走。
- **财报（比率 / 绝对值）**：Baostock 比率代数反推绝对值；可选叠加 **Tushare** 真值补全现金流绝对科目（capex / 折旧 / FCF / 分红）。
- **港股**：东财三表（不走 py_mini_racer）。
- **熔断 + 自愈**：源连续失败即隔离、流量自动绕过、冷却后探活恢复；滚动成功率 / 延迟可观测，支持**飞书 webhook 告警**。
- **冗余按机制而非厂商**：故障按接入机制聚集（socket / TCP / token / web），真冗余来自机制不同。

> 设计理念：Baostock 不是承重墙，只是注册表里负责它最擅长那几组字段的一个 Provider。

---

## Agent 架构

```
┌─────────────────────────────────────────────────────────┐
│   Data Layer  —  Baostock 主 + pytdx + AKShare（熔断/路由）│
│   Prices │ Financials │ News │ Capital Flow │ Sector │ ... │
└────────────────────────┬────────────────────────────────┘
                         │
      ┌──────────────────┼──────────────────┐
      ▼                  ▼                  ▼
┌───────────┐    ┌────────────┐    ┌──────────────┐
│ China     │    │ China      │    │ Universal    │
│ Agents    │    │ Agents     │    │ Agents       │
│           │    │            │    │              │
│ 舆情+黑天鹅│    │ 政策解读    │    │ Technicals   │
│ 资金流向   │    │ 板块轮动    │    │ Fundamentals │
│           │    │            │    │ Valuation    │
│           │    │            │    │ Nassim Taleb │
│           │    │            │    │ Warren Buffett│
└─────┬─────┘    └──────┬─────┘    └──────┬───────┘
      └─────────────────┼──────────────────┘
                        ▼
                ┌──────────────┐
                │ Risk Manager │
                └──────┬───────┘
                       ▼
                ┌──────────────┐
                │  Portfolio   │
                │  Manager     │
                └──────┬───────┘
                       ▼
                Trading Decisions
```

### 四个中国特色 Agent

**1. 舆情分析师 (`china_public_opinion`)**
多源舆情情绪分析 + **黑天鹅事件检测**（监管突袭 / 地缘政治 / 关键人物发言 / 流动性事件）。输出情绪信号 + 市场温度计（极度恐慌 → 极度贪婪）+ 黑天鹅警报。

**2. 政策解读师 (`china_policy`)**
核心理念：A 股是"政策市"，一条政策可推翻所有技术面信号。监控国务院 / 央行 / 证监会 / 发改委 / 工信部 / 财政部；按政策类型 × 影响方向 × 持续时间 × 影响幅度分析。

**3. 资金流向分析师 (`china_capital_flow`)**
北向资金（Smart Money 代理）35% + 融资融券（杠杆情绪）25% + 个股主力净流入 40%。

**4. 板块轮动分析师 (`china_sector_rotation`)**
实时行业板块排名、热门 vs 冷门轮动检测、拥挤度（高换手 + 高位）、市场广度（涨跌家数比）。

---

## 安装

### 1. 克隆仓库

```bash
git clone https://github.com/Alex-gmdotcom/AI-stock-china.git
cd AI-stock-china
```

### 2. 安装依赖

```bash
# 安装 Poetry（如果还没有）
curl -sSL https://install.python-poetry.org | python3 -

# 安装项目依赖
poetry install

# 数据源依赖
poetry add baostock akshare    # A 股主源 + 兜底
poetry add pytdx               # 通达信 TCP 价格源（国内 IP）
# 可选：poetry add tushare      # 现金流绝对科目真值（需 token + 积分）
```

### 3. 配置 API Key

```bash
cp .env.example .env
```

编辑 `.env`，至少配置一个 LLM 提供商：

```env
# 必需：至少一个 LLM API key
DEEPSEEK_API_KEY=your-key      # DeepSeek（中文 + 性价比之选，推荐）
ANTHROPIC_API_KEY=your-key     # Claude 系列
OPENAI_API_KEY=your-key        # GPT 系列

# 可选：飞书告警 webhook（数据源健康/熔断推送）
# AIHF_FEISHU_WEBHOOK=...
# AIHF_FEISHU_SECRET=...

# 可选：Tushare 现金流真值
# AIHF_TUSHARE_TOKEN=...
```

> **推荐 DeepSeek**：对中文理解优秀、成本极低，适合政策解读与舆情分析。

---

## 使用方法

### 🖥️ Web 控制台（推荐）

```bash
poetry run python src/web_app.py
# 浏览器打开 http://localhost:8000
```

在界面里选股票、选 Agent、一键分析，查看各分析师信号与最终交易决策。

### ⌨️ 命令行

```bash
# 分析 A 股（自动识别交易所）
poetry run python src/main_china.py --ticker 600519,000858,300750

# 港股 / 科创板 / 混合
poetry run python src/main_china.py --ticker 00700.HK,09988.HK
poetry run python src/main_china.py --ticker 688981
poetry run python src/main_china.py --ticker 600519,00700.HK

# 指定日期范围 + 显示推理过程
poetry run python src/main_china.py --ticker 600519 \
  --start-date 2024-01-01 --end-date 2024-06-30 --show-reasoning

# 只跑特定 Agent
poetry run python src/main_china.py --ticker 600519 \
  --analysts china_public_opinion,china_policy,technical_analyst

# 指定模型
poetry run python src/main_china.py --ticker 600519 \
  --model-name deepseek-chat --model-provider DeepSeek
```

### Ticker 格式

| 输入 | 解析 | 市场 |
|------|------|------|
| `600519` | `600519.SH` | 沪市主板 |
| `000858` | `000858.SZ` | 深市主板 |
| `300750` | `300750.SZ` | 创业板 |
| `688981` | `688981.SH` | 科创板 |
| `00700` | `00700.HK` | 港股 |
| `AAPL` | `AAPL` | 美股（回退原版 API） |

---

## 项目结构

```
src/
├── markets/                     # 市场抽象层（ticker 解析 / 交易规则）
├── data/
│   ├── models.py                # 原版数据模型（保持兼容）
│   └── models_china.py          # 中国特色数据模型
├── tools/
│   ├── api_china.py             # CN/HK 数据接口 + 多源路由 override
│   ├── baostock_data.py         # A 股主源（比率反推绝对值）
│   ├── mootdx_data.py           # pytdx 通达信 TCP 价格兜底
│   ├── tushare_data.py          # 现金流绝对科目真值（可选）
│   ├── line_items_china.py      # A 股 / 港股 line items 装配
│   └── datasource/              # 多源路由子系统
│       ├── base.py              #   Provider 抽象 + 能力矩阵
│       ├── router.py            #   按字段组失效转移
│       ├── breaker.py           #   熔断器
│       ├── health.py            #   健康观测 + 告警
│       └── feishu_alert.py      #   飞书 webhook 告警
├── agents/
│   ├── china_public_opinion.py  # 舆情 + 黑天鹅
│   ├── china_policy.py          # 政策解读
│   ├── china_capital_flow.py    # 资金流向
│   ├── china_sector_rotation.py # 板块轮动
│   └── ...                      # 原版 agents（Valuation / Buffett / Taleb 等）
├── web_app.py                   # Web 控制台入口
├── main_china.py                # 中国版 CLI 入口
└── main.py                      # 原版 CLI 入口（US）
```

---

## 可用 Analyst

| Key | 名称 | 类型 |
|-----|------|------|
| `china_public_opinion` | 舆情分析师（+ 黑天鹅） | LLM |
| `china_policy` | 政策解读师 | LLM |
| `china_capital_flow` | 资金流向分析师 | 数据驱动 |
| `china_sector_rotation` | 板块轮动分析师 | 数据驱动 |
| `technical_analyst` | 技术分析 | 数据驱动 |
| `fundamentals_analyst` | 基本面分析 | 数据驱动 |
| `valuation_analyst` | 估值分析 | LLM |
| `nassim_taleb` | 尾部风险（Taleb） | LLM |
| `warren_buffett` | 价值（Buffett） | LLM |

---

## License

MIT License — 与原版保持一致，详见 `LICENSE`。

## 致谢

- [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) — 原版框架
- [Baostock](http://baostock.com/) · [AKShare](https://github.com/akfamily/akshare) · [pytdx](https://github.com/rainx/pytdx) — 数据源
- [LangGraph](https://github.com/langchain-ai/langgraph) — Agent 编排框架
