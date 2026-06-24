"""
briefings_archive — openclaw 早晚报 / 周复盘存档与索引

v1.0.0 (2026-06-18, Phase 2 Step 2)

设计原则:
  - 不强行结构化解析正文 (中文 markdown 格式微调即 break,得不偿失)
  - 存原文 + 抽取关键 metadata (日期 / 类型 / 评分 / 自选股提及 / 模式标签)
  - 文件系统索引,JSON sidecar 存元数据,正文保持原 .md 可直接阅读

替代:本模块取代了 v3.5 的 strategy/briefing_generator.py (1071 行),
该文件 Phase 2 已删除.openclaw 跨平台搜索能力远超本地 LLM 自生,
我们只负责"消费"它的产出,不自己生成.
"""

__version__ = "1.0.0"

from .schema import (
    BriefingType,
    Briefing,
    BriefingMetadata,
    BriefingNotFoundError,
)
from .storage import BriefingStorage, default_storage_path
from .ingest import ingest_briefing, ingest_briefing_text, extract_metadata

__all__ = [
    "__version__",
    "BriefingType",
    "Briefing",
    "BriefingMetadata",
    "BriefingNotFoundError",
    "BriefingStorage",
    "default_storage_path",
    "ingest_briefing",
    "ingest_briefing_text",
    "extract_metadata",
]
