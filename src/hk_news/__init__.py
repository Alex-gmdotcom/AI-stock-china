"""
hk_news — 港股新闻 / 公告 / 研报 / 风险事件存档

v1.0.0 (2026-06-18, Phase 2 Step 3)

消费 openclaw 的港股新闻 prompt 产出 (JSON 格式).
schema 基于 2026-06-18 实测样本 (00148.HK / 01810.HK / 02513.HK).

设计原则:
  - schema 严格类型化 (dataclass + enum), 与 openclaw 输出对齐
  - ingest 容错: 字段缺失 / 类型错配 / 多余字段都不挂
  - storage 按 ticker × snapshot_at 索引 (一个 ticker 可以有多次快照)
"""

__version__ = "1.0.0"

from .schema import (
    Sentiment,
    AnnouncementType,
    RatingChange,
    RiskEventType,
    RiskSeverity,
    NewsItem,
    Announcement,
    AnalystReport,
    SentimentSignals,
    RiskEvent,
    PeerEvent,
    HKNewsSnapshot,
    HKNewsParseError,
    HKNewsNotFoundError,
)
from .storage import HKNewsStorage, default_storage_path
from .ingest import ingest_snapshot, ingest_snapshot_text, ingest_snapshot_file

__all__ = [
    "__version__",
    "Sentiment", "AnnouncementType", "RatingChange",
    "RiskEventType", "RiskSeverity",
    "NewsItem", "Announcement", "AnalystReport",
    "SentimentSignals", "RiskEvent", "PeerEvent", "HKNewsSnapshot",
    "HKNewsParseError", "HKNewsNotFoundError",
    "HKNewsStorage", "default_storage_path",
    "ingest_snapshot", "ingest_snapshot_text", "ingest_snapshot_file",
]
