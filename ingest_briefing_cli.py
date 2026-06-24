#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingest_briefing_cli.py — 股市早晚报入库入口（Phase 4 日报 pipeline 的命令行口）。

三种用法:
    # 1) 从文件
    poetry run python ingest_briefing_cli.py path/to/日报.md
    # 2) 从标准输入（粘贴后 Ctrl-Z 回车 / Ctrl-D）
    poetry run python ingest_briefing_cli.py -
    # 3) 直接传文本
    poetry run python ingest_briefing_cli.py --text "📅 2026年6月20日 ..."

入库后存到 briefings_archive 默认目录，可被后续 agent 检索。
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from src.briefings_archive.ingest import ingest_briefing_text, ingest_briefing
from src.briefings_archive.storage import BriefingStorage


def main() -> None:
    ap = argparse.ArgumentParser(description="股市早晚报入库")
    ap.add_argument("source", nargs="?", help="文件路径，或 '-' 表示从 stdin 读")
    ap.add_argument("--text", help="直接传入正文文本")
    ap.add_argument("--overwrite", action="store_true", help="同 id 已存在时覆盖")
    args = ap.parse_args()

    if args.text:
        briefing = ingest_briefing_text(args.text, source="cli_text")
    elif args.source == "-":
        raw = sys.stdin.read()
        if not raw.strip():
            print("[FAIL] stdin 为空"); sys.exit(1)
        briefing = ingest_briefing_text(raw, source="cli_stdin")
    elif args.source:
        briefing = ingest_briefing(args.source, source="cli_file")
    else:
        ap.print_help(); sys.exit(1)

    storage = BriefingStorage()
    storage.save(briefing, overwrite=args.overwrite)
    m = briefing.metadata
    print(f"[OK] 已入库: id={briefing.briefing_id}")
    print(f"     类型={briefing.briefing_type.value if hasattr(briefing.briefing_type,'value') else briefing.briefing_type}"
          f"  日期={briefing.briefing_date}")
    try:
        print(f"     抽取标的={getattr(m,'tickers',None)}")
    except Exception:
        pass
    print(f"     存储目录={storage.root}")


if __name__ == "__main__":
    main()
