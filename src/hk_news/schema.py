"""
hk_news/schema.py — 港股新闻 JSON schema

v1.0.0 (2026-06-18, Phase 2 Step 3)

字段对齐 openclaw 港股新闻 prompt 实测产出
(基于 00148.HK / 01810.HK / 02513.HK 三份样本反推).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional

__version__ = "1.0.0"


# ---------------------------------------------------------------------------
# Enums (基于真实样本统计出现值)
# ---------------------------------------------------------------------------

class Sentiment(str, Enum):
    """单条新闻 / 风险事件的情绪极性."""
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    NEUTRAL = "neutral"

    @classmethod
    def coerce(cls, v) -> "Sentiment":
        """容错: 字符串大小写不敏感, 未知值返回 NEUTRAL."""
        if isinstance(v, cls):
            return v
        if not v:
            return cls.NEUTRAL
        s = str(v).strip().lower()
        for m in cls:
            if m.value == s:
                return m
        return cls.NEUTRAL


class AnnouncementType(str, Enum):
    """港交所公告分类."""
    EARNINGS = "earnings"               # 业绩公告 / 盈喜 / 年报
    DIVIDEND = "dividend"               # 股息 / 派息
    BUYBACK = "buyback"                 # 回购
    MERGER_ACQUISITION = "merger_acquisition"  # 并购 / 大宗配售
    OTHER = "other"                     # 其他 (含股东会决议 / 月报表 / 通函)

    @classmethod
    def coerce(cls, v) -> "AnnouncementType":
        if isinstance(v, cls):
            return v
        if not v:
            return cls.OTHER
        s = str(v).strip().lower().replace("-", "_").replace(" ", "_")
        for m in cls:
            if m.value == s:
                return m
        return cls.OTHER


class RatingChange(str, Enum):
    """研报评级变动方向."""
    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"
    UNCHANGED = "unchanged"
    INITIATION = "initiation"           # 首次覆盖

    @classmethod
    def coerce(cls, v) -> "RatingChange":
        if isinstance(v, cls):
            return v
        if not v:
            return cls.UNCHANGED
        # 真样本里出现过 "down grade" (带空格), 容错处理
        s = str(v).strip().lower().replace(" ", "").replace("-", "")
        if s in ("upgrade", "upgraded"):
            return cls.UPGRADE
        if s in ("downgrade", "downgraded"):
            return cls.DOWNGRADE
        if s == "initiation":
            return cls.INITIATION
        return cls.UNCHANGED


class RiskEventType(str, Enum):
    """风险事件分类."""
    REGULATORY = "regulatory"           # 监管 / 出口管制 / 处罚
    MANAGEMENT = "management"           # 管理层减持 / 离任 / 变动
    OTHER = "other"                     # 业绩暴跌 / 股价异动 / 空头集结等

    @classmethod
    def coerce(cls, v) -> "RiskEventType":
        if isinstance(v, cls):
            return v
        if not v:
            return cls.OTHER
        s = str(v).strip().lower()
        for m in cls:
            if m.value == s:
                return m
        return cls.OTHER


class RiskSeverity(str, Enum):
    """风险事件严重度."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @classmethod
    def coerce(cls, v) -> "RiskSeverity":
        if isinstance(v, cls):
            return v
        if not v:
            return cls.LOW
        s = str(v).strip().lower()
        for m in cls:
            if m.value == s:
                return m
        return cls.LOW


# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------

def _parse_iso(v) -> Optional[datetime]:
    """容错 ISO 8601 解析. 失败返回 None."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # 兼容 "Z" 结尾 / 带毫秒等
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None


def _iso_str(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


@dataclass
class NewsItem:
    title: str
    published_at: Optional[datetime]
    source: str
    url: str
    summary: str
    sentiment: Sentiment
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "published_at": _iso_str(self.published_at),
            "source": self.source,
            "url": self.url,
            "summary": self.summary,
            "sentiment": self.sentiment.value,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NewsItem":
        return cls(
            title=str(d.get("title", "")),
            published_at=_parse_iso(d.get("published_at")),
            source=str(d.get("source", "")),
            url=str(d.get("url", "")),
            summary=str(d.get("summary", "")),
            sentiment=Sentiment.coerce(d.get("sentiment")),
            tags=list(d.get("tags") or []),
        )


@dataclass
class Announcement:
    title: str
    published_at: Optional[datetime]
    type: AnnouncementType
    summary: str
    is_material: bool = False           # 是否重大公告 (港交所披露规则)
    url: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "published_at": _iso_str(self.published_at),
            "type": self.type.value,
            "summary": self.summary,
            "is_material": self.is_material,
            "url": self.url,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Announcement":
        return cls(
            title=str(d.get("title", "")),
            published_at=_parse_iso(d.get("published_at")),
            type=AnnouncementType.coerce(d.get("type")),
            summary=str(d.get("summary", "")),
            is_material=bool(d.get("is_material", False)),
            url=str(d.get("url") or ""),
        )


@dataclass
class AnalystReport:
    institution: str
    published_at: Optional[datetime]
    rating: str                         # 中英文混用,不强制 enum (买入/增持/中性/正面...)
    rating_change: RatingChange
    target_price: Optional[float]       # 可为 null (首次覆盖未给目标价)
    target_currency: str
    summary: str

    def to_dict(self) -> dict:
        return {
            "institution": self.institution,
            "published_at": _iso_str(self.published_at),
            "rating": self.rating,
            "rating_change": self.rating_change.value,
            "target_price": self.target_price,
            "target_currency": self.target_currency,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AnalystReport":
        tp = d.get("target_price")
        try:
            tp_f = float(tp) if tp not in (None, "", "null") else None
        except (TypeError, ValueError):
            tp_f = None
        return cls(
            institution=str(d.get("institution", "")),
            published_at=_parse_iso(d.get("published_at")),
            rating=str(d.get("rating", "")),
            rating_change=RatingChange.coerce(d.get("rating_change")),
            target_price=tp_f,
            target_currency=str(d.get("target_currency") or "HKD"),
            summary=str(d.get("summary", "")),
        )


@dataclass
class SentimentSignals:
    social_media_mentions_7d_vs_30d_avg: str   # 文本: "+85%" / "+220%" / "-15%"
    notable_breaking_negative: bool
    notable_breaking_positive: bool
    key_topics: list[str]
    summary: str

    def to_dict(self) -> dict:
        return {
            "social_media_mentions_7d_vs_30d_avg": self.social_media_mentions_7d_vs_30d_avg,
            "notable_breaking_negative": self.notable_breaking_negative,
            "notable_breaking_positive": self.notable_breaking_positive,
            "key_topics": list(self.key_topics),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "SentimentSignals":
        d = d or {}
        return cls(
            social_media_mentions_7d_vs_30d_avg=str(
                d.get("social_media_mentions_7d_vs_30d_avg") or ""
            ),
            notable_breaking_negative=bool(d.get("notable_breaking_negative", False)),
            notable_breaking_positive=bool(d.get("notable_breaking_positive", False)),
            key_topics=list(d.get("key_topics") or []),
            summary=str(d.get("summary", "")),
        )


@dataclass
class RiskEvent:
    event_type: RiskEventType
    occurred_at: Optional[datetime]
    summary: str
    severity: RiskSeverity
    is_ongoing: bool = False
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "occurred_at": _iso_str(self.occurred_at),
            "summary": self.summary,
            "severity": self.severity.value,
            "is_ongoing": self.is_ongoing,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RiskEvent":
        return cls(
            event_type=RiskEventType.coerce(d.get("event_type")),
            occurred_at=_parse_iso(d.get("occurred_at")),
            summary=str(d.get("summary", "")),
            severity=RiskSeverity.coerce(d.get("severity")),
            is_ongoing=bool(d.get("is_ongoing", False)),
            source=str(d.get("source") or ""),
        )


@dataclass
class PeerEvent:
    peer_ticker: Optional[str]
    peer_name: str
    event: str
    potential_impact: str
    occurred_at: Optional[datetime]

    def to_dict(self) -> dict:
        return {
            "peer_ticker": self.peer_ticker,
            "peer_name": self.peer_name,
            "event": self.event,
            "potential_impact": self.potential_impact,
            "occurred_at": _iso_str(self.occurred_at),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PeerEvent":
        return cls(
            peer_ticker=(str(d["peer_ticker"]) if d.get("peer_ticker") else None),
            peer_name=str(d.get("peer_name", "")),
            event=str(d.get("event", "")),
            potential_impact=str(d.get("potential_impact", "")),
            occurred_at=_parse_iso(d.get("occurred_at")),
        )


# ---------------------------------------------------------------------------
# 顶层 Snapshot
# ---------------------------------------------------------------------------

@dataclass
class HKNewsSnapshot:
    """一次港股新闻快照 (按 ticker × snapshot_at 唯一)."""
    ticker: str                         # 内部规范形式 "00700.HK"
    company_name_zh: str
    company_name_en: str
    market: str                         # 应该恒为 "HK", 但样本里我们保留原值
    snapshot_at: datetime               # 快照时间
    data_window_days: int               # 数据窗口 (例 30)
    schema_version: str                 # 上游 schema 版本号 (与本模块版本独立)

    news: list[NewsItem] = field(default_factory=list)
    announcements: list[Announcement] = field(default_factory=list)
    analyst_reports: list[AnalystReport] = field(default_factory=list)
    sentiment_signals: Optional[SentimentSignals] = None
    risk_events: list[RiskEvent] = field(default_factory=list)
    peer_events: list[PeerEvent] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)

    # 本地入库元数据 (上游 schema 不含)
    ingested_at: Optional[datetime] = None

    @property
    def snapshot_id(self) -> str:
        """ticker + 快照时间 → 唯一 ID. 同 ticker 可有多次快照."""
        ts = self.snapshot_at.strftime("%Y%m%dT%H%M%S")
        return f"{self.ticker}_{ts}"

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "company_name_zh": self.company_name_zh,
            "company_name_en": self.company_name_en,
            "market": self.market,
            "snapshot_at": _iso_str(self.snapshot_at),
            "data_window_days": self.data_window_days,
            "schema_version": self.schema_version,
            "news": [n.to_dict() for n in self.news],
            "announcements": [a.to_dict() for a in self.announcements],
            "analyst_reports": [r.to_dict() for r in self.analyst_reports],
            "sentiment_signals":
                self.sentiment_signals.to_dict() if self.sentiment_signals else None,
            "risk_events": [e.to_dict() for e in self.risk_events],
            "peer_events": [p.to_dict() for p in self.peer_events],
            "data_gaps": list(self.data_gaps),
            "ingested_at": _iso_str(self.ingested_at),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HKNewsSnapshot":
        snap_at = _parse_iso(d.get("snapshot_at"))
        if snap_at is None:
            raise HKNewsParseError(f"snapshot_at 解析失败: {d.get('snapshot_at')!r}")
        return cls(
            ticker=str(d.get("ticker", "")),
            company_name_zh=str(d.get("company_name_zh", "")),
            company_name_en=str(d.get("company_name_en", "")),
            market=str(d.get("market", "HK")),
            snapshot_at=snap_at,
            data_window_days=int(d.get("data_window_days") or 0),
            schema_version=str(d.get("schema_version", "")),
            news=[NewsItem.from_dict(x) for x in (d.get("news") or [])],
            announcements=[Announcement.from_dict(x) for x in (d.get("announcements") or [])],
            analyst_reports=[AnalystReport.from_dict(x) for x in (d.get("analyst_reports") or [])],
            sentiment_signals=SentimentSignals.from_dict(d.get("sentiment_signals")),
            risk_events=[RiskEvent.from_dict(x) for x in (d.get("risk_events") or [])],
            peer_events=[PeerEvent.from_dict(x) for x in (d.get("peer_events") or [])],
            data_gaps=list(d.get("data_gaps") or []),
            ingested_at=_parse_iso(d.get("ingested_at")),
        )


class HKNewsParseError(ValueError):
    """openclaw JSON 输入无法解析."""


class HKNewsNotFoundError(KeyError):
    """按 snapshot_id 查询时未找到."""


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from datetime import datetime as _dt

    print(f"[hk_news.schema v{__version__}] self-test")
    failures: list[str] = []

    # T1: enum coerce 容错
    assert Sentiment.coerce("positive") == Sentiment.POSITIVE
    assert Sentiment.coerce("POSITIVE") == Sentiment.POSITIVE
    assert Sentiment.coerce("unknown_value") == Sentiment.NEUTRAL
    assert Sentiment.coerce(None) == Sentiment.NEUTRAL
    assert RatingChange.coerce("down grade") == RatingChange.DOWNGRADE  # 真样本 typo
    assert RatingChange.coerce("upgrade") == RatingChange.UPGRADE
    assert RatingChange.coerce("initiation") == RatingChange.INITIATION
    assert AnnouncementType.coerce("merger_acquisition") == AnnouncementType.MERGER_ACQUISITION
    assert AnnouncementType.coerce("MERGER-ACQUISITION") == AnnouncementType.MERGER_ACQUISITION
    assert RiskSeverity.coerce("HIGH") == RiskSeverity.HIGH
    print("[T1] enum coercion (case / typo / None): PASS")

    # T2: NewsItem round-trip
    n = NewsItem.from_dict({
        "title": "测试新闻",
        "published_at": "2026-06-18T15:30:00+08:00",
        "source": "财华社",
        "url": "https://example.com/n",
        "summary": "摘要",
        "sentiment": "positive",
        "tags": ["AI", "回购"],
    })
    assert n.sentiment == Sentiment.POSITIVE
    assert n.published_at is not None
    assert n.tags == ["AI", "回购"]
    d = n.to_dict()
    n2 = NewsItem.from_dict(d)
    assert n2.to_dict() == d
    print("[T2] NewsItem round-trip: PASS")

    # T3: AnalystReport with null target_price (initiation 案例)
    ar = AnalystReport.from_dict({
        "institution": "国联民生证券",
        "published_at": "2026-06-18T14:04:00+08:00",
        "rating": "推荐",
        "rating_change": "initiation",
        "target_price": None,
        "target_currency": "HKD",
        "summary": "首次覆盖",
    })
    assert ar.rating_change == RatingChange.INITIATION
    assert ar.target_price is None
    assert ar.to_dict()["target_price"] is None
    print("[T3] AnalystReport with null target_price: PASS")

    # T4: 顶层 HKNewsSnapshot round-trip
    snap = HKNewsSnapshot(
        ticker="00700.HK",
        company_name_zh="腾讯控股",
        company_name_en="Tencent",
        market="HK",
        snapshot_at=_dt(2026, 6, 18, 19, 5, 0),
        data_window_days=30,
        schema_version="1.0",
        news=[n],
        analyst_reports=[ar],
        sentiment_signals=SentimentSignals.from_dict({
            "social_media_mentions_7d_vs_30d_avg": "+15%",
            "notable_breaking_negative": False,
            "notable_breaking_positive": True,
            "key_topics": ["AI", "回购"],
            "summary": "情绪正面",
        }),
        data_gaps=["缺少汇丰研报"],
    )
    sid = snap.snapshot_id
    assert sid == "00700.HK_20260618T190500", sid
    d = snap.to_dict()
    snap2 = HKNewsSnapshot.from_dict(d)
    assert snap2.snapshot_id == sid
    assert len(snap2.news) == 1
    assert snap2.news[0].sentiment == Sentiment.POSITIVE
    assert len(snap2.analyst_reports) == 1
    assert snap2.sentiment_signals.key_topics == ["AI", "回购"]
    assert snap2.data_gaps == ["缺少汇丰研报"]
    print(f"[T4] HKNewsSnapshot round-trip: PASS (snapshot_id={sid})")

    # T5: parse 失败时清晰报错
    try:
        HKNewsSnapshot.from_dict({"ticker": "00700.HK"})  # 缺 snapshot_at
        failures.append("T5: 缺 snapshot_at 应抛 HKNewsParseError")
    except HKNewsParseError:
        pass
    print("[T5] missing snapshot_at raises HKNewsParseError: PASS")

    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for f in failures:
            print(f"  ✗ {f}")
        raise SystemExit(1)
    print(f"\n[hk_news.schema v{__version__}] self-test PASS (5 groups)")
