# -*- coding: utf-8 -*-
"""smoke_step16_hotfix2.py — 双模块实例根除验证(离线,模拟 web 双路径环境)。"""
import sys, types
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))   # 模拟 python src/web_app.py 双路径

calls = {"login": 0, "logout": 0}
fake = types.ModuleType("baostock")
fake.login = lambda: (calls.__setitem__("login", calls["login"]+1),
                      types.SimpleNamespace(error_code="0", error_msg=""))[1]
fake.logout = lambda: calls.__setitem__("logout", calls["logout"]+1)
sys.modules["baostock"] = fake

import tools.baostock_data as bsd_tools      # web 进程实际在用的实例
from src.eval import data as ed

checks = []
with ed.baostock_session():
    pass
checks.append(("T1 加入 tools.* 实例(其 _logged_in=True)", bsd_tools._logged_in is True))
checks.append(("T2 未另起 src.tools 实例", "src.tools.baostock_data" not in sys.modules))
checks.append(("T3 恰一次 login 零 logout", calls == {"login": 1, "logout": 0}))
with ed.baostock_session():
    pass
checks.append(("T4 复用会话不重登", calls["login"] == 1))
for name, c in checks:
    print(("  ✅ " if c else "  ❌ ") + name)
if all(c for _, c in checks):
    print("SMOKE HOTFIX2: ALL GREEN")
else:
    sys.exit(1)
