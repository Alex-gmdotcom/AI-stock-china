# -*- coding: utf-8 -*-
# dump_source.py  —  收集数据层源码 + 复测 605788，统一写入一个 UTF-8 文件回传
import os, sys, io, glob, datetime, traceback

ROOT = sys.argv[1] if len(sys.argv) > 1 else r"E:\AI-tool\Stock\ai-hedge-fund"
if not os.path.isdir(ROOT):
    ROOT = os.getcwd()
SRC = os.path.join(ROOT, "src")
OUT = os.path.join(ROOT, "aihf_source_dump.txt")

buf = io.StringIO()
def w(s=""):
    buf.write(str(s) + "\n")

def dump_path(abspath):
    try:
        with open(abspath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        content = "<<read error: %r>>" % e
    w("=" * 72)
    w("FILE: " + os.path.relpath(abspath, ROOT))
    w("=" * 72)
    w(content)
    w()

w("#### AIHF SOURCE DUMP ####")
w("time: " + datetime.datetime.now().isoformat())
w("python: " + sys.version.replace("\n", " "))
w("ROOT: " + ROOT)
try:
    import baostock as bs
    w("baostock file: " + getattr(bs, "__file__", "?"))
except Exception as e:
    bs = None
    w("baostock import error: %r" % e)

# ---- src 目录树 ----
w("\n#### SRC TREE ####")
if os.path.isdir(SRC):
    for dp, dn, fn in os.walk(SRC):
        dn[:] = [d for d in dn if d != "__pycache__"]
        if "__pycache__" in dp:
            continue
        w("[DIR] " + os.path.relpath(dp, ROOT))
        for name in sorted(fn):
            if name.endswith(".py"):
                w("    " + name)
else:
    w("NO src dir at: " + SRC)

# ---- 全量 dump src/data 与 src/tools 下所有 .py ----
w("\n#### FULL DUMP: src/data + src/tools ####")
dumped = set()
for sub in ["data", "tools"]:
    d = os.path.join(SRC, sub)
    for path in sorted(glob.glob(os.path.join(d, "**", "*.py"), recursive=True)):
        ap = os.path.abspath(path)
        if ap in dumped or "__pycache__" in ap:
            continue
        dumped.add(ap)
        dump_path(ap)
# 兜底：万一 models.py / api.py 不在上面两目录，按文件名再 glob 一次
for base in ["models.py", "api.py", "cache.py"]:
    for path in glob.glob(os.path.join(SRC, "**", base), recursive=True):
        ap = os.path.abspath(path)
        if ap not in dumped and "__pycache__" not in ap:
            dumped.add(ap); dump_path(ap)

# ---- get_line_items 调用点 + 请求的字段名全集 ----
w("\n#### get_line_items CALL SITES ####")
if os.path.isdir(SRC):
    for path in sorted(glob.glob(os.path.join(SRC, "**", "*.py"), recursive=True)):
        if "__pycache__" in path:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            continue
        for i, ln in enumerate(lines):
            if "get_line_items" in ln:
                depth = 0
                w("----- %s : line %d -----" % (os.path.relpath(path, ROOT), i + 1))
                for j in range(i, min(i + 30, len(lines))):
                    w(lines[j].rstrip("\n"))
                    depth += lines[j].count("(") - lines[j].count(")")
                    if j > i and depth <= 0:
                        break
                w()

# ---- 605788 复测：定位 0 行真因 ----
w("\n#### 605788 RE-PROBE ####")
if bs is not None:
    try:
        lg = bs.login()
        w("login: code=%s msg=%s" % (lg.error_code, lg.error_msg))
        FULL = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM"
        MIN  = "date,code,open,high,low,close,preclose,volume,amount,pctChg"
        tests = [
            ("FULL/adj2/近半年", FULL, "2", "2026-01-01", "2026-06-20"),
            ("MIN /adj2/近半年", MIN,  "2", "2026-01-01", "2026-06-20"),
            ("MIN /adj3/近半年", MIN,  "3", "2026-01-01", "2026-06-20"),
            ("MIN /adj3/超宽窗", MIN,  "3", "2023-01-01", "2026-06-20"),
        ]
        for label, fields, adj, sd, ed in tests:
            rs = bs.query_history_k_data_plus("sh.605788", fields,
                    start_date=sd, end_date=ed, frequency="d", adjustflag=adj)
            cnt = 0; first = None; last = None
            while (rs.error_code == "0") and rs.next():
                row = rs.get_row_data(); cnt += 1
                if first is None: first = row
                last = row
            w("[%s] err=%s msg=%s rows=%d" % (label, rs.error_code, rs.error_msg, cnt))
            if first: w("   first=%s" % first)
            if last:  w("   last =%s" % last)
        # 基本面信息（是否退市/停牌）
        try:
            rb = bs.query_stock_basic(code="sh.605788")
            rows = []
            while (rb.error_code == "0") and rb.next():
                rows.append(rb.get_row_data())
            w("stock_basic fields: %s" % rb.fields)
            w("stock_basic rows : %s" % rows)
        except Exception:
            w("stock_basic err:\n" + traceback.format_exc())
        bs.logout()
    except Exception:
        w("reprobe exception:\n" + traceback.format_exc())
else:
    w("baostock 不可用，跳过复测")

with open(OUT, "w", encoding="utf-8") as f:
    f.write(buf.getvalue())
print("WROTE:", OUT)
print("Bytes:", os.path.getsize(OUT))
print(">>> 请把 aihf_source_dump.txt 上传回来 <<<")
