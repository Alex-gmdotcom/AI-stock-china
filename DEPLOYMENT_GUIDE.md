# 部署指南 — AI Hedge Fund 中国版 + 三分法投资终端

> 最后更新：2026-06-08
> 适用版本：v2（含三分法框架 + 早晚报 + Dashboard）

---

## 零、系统全景

部署完成后你会有三样东西可以用：

```
① Python CLI 后端
   poetry run python src/main_china.py --ticker 600519,300308
   → 多 Agent 并行分析 → 交易建议
   
② 早晚报自动生成
   poetry run python -m src.strategy.run_briefing --type morning
   → 按你的 prompt v3 模板生成 → 写入 briefing-log.md
   
③ React Dashboard（可选）
   在 Claude 对话中直接使用 StockDashboardV2.jsx
   → 点击标的看 K 线 → AI 早报/复盘/深度分析
```

---

## 一、环境准备

### 1.1 前置条件

```bash
# 确认 Python 版本（需要 3.11+）
python3 --version   # 应该 ≥ 3.11

# 确认 Git
git --version

# 确认 Poetry（如果没有，安装它）
poetry --version || curl -sSL https://install.python-poetry.org | python3 -
```

### 1.2 操作系统

Windows / macOS / Linux 均可。Windows 建议用 WSL2 或 PowerShell。

---

## 二、获取代码

### 方案 A：Fork 后打补丁（推荐，适合后续开源）

```bash
# 1. Fork 原版到你自己的 GitHub
# 浏览器打开 https://github.com/virattt/ai-hedge-fund → Fork

# 2. Clone 你的 fork
git clone https://github.com/你的用户名/ai-hedge-fund.git
cd ai-hedge-fund

# 3. 解压中国版补丁
# 把 ai-hedge-fund-china-v2.tar.gz 放到项目根目录
tar xzf ai-hedge-fund-china-v2.tar.gz

# 4. 提交为一个独立分支
git checkout -b china-edition
git add .
git commit -m "feat: add China/HK market support with three-category framework"
```

### 方案 B：直接使用（快速体验）

```bash
git clone https://github.com/virattt/ai-hedge-fund.git
cd ai-hedge-fund
tar xzf /path/to/ai-hedge-fund-china-v2.tar.gz
```

### 验证文件结构

```bash
# 应该看到这些新目录/文件
ls src/markets/          # ticker.py, config.py
ls src/strategy/         # three_categories.py, briefing_generator.py
ls src/agents/china_*    # 4 个中国特色 agent
ls src/tools/api_china.py
ls src/main_china.py
```

---

## 三、安装依赖

### 3.1 原版依赖

```bash
poetry install
```

### 3.2 中国版额外依赖

```bash
# AKShare — 中国市场数据（核心，必装）
poetry run pip install akshare

# 验证安装
poetry run python -c "import akshare; print('AKShare version:', akshare.__version__)"
```

> AKShare 依赖较多（pandas, requests, beautifulsoup4 等），如果安装报错：
> ```bash
> # macOS 可能需要
> brew install libxml2 libxslt
> # Ubuntu/Debian
> sudo apt-get install python3-lxml
> ```

### 3.3 可选：中文字体（如果生成图表）

```bash
# macOS — 系统已有中文字体，无需操作
# Ubuntu
sudo apt-get install fonts-noto-cjk
# Windows — 系统已有，无需操作
```

---

## 四、配置 API Key

### 4.1 创建 .env 文件

```bash
cp .env.example .env
```

### 4.2 编辑 .env

打开 `.env`，至少配置一个 LLM：

```env
# ===== 必需：至少一个 LLM =====

# 推荐组合 1：Claude（舆情/政策分析最强）
ANTHROPIC_API_KEY=sk-ant-api03-你的key

# 推荐组合 2：DeepSeek（中文理解强 + 极低成本，适合日常运行）
DEEPSEEK_API_KEY=sk-你的key

# 推荐组合 3：OpenAI（通用性好）
OPENAI_API_KEY=sk-你的key

# ===== 可选 =====

# 原版美股数据（如果你也想分析美股）
FINANCIAL_DATASETS_API_KEY=你的key

# Gemini（免费额度高，适合高频调用）
GOOGLE_API_KEY=你的key

# Moonshot/Kimi（国内可直连，无需 VPN）
MOONSHOT_API_KEY=你的key
# 大陆用户取消下行注释：
# MOONSHOT_BASE_URL=https://api.moonshot.cn/v1
```

### 4.3 API Key 获取地址

| 提供商 | 获取地址 | 价格参考 |
|--------|---------|---------|
| Anthropic (Claude) | https://console.anthropic.com | Sonnet ~$3/M tokens |
| DeepSeek | https://platform.deepseek.com | ~¥1/M tokens（极低） |
| OpenAI | https://platform.openai.com | GPT-4.1 ~$2/M tokens |
| Google (Gemini) | https://ai.dev | Flash 免费额度大 |
| Moonshot/Kimi | https://platform.moonshot.ai | 国内直连，¥12/M tokens |

> **成本控制建议**：日常早晚报用 DeepSeek（成本约 ¥0.1/次），深度分析和舆情检测用 Claude Sonnet。

### 4.4 验证配置

```bash
# 快速验证 API key 是否有效
poetry run python -c "
from dotenv import load_dotenv
import os
load_dotenv()
keys = {
    'ANTHROPIC': os.getenv('ANTHROPIC_API_KEY'),
    'OPENAI': os.getenv('OPENAI_API_KEY'),
    'DEEPSEEK': os.getenv('DEEPSEEK_API_KEY'),
}
for name, key in keys.items():
    status = '✅ 已配置' if key and not key.startswith('your-') else '❌ 未配置'
    print(f'{name}: {status}')
"
```

---

## 五、首次运行验证

### 5.1 验证 Ticker 解析

```bash
poetry run python -c "
from src.markets.ticker import parse_ticker
tickers = ['600519', '300308.SZ', '00700.HK', '688019', 'AAPL']
for t in tickers:
    info = parse_ticker(t)
    print(f'{t:>12} → {info.full_ticker:>12}  {info.market.display_name}')
"
```

预期输出：

```
      600519 →    600519.SH  Shanghai Main Board (沪市主板)
   300308.SZ →    300308.SZ  ChiNext (创业板)
    00700.HK →    00700.HK  HKEX (港股)
      688019 →    688019.SH  STAR Market (科创板)
        AAPL →         AAPL  US Equities
```

### 5.2 验证 AKShare 数据

```bash
poetry run python -c "
from src.tools.api_china import get_prices
prices = get_prices('600519', '2026-05-01', '2026-06-01')
print(f'获取到 {len(prices)} 条价格数据')
if prices:
    p = prices[-1]
    print(f'最新: {p.time} 开{p.open} 高{p.high} 低{p.low} 收{p.close} 量{p.volume}')
"
```

### 5.3 验证三分法观察池

```bash
poetry run python -c "
from src.strategy.three_categories import ThreeCategoryPool, Category
pool = ThreeCategoryPool()
for cat in Category:
    entries = pool.get_by_category(cat)
    print(f'{cat.emoji} {cat.label_cn}类 ({len(entries)}只):')
    for e in entries:
        print(f'  {e.display}')
    print()
print(f'观察池总计: {len(pool.get_all_tickers())} 只')
print(f'含核心自选股总计: {len(pool.get_all_tickers_with_core())} 只')
"
```

### 5.4 验证完整 Agent 运行（首次会比较慢）

```bash
# 单只股票快速测试
poetry run python src/main_china.py \
  --ticker 600519 \
  --show-reasoning \
  --model-name deepseek-chat \
  --model-provider DeepSeek
```

> 首次运行预计耗时 2-5 分钟（取决于 API 响应速度）。如果超时，检查网络和 API key。

---

## 六、日常使用

### 6.1 生成早报（每日 08:00 前）

```bash
# 方式 1：纯数据版（不消耗 LLM token，秒出）
poetry run python -c "
from src.strategy.three_categories import ThreeCategoryPool
from src.strategy.briefing_generator import generate_morning_briefing
pool = ThreeCategoryPool()
report = generate_morning_briefing(pool)
print(report)
"

# 方式 2：LLM 增强版（消耗 token，但质量更高）
poetry run python -c "
from src.strategy.three_categories import ThreeCategoryPool
from src.strategy.briefing_generator import generate_llm_morning_briefing
pool = ThreeCategoryPool()
report = generate_llm_morning_briefing(pool)
print(report)
"
```

### 6.2 生成晚报（每日 16:00 后）

```bash
poetry run python -c "
from src.strategy.three_categories import ThreeCategoryPool
from src.strategy.briefing_generator import generate_evening_briefing
pool = ThreeCategoryPool()
report = generate_evening_briefing(pool)
print(report)
"
```

### 6.3 运行完整 Agent 分析

```bash
# 分析趋势类标的（每日必查）
poetry run python src/main_china.py \
  --ticker 300308,002008,603228,300502,688019 \
  --analysts china_public_opinion,china_capital_flow,technical_analyst \
  --model-name deepseek-chat \
  --model-provider DeepSeek

# 分析估值类标的（每周深度）
poetry run python src/main_china.py \
  --ticker 002444,600660,000333,000921,600887 \
  --analysts china_policy,fundamentals_analyst,valuation_analyst,warren_buffett \
  --show-reasoning

# 分析港股叙事类
poetry run python src/main_china.py \
  --ticker 09880.HK,09660.HK \
  --analysts china_public_opinion,china_policy,nassim_taleb
```

### 6.4 管理观察池

```bash
# 调整评级
poetry run python -c "
from src.strategy.three_categories import ThreeCategoryPool
pool = ThreeCategoryPool()
pool.adjust_rating('300308.SZ', 5, '光模块景气度持续超预期', 'manual')
print('调整后:', pool.get_by_ticker('300308.SZ').display)
"

# 查看待确认迁移
poetry run python -c "
from src.strategy.three_categories import ThreeCategoryPool
pool = ThreeCategoryPool()
pending = pool.get_pending_migrations()
if pending:
    for m in pending:
        print(f'{m.name}: {m.from_category.label_cn} → {m.to_category.label_cn} | {m.reason}')
else:
    print('无待确认迁移')
"
```

---

## 七、定时任务（可选）

### macOS / Linux — crontab

```bash
crontab -e

# 早报：周一到周五 08:00
0 8 * * 1-5 cd /path/to/ai-hedge-fund && poetry run python -c "from src.strategy.three_categories import ThreeCategoryPool; from src.strategy.briefing_generator import generate_llm_morning_briefing; pool = ThreeCategoryPool(); print(generate_llm_morning_briefing(pool))" >> ~/stock-briefing-output.md 2>&1

# 晚报：周一到周五 16:15
15 16 * * 1-5 cd /path/to/ai-hedge-fund && poetry run python -c "from src.strategy.three_categories import ThreeCategoryPool; from src.strategy.briefing_generator import generate_evening_briefing; pool = ThreeCategoryPool(); print(generate_evening_briefing(pool))" >> ~/stock-briefing-output.md 2>&1
```

### Windows — 任务计划程序

```powershell
# 创建早报任务
schtasks /create /tn "StockMorningBriefing" /tr "cd C:\path\to\ai-hedge-fund && poetry run python -c \"from src.strategy.three_categories import ThreeCategoryPool; from src.strategy.briefing_generator import generate_morning_briefing; pool = ThreeCategoryPool(); print(generate_morning_briefing(pool))\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 08:00
```

---

## 八、Dashboard（React 前端）

Dashboard 有两种使用方式：

### 方式 1：在 Claude 对话中直接用（零部署）

把 `StockDashboardV2.jsx` 的内容粘贴到 Claude 对话中，让 Claude 以 artifact 形式渲染。你的自选池数据和分析结果都会在 artifact 里交互式展示。

### 方式 2：独立部署为 Web 应用（进阶）

```bash
# 1. 创建 React 项目
npx create-react-app stock-dashboard
cd stock-dashboard

# 2. 替换 App.jsx
cp /path/to/StockDashboardV2.jsx src/App.jsx

# 3. 安装依赖（如果用到 recharts 等）
npm install recharts

# 4. 配置环境变量
echo "REACT_APP_ANTHROPIC_KEY=你的key" > .env.local

# 5. 修改 API 调用中的 key（生产环境不要在前端暴露 key）
# 建议部署一个 /api/analyze 代理中间层

# 6. 启动
npm start
```

> **安全提示**：Dashboard 的 Anthropic API 调用目前是直连的。生产环境必须走后端代理，不要在前端暴露 API key。Claude artifact 模式下 key 由平台管理，没有这个问题。

---

## 九、数据存储位置

| 数据 | 路径 | 说明 |
|------|------|------|
| 三分法池状态 | `~/.ai-hedge-fund/three_categories.json` | 观察池、评级、迁移记录 |
| 早晚报日志 | `~/.ai-hedge-fund/briefing-log.md` | 累积追加，不覆盖 |
| AKShare 缓存 | 由原版 `src/data/cache.py` 管理 | 避免重复 API 调用 |
| Dashboard 状态 | `window.storage`（浏览器） | 仅 artifact 模式 |

### 备份建议

```bash
# 每周备份观察池和早晚报日志
cp ~/.ai-hedge-fund/three_categories.json ~/backup/tc-$(date +%Y%m%d).json
cp ~/.ai-hedge-fund/briefing-log.md ~/backup/bl-$(date +%Y%m%d).md
```

---

## 十、网络要求

### 必须可访问

| 服务 | 域名 | 用途 |
|------|------|------|
| AKShare | 东方财富/新浪等 A 股数据源 | 行情、财务、新闻 |
| LLM API | api.anthropic.com / api.deepseek.com | AI 分析 |

### 国内网络注意事项

AKShare 数据源都是国内服务（东方财富、新浪），**不需要 VPN**。

LLM API 的情况：

| 提供商 | 国内直连 | 备注 |
|--------|---------|------|
| DeepSeek | ✅ 可直连 | api.deepseek.com 国内可达 |
| Moonshot/Kimi | ✅ 可直连 | 设置 MOONSHOT_BASE_URL=https://api.moonshot.cn/v1 |
| Anthropic | ❌ 需 VPN | 或用 OpenRouter 中转 |
| OpenAI | ❌ 需 VPN | 或用国内代理 |
| Google Gemini | ❌ 需 VPN | — |

**最省心方案**：日常用 DeepSeek（国内直连 + 便宜），重要分析用 Claude（质量最高但需VPN）。

---

## 十一、故障排查

### 问题：AKShare 获取数据报错

```
AKShare 接口可能因源站变化而失效
```

解决：

```bash
# 升级到最新版
poetry run pip install akshare --upgrade

# 如果特定接口挂了，检查 AKShare GitHub issues
# https://github.com/akfamily/akshare/issues
```

### 问题：LLM API 超时

```bash
# 方法 1：换一个提供商
--model-name deepseek-chat --model-provider DeepSeek

# 方法 2：减少分析标的数量
--ticker 600519  # 先测单只

# 方法 3：减少 Agent 数量
--analysts technical_analyst,china_capital_flow
```

### 问题：Poetry 安装依赖卡住

```bash
# 使用国内镜像
poetry config repositories.tsinghua https://pypi.tuna.tsinghua.edu.cn/simple/

# 或者直接用 pip
poetry run pip install akshare -i https://pypi.tuna.tsinghua.edu.cn/simple/
```

### 问题：ModuleNotFoundError: No module named 'src'

```bash
# 确保在项目根目录运行
cd /path/to/ai-hedge-fund
# 确保 poetry 环境激活
poetry shell
# 或者用 poetry run 前缀
poetry run python src/main_china.py --ticker 600519
```

---

## 十二、开源发布清单

如果你要把这个项目开源发布：

```
□ Fork 原版仓库到你的 GitHub
□ 创建 china-edition 分支
□ 把 .env 加入 .gitignore（已有）
□ 确认 README_CHINA.md 内容完整
□ 删除 three_categories.json 中的个人持仓数据
   → 保留 DEFAULT_ENTRIES 作为示例
□ 确认所有 API key 没有硬编码在代码里
□ 跑一遍 python -m pytest 确保不报错
□ 写一个 CHANGELOG.md 记录你的改动
□ 推到 GitHub，发 Release
```

---

## 十三、每日工作流速查

```
08:00  运行早报 → 看市场情绪/政策催化/公告排雷
09:15  看 Dashboard K 线 → 关注趋势类开盘情况
10:30  检查北向资金 → 判断外资是否认可主线
15:00  收盘
16:00  运行晚报 → 偏差评分/归因/三分法跟踪
16:15  检查迁移建议 → 记录但不操作（等周报确认）
周五 16:30  运行周报 → 确认迁移/评级调整/池增减
```

---

*最后更新：2026-06-08 · AI Hedge Fund China Edition v2*
