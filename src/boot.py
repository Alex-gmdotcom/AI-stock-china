"""
boot.py — 进程启动前置 + 版本一致性横幅

对应不变量：
    I5.1: 启动横幅必须打印关键模块的实际文件路径 + __version__
    I5.2: 每个核心模块有 __version__ 字符串
    I5.3: tools.api_bridge 必须在 agent 导入前 import

启动顺序（任何入口必须遵守）：
    1. from markets.proxy import inject_no_proxy; inject_no_proxy()
    2. import tools.api_bridge   # ★ 必须在 agents import 前
    3. from boot import boot_print_versions; boot_print_versions()
    4. from strategy.three_categories import load_pool_state
    5. 之后才能 import 其他业务模块

为什么这个顺序：
    - NO_PROXY 必须最先（在 requests 被 import 之前生效）
    - api_bridge 的 monkey-patch 必须在原版 agents import 前（不然 patch 失效）
    - boot_print_versions 是观察点，第 3 步既能看到 patch 效果又不阻塞业务

自测：
    python -m boot
"""

import importlib
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

__version__ = "v1.0.1"


# 核心模块清单（按横幅输出顺序）
# 注：Phase 1 只有 markets.proxy / boot.py 自己 / llm_text / tools.data_fallback /
# strategy.three_categories 这 5 个。其他是 Phase 2-4 的，列出来是为了让横幅一旦
# 实施完成就自动显示完整状态。当前 Phase 1 阶段，未实施的模块会显示 "(missing)"。
CORE_MODULE_NAMES = [
    # Phase 1（已实施）
    "markets.proxy",
    "llm_text",
    "tools.data_fallback",
    "strategy.three_categories",
    # Phase 2-3（待实施，先列出来防止以后忘）
    "markets.ticker",
    "markets.config",
    "tools.api_bridge",
    "tools.api_china",
    "analysis.dcf",
    "analysis.fraud_detector",
    "analysis.unlock_radar",
    "analysis.peer_compare",
    "analysis.ticker_extractor",
    # Phase 4（待实施）
    "briefings_archive.ingest",
    "hk_news.schema",
    "hk_news.ingest",
]


def _resolve_module_prefix() -> str:
    """
    自动探测 boot.py 的运行方式，推导其他模块应该用什么前缀。

    Case A: python -m src.boot (cwd=ai-hedge-fund 仓库根)
            __package__ == 'src' → 其他模块用 'src.markets.proxy' 这种
    Case B: python -m boot (cwd=ai-hedge-fund/src)
            __package__ == '' or None → 其他模块用裸 'markets.proxy'

    实际项目中只有 Case A 正确（因为 src/markets/__init__.py 用 'from src.markets.xxx'
    绝对导入，必须 cwd 在仓库根）。Case B 是开发期裸跑兼容，留着方便单文件测试。
    """
    pkg = __package__ or ""
    return f"{pkg}." if pkg else ""


_MODULE_PREFIX = _resolve_module_prefix()


def _safe_get_module(name: str) -> Any:
    """import 模块；失败返回 None（不抛）。boot 阶段宽松。

    会按 _MODULE_PREFIX 自动给 name 加前缀（如 'src.'）。
    如果带前缀加载失败，再 fallback 试裸名（兼容混合调用）。
    """
    full = f"{_MODULE_PREFIX}{name}"

    # 先看是不是已经 import 过
    for candidate in (full, name) if full != name else (name,):
        if candidate in sys.modules:
            return sys.modules[candidate]

    # 再尝试 import
    last_exc: Optional[BaseException] = None
    for candidate in (full, name) if full != name else (name,):
        try:
            return importlib.import_module(candidate)
        except ImportError as e:
            last_exc = e
            continue
        except Exception as e:
            print(f"  [warn] module {candidate} failed to import: {type(e).__name__}: {e}")
            last_exc = e
            continue
    return None


def boot_print_versions(strict: bool = False) -> List[dict]:
    """
    打印关键模块的实际文件路径 + __version__。

    Args:
        strict: True 时任一核心模块缺失就抛 RuntimeError。
                生产环境（main_china.py / web_app.py）默认 False。

    Returns:
        list of dict: 每个元素 {name, version, path, status}

    防御 Ghost Version 的关键：路径栏让 Alex 一眼看出运行的是哪份代码。
    """
    no_proxy_count = len([h for h in os.environ.get("NO_PROXY", "").split(",") if h])

    print("=" * 76)
    print(
        f"[boot] AI Hedge Fund 中国版 — 启动横幅 ({datetime.now():%Y-%m-%d %H:%M:%S})"
    )
    print(f"[boot] Python: {sys.version.split()[0]}  |  CWD: {Path.cwd()}")
    print(f"[boot] NO_PROXY hosts: {no_proxy_count}")
    print("=" * 76)
    print(f"  {'MODULE':<36s} {'VERSION':<14s} PATH")
    print("-" * 76)

    results = []
    missing = []

    for name in CORE_MODULE_NAMES:
        m = _safe_get_module(name)
        if m is None:
            row = {
                "name": name,
                "version": "(missing)",
                "path": "(not imported)",
                "status": "missing",
            }
            missing.append(name)
        else:
            row = {
                "name": name,
                "version": getattr(m, "__version__", "(no __version__)"),
                "path": str(getattr(m, "__file__", "(no __file__)")),
                "status": "ok",
            }
        results.append(row)
        print(f"  {row['name']:<36s} {row['version']:<14s} {row['path']}")

    print("=" * 76)

    if missing:
        msg = f"{len(missing)}/{len(CORE_MODULE_NAMES)} module(s) missing"
        if strict:
            raise RuntimeError(f"[boot] strict mode: {msg}: {missing}")
        else:
            print(f"[boot] WARN: {msg} (expected during Phase 1-4 implementation)")
            print(f"[boot] WARN: missing = {missing}")
    else:
        print("[boot] All core modules loaded.")

    print("=" * 76)
    return results


def _self_test() -> None:
    print("boot.py self-test\n")

    # 至少 boot 自己必须可见（虽然不在 CORE_MODULE_NAMES）
    results = boot_print_versions(strict=False)

    # 至少 markets.proxy 应该可被发现（Phase 1 同步实施）
    proxy_row = next((r for r in results if r["name"] == "markets.proxy"), None)
    if proxy_row and proxy_row["status"] == "ok":
        print(f"\n[test] PASS: markets.proxy detected ({proxy_row['version']})")
    else:
        print(f"\n[test] INFO: markets.proxy not detected (run in isolation)")

    print("[test] done.")


if __name__ == "__main__":
    _self_test()
