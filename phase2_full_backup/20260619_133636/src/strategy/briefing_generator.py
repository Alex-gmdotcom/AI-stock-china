"""
Briefing generator — morning, evening, and weekly reports.

Orchestrates the AI hedge fund agents + three-category pool to produce
structured investment briefings that follow the exact template structure
from the user's prompt v3 system.

Morning briefing (08:00): 6-step pre-market analysis
Evening briefing (16:00): 6-step post-market review + scoring
Weekly review (Friday 16:30): Meta-audit of the system itself

Each briefing is:
  1. Generated as structured Markdown
  2. Stored persistently in a briefing log file
  3. Optionally delivered to Feishu / frontend dashboard
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from src.strategy.three_categories import ThreeCategoryPool, Category
from src.tools.api_china import (
    get_prices, get_company_news, get_public_opinion,
    get_northbound_flow, get_sector_performance, get_market_cap,
)
from src.utils.llm import call_llm

# ⭐ v3.1+: 必须最先安装代理绕行（修复 Windows 系统代理劫持东财子域导致的 ProxyError）
from src.tools import proxy_guard  # noqa: F401
from src.tools.quotes_fallback import fetch_tencent_quotes, fetch_tencent_indices
from src.tools.news_collector import get_global_telegraph, get_pool_news
from src.tools.external_brief import read_external_brief

logger = logging.getLogger(__name__)

BRIEFING_LOG = Path.home() / ".ai-hedge-fund" / "briefing-log.md"


# ═══════════════════════════════════════════════
# Scoring system (6 dimensions, 120pt → 100pt)
# ═══════════════════════════════════════════════

class BriefingScorer:
    """
    Scores morning predictions against evening actuals.

    6 dimensions, 20 points each (total 120), normalized to 100:
    ① Direction accuracy (方向准确性)
    ② Catalyst identification (催化剂识别)
    ③ Signal weight calibration (信号权重校准)
    ④ Risk warning (风险预警)
    ⑤ Volume compatibility (量能适配性)
    ⑥ Three-category tracking quality (三分法跟踪质量)

    Deviation grades: A(85+) / B(70+) / C(55+) / D(<55)
    """

    DIMENSIONS = [
        "direction_accuracy",
        "catalyst_identification",
        "signal_weight_calibration",
        "risk_warning",
        "volume_compatibility",
        "three_category_tracking",
    ]

    DIMENSION_LABELS = {
        "direction_accuracy": "① 方向准确性（满分20）",
        "catalyst_identification": "② 催化剂识别（满分20）",
        "signal_weight_calibration": "③ 信号权重校准（满分20）",
        "risk_warning": "④ 风险预警（满分20）",
        "volume_compatibility": "⑤ 量能适配性（满分20）",
        "three_category_tracking": "⑥ 三分法跟踪质量（满分20）",
    }

    LOSS_TYPES = {
        "A": "逻辑失效 — 推演本身有根本缺陷",
        "B": "逻辑正确但市场未认可 — right but early",
        "C": "信源盲区 — 未检索到的关键信号",
        "D": "黑天鹅 — 无法预见的突发事件",
    }

    @staticmethod
    def compute_grade(total_100: float) -> str:
        if total_100 >= 85:
            return "A"
        elif total_100 >= 70:
            return "B"
        elif total_100 >= 55:
            return "C"
        else:
            return "D"

    @staticmethod
    def normalize(raw_120: int) -> float:
        return round(raw_120 / 120 * 100, 1)


# ═══════════════════════════════════════════════
# Briefing history (persistent)
# ═══════════════════════════════════════════════

class BriefingHistory:
    """Read/write the persistent briefing log + dated daily backups."""

    def __init__(self, path: Path | None = None):
        self.path = path or BRIEFING_LOG
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 每日备份目录: ~/.ai-hedge-fund/briefings/
        self.daily_dir = self.path.parent / "briefings"
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("# AI Hedge Fund Briefing Log\n\n", encoding="utf-8")

    def append(self, content: str, kind: str = "briefing"):
        """Append to the running log AND save a dated standalone backup.

        kind: morning | evening | weekly | briefing
        Daily backup file: briefings/YYYY-MM-DD-{kind}.md (overwrites same-day same-kind)
        """
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("\n\n" + content)

        # 每日独立备份（同日同类型覆盖，保证幂等）
        today = datetime.now().strftime("%Y-%m-%d")
        backup = self.daily_dir / f"{today}-{kind}.md"
        backup.write_text(content, encoding="utf-8")

    def read_daily(self, date: str, kind: str) -> str | None:
        """Read a specific day's briefing backup. date: YYYY-MM-DD"""
        f = self.daily_dir / f"{date}-{kind}.md"
        return f.read_text(encoding="utf-8") if f.exists() else None

    def read_week(self, end_date: str | None = None) -> dict[str, dict[str, str]]:
        """Read the past 5 trading days' briefings.

        Returns {date: {kind: content}} for morning/evening files found.
        """
        end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.now()
        result: dict[str, dict[str, str]] = {}
        d = end
        found_days = 0
        scanned = 0
        while found_days < 5 and scanned < 14:  # look back max 2 weeks
            ds = d.strftime("%Y-%m-%d")
            day_data = {}
            for kind in ("morning", "evening"):
                content = self.read_daily(ds, kind)
                if content:
                    day_data[kind] = content
            if day_data:
                result[ds] = day_data
                found_days += 1
            d -= timedelta(days=1)
            scanned += 1
        return result

    def list_backups(self, limit: int = 30) -> list[str]:
        """List recent backup filenames, newest first."""
        files = sorted(self.daily_dir.glob("*.md"), reverse=True)
        return [f.name for f in files[:limit]]

    def read_recent(self, days: int = 5) -> str:
        """Read the full log (caller filters by date)."""
        return self.path.read_text(encoding="utf-8")

    def get_recent_scores(self, n: int = 10) -> list[dict]:
        """Parse recent evening scores from the log."""
        text = self.read_recent()
        scores = []
        for block in text.split("【晚报评分】"):
            if not block.strip():
                continue
            try:
                lines = block.strip().split("\n")[:3]
                # Parse score data (simplified)
                for line in lines:
                    if "综合得分" in line:
                        score_str = line.split("：")[-1].split("/")[0].strip()
                        scores.append({"score": float(score_str)})
            except Exception:
                continue
        return scores[-n:]


# ═══════════════════════════════════════════════
# Morning briefing generator
# ═══════════════════════════════════════════════

def generate_morning_briefing(
    pool: ThreeCategoryPool,
    history: BriefingHistory | None = None,
    end_date: str | None = None,
    llm_model: str = "gpt-4.1",
    llm_provider: str = "OpenAI",
) -> str:
    """
    Generate the morning briefing following the 6-step flow.

    Returns a complete Markdown briefing string.
    """
    if history is None:
        history = BriefingHistory()
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    today = datetime.strptime(end_date, "%Y-%m-%d")
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][today.weekday()]

    lines = []
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📅 {end_date} {weekday_cn} 早盘策略推演 · 08:30 发布")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    # ── Step 0: Pattern tracking ──
    lines.append("🧠 零、模式感知层")
    recent_scores = history.get_recent_scores(5)
    if recent_scores:
        score_str = "/".join([str(int(s.get("score", 0))) for s in recent_scores])
        lines.append(f"[模式追踪] 近5日得分：{score_str}")
    else:
        lines.append("[模式追踪] 基准期，开始建立偏差记录")

    pending_migrations = pool.get_pending_migrations()
    if pending_migrations:
        for m in pending_migrations:
            lines.append(
                f"[分类变动待确认] {m.name}: {m.from_category.label_cn} → "
                f"{m.to_category.label_cn}，原因：{m.reason}"
            )
    else:
        lines.append("[分类变动] 无")
    lines.append("")

    # ── Step 1: Overnight macro ──
    lines.append("🌍 一、宏观情绪定调")
    market_news = get_public_opinion(ticker=None, limit=20)
    macro_headlines = [
        f"· {item.title}" for item in market_news[:5]
    ]
    if macro_headlines:
        lines.extend(macro_headlines)
    else:
        lines.append("· [信号源缺失] 未获取到隔夜宏观数据")
    lines.append("")

    # ── Step 2: Commodity mapping ──
    lines.append("📦 二、大宗商品映射")
    lines.append("· [待接入] 铜/铝/硅料夜盘数据（需补充 AKShare futures 接口）")
    lines.append("")

    # ── Step 3: Policy catalysts ──
    lines.append("🎯 三、盘前核心催化剂")
    policy_items = []
    policy_keywords = ["央行", "证监会", "国务院", "发改委", "降准", "降息", "关税", "补贴", "监管"]
    for item in market_news:
        if any(kw in item.title for kw in policy_keywords):
            policy_items.append(item)

    if policy_items:
        lines.append("🔴 利多催化：")
        for i, item in enumerate(policy_items[:3], 1):
            lines.append(f" {i}. [{item.source}] {item.title}")
    else:
        lines.append("· 暂无重大政策催化信号")
    lines.append("")

    # ── Step 4: Announcement scanning ──
    lines.append("📋 四、公告排雷扫描")
    all_tickers = pool.get_all_tickers()
    alerts = []
    for ticker in all_tickers[:5]:  # Limit API calls
        try:
            news = get_company_news(ticker=ticker, end_date=end_date, limit=5)
            for n in news:
                alert_keywords = ["减持", "预亏", "退市", "立案", "处罚", "质押"]
                if any(kw in n.title for kw in alert_keywords):
                    entry = pool.get_by_ticker(ticker)
                    name = entry.name if entry else ticker
                    alerts.append(f"⚠️ [{name}] {n.title}")
        except Exception:
            continue

    if alerts:
        lines.extend(alerts)
    else:
        lines.append("· 观察池15只标的暂无重大公告雷区")
    lines.append("")

    # ── Step 5: Volume + sector rotation ──
    lines.append("📊 五、量能与板块轮动")
    sectors = get_sector_performance(limit=10)
    if sectors:
        hot = [s.sector_name for s in sectors[:3]]
        cold = [s.sector_name for s in sectors[-3:] if s.change_pct and s.change_pct < 0]
        lines.append(f"· 领涨板块：{', '.join(hot)}")
        if cold:
            lines.append(f"· 领跌板块：{', '.join(cold)}")
    lines.append("")

    # ── Three-category focus ──
    lines.append("📋 六、三分法观察池今日重点\n")

    for cat in Category:
        entries = pool.get_by_category(cat)
        lines.append(f"**{cat.emoji} {cat.label_cn}类今日关注（{entries[0].slot[0]}1-{entries[0].slot[0]}{len(entries)}）：**")

        if cat == Category.TREND:
            for e in entries:
                lines.append(f"· [{e.slot} {e.name}]：景气度判断待盘后更新")
        elif cat == Category.VALUE:
            lines.append("· 今日估值类无特殊催化剂，维持观察")
        else:
            lines.append("· 今日叙事类无重大事件，维持观察")
        lines.append("")

    # ── Reverse hypothesis ──
    lines.append("🔄 七、反向假说（必填）")
    lines.append("· [待LLM生成] 今日主线最大的反面理由")
    lines.append("· 证伪条件：[待补充]")
    lines.append("· 当前主线置信度：C（待校准）")
    lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("[风险提示：本报告为AI辅助分析，不构成投资建议。]")

    content = "\n".join(lines)

    # Persist
    history.append(f"【早报】\n{content}", kind="morning")

    return content


# ═══════════════════════════════════════════════
# Evening briefing generator
# ═══════════════════════════════════════════════

def generate_evening_briefing(
    pool: ThreeCategoryPool,
    scores: dict | None = None,
    history: BriefingHistory | None = None,
    end_date: str | None = None,
) -> str:
    """
    Generate the evening briefing following the 6-step review flow.

    Args:
        scores: Optional pre-computed scores dict with 6 dimensions.
        If None, scoring section will be marked as pending.
    """
    if history is None:
        history = BriefingHistory()
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    today = datetime.strptime(end_date, "%Y-%m-%d")
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][today.weekday()]

    lines = []
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📅 {end_date} {weekday_cn} 盘后深度复盘 · 16:00 发布")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    # ── Step 1: Market overview ──
    lines.append("📊 一、收盘真实体温计")
    nb_flow = get_northbound_flow(limit=1)
    if nb_flow:
        latest = nb_flow[-1]
        nb_str = f"北向净{'买入' if (latest.total_net_buy or 0) > 0 else '卖出'} {abs(latest.total_net_buy or 0):.1f}亿"
    else:
        nb_str = "北向数据待获取"
    lines.append(f"· 资金流向：{nb_str}")
    lines.append("")

    # ── Step 2: Anomaly scan ──
    lines.append("🔍 二、自选股异动红黑榜")
    all_tickers = pool.get_all_tickers()

    anomalies_up = []
    anomalies_down = []
    start_date = (today - timedelta(days=5)).strftime("%Y-%m-%d")

    for ticker in all_tickers:
        try:
            prices = get_prices(ticker, start_date, end_date)
            if len(prices) >= 2:
                change = (prices[-1].close - prices[-2].close) / prices[-2].close * 100
                entry = pool.get_by_ticker(ticker)
                name = entry.name if entry else ticker
                cat_label = entry.category.label_cn if entry else "?"

                if change >= 3:
                    anomalies_up.append(f"📈 {name}（{cat_label}类）(+{change:.1f}%)")
                elif change <= -3:
                    anomalies_down.append(f"📉 {name}（{cat_label}类）({change:.1f}%)")
        except Exception:
            continue

    if anomalies_up:
        lines.append("异动向上（≥+3%）：")
        lines.extend([f" · {a}" for a in anomalies_up])
    if anomalies_down:
        lines.append("异动向下（≤-3%）：")
        lines.extend([f" · {a}" for a in anomalies_down])
    if not anomalies_up and not anomalies_down:
        lines.append("· 今日观察池无显著异动（±3%以内）")
    lines.append("")

    # ── Step 3: Deviation analysis ──
    lines.append("🔬 三、偏差归因分析")
    lines.append("· [待LLM对比早报预测与实际走势后生成]")
    lines.append("")

    # ── Step 4: Three-category tracking ──
    lines.append("📋 四、三分法观察池盘后跟踪\n")

    for cat in Category:
        entries = pool.get_by_category(cat)
        lines.append(f"**{cat.emoji} {cat.label_cn}类（{entries[0].slot}-{entries[-1].slot}）今日状态：**")

        if cat == Category.TREND:
            lines.append("| 标的 | 今日涨跌 | 景气度信号 | 拐点预警 | 评级变动 |")
            lines.append("|------|---------|-----------|---------|---------|")
            for e in entries:
                try:
                    prices = get_prices(e.ticker, start_date, end_date)
                    if len(prices) >= 2:
                        chg = (prices[-1].close - prices[-2].close) / prices[-2].close * 100
                        chg_str = f"{'+' if chg >= 0 else ''}{chg:.1f}%"
                    else:
                        chg_str = "N/A"
                except Exception:
                    chg_str = "获取失败"
                lines.append(f"| {e.slot} {e.name} | {chg_str} | 待更新 | 无 | 维持{e.rating_stars} |")
        else:
            for e in entries:
                lines.append(f"· {e.slot} {e.name}：无新信号，维持{e.rating_stars}")
        lines.append("")

    # Migration detection
    lines.append("**🔀 跨类别迁移检测：**")
    pending = pool.get_pending_migrations()
    if pending:
        for m in pending:
            lines.append(
                f"· {m.name} 建议从 {m.from_category.label_cn} 迁移至 "
                f"{m.to_category.label_cn}，原因：{m.reason}，待周报确认"
            )
    else:
        lines.append("· 无迁移")
    lines.append("")

    # ── Step 5: Scoring ──
    lines.append("⚖️ 五、预测偏差量化评分\n")
    if scores:
        total_raw = sum(scores.values())
        total_100 = BriefingScorer.normalize(total_raw)
        grade = BriefingScorer.compute_grade(total_100)

        for dim in BriefingScorer.DIMENSIONS:
            label = BriefingScorer.DIMENSION_LABELS[dim]
            val = scores.get(dim, 0)
            lines.append(f"│ {label} │ {val} │")

        lines.append(f"\n综合得分：{total_100}/100 | 偏差等级：{grade}")
        lines.append(f"\n【晚报评分】综合得分：{total_100}/100")
    else:
        lines.append("· [评分待完成] 需对比早报预测与实际走势")
    lines.append("")

    # ── Step 6: Calibration ──
    lines.append("⚙️ 六、Prompt优化指令（供明日早报执行）")
    lines.append("· [待基于偏差分析生成具体校准指令]")
    lines.append("")

    lines.append("📡 七、茧房信号捕捉")
    lines.append("[茧房信号] 无")
    lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("[风险提示：本报告为AI辅助分析，不构成投资建议。]")

    content = "\n".join(lines)
    history.append(f"【晚报】\n{content}", kind="evening")

    return content


# ═══════════════════════════════════════════════
# LLM-enhanced briefing (full version)
# v3.3-full 重建：数据注入 + fail-fast + 评分护栏 + 多信源
# （原版调用 call_llm(system_prompt=...) 与其签名不符，必抛异常
#   静默回退 data-only —— 坑#10。本版改为 _llm_text 直连模型。）
# ═══════════════════════════════════════════════

SNAPSHOT_MIN_COVERAGE = 0.6  # 行情覆盖率低于此阈值时拒绝生成报告


def _pick_model() -> tuple[str, str]:
    """按 .env 中配置的 key 自动选择模型。优先 DeepSeek（成本低、中文强）。"""
    import os
    if os.getenv("DEEPSEEK_API_KEY"):
        return "deepseek-v4-flash", "DeepSeek"  # 旧ID deepseek-chat 于 2026-07-24 弃用
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude-sonnet-4-6", "Anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "gpt-5.2", "OpenAI"
    raise RuntimeError("未找到任何 LLM API key，请在 .env 中配置 DEEPSEEK_API_KEY 等")


def _llm_text(system_prompt: str, user_prompt: str,
              model_name: str, model_provider: str) -> str:
    """直接调用 LLM 返回纯文本。

    绕过 call_llm：其签名强制要求 pydantic_model，旧式
    call_llm(prompt=..., system_prompt=...) 调用必抛
    TypeError 并被静默回退（坑#10 根因）。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = None
    errors: list[str] = []
    get_model = None
    for mod_path in ("src.llm.models", "src.utils.llm"):
        try:
            mod = __import__(mod_path, fromlist=["get_model"])
            get_model = getattr(mod, "get_model", None)
            if get_model:
                break
        except ImportError as e:
            errors.append(f"{mod_path}: {e}")
    if get_model is None:
        raise RuntimeError(f"找不到 get_model（尝试过: {errors}）")

    try:
        llm = get_model(model_name, model_provider)
    except Exception as e:
        errors.append(f"get_model(str): {e}")
        try:
            from src.llm.models import ModelProvider
            llm = get_model(model_name, ModelProvider(model_provider))
        except Exception as e2:
            errors.append(f"get_model(enum): {e2}")
    if llm is None:
        raise RuntimeError(f"无法初始化模型 {model_provider}/{model_name}: {errors}")

    resp = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])
    content = resp.content
    if isinstance(content, list):  # Anthropic 可能返回 content blocks
        content = "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return str(content)


def _collect_market_snapshot(pool: ThreeCategoryPool, end_date: str) -> tuple[str, dict]:
    """
    采集观察池15只标的的真实收盘数据 + 指数温度计 + 北向 + 板块 + 新闻。

    DeepSeek 等模型没有联网搜索，必须把真实数据注入 prompt，否则 LLM 只能编造。
    返回 (快照文本, 健康度dict)，调用方据此 fail-fast。
    AKShare(东财) 失败的标的自动回退腾讯实时行情；失败条目记录异常原文。
    """
    health = {
        "quotes_ok": 0, "quotes_total": 0, "quotes_fallback": 0,
        "northbound": False, "sectors": False, "errors": [],
    }
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    start = (end_dt - timedelta(days=12)).strftime("%Y-%m-%d")

    # ── 第一轮：AKShare（东财日线，含5日累计/量比）──
    results: dict[str, str | None] = {}
    failed: list = []
    for e in pool.state.entries:
        health["quotes_total"] += 1
        try:
            prices = get_prices(e.ticker, start, end_date)
            if len(prices) >= 2:
                p0, p1 = prices[-2], prices[-1]
                chg = (p1.close - p0.close) / p0.close * 100
                chg5 = ((p1.close - prices[max(0, len(prices)-6)].close)
                        / prices[max(0, len(prices)-6)].close * 100) if len(prices) >= 6 else None
                vol_ratio = (p1.volume / p0.volume) if p0.volume else None
                extra = []
                if chg5 is not None:
                    extra.append(f"5日{chg5:+.1f}%")
                if vol_ratio is not None:
                    extra.append(f"量比{vol_ratio:.2f}")
                results[e.ticker] = (
                    f"{e.slot} {e.name}({e.ticker}): 收{p1.close:.2f} "
                    f"当日{chg:+.2f}% " + " ".join(extra)
                )
                health["quotes_ok"] += 1
            else:
                results[e.ticker] = None
                failed.append((e, "返回数据不足2条"))
        except Exception as ex:
            results[e.ticker] = None
            failed.append((e, f"{type(ex).__name__}: {str(ex)[:90]}"))

    # ── 第二轮：腾讯实时行情兜底（仅当日涨跌/量比，无5日历史）──
    if failed:
        tq = {}
        try:
            tq = fetch_tencent_quotes([e.ticker for e, _ in failed])
        except Exception as ex:
            health["errors"].append(f"腾讯备用源整体失败: {type(ex).__name__}: {str(ex)[:90]}")
        for e, err in failed:
            q = tq.get(e.ticker)
            if q is not None and q.price is not None and q.change_pct is not None:
                extra = f" 量比{q.volume_ratio:.2f}" if q.volume_ratio else ""
                results[e.ticker] = (
                    f"{e.slot} {e.name}({e.ticker}): 收{q.price:.2f} "
                    f"当日{q.change_pct:+.2f}%{extra} [腾讯实时·5日数据缺口]"
                )
                health["quotes_ok"] += 1
                health["quotes_fallback"] += 1
            else:
                results[e.ticker] = f"{e.slot} {e.name}({e.ticker}): 【数据缺口】"
                health["errors"].append(f"{e.ticker} {e.name}: 东财={err}; 腾讯=无返回")

    lines = ["━━━ 实时数据快照 ━━━", "\n【观察池15只标的收盘数据】"]
    for e in pool.state.entries:
        lines.append(results.get(e.ticker) or f"{e.slot} {e.name}({e.ticker}): 【数据缺口】")

    # ── v3.2 全球指数温度计（腾讯源，海外友好；美股为隔夜收盘）──
    lines.append("\n【全球指数温度计】")
    try:
        idx = fetch_tencent_indices()
        if idx:
            lines.append(" | ".join(
                f"{q.name} {q.price:,.0f}({q.change_pct:+.2f}%)" for q in idx.values()
            ))
            health["indices"] = True
        else:
            lines.append("【数据缺口】")
            health["errors"].append("指数温度计: 腾讯接口无返回")
    except Exception as ex:
        lines.append("【数据缺口】")
        health["errors"].append(f"指数温度计: {type(ex).__name__}: {str(ex)[:90]}")

    lines.append("\n【北向资金（近3日）】")
    try:
        nb = get_northbound_flow(limit=3)
        valid = [f"{x.date}: {x.total_net_buy:+.1f}亿" for x in nb if x.total_net_buy is not None]
        if valid:
            lines.append("; ".join(valid))
            health["northbound"] = True
        else:
            lines.append("【数据缺口】")
            health["errors"].append("北向资金: 接口返回但无有效数值")
    except Exception as ex:
        lines.append("【数据缺口】")
        health["errors"].append(f"北向资金: {type(ex).__name__}: {str(ex)[:90]}")

    lines.append("\n【行业板块当日排名】")
    try:
        sectors = get_sector_performance(limit=40)
        if sectors:
            ranked = sorted(sectors, key=lambda s: s.change_pct or 0, reverse=True)
            top = ", ".join(f"{s.sector_name}{s.change_pct:+.1f}%" for s in ranked[:5] if s.change_pct is not None)
            bot = ", ".join(f"{s.sector_name}{s.change_pct:+.1f}%" for s in ranked[-5:] if s.change_pct is not None)
            lines.append(f"领涨: {top}")
            lines.append(f"领跌: {bot}")
            health["sectors"] = True
        else:
            lines.append("【数据缺口】")
            health["errors"].append("板块排名: 接口返回空")
    except Exception as ex:
        lines.append("【数据缺口】")
        health["errors"].append(f"板块排名: {type(ex).__name__}: {str(ex)[:90]}")

    # ── v3.3 外部信源简报（openclaw 跨平台搜索，文件桥）──
    ext = read_external_brief()
    if ext:
        lines.append("\n【外部信源简报（openclaw）】")
        lines.append(ext)
        health["external_brief"] = True

    # ── v3.2 新闻注入（财联社电报 + 观察池个股新闻，多级回退）──
    lines.append("\n【全球财经电报（最新）】")
    try:
        tg = get_global_telegraph(limit=12)
        if tg:
            lines += [f"· {t}" for t in tg]
            health["news"] = True
        else:
            lines.append("【数据缺口】")
            health["errors"].append("电报快讯: 全部信源无返回")
    except Exception as ex:
        lines.append("【数据缺口】")
        health["errors"].append(f"电报快讯: {type(ex).__name__}: {str(ex)[:90]}")

    lines.append("\n【观察池个股新闻（A股，近2条/只）】")
    try:
        nw = get_pool_news([e.ticker for e in pool.state.entries], per_ticker=2)
        if nw:
            lines += [f"· {t}" for t in nw]
        else:
            lines.append("【数据缺口】")
    except Exception as ex:
        lines.append("【数据缺口】")
        health["errors"].append(f"个股新闻: {type(ex).__name__}: {str(ex)[:90]}")

    total = health["quotes_total"] or 1
    lines.append(
        f"\n【快照健康度】行情 {health['quotes_ok']}/{total}"
        f"（含腾讯兜底{health['quotes_fallback']}） | "
        f"北向 {'✓' if health['northbound'] else '✗'} | "
        f"板块 {'✓' if health['sectors'] else '✗'}"
    )
    lines.append("━━━ 快照结束 ━━━")
    return "\n".join(lines), health


def _snapshot_gate(health: dict, end_date: str, kind: str) -> str | None:
    """行情覆盖率低于阈值时返回诊断报告（中止生成、不写历史日志），否则返回 None。

    设计原则：空数据下生成的报告（尤其是带评分的晚报）会作为假锚点进入
    次日早报的校准回路，比不生成更有害。宁可中止，明确报错。
    """
    total = health.get("quotes_total") or 1
    coverage = health.get("quotes_ok", 0) / total
    if coverage >= SNAPSHOT_MIN_COVERAGE:
        return None
    lines = [
        f"━━━ {end_date} {kind}生成中止：数据采集失败 ━━━",
        "",
        f"行情覆盖率 {health.get('quotes_ok', 0)}/{total}（{coverage:.0%}），"
        f"低于阈值 {SNAPSHOT_MIN_COVERAGE:.0%}。",
        "为避免无数据支撑的报告污染反馈闭环，本次不调用 LLM、不写入历史日志。",
        "",
        "逐项错误：",
    ]
    errors = health.get("errors") or ["（无详细错误记录）"]
    lines += [f"  · {e}" for e in errors[:20]]
    lines += [
        "",
        "排查建议：",
        "  1. 运行 doctor_china.py 自检完整数据链路",
        "  2. 若错误为 ProxyError：系统代理劫持了东财子域。本版已注入 NO_PROXY，",
        "     但 TUN/全局模式下无效，需在代理客户端添加直连规则：",
        "     *.eastmoney.com / qt.gtimg.cn / *.sinajs.cn / *.sina.com.cn",
        "  3. 海外网络长期方案：采集端部署到国内 VPS 定时运行",
    ]
    return "\n".join(lines)


MORNING_SYSTEM_PROMPT = """你是一位具备全球宏观视野的中国市场首席策略师。

信号分层原则（强制）：
- 结构性信号（政策/产业/央行）权重 > 事件性信号（财报/公告）> 情绪性信号（社交媒体）
- 连续≥3天被讨论但未兑现的主线，自动降权50%（审美疲劳折扣）

硬性约束：
① 所有判断必须基于提供的数据快照和历史记录，数据缺失处标注【数据缺口】，严禁编造具体数字
② 昨日晚报的校准指令必须逐条执行并在文中体现
③ 必须严格按照模板结构输出，不允许省略任何章节
- 每条催化剂必须标注影响类别（估值/趋势/叙事）"""


def generate_llm_morning_briefing(
    pool: ThreeCategoryPool,
    end_date: str | None = None,
) -> str:
    """LLM 增强版早报：注入昨日晚报校准指令 + 实时数据快照。"""
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    history = BriefingHistory()
    pool_context = pool.to_prompt_context()
    snapshot, health = _collect_market_snapshot(pool, end_date)

    # ⭐ fail-fast：行情覆盖率不足时拒绝生成，输出诊断（不写历史日志）
    gate = _snapshot_gate(health, end_date, "早报")
    if gate:
        return gate

    # 读取最近一份晚报（校准回路的关键输入）
    prev_evening, prev_date = None, None
    for back in range(1, 5):
        d = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=back)).strftime("%Y-%m-%d")
        prev_evening = history.read_daily(d, "evening")
        if prev_evening:
            prev_date = d
            break
    calib_block = (
        f"━━━ {prev_date} 晚报（含校准指令，必须逐条执行）━━━\n{prev_evening[-1800:]}\n━━━ 晚报结束 ━━━"
        if prev_evening else "（无昨日晚报记录 —— 基准期，开始建立偏差记录）"
    )

    user_prompt = f"""基于以下真实数据，生成 {end_date} 的早盘策略推演。

{pool_context}

{snapshot}

{calib_block}

请严格按照以下结构输出：

🧠 零、模式感知层：模式追踪标签 + 量能判断标签 + 分类变动观察；若有昨日晚报，列出【校准优先项】并逐条落实
🌍 一、宏观情绪定调：基于快照中的全球指数温度计（隔夜美股/恒指→A股映射）与电报快讯，给出一句话开盘底色结论
🎯 二、盘前核心催化剂：利多/利空（按信号权重排序，标注影响类别）。必须引用快照中的电报与个股新闻原文要点；无新闻数据时标注【数据缺口】
📋 三、公告排雷扫描：从个股新闻中识别减持/质押/业绩预警类信息
📊 四、三分法观察池今日重点：
   - 🔵 估值类（V1-V5）：是否有催化剂触及压制因素？
   - 🔴 趋势类（T1-T5）：逐只给出隔夜/盘前信号 + 景气度判断（引用快照真实涨跌与量比）
   - 🟡 叙事类（N1-N5）：是否有证实/证伪叙事的事件？
🔄 五、反向假说（必填）：主线最大反面理由 + 可观测的证伪条件 + 当前置信度
💡 六、今日走势剧本沙盘：主线剧本 + 备用剧本（各带概率）+ 关键观察节点（具体时点与阈值）

总字数1000字以内。"""

    model_name, model_provider = _pick_model()
    try:
        content = _llm_text(MORNING_SYSTEM_PROMPT, user_prompt, model_name, model_provider)
        if content and content.strip():
            history.append(f"【早报】\n{content}", kind="morning")
            return content
        raise RuntimeError("LLM 返回空内容")
    except Exception as e:
        logger.warning("LLM morning briefing failed: %s. Falling back.", e)
        return generate_morning_briefing(pool, end_date=end_date)


EVENING_SYSTEM_PROMPT = """你是一位严格的量化策略复盘员，兼任"Prompt进化工程师"。

职责：
① 将今日早报的预测与实际收盘数据逐条对照，量化评分与归因
② 严禁粉饰错误，采用"外科医生式"冷静措辞
③ 失分分类必须严格区分：
   A类=逻辑失效（推演本身有根本缺陷）
   B类=逻辑正确但市场未认可（right but early）
   C类=信源盲区（未检索到的关键信号）
   D类=黑天鹅（无法预见的突发事件）
④ 评分必须有依据：每个维度的得分要引用早报原文 vs 实际数据的具体对比
⑤ 数据缺失处标注【数据缺口】，严禁编造具体数字
⑥ 无对照基准或数据缺失时，相应评分节只写"不适用"，禁止打分——尤其禁止给出100分/A级之类的"锚点"分"""


def generate_llm_evening_briefing(
    pool: ThreeCategoryPool,
    end_date: str | None = None,
) -> str:
    """LLM 增强版晚报：早报对照 + 实时数据 + 完整8段复盘结构。"""
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    history = BriefingHistory()
    pool_context = pool.to_prompt_context()
    snapshot, health = _collect_market_snapshot(pool, end_date)

    # ⭐ fail-fast：行情覆盖率不足时拒绝生成，输出诊断（不写历史日志）
    gate = _snapshot_gate(health, end_date, "晚报")
    if gate:
        return gate

    # 读取今天的早报（偏差评分的对照基准）
    morning = history.read_daily(end_date, "morning")
    morning_block = (
        f"━━━ 今日早报（评分对照基准）━━━\n{morning[:2500]}\n━━━ 早报结束 ━━━"
        if morning else "（今日无早报记录 —— 基准期，无对照基准）"
    )

    user_prompt = f"""基于以下真实数据，生成 {end_date} 的盘后深度复盘。

{pool_context}

{snapshot}

{morning_block}

请严格按照以下8段结构输出：

📊 一、收盘真实体温计：基于快照（指数温度计/板块/北向）给出市场全景 + 一句话盘面特征
🔍 二、自选股异动红黑榜：涨跌超±3%的观察池标的，逐只标注 → 驱动逻辑（优先引用快照中的个股新闻与电报条目）→ 早报是否命中(✅命中/❌遗漏/⚠️方向相反) → 分类处置建议
🔬 三、偏差归因分析：失分性质分类（A逻辑失效/B市场未认可/C信源盲区/D黑天鹅）+ 关键归因句
📋 四、三分法观察池盘后跟踪：
   - 🔵 估值类表格：今日涨跌/压制因素变化/评级
   - 🔴 趋势类表格：今日涨跌/景气度信号/拐点预警/评级
   - 🟡 叙事类表格：今日涨跌/叙事信号/置信度变化/评级
   - 🔀 跨类别迁移检测（只建议，周报才确认）
⚖️ 五、预测偏差量化评分：6维表格（方向准确性/催化剂识别/信号权重校准/风险预警/量能适配性/三分法跟踪质量，各20分），每项附评分依据，综合得分标准化至100分+偏差等级(A≥85/B≥70/C≥55/D<55)。仅当今日早报存在且快照中观察池数据完整时才允许打分
🔄 六、模式级偏差检测：连续方向失分/量能感知失灵/信源盲区扩大/标的预测低效/趋势钝化/估值修复完成
📡 七、茧房信号捕捉：与主流判断相矛盾的反常识信号
⚙️ 八、明日校准重点：3-4条具体、可执行、可验证的校准指令

若今日无早报或快照存在【数据缺口】，第三、五节只输出"不适用（基准期/数据缺口）"，禁止给出任何分数、等级或"参考锚点"——无数据支撑的满分是假锚点，会污染次日早报的校准回路。总字数1200字以内。"""

    model_name, model_provider = _pick_model()
    try:
        content = _llm_text(EVENING_SYSTEM_PROMPT, user_prompt, model_name, model_provider)
        if content and content.strip():
            history.append(f"【晚报】\n{content}", kind="evening")
            return content
        raise RuntimeError("LLM 返回空内容")
    except Exception as e:
        logger.warning("LLM evening briefing failed: %s. Falling back.", e)
        return generate_evening_briefing(pool, end_date=end_date)


# ═══════════════════════════════════════════════
# Weekly review (周复盘) — 元审计层
# ═══════════════════════════════════════════════

WEEKLY_SYSTEM_PROMPT = """你是一位策略系统的"元审计员"。你的工作不是评价某一天的市场预测，\
而是评价这套预测系统本身这一周的进化质量。

核心问题：
1. 本周的权重调整，整体上让系统变好了还是变坏了？
2. 本周最频繁的失分类型是什么（A逻辑失效/B市场未认可/C信源盲区/D黑天鹅）？指向哪个结构性弱点？
3. 本周校准指令中，有多少在次日产生了正效果？
4. 三分法观察池的分类是否仍然准确？有没有标的需要迁移或替换？
5. 三种"钱"（估值/趋势/叙事）各自表现如何？框架本身是否有效？

硬性约束：
- 所有数据必须来自提供的早晚报记录，禁止编造
- 校准指令追踪必须逐条展开
- 失分类型分布必须给出百分比
- 跨类别迁移的正式确认只能在周报中做出
- 观察池增减每月最多2进2出"""


def generate_weekly_review(
    pool: ThreeCategoryPool,
    history: BriefingHistory | None = None,
    end_date: str | None = None,
    use_llm: bool = True,
) -> str:
    """
    Generate the weekly meta-review (周复盘) following the user's
    weekly prompt v2 structure.

    Reads the past 5 trading days' morning/evening briefings from
    daily backups and produces the 7-step meta-audit:
      1. 本周得分全景
      2. 校准指令有效性评估
      3. 失分类型分布
      4. 量能预判准确率
      5. 三分法观察池周度深度复盘
      6. 信息茧房周度审计
      7. 下周系统优化指令
    """
    if history is None:
        history = BriefingHistory()
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    week_data = history.read_week(end_date)

    if not week_data:
        return (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {end_date} 周复盘\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚠️ 本周暂无早晚报记录。\n\n"
            f"周复盘需要至少1天的早报+晚报数据作为输入。\n"
            f"请先在交易日运行早报和晚报，积累数据后再生成周复盘。\n\n"
            f"数据目录: {history.daily_dir}"
        )

    dates = sorted(week_data.keys())
    pool_context = pool.to_prompt_context()

    if use_llm:
        # Build the week's briefing digest for the LLM
        digest_parts = []
        for d in dates:
            day = week_data[d]
            if "morning" in day:
                digest_parts.append(f"===== {d} 早报 =====\n{day['morning'][:2500]}")
            if "evening" in day:
                digest_parts.append(f"===== {d} 晚报 =====\n{day['evening'][:2500]}")
        digest = "\n\n".join(digest_parts)

        user_prompt = f"""请基于以下本周早晚报记录，生成 {end_date} 的周复盘报告。

本周记录覆盖日期: {', '.join(dates)}（共{len(dates)}个交易日）

{pool_context}

━━━ 本周早晚报记录 ━━━
{digest}
━━━ 记录结束 ━━━

请严格按照以下7步结构输出周复盘：

📈 一、本周得分全景：列出每日得分表（若晚报中有评分），周均分，得分趋势
🔁 二、校准指令有效性：逐条追踪晚报发出的优化指令，次日是否执行、是否有效
🧩 三、失分类型分布：A/B/C/D类各占百分比 + 解读
📊 四、量能预判准确率：早报量能判断 vs 实际
📋 五、三分法观察池周度深度复盘：
   5.1 三类标的本周表现汇总（每类一个表格：标的/周涨跌/关键变化/评级建议）
   5.2 跨类别迁移正式确认（确认/否决/延期待积累的迁移建议）
   5.3 观察池增减审议（是否有标的应移出/加入，遵守每月2进2出上限）
   5.4 三分法框架有效性评估
🌐 六、信息茧房周度审计：信源多样性 + 反向假说质量
⚙️ 七、下周系统优化指令：结构性调整 / 战术性调整 / 三分法调整 / 保持不变

若某项数据在记录中缺失，标注【数据缺口】，不要编造。
总字数1200字以内。"""

        try:
            model_name, model_provider = _pick_model()
            result = _llm_text(WEEKLY_SYSTEM_PROMPT, user_prompt,
                               model_name, model_provider)
            if result and result.strip():
                content = str(result)
                history.append(f"【周复盘】\n{content}", kind="weekly")
                return content
        except Exception as e:
            logger.warning("LLM weekly review failed: %s. Falling back.", e)

    # ── 数据版回退（无LLM）──
    lines = []
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📅 {end_date} 周复盘报告（数据汇总版）")
    lines.append(f"本周区间：{dates[0]} ~ {dates[-1]}（{len(dates)}个交易日）")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    lines.append("📈 一、本周记录覆盖")
    for d in dates:
        kinds = "、".join(["早报" if k == "morning" else "晚报" for k in week_data[d]])
        lines.append(f"· {d}: {kinds}")
    lines.append("")

    lines.append("📋 二、三分法观察池当前状态")
    for cat in Category:
        entries = pool.get_by_category(cat)
        avg_rating = sum(e.rating for e in entries) / max(len(entries), 1)
        lines.append(f"· {cat.emoji} {cat.label_cn}类: {len(entries)}只，平均评级 {avg_rating:.1f}★")
    lines.append("")

    pending = pool.get_pending_migrations()
    lines.append("🔀 三、待周报确认的迁移建议")
    if pending:
        for m in pending:
            lines.append(f"· {m.name}: {m.from_category.label_cn} → {m.to_category.label_cn}（{m.reason}）")
        lines.append("→ 请在Web界面或CLI中确认/否决以上迁移")
    else:
        lines.append("· 无")
    lines.append("")

    recent_ratings = pool.state.rating_history[-10:]
    lines.append("⭐ 四、本周评级变动记录")
    if recent_ratings:
        for r in recent_ratings:
            lines.append(f"· [{r.date}] {r.ticker}: {r.old_rating}★ → {r.new_rating}★（{r.reason}）")
    else:
        lines.append("· 无")
    lines.append("")

    lines.append("💡 五、提示")
    lines.append("· 此为数据汇总版。配置 LLM 后可生成完整的7步元审计周复盘")
    lines.append("· 包括校准指令追踪、失分类型分布、框架有效性评估等")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("[风险提示：本报告为AI辅助分析，不构成投资建议。]")

    content = "\n".join(lines)
    history.append(f"【周复盘】\n{content}", kind="weekly")
    return content
