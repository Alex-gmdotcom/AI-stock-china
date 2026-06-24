"""
briefings_archive/schema.py — Briefing 数据模型 (v1.0.0)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Optional

__version__ = "1.0.0"


class BriefingType(str, Enum):
    """早晚报 / 周复盘类型."""
    MORNING = "morning"      # 早盘策略推演
    EVENING = "evening"      # 盘后深度复盘
    WEEKLY = "weekly"        # 周复盘元审计
    UNKNOWN = "unknown"      # 未识别 (但仍保留正文)


@dataclass
class BriefingMetadata:
    """从正文抽取的元数据 (best-effort,失败字段留 None).

    所有字段都是从中文 markdown 正文里 regex 抽出来的, 不保证完整.
    如果上游 openclaw 改了格式, 这些字段可能拿不到, 但 Briefing.raw_content
    始终是原文, 阅读不受影响.
    """
    score_grade: Optional[str] = None       # "A+" / "A" / "B+" / "B" / "C+" / "C"
    score_value: Optional[int] = None        # 0-100 标准化得分,如 69
    fault_categories: list[str] = field(default_factory=list)
    # 失分性质: ["C类-信源盲区", "A类正向超预期", ...]
    tickers_mentioned: list[str] = field(default_factory=list)
    # 提到的自选股 (T1/T2/V1/N1 等代号 + 具体名称),例: ["T1", "中际旭创", "T5"]
    pattern_alerts: list[str] = field(default_factory=list)
    # 模式级告警: ["量能感知失灵", "趋势钝化", ...]
    cocoon_signals: list[str] = field(default_factory=list)
    # 茧房信号 / 反共识发现
    issued_at_str: Optional[str] = None      # 发布时间原文,例 "08:30 发布"
    title_subtitle: Optional[str] = None     # 副标题,例 "FOMC决议日·沃什鹰派首秀..."

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Briefing:
    """单份早晚报或周复盘.

    永远存原文 (raw_content). metadata 只是为了索引/搜索方便,
    可能字段不全, 但绝不污染原文.
    """
    briefing_id: str                      # 例:"2026-06-18_morning"
    briefing_type: BriefingType
    briefing_date: date                   # 报告日期
    raw_content: str                      # 原始 markdown / 半结构化文本
    metadata: BriefingMetadata
    ingested_at: datetime                  # 入库时间 (区分发布时间与入库时间)
    source: str = "openclaw_paste"        # 来源标签 — 后续可以 "openclaw_paste" / "manual" / "import"
    source_path: Optional[str] = None      # 若来自文件,保留原路径

    def to_dict(self) -> dict:
        d = asdict(self)
        # 把 enum / date / datetime 转 ISO 格式
        d["briefing_type"] = self.briefing_type.value
        d["briefing_date"] = self.briefing_date.isoformat()
        d["ingested_at"] = self.ingested_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Briefing":
        d = dict(d)
        d["briefing_type"] = BriefingType(d["briefing_type"])
        d["briefing_date"] = date.fromisoformat(d["briefing_date"])
        d["ingested_at"] = datetime.fromisoformat(d["ingested_at"])
        d["metadata"] = BriefingMetadata(**d["metadata"])
        return cls(**d)


class BriefingNotFoundError(KeyError):
    """按 briefing_id 查询时未找到."""


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[briefings_archive.schema v{__version__}] self-test")

    md = BriefingMetadata(
        score_grade="B", score_value=69,
        fault_categories=["C类-信源盲区"],
        tickers_mentioned=["T1", "中际旭创"],
        pattern_alerts=["量能感知失灵"],
        cocoon_signals=["科技换锚"],
    )
    b = Briefing(
        briefing_id="2026-06-18_evening",
        briefing_type=BriefingType.EVENING,
        briefing_date=date(2026, 6, 18),
        raw_content="📅 2026 年 6 月 18 日 ... (mock body) ...",
        metadata=md,
        ingested_at=datetime(2026, 6, 18, 16, 5, 0),
    )

    # 序列化往返
    d = b.to_dict()
    assert d["briefing_type"] == "evening"
    assert d["briefing_date"] == "2026-06-18"
    assert d["metadata"]["score_value"] == 69

    b2 = Briefing.from_dict(d)
    assert b2.briefing_type == BriefingType.EVENING
    assert b2.briefing_date == date(2026, 6, 18)
    assert b2.metadata.score_value == 69
    assert b2.metadata.tickers_mentioned == ["T1", "中际旭创"]

    # raw_content 永不丢失
    assert b2.raw_content.startswith("📅")

    print("[T1] Briefing.to_dict / from_dict round-trip: PASS")
    print(f"[T2] enum / date / datetime serialization: PASS")
    print(f"[T3] metadata fields preserved: PASS")
    print(f"\n[briefings_archive.schema v{__version__}] self-test PASS (3 groups)")
