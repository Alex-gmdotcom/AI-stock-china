# AI Hedge Fund — China / Hong Kong Market Edition (中国A股/港股版)

基于 [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) 的中国 A 股（主板/创业板/科创板）和香港港股适配版本。

## 与原版的区别

| 维度 | 原版 (US) | 中国版 (CN/HK) |
|------|----------|---------------|
| 数据源 | financialdatasets.ai (付费) | AKShare (开源免费) |
| Ticker 格式 | AAPL, NVDA | 600519.SH, 00700.HK |
| 投资者 Agent | 13 位西方名人 | 保留适用的 + 4 个中国特色 Agent |
| 舆情分析 | 简单新闻分类 | 多源舆情 + 黑天鹅检测 |
| 政策分析 | 无 | 央行/证监会/国务院政策解读 |
| 资金流向 | Insider trading | 北向资金/融资融券/主力资金 |
| 板块分析 | 无 | 板块轮动 + 概念热度 |
| 交易规则 | T+0, 无涨跌停 | T+1, ±10%/±20% 涨跌停适配 |

## 新增 Agent 架构

```
┌─────────────────────────────────────────────────┐
│              Data Layer (AKShare)                │
│  Prices │ Financials │ News │ Capital Flow │ ... │
└──────────────────┬──────────────────────────────┘
                   │
    ┌──────────────┼──────────────┐
    │              │              │
    ▼              ▼              ▼
┌────────┐  ┌──────────┐  ┌──────────────┐
│ China  │  │ China    │  │ Universal    │
│ Agents │  │ Agents   │  │ Agents       │
│        │  │          │  │              │
│ 舆情    │  │ 政策解读  │  │ Technicals   │
│ 资金流向 │  │ 板块轮动  │  │ Fundamentals │
│        │  │          │  │ Valuation    │
│        │  │          │  │ Nassim Taleb │
│        │  │          │  │ Buffett      │
└───┬────┘  └────┬─────┘  └──────┬───────┘
    │            │               │
    └────────────┼───────────────┘
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

#### 1. 舆情分析师 (`china_public_opinion`)
- **数据源**: 东方财富新闻 + 全球财经资讯（可扩展财联社/雪球/微博）
- **核心能力**: 多源舆情情绪分析 + **黑天鹅事件检测**
- **黑天鹅识别**: 监管突袭、地缘政治、关键人物发言、流动性事件
- **输出**: 情绪信号 + 市场温度计（极度恐慌→极度贪婪）+ 黑天鹅警报

#### 2. 政策解读师 (`china_policy`)
- **核心理念**: A 股是"政策市"，一条政策可以推翻所有技术面信号
- **监控来源**: 国务院/央行/证监会/发改委/工信部/财政部公告
- **分析维度**: 政策类型 × 影响方向 × 持续时间 × 影响幅度
- **关键词过滤**: 70+ 政策相关关键词预过滤 + LLM 深度分析

#### 3. 资金流向分析师 (`china_capital_flow`)
- **北向资金**: 沪深港通外资流向（Smart Money 代理指标）
- **融资融券**: 杠杆资金情绪指标
- **主力资金**: 个股超大单/大单净流入
- **信号权重**: 北向 35% + 融资融券 25% + 个股主力 40%

#### 4. 板块轮动分析师 (`china_sector_rotation`)
- **板块排名**: 实时行业板块涨跌幅排名
- **轮动检测**: 热门板块 vs 冷门板块识别
- **拥挤度**: 高换手率 + 高位 = 拥挤风险
- **市场广度**: 上涨/下跌家数比例

## 安装

### 1. 克隆仓库

```bash
git clone https://github.com/YOUR_USERNAME/ai-hedge-fund-china.git
cd ai-hedge-fund-china
```

### 2. 安装依赖

```bash
# 安装 Poetry（如果还没有）
curl -sSL https://install.python-poetry.org | python3 -

# 安装项目依赖
poetry install

# 安装 AKShare（中国市场数据）
poetry run pip install akshare
```

### 3. 配置 API Key

```bash
cp .env.example .env
```

编辑 `.env`，至少配置一个 LLM 提供商：

```env
# 必需：至少一个 LLM API key
OPENAI_API_KEY=your-key        # GPT-4 系列
ANTHROPIC_API_KEY=your-key     # Claude 系列
DEEPSEEK_API_KEY=your-key      # DeepSeek（性价比之选）

# 可选：原版美股数据（如果也需要分析美股）
FINANCIAL_DATASETS_API_KEY=your-key
```

> **推荐**: DeepSeek 对中文理解优秀且成本极低，适合政策解读和舆情分析。

## 使用方法

### 基本用法

```bash
# 分析 A 股（自动识别交易所）
poetry run python src/main_china.py --ticker 600519,000858,300750

# 分析港股
poetry run python src/main_china.py --ticker 00700.HK,09988.HK

# 混合分析
poetry run python src/main_china.py --ticker 600519,00700.HK

# 科创板
poetry run python src/main_china.py --ticker 688981

# 指定日期范围
poetry run python src/main_china.py --ticker 600519 \
  --start-date 2024-01-01 --end-date 2024-06-30
```

### 进阶用法

```bash
# 显示每个 Agent 的详细推理过程
poetry run python src/main_china.py --ticker 600519 --show-reasoning

# 只运行特定 Agent
poetry run python src/main_china.py --ticker 600519 \
  --analysts china_public_opinion,china_policy,technical_analyst

# 使用 DeepSeek 模型
poetry run python src/main_china.py --ticker 600519 \
  --model-name deepseek-chat --model-provider DeepSeek

# 使用 Claude
poetry run python src/main_china.py --ticker 600519 \
  --model-name claude-sonnet-4-20250514 --model-provider Anthropic
```

### Ticker 格式

| 输入格式 | 解析结果 | 市场 |
|---------|---------|------|
| `600519` | `600519.SH` | 沪市主板 |
| `000858` | `000858.SZ` | 深市主板 |
| `300750` | `300750.SZ` | 创业板 |
| `688981` | `688981.SH` | 科创板 |
| `00700` | `00700.HK` | 港股 |
| `600519.SH` | `600519.SH` | 沪市主板（显式） |
| `AAPL` | `AAPL` | 美股（回退到原版 API） |

## 项目结构

```
src/
├── markets/                    # 市场抽象层
│   ├── __init__.py
│   ├── ticker.py              # Ticker 解析/验证/市场检测
│   └── config.py              # 交易规则配置（T+1/涨跌停/最小手数等）
│
├── data/
│   ├── models.py              # 原版数据模型（保持兼容）
│   └── models_china.py        # 中国特色数据模型（北向资金/融资融券/政策事件等）
│
├── tools/
│   ├── api.py                 # 原版 US 数据接口（保持不动）
│   ├── api_china.py           # AKShare 数据接口（核心替换）
│   └── api_factory.py         # 数据源工厂（根据 Ticker 自动路由）
│
├── agents/
│   ├── china_public_opinion.py  # 舆情分析 + 黑天鹅检测
│   ├── china_policy.py          # 政策解读
│   ├── china_capital_flow.py    # 资金流向分析
│   ├── china_sector_rotation.py # 板块轮动分析
│   ├── ... (原版 agents 保持不动)
│
├── utils/
│   ├── analysts.py            # 原版 analyst 配置
│   └── analysts_china.py      # 中国版 analyst 配置
│
├── main.py                    # 原版入口（US market）
└── main_china.py              # 中国版入口
```

## 可用 Analyst 列表

### 中国特色 Agent（默认启用）

| Key | 名称 | 类型 | 说明 |
|-----|------|------|------|
| `china_public_opinion` | 舆情分析师 | LLM | 多源舆情 + 黑天鹅检测 |
| `china_policy` | 政策解读师 | LLM | 政策信号对个股的影响分析 |
| `china_capital_flow` | 资金流向分析师 | 数据驱动 | 北向+融资融券+主力资金 |
| `china_sector_rotation` | 板块轮动分析师 | 数据驱动 | 板块排名+轮动阶段 |

### 通用 Agent（默认启用）

| Key | 名称 | 类型 |
|-----|------|------|
| `technical_analyst` | 技术分析 | 数据驱动 |
| `fundamentals_analyst` | 基本面分析 | 数据驱动 |
| `valuation_analyst` | 估值分析 | LLM |

### 名人投资者 Agent（可选）

| Key | 名称 | 适用场景 |
|-----|------|---------|
| `nassim_taleb` | Nassim Taleb | A 股尾部风险分析 |
| `warren_buffett` | Warren Buffett | 大盘蓝筹价值分析 |
| `peter_lynch` | Peter Lynch | 消费股/成长股 |

## 扩展数据源

`api_china.py` 设计为可扩展的数据层。要添加新数据源：

### 添加财联社电报

```python
# 在 api_china.py 中新增
def get_cls_telegraph(limit: int = 50) -> list[PublicOpinionItem]:
    """Fetch 财联社电报 (需要自行实现爬虫或 API 接入)"""
    # 实现方案:
    # 1. 财联社 RSS
    # 2. 自建爬虫
    # 3. 第三方聚合 API
    pass
```

### 添加雪球热帖

```python
def get_xueqiu_hot_posts(ticker: str, limit: int = 20) -> list[PublicOpinionItem]:
    """Fetch 雪球热门帖子 (需要 Cookie 认证)"""
    pass
```

## 重要提醒

⚠️ **本项目仅供学习和研究使用，不构成任何投资建议。**

- A 股市场具有独特的政策风险和散户主导特征
- AI 分析结果仅为参考，不能替代专业投资顾问
- 过往表现不代表未来收益
- 使用本软件进行真实交易的风险自担

## License

MIT License — 与原版保持一致。

## 致谢

- [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) — 原版框架
- [AKShare](https://github.com/akfamily/akshare) — 开源中国金融数据
- [LangGraph](https://github.com/langchain-ai/langgraph) — Agent 编排框架
