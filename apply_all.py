#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键打补丁 — 在新设备上按序执行全部补丁（v3.1 → v3.2 → v3.3）。

前提：本包内容已复制到仓库根目录（src/ 覆盖、patches/ 与本脚本在根目录），
且仓库已含你的 v3 中国版基础代码（briefing_generator.py 等 22 个文件）。

用法（仓库根目录）:
    poetry run python apply_all.py

行为：按序运行 patches/ 下三个补丁脚本，任一失败立即中止并提示。
每个补丁脚本自身具备：精确匹配否则不改文件、自动备份、幂等可重复运行。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

STEPS = [
    ("v3.1 数据链路修复 + fail-fast + 评分护栏",
     "patches/apply_briefing_patch.py"),
    ("v3.2 信息增强（指数温度计 + 电报/个股新闻）",
     "patches/apply_briefing_patch_v32.py"),
    ("v3.3 接口修复 + schema 硬化 + openclaw 文件桥",
     "patches/apply_v33_patch.py"),
    ("v3.4 价格多源回退 + web控制台结论层（修'No valid trade available'）",
     "patches/apply_v34_patch.py"),
]


def main():
    root = Path.cwd()
    if not (root / "src" / "strategy" / "briefing_generator.py").exists():
        sys.exit("[中止] 当前目录不是仓库根目录"
                 "（找不到 src/strategy/briefing_generator.py）")

    for title, script in STEPS:
        print(f"\n━━━ {title} ━━━")
        if not (root / script).exists():
            sys.exit(f"[中止] 找不到 {script}，请确认本包已完整复制到仓库根目录")
        ret = subprocess.run([sys.executable, script], cwd=root)
        if ret.returncode != 0:
            sys.exit(f"\n[中止] {script} 失败（见上方输出）。"
                     "未应用的文件保持原样，修复后可直接重跑本脚本（幂等）。")

    print("\n━━━ 全部补丁应用完成 ━━━")
    print("下一步（按 MIGRATION.md 第四节验证）:")
    print("  1. poetry run python doctor_china.py")
    print("  2. poetry run python src/tools/quotes_fallback.py")
    print("  3. poetry run python src/tools/news_collector.py")
    print('  4. poetry run python -c "import src.strategy.briefing_generator as bg; '
          "print(bg.__file__); print('OK' if hasattr(bg, '_snapshot_gate') else '旧版本!')\"")
    print("  5. poetry run python src/main_china.py --ticker 00148.HK"
          "   # 预期: 真实信号+结论段，不再 'No valid trade available'")


if __name__ == "__main__":
    main()
