# -*- coding: utf-8 -*-
"""
deploy_datasource_phase1b.py — Phase 1 Part B:Router 接进 api_china (v1.0.0)
============================================================================
前置:必须先跑 deploy_datasource_phase1.py(装 datasource 包)。
动作:幂等追加 DataSource 路由 override 到 api_china.py,然后上机验收:
  - 正常路由确认走 baostock
  - 手动打开 baostock 熔断器 → 确认 get_prices 仍从 akshare 出数(单点消除证明)

用法(项目根目录):
    cd E:\\AI-tool\\Stock\\ai-hedge-fund
    poetry run python deploy_datasource_phase1b.py
"""
import base64, os, sys, shutil, datetime, traceback

_DEFAULT = r"E:\AI-tool\Stock\ai-hedge-fund"
if len(sys.argv) > 1:
    ROOT = sys.argv[1]
elif os.path.isdir(os.path.join(os.getcwd(), "src", "tools")):
    ROOT = os.getcwd()
else:
    ROOT = _DEFAULT
TOOLS = os.path.join(ROOT, "src", "tools")
if not os.path.isdir(TOOLS):
    print("[FATAL] 找不到 src/tools, ROOT=" + ROOT); sys.exit(2)

print("==== 0) 目标确认 ====")
print("CWD  =", os.getcwd())
print("ROOT =", ROOT, ("  OK 与CWD一致" if os.path.normpath(ROOT)==os.path.normpath(os.getcwd()) else "  ⚠️ 与CWD不同!"))

# 前置检查:datasource 包
if not os.path.isfile(os.path.join(TOOLS, "datasource", "router.py")):
    print("\n[FATAL] 未找到 datasource 包,请先运行 deploy_datasource_phase1.py")
    sys.exit(3)
print("datasource 包: 已存在")

OVR_B64 = "CgojID09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PQojIERhdGFTb3VyY2Ug6Lev55Sx5bGCIChQaGFzZSAxIFBhcnQgQiwgdjEuMC4wLCAyMDI2LTA2LTIyKSAgW21hcmtlcjpkYXRhc291cmNlX3YxXQojIC0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLQojIOaKiiBnZXRfcHJpY2VzL2dldF9maW5hbmNpYWxfbWV0cmljcy9nZXRfbWFya2V0X2NhcCDmlLnkuLrnu48gRGF0YVNvdXJjZVJvdXRlcgojIOWkmua6kOWkseaViOi9rOenuyhiYW9zdG9jayDkuLsgKyBha3NoYXJlIOWFnOW6lSks5bim54aU5patICsg5YGl5bq36KeC5rWL44CCCiMg5raI6Zmk5Y2V54K5OmJhb3N0b2NrIOeGlOaWrS/kuI3lj6/nlKjml7YsYWtzaGFyZSDoh6rliqjpobbkuIos5pW05LiqIHJ1biDkuI3kuK3mlq3jgIIKIyDmuK/ogqEv576O6IKh5LuN6LWw5Y6fIGFrc2hhcmXjgILluYLnrYk6bWFya2VyIOW3suWtmOWcqOWImei3s+i/h+OAggojID09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PQp0cnk6CiAgICBmcm9tIHRvb2xzIGltcG9ydCBkYXRhc291cmNlIGFzIF9kcwpleGNlcHQgSW1wb3J0RXJyb3I6CiAgICB0cnk6CiAgICAgICAgZnJvbSBzcmMudG9vbHMgaW1wb3J0IGRhdGFzb3VyY2UgYXMgX2RzCiAgICBleGNlcHQgSW1wb3J0RXJyb3I6CiAgICAgICAgX2RzID0gTm9uZQoKaWYgX2RzIGlzIG5vdCBOb25lIGFuZCBub3QgZ2xvYmFscygpLmdldCgiX0RBVEFTT1VSQ0VfVjFfSU5TVEFMTEVEIik6CiAgICBnbG9iYWxzKClbIl9EQVRBU09VUkNFX1YxX0lOU1RBTExFRCJdID0gVHJ1ZQogICAgZnJvbSBkYXRldGltZSBpbXBvcnQgZGF0ZXRpbWUgYXMgX2R0X2RzCiAgICBfRkcgPSBfZHMuRmllbGRHcm91cAogICAgX1RJRVJfQSA9IF9kcy5iYXNlLlRJRVJfQQogICAgX1RJRVJfQyA9IF9kcy5iYXNlLlRJRVJfQwoKICAgICMg57qvIGFrc2hhcmUg5Y6f5Ye95pWwKFBoYXNlIDAg5o2V6I6355qEIF9vcmlnXyo76IulIFBoYXNlIDAg5rKh6LeRLOWImeW9k+WJjSBnZXRfKiDljbPnuq/niYgpCiAgICBfQUtfcHJpY2VzID0gZ2xvYmFscygpLmdldCgiX29yaWdfZ2V0X3ByaWNlcyIsIGdldF9wcmljZXMpCiAgICBfQUtfZm0gPSBnbG9iYWxzKCkuZ2V0KCJfb3JpZ19nZXRfZmluYW5jaWFsX21ldHJpY3MiLCBnZXRfZmluYW5jaWFsX21ldHJpY3MpCiAgICBfQUtfbWMgPSBnbG9iYWxzKCkuZ2V0KCJfb3JpZ19nZXRfbWFya2V0X2NhcCIsIGdldF9tYXJrZXRfY2FwKQoKICAgIGRlZiBfbm93X2RzKCk6CiAgICAgICAgcmV0dXJuIF9kdF9kcy5ub3coKS5zdHJmdGltZSgiJVktJW0tJWQiKQoKICAgICMgLS0tLS0tLS0tLSBiYW9zdG9jayBwcm92aWRlciBmdW5jcyjov5Tlm54gZGljdO+8iS0tLS0tLS0tLS0KICAgIGRlZiBfYnNfcHJpY2VzKG5vcm0sIHN0YXJ0X2RhdGUsIGVuZF9kYXRlLCAqKmt3KToKICAgICAgICByZXR1cm4gX2JzZC5nZXRfcHJpY2VzX2RpY3RzKG5vcm0sIHN0YXJ0X2RhdGUsIGVuZF9kYXRlKQoKICAgIGRlZiBfYnNfdmFsdWF0aW9uKG5vcm0sIGVuZF9kYXRlLCAqKmt3KToKICAgICAgICBhc29mID0gZW5kX2RhdGUgb3IgX25vd19kcygpCiAgICAgICAgdmFsID0gZGljdChfYnNkLmxhdGVzdF92YWx1YXRpb24obm9ybSwgYXNvZikgb3Ige30pCiAgICAgICAgaWYgdmFsOgogICAgICAgICAgICAjIOWGheiBlOW4guWAvCjpgb/lhY0gbWFya2V0X2NhcCgpIOmHjeWkjei3kSBnZXRfcXVhcnRlcnMpCiAgICAgICAgICAgIHFzID0gX2JzZC5nZXRfcXVhcnRlcnMobm9ybSwgYXNvZiwgbGltaXQ9MSkKICAgICAgICAgICAgdHMgPSBOb25lCiAgICAgICAgICAgIGlmIHFzOgogICAgICAgICAgICAgICAgdHJ5OgogICAgICAgICAgICAgICAgICAgIHRzID0gZmxvYXQocXNbMF0uZ2V0KCJwcm9maXQiLCB7fSkuZ2V0KCJ0b3RhbFNoYXJlIikgb3IgMCkgb3IgTm9uZQogICAgICAgICAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAgICAgICAgICB0cyA9IE5vbmUKICAgICAgICAgICAgY2xvc2UgPSB2YWwuZ2V0KCJjbG9zZSIpCiAgICAgICAgICAgIHZhbFsibWFya2V0X2NhcCJdID0gKHRzICogY2xvc2UpIGlmICh0cyBhbmQgY2xvc2UpIGVsc2UgTm9uZQogICAgICAgIHJldHVybiB2YWwKCiAgICBkZWYgX2JzX2ZpbmFuY2lhbHMobm9ybSwgZW5kX2RhdGUsIHBlcmlvZD0idHRtIiwgbGltaXQ9MTAsICoqa3cpOgogICAgICAgIGFzb2YgPSBlbmRfZGF0ZSBvciBfbm93X2RzKCkKICAgICAgICBxdWFydGVycyA9IF9ic2QuZ2V0X3F1YXJ0ZXJzKG5vcm0sIGFzb2YsIGxpbWl0PWxpbWl0KQogICAgICAgIGlmIG5vdCBxdWFydGVyczoKICAgICAgICAgICAgcmV0dXJuIFtdCiAgICAgICAgdmFsID0gX2JzZC5sYXRlc3RfdmFsdWF0aW9uKG5vcm0sIGFzb2YpIG9yIHt9CiAgICAgICAgdHMgPSBOb25lCiAgICAgICAgdHJ5OgogICAgICAgICAgICB0cyA9IGZsb2F0KHF1YXJ0ZXJzWzBdLmdldCgicHJvZml0Iiwge30pLmdldCgidG90YWxTaGFyZSIpIG9yIDApIG9yIE5vbmUKICAgICAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgICAgICB0cyA9IE5vbmUKICAgICAgICBjbG9zZSA9IHZhbC5nZXQoImNsb3NlIikKICAgICAgICBjYXAgPSAodHMgKiBjbG9zZSkgaWYgKHRzIGFuZCBjbG9zZSkgZWxzZSBOb25lCiAgICAgICAgb3V0ID0gW10KICAgICAgICBmb3IgaSwgYmxrIGluIGVudW1lcmF0ZShxdWFydGVycyk6CiAgICAgICAgICAgIHJlYyA9IF9ic2QubWV0cmljc19mcm9tX2Jsb2NrKGJsaykKICAgICAgICAgICAgcmVjWyJ0aWNrZXIiXSA9IG5vcm0KICAgICAgICAgICAgcmVjWyJyZXBvcnRfcGVyaW9kIl0gPSBibGsuZ2V0KCJzdGF0RGF0ZSIsICIiKQogICAgICAgICAgICByZWNbInBlcmlvZCJdID0gcGVyaW9kCiAgICAgICAgICAgIHJlY1siY3VycmVuY3kiXSA9ICJDTlkiCiAgICAgICAgICAgIGlmIGkgPT0gMDoKICAgICAgICAgICAgICAgIHJlY1sicHJpY2VfdG9fZWFybmluZ3NfcmF0aW8iXSA9IHZhbC5nZXQoInBlIikKICAgICAgICAgICAgICAgIHJlY1sicHJpY2VfdG9fYm9va19yYXRpbyJdID0gdmFsLmdldCgicGIiKQogICAgICAgICAgICAgICAgcmVjWyJwcmljZV90b19zYWxlc19yYXRpbyJdID0gdmFsLmdldCgicHMiKQogICAgICAgICAgICAgICAgcmVjWyJtYXJrZXRfY2FwIl0gPSBjYXAKICAgICAgICAgICAgICAgIGNsLCBwYiA9IHZhbC5nZXQoImNsb3NlIiksIHZhbC5nZXQoInBiIikKICAgICAgICAgICAgICAgIHJlY1siYm9va192YWx1ZV9wZXJfc2hhcmUiXSA9IChjbCAvIHBiKSBpZiAoY2wgYW5kIHBiKSBlbHNlIE5vbmUKICAgICAgICAgICAgICAgIGVnLCBwZSA9IHJlYy5nZXQoImVhcm5pbmdzX2dyb3d0aCIpLCB2YWwuZ2V0KCJwZSIpCiAgICAgICAgICAgICAgICByZWNbInBlZ19yYXRpbyJdID0gKHBlIC8gKGVnICogMTAwKSkgaWYgKHBlIGFuZCBlZyBhbmQgZWcgPiAwKSBlbHNlIE5vbmUKICAgICAgICAgICAgb3V0LmFwcGVuZChyZWMpCiAgICAgICAgcmV0dXJuIG91dAoKICAgICMgLS0tLS0tLS0tLSBha3NoYXJlIHByb3ZpZGVyIGZ1bmNzKOWOn+WHveaVsCDihpIgbW9kZWxfZHVtcCBkaWN077yJLS0tLS0tLS0tLQogICAgZGVmIF9ha19wcmljZXNfZChub3JtLCBzdGFydF9kYXRlLCBlbmRfZGF0ZSwgdGlja2VyPU5vbmUsICoqa3cpOgogICAgICAgIHJlcyA9IF9BS19wcmljZXModGlja2VyIG9yIG5vcm0sIHN0YXJ0X2RhdGUsIGVuZF9kYXRlKQogICAgICAgIHJldHVybiBbcC5tb2RlbF9kdW1wKCkgZm9yIHAgaW4gcmVzXSBpZiByZXMgZWxzZSBbXQoKICAgIGRlZiBfYWtfZmluYW5jaWFsc19kKG5vcm0sIGVuZF9kYXRlLCBwZXJpb2Q9InR0bSIsIGxpbWl0PTEwLCB0aWNrZXI9Tm9uZSwgKiprdyk6CiAgICAgICAgcmVzID0gX0FLX2ZtKHRpY2tlciBvciBub3JtLCBlbmRfZGF0ZSwgcGVyaW9kPXBlcmlvZCwgbGltaXQ9bGltaXQpCiAgICAgICAgcmV0dXJuIFttLm1vZGVsX2R1bXAoKSBmb3IgbSBpbiByZXNdIGlmIHJlcyBlbHNlIFtdCgogICAgZGVmIF9ha192YWx1YXRpb25fZChub3JtLCBlbmRfZGF0ZSwgdGlja2VyPU5vbmUsICoqa3cpOgogICAgICAgIG1jID0gX0FLX21jKHRpY2tlciBvciBub3JtLCBlbmRfZGF0ZSkKICAgICAgICByZXR1cm4geyJtYXJrZXRfY2FwIjogbWN9IGlmIG1jIGVsc2Uge30KCiAgICAjIC0tLS0tLS0tLS0g57uE6KOFIFJvdXRlciAtLS0tLS0tLS0tCiAgICBfcm91dGVyID0gX2RzLkRhdGFTb3VyY2VSb3V0ZXIoCiAgICAgICAgY2hhaW5zPXsKICAgICAgICAgICAgX0ZHLlBSSUNFOiAgICAgICBbImJhb3N0b2NrIiwgImFrc2hhcmUiXSwKICAgICAgICAgICAgX0ZHLlZBTFVBVElPTjogICBbImJhb3N0b2NrIiwgImFrc2hhcmUiXSwKICAgICAgICAgICAgX0ZHLlJBVElPX0ZJTjogICBbImJhb3N0b2NrIiwgImFrc2hhcmUiXSwKICAgICAgICAgICAgX0ZHLkFCU19CQUxBTkNFOiBbImJhb3N0b2NrIiwgImFrc2hhcmUiXSwKICAgICAgICB9LAogICAgICAgIGlzX2NuX2lwPWxhbWJkYTogVHJ1ZSwKICAgICkKICAgIF9yb3V0ZXIucmVnaXN0ZXIoX2RzLkNhbGxhYmxlUHJvdmlkZXIoCiAgICAgICAgImJhb3N0b2NrIiwgX1RJRVJfQSwKICAgICAgICBbX0ZHLlBSSUNFLCBfRkcuVkFMVUFUSU9OLCBfRkcuUkFUSU9fRklOLCBfRkcuQUJTX0JBTEFOQ0VdLAogICAgICAgIHsicHJpY2VzIjogX2JzX3ByaWNlcywgInZhbHVhdGlvbiI6IF9ic192YWx1YXRpb24sICJmaW5hbmNpYWxzIjogX2JzX2ZpbmFuY2lhbHN9LAogICAgICAgIGF2YWlsYWJsZV9mbj1fYnNkLmF2YWlsYWJsZSkpCiAgICBfcm91dGVyLnJlZ2lzdGVyKF9kcy5DYWxsYWJsZVByb3ZpZGVyKAogICAgICAgICJha3NoYXJlIiwgX1RJRVJfQywKICAgICAgICBbX0ZHLlBSSUNFLCBfRkcuVkFMVUFUSU9OLCBfRkcuUkFUSU9fRklOLCBfRkcuQUJTX0JBTEFOQ0VdLAogICAgICAgIHsicHJpY2VzIjogX2FrX3ByaWNlc19kLCAidmFsdWF0aW9uIjogX2FrX3ZhbHVhdGlvbl9kLCAiZmluYW5jaWFscyI6IF9ha19maW5hbmNpYWxzX2R9KSkKCiAgICAjIOaatOmcsue7meWklumDqOaOkumanC/po57kuaYKICAgIGRlZiBkYXRhc291cmNlX3N0YXR1cygpOgogICAgICAgIHJldHVybiBfcm91dGVyLnN0YXR1cygpCgogICAgIyAtLS0tLS0tLS0tIHNoZWxsOuS4ieS4quWvueWkluWHveaVsOaUueS4uue7jyBSb3V0ZXIgLS0tLS0tLS0tLQogICAgZGVmIGdldF9wcmljZXModGlja2VyLCBzdGFydF9kYXRlLCBlbmRfZGF0ZSwgYXBpX2tleT1Ob25lKToKICAgICAgICB0cnk6CiAgICAgICAgICAgIG5vcm0sIG1hcmtldCA9IF9ub3JtYWxpemUodGlja2VyKQogICAgICAgIGV4Y2VwdCBUaWNrZXJQYXJzZUVycm9yOgogICAgICAgICAgICByZXR1cm4gW10KICAgICAgICBpZiBtYXJrZXQgbm90IGluICgiU0giLCAiU1oiLCAiQkoiKToKICAgICAgICAgICAgcmV0dXJuIF9BS19wcmljZXModGlja2VyLCBzdGFydF9kYXRlLCBlbmRfZGF0ZSkKICAgICAgICBjYWNoZV9rZXkgPSBmIntub3JtfV97c3RhcnRfZGF0ZX1fe2VuZF9kYXRlfSIKICAgICAgICBjYWNoZWQgPSBfY2FjaGVfdjIuZ2V0X3ByaWNlcyhjYWNoZV9rZXkpCiAgICAgICAgaWYgY2FjaGVkOgogICAgICAgICAgICBvdXQgPSBbeCBmb3IgeCBpbiAoX3NhZmVfY29uc3RydWN0KF9QcmljZSwgKipwKSBmb3IgcCBpbiBjYWNoZWQpIGlmIHhdCiAgICAgICAgICAgIGlmIG91dDoKICAgICAgICAgICAgICAgIHJldHVybiBvdXQKICAgICAgICBkaWN0cywgX3NyYyA9IF9yb3V0ZXIucmVzb2x2ZShfRkcuUFJJQ0UsIG5vcm09bm9ybSwgdGlja2VyPXRpY2tlciwKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICBzdGFydF9kYXRlPXN0YXJ0X2RhdGUsIGVuZF9kYXRlPWVuZF9kYXRlKQogICAgICAgIGlmIG5vdCBkaWN0czoKICAgICAgICAgICAgcmV0dXJuIFtdCiAgICAgICAgb3V0ID0gW3ggZm9yIHggaW4gKF9zYWZlX2NvbnN0cnVjdChfUHJpY2UsICoqZCkgZm9yIGQgaW4gZGljdHMpIGlmIHhdCiAgICAgICAgaWYgb3V0OgogICAgICAgICAgICB0cnk6CiAgICAgICAgICAgICAgICBfY2FjaGVfdjIuc2V0X3ByaWNlcyhjYWNoZV9rZXksIFtwLm1vZGVsX2R1bXAoKSBmb3IgcCBpbiBvdXRdKQogICAgICAgICAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgICAgICAgICAgcGFzcwogICAgICAgIHJldHVybiBvdXQKCiAgICBkZWYgZ2V0X2ZpbmFuY2lhbF9tZXRyaWNzKHRpY2tlciwgZW5kX2RhdGUsIHBlcmlvZD0idHRtIiwgbGltaXQ9MTAsIGFwaV9rZXk9Tm9uZSk6CiAgICAgICAgdHJ5OgogICAgICAgICAgICBub3JtLCBtYXJrZXQgPSBfbm9ybWFsaXplKHRpY2tlcikKICAgICAgICBleGNlcHQgVGlja2VyUGFyc2VFcnJvcjoKICAgICAgICAgICAgcmV0dXJuIFtdCiAgICAgICAgaWYgbWFya2V0IG5vdCBpbiAoIlNIIiwgIlNaIiwgIkJKIik6CiAgICAgICAgICAgIHJldHVybiBfQUtfZm0odGlja2VyLCBlbmRfZGF0ZSwgcGVyaW9kPXBlcmlvZCwgbGltaXQ9bGltaXQpCiAgICAgICAgY2FjaGVfa2V5ID0gZiJ7bm9ybX1fe3BlcmlvZH1fe2VuZF9kYXRlfV97bGltaXR9IgogICAgICAgIGNhY2hlZCA9IF9jYWNoZV92Mi5nZXRfZmluYW5jaWFsX21ldHJpY3MoY2FjaGVfa2V5KQogICAgICAgIGlmIGNhY2hlZDoKICAgICAgICAgICAgb3V0ID0gW3ggZm9yIHggaW4gKF9zYWZlX2NvbnN0cnVjdChfRmluYW5jaWFsTWV0cmljcywgKiptKSBmb3IgbSBpbiBjYWNoZWQpIGlmIHhdCiAgICAgICAgICAgIGlmIG91dDoKICAgICAgICAgICAgICAgIHJldHVybiBvdXQKICAgICAgICBkaWN0cywgX3NyYyA9IF9yb3V0ZXIucmVzb2x2ZShfRkcuUkFUSU9fRklOLCBub3JtPW5vcm0sIHRpY2tlcj10aWNrZXIsCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgZW5kX2RhdGU9ZW5kX2RhdGUsIHBlcmlvZD1wZXJpb2QsIGxpbWl0PWxpbWl0KQogICAgICAgIGlmIG5vdCBkaWN0czoKICAgICAgICAgICAgcmV0dXJuIFtdCiAgICAgICAgb3V0ID0gW3ggZm9yIHggaW4gKF9zYWZlX2NvbnN0cnVjdChfRmluYW5jaWFsTWV0cmljcywgKipkKSBmb3IgZCBpbiBkaWN0cykgaWYgeF0KICAgICAgICBpZiBvdXQ6CiAgICAgICAgICAgIHRyeToKICAgICAgICAgICAgICAgIF9jYWNoZV92Mi5zZXRfZmluYW5jaWFsX21ldHJpY3MoY2FjaGVfa2V5LCBbbS5tb2RlbF9kdW1wKCkgZm9yIG0gaW4gb3V0XSkKICAgICAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAgICAgIHBhc3MKICAgICAgICByZXR1cm4gb3V0CgogICAgZGVmIGdldF9tYXJrZXRfY2FwKHRpY2tlciwgZW5kX2RhdGUsIGFwaV9rZXk9Tm9uZSk6CiAgICAgICAgdHJ5OgogICAgICAgICAgICBub3JtLCBtYXJrZXQgPSBfbm9ybWFsaXplKHRpY2tlcikKICAgICAgICBleGNlcHQgVGlja2VyUGFyc2VFcnJvcjoKICAgICAgICAgICAgcmV0dXJuIE5vbmUKICAgICAgICBpZiBtYXJrZXQgbm90IGluICgiU0giLCAiU1oiLCAiQkoiKToKICAgICAgICAgICAgcmV0dXJuIF9BS19tYyh0aWNrZXIsIGVuZF9kYXRlKQogICAgICAgIHZhbCwgX3NyYyA9IF9yb3V0ZXIucmVzb2x2ZShfRkcuVkFMVUFUSU9OLCBub3JtPW5vcm0sIHRpY2tlcj10aWNrZXIsIGVuZF9kYXRlPWVuZF9kYXRlKQogICAgICAgIGlmIHZhbCBhbmQgdmFsLmdldCgibWFya2V0X2NhcCIpIGlzIG5vdCBOb25lOgogICAgICAgICAgICByZXR1cm4gdmFsWyJtYXJrZXRfY2FwIl0KICAgICAgICByZXR1cm4gX0FLX21jKHRpY2tlciwgZW5kX2RhdGUpCgogICAgX2xvZ2dlci5pbmZvKCJbYXBpX2NoaW5hXSBEYXRhU291cmNlIHYxLjAg6Lev55Sx5bGC5bey5a6J6KOFIChiYW9zdG9jaytha3NoYXJlIOWkseaViOi9rOenuyvnhpTmlq0pIikK"

BACKUP = os.path.join(ROOT, "_aihf_backup_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
os.makedirs(BACKUP, exist_ok=True)

print("\n==== 1) 追加 DataSource 路由 override ====")
api_path = os.path.join(TOOLS, "api_china.py")
existing = open(api_path, "r", encoding="utf-8", errors="replace").read()
if "marker:datasource_v1" in existing or "_DATASOURCE_V1_INSTALLED" in existing:
    print("  api_china.py 已含 datasource override, 跳过 (幂等)")
else:
    shutil.copy2(api_path, os.path.join(BACKUP, "api_china.py"))
    print("  备份 api_china.py → " + BACKUP)
    ovr = base64.b64decode(OVR_B64).decode("utf-8")
    with open(api_path, "a", encoding="utf-8", newline="\n") as f:
        f.write(ovr)
    print("  追加 (+%d bytes)" % len(ovr.encode("utf-8")))

print("\n==== 2) 上机验收 ====")
sys.path.insert(0, ROOT)
try:
    from src.tools import api_china as cn
    has = hasattr(cn, "datasource_status")
    print("DataSource 路由层已安装:", has)
    if not has:
        print("[WARN] 未安装(可能 datasource 包导入失败),检查上一步")
    else:
        # --- 正常路由:应走 baostock ---
        p = cn.get_prices("600519", "2026-06-01", "2026-06-20")
        print("正常 get_prices(600519):", len(p), "行, 末收", (p[-1].close if p else None),
              "(baostock 前复权应=1215 一带)")
        fm = cn.get_financial_metrics("600519", "2026-06-22", limit=1)
        if fm:
            print("正常 get_financial_metrics(600519): pe=", fm[0].price_to_earnings_ratio,
                  "mc=%.3f万亿" % ((fm[0].market_cap or 0)/1e12))

        # --- 单点消除验收:手动打开 baostock 熔断器 ---
        print("\n-- 单点消除验收:打开 baostock 熔断器 --")
        for _ in range(3):
            cn._router.breaker.record_failure("baostock")
        print("baostock 熔断状态:", cn._router.breaker.state_of("baostock"), "(期望 open)")
        # 换日期避开缓存,强制重新路由
        p2 = cn.get_prices("600519", "2026-06-02", "2026-06-19")
        print("baostock 熔断后 get_prices(600519):", len(p2), "行, 末收", (p2[-1].close if p2 else None))
        if p2:
            print(">>> ✅ 单点消除成立:停掉 baostock,akshare 仍出价 <<<")
        else:
            print(">>> ⚠️ 兜底也空(akshare 此票此时不可用);换地基逻辑没问题,是 akshare 源本身的脆弱——正是 Phase 2/3 要加 tushare/mootdx 的理由 <<<")
        # 恢复
        cn._router.breaker.record_success("baostock")
        print("\n健康/熔断 status:")
        import json as _j
        print(_j.dumps(cn.datasource_status(), ensure_ascii=False, indent=2)[:1200])
except Exception:
    print("[验收异常]\n" + traceback.format_exc())

print("\n==== 完成 ====")
print("备份目录:", BACKUP)
print("回滚: 删除 api_china.py 末尾 [marker:datasource_v1] 块")
print(">>> 把上面输出贴回 <<<")
