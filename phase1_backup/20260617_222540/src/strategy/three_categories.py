"""
Three Categories Investment Framework (投资收益来源三分法).

Classifies all watchlist stocks into three categories based on the
source of expected returns:

  V (Value / 估值的钱):  Undervalued + external suppression → mean reversion
  T (Trend / 趋势的钱):  Accelerating earnings + sector momentum → double-click
  N (Narrative / 叙事的钱): Long-term story + imagination premium → faith + discipline

Each category has different:
  - Tracking frequency (V=weekly, T=daily, N=bi-weekly)
  - Key metrics to monitor
  - Entry/exit signals
  - Migration triggers to other categories

This module manages:
  1. Stock pool with category assignments and ratings
  2. Rating adjustments (⭐ to ⭐⭐⭐⭐⭐)
  3. Cross-category migration detection
  4. Persistent storage of pool state and history
  5. Pre-loaded default pool matching the user's existing watchlist
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# Category definitions
# ═══════════════════════════════════════════════

class Category(str, Enum):
    VALUE = "V"       # 估值的钱
    TREND = "T"       # 趋势的钱
    NARRATIVE = "N"   # 叙事的钱

    @property
    def label_cn(self) -> str:
        return {"V": "估值", "T": "趋势", "N": "叙事"}[self.value]

    @property
    def label_en(self) -> str:
        return {"V": "Value", "T": "Trend", "N": "Narrative"}[self.value]

    @property
    def color(self) -> str:
        return {"V": "#3b82f6", "T": "#ef4444", "N": "#eab308"}[self.value]

    @property
    def emoji(self) -> str:
        return {"V": "🔵", "T": "🔴", "N": "🟡"}[self.value]

    @property
    def tracking_frequency(self) -> str:
        return {
            "V": "weekly (每周深度检查1次)",
            "T": "daily (每日盘后必查)",
            "N": "bi-weekly (每周检查2次)",
        }[self.value]


# ═══════════════════════════════════════════════
# Stock entry model
# ═══════════════════════════════════════════════

class WatchlistEntry(BaseModel):
    """A single stock in the three-category observation pool."""
    slot: str                           # e.g. "V1", "T3", "N5"
    ticker: str                         # e.g. "002444.SZ"
    name: str                           # e.g. "巨星科技"
    category: Category
    rating: int = Field(ge=1, le=5, default=3)  # ⭐ count (1-5)

    # Category-specific fields
    core_logic: str = ""                # 核心投资逻辑
    suppression_factor: str = ""        # V: 压制因素
    catalyst: str = ""                  # V: 催化剂
    prosperity_indicator: str = ""      # T: 景气度指标
    inflection_signal: str = ""         # T: 拐点信号
    narrative_thesis: str = ""          # N: 核心叙事
    validation_node: str = ""           # N: 关键验证节点
    falsification_signal: str = ""      # N: 证伪信号

    # Tracking state
    added_date: str = ""
    last_reviewed: str = ""
    consecutive_weak_weeks: int = 0     # For removal trigger (4+ weeks ⭐⭐ = remove)

    @property
    def rating_stars(self) -> str:
        return "⭐" * self.rating

    @property
    def display(self) -> str:
        return f"{self.slot} {self.name} ({self.ticker}) {self.rating_stars}"


class CoreWatchlistEntry(BaseModel):
    """A stock in the original core watchlist (not in the 15-stock pool)."""
    ticker: str
    name: str
    market: Literal["HK", "AShare"]


class MigrationRecord(BaseModel):
    """Record of a cross-category migration suggestion or confirmation."""
    date: str
    ticker: str
    name: str
    from_category: Category
    to_category: Category
    reason: str
    status: Literal["suggested", "confirmed", "rejected", "deferred"] = "suggested"
    confirmed_date: str | None = None


class RatingChange(BaseModel):
    """Record of a rating adjustment."""
    date: str
    ticker: str
    old_rating: int
    new_rating: int
    reason: str
    trigger: str  # "evening_review" | "weekly_review" | "manual"


# ═══════════════════════════════════════════════
# Pool state
# ═══════════════════════════════════════════════

class PoolState(BaseModel):
    """Complete state of the three-category observation pool."""
    entries: list[WatchlistEntry] = Field(default_factory=list)
    core_watchlist: list[CoreWatchlistEntry] = Field(default_factory=list)
    migration_history: list[MigrationRecord] = Field(default_factory=list)
    rating_history: list[RatingChange] = Field(default_factory=list)
    last_updated: str = ""

    # Pool constraints
    max_per_category: int = 5
    max_monthly_additions: int = 2
    max_monthly_removals: int = 2
    monthly_additions_used: int = 0
    monthly_removals_used: int = 0
    month_tracker: str = ""  # "2026-06" to reset monthly counters


# ═══════════════════════════════════════════════
# Default pool (matches user's prompt v3)
# ═══════════════════════════════════════════════

DEFAULT_ENTRIES = [
    # V: Value (估值的钱)
    WatchlistEntry(slot="V1", ticker="002444.SZ", name="巨星科技", category=Category.VALUE, rating=4,
                   core_logic="亚洲最大手工具企业，绑定HD/沃尔玛",
                   suppression_factor="关税55%、汇率", catalyst="东南亚产能释放、降息"),
    WatchlistEntry(slot="V2", ticker="600660.SH", name="福耀玻璃", category=Category.VALUE, rating=5,
                   core_logic="全球汽车玻璃龙头，市占率>25%",
                   suppression_factor="关税+车市周期", catalyst="智能汽车玻璃升级、美国本土产能"),
    WatchlistEntry(slot="V3", ticker="000333.SZ", name="美的集团", category=Category.VALUE, rating=4,
                   core_logic="白电龙头，海外收入占比>42%",
                   suppression_factor="国补退坡担忧", catalyst="B端第二曲线、海外OBM增长"),
    WatchlistEntry(slot="V4", ticker="000921.SZ", name="海信家电", category=Category.VALUE, rating=4,
                   core_logic="白电估值洼地，PE~10x",
                   suppression_factor="市场关注度低", catalyst="海外增长、低基数效应"),
    WatchlistEntry(slot="V5", ticker="600887.SH", name="伊利股份", category=Category.VALUE, rating=3,
                   core_logic="乳制品全球龙头，必选消费",
                   suppression_factor="消费复苏不及预期", catalyst="渠道下沉、股息率~3.8%"),
    # T: Trend (趋势的钱)
    WatchlistEntry(slot="T1", ticker="300308.SZ", name="中际旭创", category=Category.TREND, rating=4,
                   core_logic="光模块龙头，Q1营收同比+192%",
                   prosperity_indicator="AI大厂capex、800G/1.6T出货量",
                   inflection_signal="capex指引下调、竞争格局恶化"),
    WatchlistEntry(slot="T2", ticker="002008.SZ", name="大族激光", category=Category.TREND, rating=4,
                   core_logic="PCB设备龙头，受益AI算力基建",
                   prosperity_indicator="PCB设备订单、子公司大族数控业绩",
                   inflection_signal="AI资本开支放缓、订单增速放缓"),
    WatchlistEntry(slot="T3", ticker="603228.SH", name="景旺电子", category=Category.TREND, rating=3,
                   core_logic="PCB制造商，受益算力/汽车电子",
                   prosperity_indicator="产能利用率、订单能见度",
                   inflection_signal="行业产能过剩信号"),
    WatchlistEntry(slot="T4", ticker="300502.SZ", name="新易盛", category=Category.TREND, rating=3,
                   core_logic="光模块第二梯队，LPO方案优势",
                   prosperity_indicator="800G市占率、海外客户拓展",
                   inflection_signal="技术路线切换风险（CPO vs LPO）"),
    WatchlistEntry(slot="T5", ticker="688019.SH", name="安集科技", category=Category.TREND, rating=3,
                   core_logic="半导体材料，受益国产替代",
                   prosperity_indicator="国产替代率、下游晶圆厂扩产",
                   inflection_signal="国产替代进度不及预期"),
    # N: Narrative (叙事的钱)
    WatchlistEntry(slot="N1", ticker="09880.HK", name="优必选", category=Category.NARRATIVE, rating=3,
                   narrative_thesis="人形机器人量产先行者",
                   validation_node="订单规模、产线良率",
                   falsification_signal="技术路线被替代、商业化持续不及预期"),
    WatchlistEntry(slot="N2", ticker="09660.HK", name="地平线", category=Category.NARRATIVE, rating=3,
                   narrative_thesis="自动驾驶芯片平台",
                   validation_node="车企定点数量、征程6出货",
                   falsification_signal="英伟达/高通抢占国内份额"),
    WatchlistEntry(slot="N3", ticker="601985.SH", name="中国核电", category=Category.NARRATIVE, rating=4,
                   narrative_thesis="核电重启+新堆型",
                   validation_node="核准机组数量、在建工程进度",
                   falsification_signal="核电政策转向、安全事件"),
    WatchlistEntry(slot="N4", ticker="002050.SZ", name="三花智控", category=Category.NARRATIVE, rating=3,
                   narrative_thesis="热管理平台型公司→机器人零部件",
                   validation_node="机器人订单占比、新能源车热管理份额",
                   falsification_signal="机器人产业化进度大幅低于预期"),
    WatchlistEntry(slot="N5", ticker="002594.SZ", name="比亚迪", category=Category.NARRATIVE, rating=4,
                   narrative_thesis="新能源车全球化+智能化",
                   validation_node="海外工厂产能、智驾方案落地",
                   falsification_signal="价格战侵蚀利润率、海外政策壁垒"),
]

DEFAULT_CORE_WATCHLIST = [
    CoreWatchlistEntry(ticker="01810.HK", name="小米集团", market="HK"),
    CoreWatchlistEntry(ticker="09880.HK", name="优必选", market="HK"),
    CoreWatchlistEntry(ticker="02285.HK", name="泉峰控股", market="HK"),
    CoreWatchlistEntry(ticker="09660.HK", name="地平线", market="HK"),
    CoreWatchlistEntry(ticker="002139.SZ", name="拓邦股份", market="AShare"),
    CoreWatchlistEntry(ticker="600089.SH", name="特变电工", market="AShare"),
    CoreWatchlistEntry(ticker="688019.SH", name="安集科技", market="AShare"),
    CoreWatchlistEntry(ticker="002402.SZ", name="和而泰", market="AShare"),
    CoreWatchlistEntry(ticker="002594.SZ", name="比亚迪", market="AShare"),
    CoreWatchlistEntry(ticker="002346.SZ", name="柘中股份", market="AShare"),
    CoreWatchlistEntry(ticker="601985.SH", name="中国核电", market="AShare"),
    CoreWatchlistEntry(ticker="000400.SZ", name="许继电气", market="AShare"),
    CoreWatchlistEntry(ticker="603228.SH", name="景旺电子", market="AShare"),
    CoreWatchlistEntry(ticker="002340.SZ", name="格林美", market="AShare"),
    CoreWatchlistEntry(ticker="002050.SZ", name="三花智控", market="AShare"),
]


# ═══════════════════════════════════════════════
# Pool manager
# ═══════════════════════════════════════════════

class ThreeCategoryPool:
    """
    Manages the three-category observation pool.

    Handles classification, rating, migration detection,
    and persistent storage of pool state.
    """

    def __init__(self, storage_path: str | None = None):
        self.storage_path = storage_path or os.path.expanduser(
            "~/.ai-hedge-fund/three_categories.json"
        )
        self.state = self._load_or_init()

    def _load_or_init(self) -> PoolState:
        """Load existing state or initialize with defaults."""
        path = Path(self.storage_path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return PoolState(**data)
            except Exception as e:
                logger.warning("Failed to load pool state: %s. Re-initializing.", e)

        # Initialize with defaults
        today = datetime.now().strftime("%Y-%m-%d")
        for entry in DEFAULT_ENTRIES:
            entry.added_date = today
        state = PoolState(
            entries=DEFAULT_ENTRIES,
            core_watchlist=DEFAULT_CORE_WATCHLIST,
            last_updated=today,
            month_tracker=today[:7],
        )
        self._save(state)
        return state

    def _save(self, state: PoolState | None = None):
        """Persist state to disk."""
        if state is None:
            state = self.state
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def save(self):
        """Public save method."""
        self.state.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save()

    # ── Queries ──────────────────────────────

    def get_by_category(self, cat: Category) -> list[WatchlistEntry]:
        return [e for e in self.state.entries if e.category == cat]

    def get_by_slot(self, slot: str) -> WatchlistEntry | None:
        for e in self.state.entries:
            if e.slot == slot:
                return e
        return None

    def get_by_ticker(self, ticker: str) -> WatchlistEntry | None:
        for e in self.state.entries:
            if e.ticker == ticker:
                return e
        return None

    def get_all_tickers(self) -> list[str]:
        """All tickers in the pool (15 stocks)."""
        return [e.ticker for e in self.state.entries]

    def get_trend_tickers(self) -> list[str]:
        """T-category tickers that need daily tracking."""
        return [e.ticker for e in self.state.entries if e.category == Category.TREND]

    def get_all_tickers_with_core(self) -> list[str]:
        """All tickers: pool + core watchlist (deduplicated)."""
        pool = set(self.get_all_tickers())
        core = {e.ticker for e in self.state.core_watchlist}
        return list(pool | core)

    # ── Rating ───────────────────────────────

    def adjust_rating(
        self, ticker: str, new_rating: int, reason: str, trigger: str = "evening_review"
    ) -> bool:
        entry = self.get_by_ticker(ticker)
        if not entry:
            return False

        old_rating = entry.rating
        if old_rating == new_rating:
            return False

        entry.rating = max(1, min(5, new_rating))
        self.state.rating_history.append(RatingChange(
            date=datetime.now().strftime("%Y-%m-%d"),
            ticker=ticker,
            old_rating=old_rating,
            new_rating=entry.rating,
            reason=reason,
            trigger=trigger,
        ))
        self.save()
        return True

    # ── Migration ────────────────────────────

    def suggest_migration(
        self, ticker: str, to_category: Category, reason: str
    ) -> MigrationRecord | None:
        """Suggest a migration (晚报只能建议，周报才能确认)."""
        entry = self.get_by_ticker(ticker)
        if not entry or entry.category == to_category:
            return None

        record = MigrationRecord(
            date=datetime.now().strftime("%Y-%m-%d"),
            ticker=ticker,
            name=entry.name,
            from_category=entry.category,
            to_category=to_category,
            reason=reason,
            status="suggested",
        )
        self.state.migration_history.append(record)
        self.save()
        return record

    def confirm_migration(self, ticker: str) -> bool:
        """Confirm a pending migration (only in weekly review)."""
        # Find the latest suggestion for this ticker
        for record in reversed(self.state.migration_history):
            if record.ticker == ticker and record.status == "suggested":
                entry = self.get_by_ticker(ticker)
                if not entry:
                    return False

                # Update category and slot
                old_cat = entry.category
                entry.category = record.to_category
                # Reassign slot number
                cat_entries = self.get_by_category(record.to_category)
                next_num = len(cat_entries)  # Will be +1 after this
                entry.slot = f"{record.to_category.value}{next_num}"

                record.status = "confirmed"
                record.confirmed_date = datetime.now().strftime("%Y-%m-%d")
                self.save()
                return True
        return False

    def get_pending_migrations(self) -> list[MigrationRecord]:
        return [r for r in self.state.migration_history if r.status == "suggested"]

    # ── Migration detection logic ────────────

    def detect_migrations(self, ticker: str, daily_data: dict) -> MigrationRecord | None:
        """
        Detect if a stock should be migrated based on daily data.

        Migration triggers:
        - T→V: Trend broke, but company quality good, valuation cheap
        - N→T: Narrative starting to deliver earnings
        - V→T: Suppression removed, earnings accelerating
        - Any→Remove: Core logic falsified
        """
        entry = self.get_by_ticker(ticker)
        if not entry:
            return None

        change_pct = daily_data.get("change_pct", 0)

        if entry.category == Category.TREND:
            # T→V: 3 consecutive weak days without inflection warning
            weak_days = daily_data.get("consecutive_weak_days", 0)
            if weak_days >= 3:
                return self.suggest_migration(
                    ticker, Category.VALUE,
                    f"景气度连续{weak_days}日走弱，建议从趋势切换为等待估值回归"
                )

        elif entry.category == Category.VALUE:
            # V→T: 5 consecutive up days >15% total
            cum_gain = daily_data.get("five_day_gain_pct", 0)
            if cum_gain >= 15:
                return self.suggest_migration(
                    ticker, Category.TREND,
                    f"5日累计上涨{cum_gain:.1f}%，估值修复可能已完成，建议切换为趋势跟踪"
                )

        elif entry.category == Category.NARRATIVE:
            # N→T: Narrative starting to deliver
            has_earnings_beat = daily_data.get("earnings_beat", False)
            if has_earnings_beat:
                return self.suggest_migration(
                    ticker, Category.TREND,
                    "叙事开始兑现业绩，建议从信仰模式切换为景气度跟踪"
                )

        return None

    # ── Pool management ──────────────────────

    def _reset_monthly_counters(self):
        """Reset monthly add/remove counters if new month."""
        current_month = datetime.now().strftime("%Y-%m")
        if self.state.month_tracker != current_month:
            self.state.month_tracker = current_month
            self.state.monthly_additions_used = 0
            self.state.monthly_removals_used = 0

    def can_add(self) -> bool:
        self._reset_monthly_counters()
        return self.state.monthly_additions_used < self.state.max_monthly_additions

    def can_remove(self) -> bool:
        self._reset_monthly_counters()
        return self.state.monthly_removals_used < self.state.max_monthly_removals

    def add_entry(self, entry: WatchlistEntry) -> bool:
        """Add a new stock to the pool (max 2 per month, total 15)."""
        if not self.can_add():
            logger.warning("Monthly addition limit reached")
            return False
        if len(self.state.entries) >= 15:
            logger.warning("Pool is full (15 stocks)")
            return False
        if self.get_by_ticker(entry.ticker):
            logger.warning("Ticker already in pool: %s", entry.ticker)
            return False

        entry.added_date = datetime.now().strftime("%Y-%m-%d")
        self.state.entries.append(entry)
        self.state.monthly_additions_used += 1
        self.save()
        return True

    def remove_entry(self, ticker: str, reason: str) -> bool:
        """Remove a stock from the pool (max 2 per month)."""
        if not self.can_remove():
            logger.warning("Monthly removal limit reached")
            return False

        entry = self.get_by_ticker(ticker)
        if not entry:
            return False

        self.state.entries = [e for e in self.state.entries if e.ticker != ticker]
        self.state.monthly_removals_used += 1
        logger.info("Removed %s (%s) from pool: %s", entry.name, ticker, reason)
        self.save()
        return True

    # ── Export for prompts ───────────────────

    def to_prompt_context(self) -> str:
        """Generate a text block for injection into LLM prompts."""
        lines = ["## 三分法观察池当前状态\n"]

        for cat in Category:
            entries = self.get_by_category(cat)
            lines.append(f"### {cat.emoji} {cat.label_cn}类 ({cat.label_en})")
            lines.append(f"跟踪频率: {cat.tracking_frequency}\n")
            for e in entries:
                lines.append(f"- **{e.slot} {e.name}** ({e.ticker}) {e.rating_stars}")
                if cat == Category.VALUE:
                    lines.append(f"  核心逻辑: {e.core_logic}")
                    lines.append(f"  压制因素: {e.suppression_factor}")
                    lines.append(f"  催化剂: {e.catalyst}")
                elif cat == Category.TREND:
                    lines.append(f"  核心逻辑: {e.core_logic}")
                    lines.append(f"  景气度指标: {e.prosperity_indicator}")
                    lines.append(f"  拐点信号: {e.inflection_signal}")
                elif cat == Category.NARRATIVE:
                    lines.append(f"  核心叙事: {e.narrative_thesis}")
                    lines.append(f"  验证节点: {e.validation_node}")
                    lines.append(f"  证伪信号: {e.falsification_signal}")
                lines.append("")

        pending = self.get_pending_migrations()
        if pending:
            lines.append("### 待确认迁移建议")
            for m in pending:
                lines.append(
                    f"- {m.name}: {m.from_category.label_cn} → {m.to_category.label_cn} "
                    f"({m.reason}) [建议于 {m.date}]"
                )

        return "\n".join(lines)

    def to_json_for_frontend(self) -> dict:
        """Export pool state for the React dashboard."""
        return {
            "entries": [e.model_dump() for e in self.state.entries],
            "core_watchlist": [c.model_dump() for c in self.state.core_watchlist],
            "pending_migrations": [m.model_dump() for m in self.get_pending_migrations()],
            "recent_rating_changes": [
                r.model_dump() for r in self.state.rating_history[-10:]
            ],
        }
