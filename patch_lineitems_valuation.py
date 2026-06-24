# -*- coding: utf-8 -*-
"""
patch_lineitems_valuation.py — 修 Valuation 500(working_capital 缺失)
====================================================================
两处幂等 in-place 外科补丁(改前判 marker、改后 py_compile 自检、坏则回滚):

  [1] src/tools/line_items_china.py  (结构层,治本)
      构造 LineItem 前预置所有被请求字段为 None → 复刻上游 financialdatasets
      契约(请求字段必存在,无数据则 None)→ 根除 agent 裸访问未派生字段的
      AttributeError(不止 working_capital)。marker:[patch:preseed_requested]

  [2] src/tools/baostock_data.py     (数据层,补全)
      line_items_from_block emit working_capital = 流动资产 − 流动负债
      (数据已在手)。marker:"working_capital":

幂等、Windows 安全、优先当前目录。两处任一已打则跳过。
"""
import base64, os, sys, tempfile, py_compile

PATCHES = [
    {
        "file": "line_items_china.py",
        "skip_if_contains": "[patch:preseed_requested]",
        "anchor_b64": "ICAgICAgICBmb3IgZmllbGRfbmFtZSwgdmFsdWUgaW4gc2xvdC5pdGVtcygpOgogICAgICAgICAgICBrd2FyZ3NbZmllbGRfbmFtZV0gPSB2YWx1ZQo=",
        "repl_b64":   "ICAgICAgICAjIOmihOe9ruaJgOacieiiq+ivt+axguWtl+auteS4uiBOb25lLOWkjeWIu+S4iua4uCBmaW5hbmNpYWxkYXRhc2V0cyDlpZHnuqY6CiAgICAgICAgIyDor7fmsYLnmoTlrZfmrrXlv4XnhLblrZjlnKgo5pWw5o2u5rqQ5rKh5pyJ5YiZIE5vbmUpLOmBv+WFjSBhZ2VudCDnm7TmjqXoo7jorr/pl67mnKrmtL7nlJ8KICAgICAgICAjIOWtl+auteaXtiBBdHRyaWJ1dGVFcnJvciDmiormlbTova4gcnVuIOeCuOaOieOAgiAgW3BhdGNoOnByZXNlZWRfcmVxdWVzdGVkXQogICAgICAgIGZvciBfbGkgaW4gbGluZV9pdGVtczoKICAgICAgICAgICAgaWYgX2xpIG5vdCBpbiBrd2FyZ3M6CiAgICAgICAgICAgICAgICBrd2FyZ3NbX2xpXSA9IE5vbmUKICAgICAgICBmb3IgZmllbGRfbmFtZSwgdmFsdWUgaW4gc2xvdC5pdGVtcygpOgogICAgICAgICAgICBrd2FyZ3NbZmllbGRfbmFtZV0gPSB2YWx1ZQo=",
    },
    {
        "file": "baostock_data.py",
        "skip_if_contains": '"working_capital":',
        "anchor_b64": "ICAgICAgICAiY3VycmVudF9hc3NldHMiOiBjdXJyZW50X2Fzc2V0cywKICAgICAgICAiY3VycmVudF9saWFiaWxpdGllcyI6IGN1cnJlbnRfbGlhYmlsaXRpZXMsCiAgICAgICAgInNoYXJlaG9sZGVyc19lcXVpdHkiOiBzaGFyZWhvbGRlcnNfZXF1aXR5LAo=",
        "repl_b64":   "ICAgICAgICAiY3VycmVudF9hc3NldHMiOiBjdXJyZW50X2Fzc2V0cywKICAgICAgICAiY3VycmVudF9saWFiaWxpdGllcyI6IGN1cnJlbnRfbGlhYmlsaXRpZXMsCiAgICAgICAgIyB3b3JraW5nX2NhcGl0YWwgPSDmtYHliqjotYTkuqcg4oiSIOa1geWKqOi0n+WAuijmlbDmja7lt7LlnKjmiYss6KGlIGVtaXQ7dmFsdWF0aW9uIOijuOiuv+mXrumcgOimgeWugykKICAgICAgICAid29ya2luZ19jYXBpdGFsIjogKAogICAgICAgICAgICAoY3VycmVudF9hc3NldHMgLSBjdXJyZW50X2xpYWJpbGl0aWVzKQogICAgICAgICAgICBpZiAoY3VycmVudF9hc3NldHMgaXMgbm90IE5vbmUgYW5kIGN1cnJlbnRfbGlhYmlsaXRpZXMgaXMgbm90IE5vbmUpIGVsc2UgTm9uZQogICAgICAgICksCiAgICAgICAgInNoYXJlaG9sZGVyc19lcXVpdHkiOiBzaGFyZWhvbGRlcnNfZXF1aXR5LAo=",
    },
]


def _log(m): print("[patch_li_val] " + m)


def find_tools_dir():
    cwd = os.getcwd(); here = os.path.dirname(os.path.abspath(__file__))
    for root in (cwd, here):
        for sub in (("src", "tools"), ("tools",)):
            d = os.path.join(root, *sub)
            if os.path.exists(os.path.join(d, "line_items_china.py")):
                return d
    raise SystemExit("找不到 src/tools/line_items_china.py(请在项目根运行)")


def apply_patch(tdir, p):
    path = os.path.join(tdir, p["file"])
    if not os.path.exists(path):
        raise SystemExit("缺文件: " + path)
    src = open(path, "r", encoding="utf-8").read()
    if p["skip_if_contains"] in src:
        _log("%s 已打补丁(幂等),跳过。" % p["file"])
        return False
    anchor = base64.b64decode(p["anchor_b64"]).decode("utf-8")
    repl   = base64.b64decode(p["repl_b64"]).decode("utf-8")
    n = src.count(anchor)
    if n == 0:
        raise SystemExit("%s: 锚点未找到(文件可能已改动,请人工核对)。" % p["file"])
    if n > 1:
        raise SystemExit("%s: 锚点出现 %d 次,不唯一,放弃自动补丁。" % (p["file"], n))
    new = src.replace(anchor, repl, 1)
    fd, tmp = tempfile.mkstemp(dir=tdir, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new)
    try:
        py_compile.compile(tmp, doraise=True)
    except Exception as e:
        os.remove(tmp); raise SystemExit("%s 补丁后语法失败,已回滚: %s" % (p["file"], e))
    os.replace(tmp, path)
    _log("%s 补丁已应用。" % p["file"])
    return True


def main():
    tdir = find_tools_dir()
    _log("tools: " + tdir)
    changed = 0
    for p in PATCHES:
        if apply_patch(tdir, p):
            changed += 1
    _log("完成,应用 %d/%d 处。重启 webapp 后端让改动生效。" % (changed, len(PATCHES)))


if __name__ == "__main__":
    main()
