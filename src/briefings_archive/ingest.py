"""
briefings_archive/ingest.py — 早晚报 ingest pipeline

v1.0.0 (2026-06-18, Phase 2 Step 2)

抽取策略 (best-effort,失败字段留 None):
  - date     从 "📅 YYYY 年 MM 月 DD 日" 抓取
  - type     从"早盘策略推演" / "盘后深度复盘" / "周复盘" 等关键词识别
  - score    从 "B 级 (69/100)" / "B+(79)" / 标准化 69/100 等抓取
  - faults   "C 类 - 信源盲区" / "A 类正向超预期" 等
  - tickers  "T1 中际旭创" / "N3" / "V1-V5" 等代号 + 后接的中文名
  - patterns "量能感知失灵 ⚠️ 已触发" 等
  - cocoons  "茧房信号 ①...②...③..."
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

try:
    from .schema import Briefing, BriefingMetadata, BriefingType  # type: ignore
except ImportError:
    try:
        from briefings_archive.schema import (  # type: ignore
            Briefing, BriefingMetadata, BriefingType,
        )
    except ImportError:
        from src.briefings_archive.schema import (  # type: ignore
            Briefing, BriefingMetadata, BriefingType,
        )

__version__ = "1.0.0"

# ---------------------------------------------------------------------------
# 正则
# ---------------------------------------------------------------------------

# 日期: "📅 2026 年 6 月 18 日" / "2026-06-18" / "2026/06/18"
_RE_DATE_CN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_RE_DATE_ISO = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")

# 报告类型识别
_TYPE_KEYWORDS = {
    BriefingType.MORNING: ["早盘策略", "早报", "盘前", "早盘推演", "08:30 发布", "08:00 发布"],
    BriefingType.EVENING: ["盘后深度", "晚报", "盘后复盘", "16:00 发布", "15:30 发布"],
    BriefingType.WEEKLY: ["周复盘", "周度元审计", "每周复盘", "周报"],
}

# 评分: "B级 (69/100)" / "B+ (79)" / "标准化 69/100" / "标准化至 100 分: 69"
_RE_GRADE = re.compile(r"([A-DF][+\-]?)\s*[级]?\s*\(?\s*(\d{1,3})\s*[/／]\s*100\)?")
# 兜底: 单独得分 "标准化 69/100" 形态
_RE_GRADE_LOOSE = re.compile(r"标准化\s*[至到]?\s*(?:100\s*分)?\s*[:：]?\s*(\d{1,3})")
# 还有: "综合得分 83/120 (标准化 69/100)"
_RE_GRADE_NORM = re.compile(r"标准化\s*(\d{1,3})\s*/\s*100")

# 等级: "B 级" / "A-" / "B+"
_RE_GRADE_ONLY = re.compile(r"([A-DF][+\-]?)\s*级")

# 失分类型: "C类-信源盲区" / "C 类 - 信源盲区" / "A 类正向超预期"
_RE_FAULT = re.compile(r"([A-D]\s*类\s*[—\-]?\s*[^\s|│、\n]{2,12})")

# 自选股代号: T1/T2/V1-V5/N1-N5/W1 等(单个代号或带短名)
# 也匹配 "・T1 中际旭创" 这种格式
_RE_TICKER_CODE = re.compile(r"([TVNWS]\d+)(?:[ ·　]+([\u4e00-\u9fa5A-Za-z0-9]{2,10}))?")

# 模式告警 - 触发标识
_RE_PATTERN_TRIGGERED = re.compile(
    r"([\u4e00-\u9fa5]{2,10}(?:失灵|钝化|疲劳|盲区|衰竭|警告|预警|触发))"
)

# 茧房信号 (匹配数字编号后跟空格再跟中文短语)
_RE_COCOON = re.compile(r"[①②③④⑤]\s*([^①②③④⑤\n]{6,80})")


# ---------------------------------------------------------------------------
# Metadata 抽取
# ---------------------------------------------------------------------------

def _extract_date(text: str) -> Optional[date]:
    m = _RE_DATE_CN.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = _RE_DATE_ISO.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _extract_type(text: str) -> BriefingType:
    # 先看头 500 字符 (标题/副标题区)
    head = text[:500]
    for btype, keywords in _TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in head:
                return btype
    # 兜底再扫全文
    for btype, keywords in _TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return btype
    return BriefingType.UNKNOWN


def _extract_score(text: str) -> tuple[Optional[str], Optional[int]]:
    """提取 (等级, 标准化分值)."""
    grade: Optional[str] = None
    value: Optional[int] = None

    # 优先: 完整形态 "B 级 (69/100)"
    m = _RE_GRADE.search(text)
    if m:
        grade = m.group(1).replace(" ", "")
        try:
            v = int(m.group(2))
            if 0 <= v <= 100:
                value = v
        except ValueError:
            pass

    # 没拿到 value, 试 "标准化 69/100"
    if value is None:
        m = _RE_GRADE_NORM.search(text)
        if m:
            try:
                v = int(m.group(1))
                if 0 <= v <= 100:
                    value = v
            except ValueError:
                pass

    # 没拿到 grade, 试单独"B 级"形态
    if grade is None:
        m = _RE_GRADE_ONLY.search(text)
        if m:
            grade = m.group(1).replace(" ", "")

    return grade, value


def _extract_faults(text: str) -> list[str]:
    raw = _RE_FAULT.findall(text)
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for f in raw:
        # 规范化空白
        norm = re.sub(r"\s+", "", f)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out[:10]  # 不要太多


def _extract_tickers(text: str) -> list[str]:
    """抽取自选股: 既有代号 (T1/V2/N3),也有中文名 (中际旭创/新易盛).

    返回去重保序的混合列表.
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in _RE_TICKER_CODE.finditer(text):
        code = m.group(1)
        name = m.group(2)
        if code and code not in seen:
            seen.add(code)
            out.append(code)
        if name and name not in seen and not name.isdigit():
            # 跳过纯数字名 (避免 "T1 5" 这种伪匹配)
            if any("\u4e00" <= ch <= "\u9fa5" for ch in name) or len(name) >= 3:
                seen.add(name)
                out.append(name)
    return out[:30]


def _extract_patterns(text: str) -> list[str]:
    raw = _RE_PATTERN_TRIGGERED.findall(text)
    seen: set[str] = set()
    out: list[str] = []
    for p in raw:
        norm = re.sub(r"\s+", "", p)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out[:10]


def _extract_cocoons(text: str) -> list[str]:
    """抽 ① ② ③ ④ ⑤ 编号后的茧房信号短句."""
    out: list[str] = []
    seen: set[str] = set()
    # 优先在 "茧房信号" / "茧房捕捉" / "茧房" 字样后的窗口里找
    cocoon_marker = re.search(r"茧房", text)
    if cocoon_marker:
        window = text[cocoon_marker.start(): cocoon_marker.start() + 2000]
        for m in _RE_COCOON.finditer(window):
            s = m.group(1).strip()
            # 砍尾部修饰符
            s = re.sub(r"[—。\.\s]+$", "", s)
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out[:5]


def _extract_subtitle(text: str) -> Optional[str]:
    """副标题通常在第一行括号里或第二行,例 (FOMC 决议日・沃什鹰派首秀・...)."""
    head = text[:1000]
    m = re.search(r"[（(]([^）)]{10,200})[）)]", head)
    if m:
        return m.group(1).strip()
    return None


def extract_metadata(text: str) -> BriefingMetadata:
    """对外暴露的总抽取函数. 任何子项失败都返回默认值 / 空列表."""
    grade, value = _extract_score(text)
    return BriefingMetadata(
        score_grade=grade,
        score_value=value,
        fault_categories=_extract_faults(text),
        tickers_mentioned=_extract_tickers(text),
        pattern_alerts=_extract_patterns(text),
        cocoon_signals=_extract_cocoons(text),
        title_subtitle=_extract_subtitle(text),
    )


# ---------------------------------------------------------------------------
# 入库入口
# ---------------------------------------------------------------------------

def _make_briefing_id(d: date, btype: BriefingType) -> str:
    return f"{d.isoformat()}_{btype.value}"


def ingest_briefing_text(
    raw_text: str,
    *,
    briefing_date: Optional[date] = None,
    briefing_type: Optional[BriefingType] = None,
    source: str = "openclaw_paste",
    source_path: Optional[str] = None,
) -> Briefing:
    """从文本入库 (briefing_date / briefing_type 可显式指定覆盖自动抽取).

    Args:
        raw_text: 早晚报原文 markdown / 半结构化文本.
        briefing_date: 显式日期. None 则自动从正文抽 "📅 YYYY 年 MM 月 DD 日".
        briefing_type: 显式类型. None 则自动识别.
        source: 来源标签.
        source_path: 若来自文件,记录原路径.

    Returns:
        构造好的 Briefing 对象.如果连日期都抽不到,用今天.
    """
    metadata = extract_metadata(raw_text)
    d = briefing_date or _extract_date(raw_text) or date.today()
    t = briefing_type or _extract_type(raw_text)
    return Briefing(
        briefing_id=_make_briefing_id(d, t),
        briefing_type=t,
        briefing_date=d,
        raw_content=raw_text,
        metadata=metadata,
        ingested_at=datetime.now(),
        source=source,
        source_path=source_path,
    )


def ingest_briefing(
    path: Union[str, Path],
    *,
    briefing_date: Optional[date] = None,
    briefing_type: Optional[BriefingType] = None,
    source: str = "openclaw_paste",
) -> Briefing:
    """从文件入库 (.txt / .md 均可).

    Args:
        path: 早晚报文件路径 (UTF-8).
    """
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    return ingest_briefing_text(
        raw,
        briefing_date=briefing_date,
        briefing_type=briefing_type,
        source=source,
        source_path=str(p.resolve()),
    )


# ---------------------------------------------------------------------------
# Self-test (基于真实早晚报样本验证抽取)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os, sys

    print(f"[briefings_archive.ingest v{__version__}] self-test")
    failures: list[str] = []

    # ── T1: 早报抽取
    morning_text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📅 2026 年 6 月 18 日 星期四 早盘策略推演・08:30 发布\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🧠 零、模式感知层\n"
        "[模式追踪] 近 5 日偏差：B (74)/B (74)/B (74)/B (74)/ 本日待评\n"
        "[量能判断] 前日成交 3.09 万亿(连续 4 日 > 3 万亿)\n"
        "[主线疲劳警告] PCB/CPO 运行 4 日接近疲劳阈值\n"
        "🌍 一、宏观情绪定调\n"
        "・T1 中际旭创：维持⭐⭐⭐⭐\n"
        "・T5 安集科技：⭐⭐⭐⭐⭐\n"
    )
    b1 = ingest_briefing_text(morning_text)
    if b1.briefing_type != BriefingType.MORNING:
        failures.append(f"T1.type: 期望 morning 得 {b1.briefing_type}")
    if b1.briefing_date != date(2026, 6, 18):
        failures.append(f"T1.date: 期望 2026-06-18 得 {b1.briefing_date}")
    if b1.briefing_id != "2026-06-18_morning":
        failures.append(f"T1.id: 期望 2026-06-18_morning 得 {b1.briefing_id}")
    if "T1" not in b1.metadata.tickers_mentioned:
        failures.append(f"T1.tickers: T1 缺失 — {b1.metadata.tickers_mentioned}")
    if "中际旭创" not in b1.metadata.tickers_mentioned:
        failures.append(f"T1.tickers: 中际旭创 缺失 — {b1.metadata.tickers_mentioned}")
    if not any("疲劳" in p or "警告" in p for p in b1.metadata.pattern_alerts):
        failures.append(f"T1.patterns: 主线疲劳警告 缺失 — {b1.metadata.pattern_alerts}")
    if not failures:
        print(f"[T1] morning briefing extract: PASS "
              f"(date={b1.briefing_date}, type={b1.briefing_type.value}, "
              f"tickers={len(b1.metadata.tickers_mentioned)}, "
              f"patterns={len(b1.metadata.pattern_alerts)})")

    # ── T2: 晚报抽取
    evening_text = (
        "📅 2026 年 6 月 18 日 星期四 盘后深度复盘・16:00 发布\n"
        "📊 一、收盘真实体温计\n"
        "・上证 -0.43% | 深证 +0.94% | 创业板 +2.05%\n"
        "🔬 三、核心偏差归因 —— B 级 (69/100)\n"
        "✅ 科技方向判断连续第 4 日命中\n"
        "❌ 核心漏判：陆家嘴论坛吴清 \"全力拥抱科技\" 信号完全遗漏\n"
        "失分项 | 性质 | 说明\n"
        "中际旭创涨幅严重低估 | C类-信源盲区 | 陆家嘴论坛信号未前瞻\n"
        "科创50连续两日+8.53% | A类正向超预期 | 方向判断正确\n"
        "・量能感知失灵 ⚠️ 已触发(连续 2 日预判偏低)\n"
        "・综合得分 83/120(标准化 69/100)\n"
        "📡 七、三大茧房信号\n"
        "① 中际旭创超越茅台 ——A 股历史级 \"科技换锚\" 时刻\n"
        "② 沃什鹰派后 A 股暴涨 —— 跨市场背离再次确认\n"
        "③ 中际旭创连续缩量暴涨 —— 卖方惜售的 \"新股王模式\"\n"
    )
    pre_count = len(failures)
    b2 = ingest_briefing_text(evening_text)
    if b2.briefing_type != BriefingType.EVENING:
        failures.append(f"T2.type: 期望 evening 得 {b2.briefing_type}")
    if b2.metadata.score_grade not in ("B", "B级"):
        failures.append(f"T2.grade: 期望 B 得 {b2.metadata.score_grade!r}")
    if b2.metadata.score_value != 69:
        failures.append(f"T2.value: 期望 69 得 {b2.metadata.score_value}")
    # 至少 2 类失分类型被抽到
    if len(b2.metadata.fault_categories) < 2:
        failures.append(f"T2.faults: 期望 >=2 得 {b2.metadata.fault_categories}")
    if not any("信源盲区" in f for f in b2.metadata.fault_categories):
        failures.append(f"T2.faults: 信源盲区 缺失 — {b2.metadata.fault_categories}")
    # 至少抽到 1 个茧房信号
    if len(b2.metadata.cocoon_signals) < 2:
        failures.append(f"T2.cocoons: 期望 >=2 得 {b2.metadata.cocoon_signals}")
    if not any("失灵" in p for p in b2.metadata.pattern_alerts):
        failures.append(f"T2.patterns: 量能感知失灵 缺失 — {b2.metadata.pattern_alerts}")
    if len(failures) == pre_count:
        print(f"[T2] evening briefing extract: PASS "
              f"(grade={b2.metadata.score_grade}/{b2.metadata.score_value}, "
              f"faults={len(b2.metadata.fault_categories)}, "
              f"cocoons={len(b2.metadata.cocoon_signals)})")

    # ── T3: 真早报 / 晚报样本回归测试 (如果 sandbox 里有)
    uploads = Path("/mnt/user-data/uploads")
    real_morning = uploads / "早报.txt"
    real_evening = uploads / "晚报.txt"

    if real_morning.exists():
        rb = ingest_briefing(real_morning)
        if rb.briefing_type != BriefingType.MORNING:
            failures.append(f"T3.morning.type: {rb.briefing_type}")
        if rb.briefing_date != date(2026, 6, 18):
            failures.append(f"T3.morning.date: {rb.briefing_date}")
        if len(rb.raw_content) < 100:
            failures.append(f"T3.morning.raw_content too short")
        if not any("T1" in t or "T5" in t for t in rb.metadata.tickers_mentioned):
            failures.append(f"T3.morning.tickers: T1/T5 都未抽到 — {rb.metadata.tickers_mentioned}")
        if len([f for f in failures if "T3.morning" in f]) == 0:
            print(f"[T3.morning] real 早报.txt: PASS "
                  f"(raw={len(rb.raw_content)} chars, "
                  f"tickers={rb.metadata.tickers_mentioned[:5]}, "
                  f"patterns={rb.metadata.pattern_alerts[:3]})")

    if real_evening.exists():
        rb = ingest_briefing(real_evening)
        if rb.briefing_type != BriefingType.EVENING:
            failures.append(f"T3.evening.type: {rb.briefing_type}")
        if rb.briefing_date != date(2026, 6, 18):
            failures.append(f"T3.evening.date: {rb.briefing_date}")
        if rb.metadata.score_value != 69:
            failures.append(f"T3.evening.score: 期望 69 得 {rb.metadata.score_value}")
        if len([f for f in failures if "T3.evening" in f]) == 0:
            print(f"[T3.evening] real 晚报.txt: PASS "
                  f"(grade={rb.metadata.score_grade}/{rb.metadata.score_value}, "
                  f"faults={rb.metadata.fault_categories[:3]}, "
                  f"cocoons={len(rb.metadata.cocoon_signals)})")

    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for f in failures:
            print(f"  ✗ {f}")
        raise SystemExit(1)
    print(f"\n[briefings_archive.ingest v{__version__}] self-test PASS")
