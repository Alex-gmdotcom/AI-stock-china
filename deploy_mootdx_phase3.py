# -*- coding: utf-8 -*-
"""
deploy_mootdx_phase3.py (v2.1) — Phase 3 价格源:pytdx 直连 TCP
============================================================
两件事(均幂等、Windows 安全、优先当前目录、写前语法自检):
  1) 写  <tools>/mootdx_data.py   —— pytdx 直连 TCP(零 httpx / 零 py_mini_racer)。
  2) 追加 <tools>/api_china.py 的 [marker:mootdx_v3] override:
     把 pytdx 源插进 DataSourceRouter 的 PRICE 链 → baostock → mootdx → akshare。

前置:`poetry add pytdx`(零冲突)。未装 pytdx → available()=False,Router 惰性跳过。
缘由:mootdx 0.11.7 依赖 httpx<0.26(与本项目 httpx^0.27 冲突)且拖回 py-mini-racer/V8
     (Phase 0 刚铲掉的崩溃源)→ 弃 wrapper,改 pytdx 底层直连。文件名沿用 phase3,
     接线脚本无需改动。
"""
import base64, os, sys, tempfile, py_compile, importlib.util

MODULE_NAME    = "mootdx_data.py"
OVERRIDE_MARKER = "[marker:mootdx_v3]"

MODULE_B64 = (
    "IyAtKi0gY29kaW5nOiB1dGYtOCAtKi0KIiIiCm1vb3RkeF9kYXRhLnB5IOKAlCBweXRkeCDnm7Tov57pgJrovr7k"
    "v6EgVENQIOS7t+agvOa6kCAoUGhhc2UgMywgUFJJQ0Ug5YWc5bqVKQo9PT09PT09PT09PT09PT09PT09PT09PT09"
    "PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0K6K6+6K6hOlBSSUNFIOWtl+autee7hOmH"
    "jCBiYW9zdG9jayDkuYvlkI7jgIFha3NoYXJlIOS5i+WJjeeahOWFnOW6lea6kOOAggotIOi1sCBweXRkeChUZHhI"
    "cV9BUEkp57qvIFRDUCDljY/orq4g4oaSIOS4jeWwgSBJUOOAgembtiBodHRweCAvIHB5X21pbmlfcmFjZXIg5L6d"
    "6LWW44CCCi0g5Y+q5pyN5YqhIFBSSUNFO25lZWRzX2NuX2lwPVRydWUo5rW35aSWIFJvdXRlciDoh6rliqjot7Po"
    "v4cp44CCCi0g6L+U5ZueKirkuI3lpI3mnYMqKuaXpee6vyjkuI4gYmFvc3RvY2sg5YmN5aSN5p2D5Y+j5b6E5LiN"
    "5ZCMIOKGkiDku4XlhZzlupUs6KeB5qih5Z2X5bC+5rOoKeOAggotIOWksei0peivreS5ieWvuem9kCBSb3V0ZXIg"
    "57qm5a6aKGRhdGFzb3VyY2Uvcm91dGVyLnB5KToKICAgICog6L+e5LiN5LiK5Lu75L2VIFREWCDmnI3liqHlmago"
    "6L+e5o6l57qn5pWF6ZqcKeKGkiByYWlzZShSb3V0ZXIg6K6w54aU5patLOmalOemu+acrOa6kCnjgIIKICAgICog"
    "5p+l5peg5pWw5o2uIC8g5LiN5pSv5oyB55qE5biC5Zy6KEhLL0JKKeKGkiDov5Tlm54gW10oUm91dGVyIOi9rOS4"
    "i+S4gOa6kCzkuI3nrpfmlYXpmpwp44CCCgrkvp3otZY6cHl0ZHgoYHBvZXRyeSBhZGQgcHl0ZHhgLOmbtuWGsueq"
    "gSnjgILmnKroo4XliJkgYXZhaWxhYmxlKCk9RmFsc2UsUm91dGVyIOi3s+i/h+OAggoK5Y+j5b6E5ZGK6K2mKOW/"
    "heivuyk6CiAgLSB2b2wg5Y2V5L2N5bey5a6e55uY5qC45a+5OnB5dGR4IOi/lOWbnioq5omLKiosYmFvc3RvY2sg"
    "5Li6KirogqEqKizlt64gMTAwIOWAjeOAggogICAg6buY6K6kIF9WT0xfU0NBTEU9MTAwKOaJi+KGkuiCoSnlr7np"
    "vZAgYmFvc3RvY2vjgILlrp7mtYsgNjAwNTE5IDIwMjYtMDYtMTg6CiAgICBweXRkeCA1NzQ3MSDmiYsgw5cgMTAw"
    "ID0gNSw3NDcsMTAwIOKJiCBiYW9zdG9jayA1LDc0NywxNzMg6IKhKOmbtuWktOS4uiBzdWIt5omL6IiN5YWlKeOA"
    "ggogICAg5aaC6YGH5byC5bi45rqQ5Y2V5L2NLOeUqOeOr+Wig+WPmOmHjyBBSUhGX1REWF9WT0xfU0NBTEUg6KaG"
    "55uW44CCCiAgLSDku7fkuLrkuI3lpI3mnYM7YmFvc3RvY2sg5Li75rqQ5piv5YmN5aSN5p2D44CC5LuF5b2TIGJh"
    "b3N0b2NrIOeGlOaWrS/okL3nqbrml7YgbW9vdGR4IOaJjemhtuS4iiwKICAgIOi3qOa6kOaLvOaOpemCo+S4gOau"
    "teS7t+WPo+W+hOS8mui3syzlsZ7lt7LmjqXlj5fnmoTlhZzlupXlj5boiI3jgIIKIiIiCmZyb20gX19mdXR1cmVf"
    "XyBpbXBvcnQgYW5ub3RhdGlvbnMKCmltcG9ydCBvcwppbXBvcnQgdGhyZWFkaW5nCmZyb20gdHlwaW5nIGltcG9y"
    "dCBPcHRpb25hbAoKdHJ5OgogICAgZnJvbSBweXRkeC5ocSBpbXBvcnQgVGR4SHFfQVBJCiAgICBfSEFWRV9QWVRE"
    "WCA9IFRydWUKZXhjZXB0IEV4Y2VwdGlvbjoKICAgIFRkeEhxX0FQSSA9IE5vbmUgICMgdHlwZTogaWdub3JlCiAg"
    "ICBfSEFWRV9QWVREWCA9IEZhbHNlCgojIOmAmui+vuS/oeaXpSBLIGNhdGVnb3J5KD0gVERYUGFyYW1zLktMSU5F"
    "X1RZUEVfUklfSykKX0NBVF9EQUlMWSA9IDkKCiMgdm9sIOe8qeaUvjpweXRkeCDov5Tlm57miYvjgIFiYW9zdG9j"
    "ayDkuLrogqEg4oaSIOm7mOiupCAxMDAo5omL4oaS6IKhLOW3suWunuebmOaguOWvuSkKX1ZPTF9TQ0FMRSA9IGZs"
    "b2F0KG9zLmVudmlyb24uZ2V0KCJBSUhGX1REWF9WT0xfU0NBTEUiLCAiMTAwIikgb3IgIjEwMCIpCgojIFREWCDo"
    "oYzmg4XmnI3liqHlmago5Y+v55SoIEFJSEZfVERYX0hPU1RTPSJpcDpwb3J0LGlwOnBvcnQiIOimhueblikKX0RF"
    "RkFVTFRfSE9TVFM6IGxpc3RbdHVwbGVbc3RyLCBpbnRdXSA9IFsKICAgICgiMTE5LjE0Ny4yMTIuODEiLCA3NzA5"
    "KSwKICAgICgiNjAuMTIuMTM2LjI1MCIsIDc3MDkpLAogICAgKCIyMTguMTA4Ljk4LjI0NCIsIDc3MDkpLAogICAg"
    "KCIxMjMuMTI1LjEwOC4xNCIsIDc3MDkpLAogICAgKCIxODAuMTUzLjE4LjE3MCIsIDc3MDkpLAogICAgKCIxODAu"
    "MTUzLjE4LjE3MSIsIDc3MDkpLAogICAgKCIyMDIuMTA4LjI1My4xMzAiLCA3NzA5KSwKICAgICgiNjAuMTkxLjEx"
    "Ny4xNjciLCA3NzA5KSwKXQoKX0NPTk5FQ1RfVElNRU9VVCA9IGZsb2F0KG9zLmVudmlyb24uZ2V0KCJBSUhGX1RE"
    "WF9USU1FT1VUIiwgIjQiKSBvciAiNCIpCl9NQVhfSE9TVFNfUEVSX0NBTEwgPSBpbnQob3MuZW52aXJvbi5nZXQo"
    "IkFJSEZfVERYX01BWF9IT1NUUyIsICI1Iikgb3IgIjUiKQpfTUFYX1BBR0VTID0gMTIgICAgICAgICAgIyAxMiDD"
    "lyA4MDAg4omIIDk2MDAg5qC5IOKJiCAzOCDlubQs6Laz5aSf5Lu75L2V5Zue5rWL56qX5Y+jCl9QQUdFID0gODAw"
    "ICAgICAgICAgICAgICAjIHB5dGR4IOWNleasoeS4iumZkAoKIyBweXRkeCDmr4/mrKHnlKjni6znq4sgYXBpIOWu"
    "nuS+iyjlkIToh6ogc29ja2V0KSzkvYbku43kuLLooYzljJY6CiMgICDpgb/lhY0gOSDot68gYWdlbnQg5YWo6LeM"
    "5YiwIG1vb3RkeCDml7blr7kgVERYIOacjeWKoeWZqOW5tuWPkeeMm+i/nuiiq+mZkOOAggojICAgbW9vdGR4IOaY"
    "r+eogOacieWFnOW6leOAgemdnueDrei3r+W+hCzkuLLooYzlj6/mjqXlj5fjgIIKX0xPQ0sgPSB0aHJlYWRpbmcu"
    "UkxvY2soKQoKCmNsYXNzIF9UZHhVbmF2YWlsYWJsZShSdW50aW1lRXJyb3IpOgogICAgIiIi6L+e5LiN5LiK5Lu7"
    "5L2VIFREWCDmnI3liqHlmagg4oCU4oCUIOi/nuaOpee6p+aVhemanCzkuqTnu5kgUm91dGVyIOiusOeGlOaWreOA"
    "giIiIgoKCiMg4pSA4pSAIOWvueWkluiDveWKm+aOoua1iyDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIAKZGVmIGF2YWlsYWJsZSgpIC0+IGJvb2w6CiAgICByZXR1cm4gX0hBVkVfUFlURFgKCgojIOKUgOKUgCDk"
    "u6PnoIHmmKDlsIQg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA"
    "4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmRlZiB0"
    "b190ZHgobm9ybTogc3RyKSAtPiBPcHRpb25hbFt0dXBsZVtpbnQsIHN0cl1dOgogICAgIiIiJzYwMDUxOS5TSCcg"
    "4oaSICgxLCc2MDA1MTknKTsnMDAwMzMzLlNaJyDihpIgKDAsJzAwMDMzMycpO+WFtuWugyhISy9CSinihpIgTm9u"
    "ZeOAggoKICAgIG1hcmtldDogMT3kuIrmtbcsIDA95rex5Zyz44CCQkov5YyX5Lqk5omAIHB5dGR4IOWPo+W+hOS4"
    "jeeosyDihpIg6L+U5ZueIE5vbmUg5LqkIGFrc2hhcmUg5YWc5bqVLAogICAg5LiOIGJhb3N0b2NrX2RhdGEudG9f"
    "YnNfY29kZSDlr7kgQkog55qE55+t6Lev562W55Wl5LiA6Ie044CCCiAgICAiIiIKICAgIHRyeToKICAgICAgICBj"
    "b2RlLCBzdWYgPSBub3JtLnNwbGl0KCIuIikKICAgIGV4Y2VwdCBWYWx1ZUVycm9yOgogICAgICAgIHJldHVybiBO"
    "b25lCiAgICBzdWYgPSBzdWYudXBwZXIoKQogICAgaWYgc3VmID09ICJTSCI6CiAgICAgICAgcmV0dXJuICgxLCBj"
    "b2RlKQogICAgaWYgc3VmID09ICJTWiI6CiAgICAgICAgcmV0dXJuICgwLCBjb2RlKQogICAgcmV0dXJuIE5vbmUg"
    "ICMgSEsgLyBCSiAvIOWFtuWugwoKCiMg4pSA4pSAIOacjeWKoeWZqCDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKZGVmIF9ob3N0cygpIC0+IGxpc3RbdHVwbGVbc3RyLCBpbnRd"
    "XToKICAgIGVudiA9IChvcy5lbnZpcm9uLmdldCgiQUlIRl9URFhfSE9TVFMiLCAiIikgb3IgIiIpLnN0cmlwKCkK"
    "ICAgIGlmIGVudjoKICAgICAgICBvdXQ6IGxpc3RbdHVwbGVbc3RyLCBpbnRdXSA9IFtdCiAgICAgICAgZm9yIHRv"
    "ayBpbiBlbnYuc3BsaXQoIiwiKToKICAgICAgICAgICAgdG9rID0gdG9rLnN0cmlwKCkKICAgICAgICAgICAgaWYg"
    "bm90IHRvayBvciAiOiIgbm90IGluIHRvazoKICAgICAgICAgICAgICAgIGNvbnRpbnVlCiAgICAgICAgICAgIGlw"
    "LCBfLCBwb3J0ID0gdG9rLnBhcnRpdGlvbigiOiIpCiAgICAgICAgICAgIHRyeToKICAgICAgICAgICAgICAgIG91"
    "dC5hcHBlbmQoKGlwLnN0cmlwKCksIGludChwb3J0KSkpCiAgICAgICAgICAgIGV4Y2VwdCBWYWx1ZUVycm9yOgog"
    "ICAgICAgICAgICAgICAgY29udGludWUKICAgICAgICBpZiBvdXQ6CiAgICAgICAgICAgIHJldHVybiBvdXQKICAg"
    "IHJldHVybiBsaXN0KF9ERUZBVUxUX0hPU1RTKQoKCmRlZiBfY29ubmVjdF9hbnkoYXBpKSAtPiB0dXBsZVtzdHIs"
    "IGludF06CiAgICAiIiLpgJDkuKror5Xov54s5oiQ5Yqf5Y2z6L+U5ZueIChpcCxwb3J0KTvlhajlpLHotKUg4oaS"
    "IF9UZHhVbmF2YWlsYWJsZeOAgiIiIgogICAgbGFzdCA9IE5vbmUKICAgIGZvciBpcCwgcG9ydCBpbiBfaG9zdHMo"
    "KVs6X01BWF9IT1NUU19QRVJfQ0FMTF06CiAgICAgICAgdHJ5OgogICAgICAgICAgICBpZiBhcGkuY29ubmVjdChp"
    "cCwgcG9ydCwgdGltZV9vdXQ9X0NPTk5FQ1RfVElNRU9VVCk6CiAgICAgICAgICAgICAgICByZXR1cm4gaXAsIHBv"
    "cnQKICAgICAgICBleGNlcHQgRXhjZXB0aW9uIGFzIGV4YzogICMgbm9xYTogQkxFMDAxCiAgICAgICAgICAgIGxh"
    "c3QgPSBleGMKICAgICAgICAgICAgdHJ5OgogICAgICAgICAgICAgICAgYXBpLmRpc2Nvbm5lY3QoKQogICAgICAg"
    "ICAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgICAgICAgICAgcGFzcwogICAgICAgICAgICBjb250aW51ZQog"
    "ICAgcmFpc2UgX1RkeFVuYXZhaWxhYmxlKCJhbGwgVERYIGhvc3RzIHVucmVhY2hhYmxlIChsYXN0PSVzKSIgJSAo"
    "bGFzdCwpKQoKCiMg4pSA4pSAIOWPluaVsCDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDi"
    "lIDilIDilIDilIDilIDilIDilIAKZGVmIF9mZXRjaF9yYXcobWFya2V0OiBpbnQsIGNvZGU6IHN0ciwgc3RhcnRf"
    "ZGF0ZTogc3RyKSAtPiBsaXN0W2RpY3RdOgogICAgIiIi5YiG6aG15ZCR5YmN57+7LOWPluWIsOimhuebliBzdGFy"
    "dF9kYXRlIOS4uuatojvov5Tlm57ljp/lp4sga2xpbmUgZGljdCjlt7LlkKsgX2RhdGUp44CCIiIiCiAgICBzZCA9"
    "IHN0YXJ0X2RhdGVbOjEwXQogICAgYXBpID0gVGR4SHFfQVBJKGhlYXJ0YmVhdD1UcnVlLCByYWlzZV9leGNlcHRp"
    "b249VHJ1ZSkKICAgIF9jb25uZWN0X2FueShhcGkpICAjIOi/nuS4jeS4iiDihpIgX1RkeFVuYXZhaWxhYmxlIOS4"
    "iuaKmyhSb3V0ZXIg54aU5patKQogICAgdHJ5OgogICAgICAgIGJ5X2RhdGU6IGRpY3Rbc3RyLCBkaWN0XSA9IHt9"
    "CiAgICAgICAgZm9yIHBhZ2UgaW4gcmFuZ2UoX01BWF9QQUdFUyk6CiAgICAgICAgICAgIGJhcnMgPSBhcGkuZ2V0"
    "X3NlY3VyaXR5X2JhcnMoX0NBVF9EQUlMWSwgbWFya2V0LCBjb2RlLCBwYWdlICogX1BBR0UsIF9QQUdFKQogICAg"
    "ICAgICAgICBpZiBub3QgYmFyczoKICAgICAgICAgICAgICAgIGJyZWFrCiAgICAgICAgICAgIHBhZ2Vfb2xkZXN0"
    "ID0gTm9uZQogICAgICAgICAgICBmb3IgYiBpbiBiYXJzOgogICAgICAgICAgICAgICAgZCA9ICIlMDRkLSUwMmQt"
    "JTAyZCIgJSAoaW50KGJbInllYXIiXSksIGludChiWyJtb250aCJdKSwgaW50KGJbImRheSJdKSkKICAgICAgICAg"
    "ICAgICAgIGJbIl9kYXRlIl0gPSBkCiAgICAgICAgICAgICAgICBieV9kYXRlW2RdID0gYiAgIyDljrvph40o6Leo"
    "6aG16Iul5pyJ6YeN5Y+gLOWQjuWGmeimhueblikKICAgICAgICAgICAgICAgIGlmIHBhZ2Vfb2xkZXN0IGlzIE5v"
    "bmUgb3IgZCA8IHBhZ2Vfb2xkZXN0OgogICAgICAgICAgICAgICAgICAgIHBhZ2Vfb2xkZXN0ID0gZAogICAgICAg"
    "ICAgICBpZiBwYWdlX29sZGVzdCBpcyBub3QgTm9uZSBhbmQgcGFnZV9vbGRlc3QgPD0gc2Q6CiAgICAgICAgICAg"
    "ICAgICBicmVhawogICAgICAgICAgICBpZiBsZW4oYmFycykgPCBfUEFHRToKICAgICAgICAgICAgICAgIGJyZWFr"
    "CiAgICAgICAgcmV0dXJuIGxpc3QoYnlfZGF0ZS52YWx1ZXMoKSkKICAgIGZpbmFsbHk6CiAgICAgICAgdHJ5Ogog"
    "ICAgICAgICAgICBhcGkuZGlzY29ubmVjdCgpCiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAg"
    "cGFzcwoKCmRlZiBnZXRfcHJpY2VzX2RpY3RzKG5vcm06IHN0ciwgc3RhcnRfZGF0ZTogc3RyLCBlbmRfZGF0ZTog"
    "c3RyKSAtPiBsaXN0W2RpY3RdOgogICAgIiIi4oaSIGxpc3Rbe3RpbWUsb3BlbixjbG9zZSxoaWdoLGxvdyx2b2x1"
    "bWV9XSzkuI4gYmFvc3RvY2tfZGF0YS5nZXRfcHJpY2VzX2RpY3RzIOWQjOW9ouOAggoKICAgIOS4jeaUr+aMgeea"
    "hOelqChISy9CSi/op6PmnpDlpLHotKUp4oaSIFtdO+afpeaXoOaVsOaNriDihpIgW1076L+e5o6l57qn5pWF6Zqc"
    "IOKGkiByYWlzZeOAggogICAgIiIiCiAgICBpZiBub3QgX0hBVkVfUFlURFg6CiAgICAgICAgcmV0dXJuIFtdCiAg"
    "ICBtYyA9IHRvX3RkeChub3JtKQogICAgaWYgbWMgaXMgTm9uZToKICAgICAgICByZXR1cm4gW10KICAgIG1hcmtl"
    "dCwgY29kZSA9IG1jCiAgICBzZCwgZWQgPSBzdGFydF9kYXRlWzoxMF0sIGVuZF9kYXRlWzoxMF0KICAgIHdpdGgg"
    "X0xPQ0s6CiAgICAgICAgcmF3ID0gX2ZldGNoX3JhdyhtYXJrZXQsIGNvZGUsIHN0YXJ0X2RhdGUpCiAgICBvdXQ6"
    "IGxpc3RbZGljdF0gPSBbXQogICAgZm9yIGIgaW4gcmF3OgogICAgICAgIGQgPSBiLmdldCgiX2RhdGUiLCAiIikK"
    "ICAgICAgICBpZiBub3QgKHNkIDw9IGQgPD0gZWQpOgogICAgICAgICAgICBjb250aW51ZQogICAgICAgIG91dC5h"
    "cHBlbmQoewogICAgICAgICAgICAidGltZSI6IGQsCiAgICAgICAgICAgICJvcGVuIjogZmxvYXQoYi5nZXQoIm9w"
    "ZW4iKSBvciAwLjApLAogICAgICAgICAgICAiY2xvc2UiOiBmbG9hdChiLmdldCgiY2xvc2UiKSBvciAwLjApLAog"
    "ICAgICAgICAgICAiaGlnaCI6IGZsb2F0KGIuZ2V0KCJoaWdoIikgb3IgMC4wKSwKICAgICAgICAgICAgImxvdyI6"
    "IGZsb2F0KGIuZ2V0KCJsb3ciKSBvciAwLjApLAogICAgICAgICAgICAidm9sdW1lIjogaW50KChmbG9hdChiLmdl"
    "dCgidm9sIikgb3IgMC4wKSkgKiBfVk9MX1NDQUxFKSwKICAgICAgICB9KQogICAgb3V0LnNvcnQoa2V5PWxhbWJk"
    "YSB4OiB4WyJ0aW1lIl0pCiAgICByZXR1cm4gb3V0CgoKIyDilIDilIAg6Ieq5rWLIOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKU"
    "gOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgAppZiBfX25hbWVfXyA9PSAiX19tYWlu"
    "X18iOgogICAgcHJpbnQoIlttb290ZHhfZGF0YV0gYXZhaWxhYmxlKHB5dGR4IGluc3RhbGxlZCk6IiwgYXZhaWxh"
    "YmxlKCkpCiAgICBwcmludCgiW21vb3RkeF9kYXRhXSB0b190ZHgoJzYwMDUxOS5TSCcpID0iLCB0b190ZHgoIjYw"
    "MDUxOS5TSCIpKQogICAgcHJpbnQoIlttb290ZHhfZGF0YV0gdG9fdGR4KCcwMDAzMzMuU1onKSA9IiwgdG9fdGR4"
    "KCIwMDAzMzMuU1oiKSkKICAgIHByaW50KCJbbW9vdGR4X2RhdGFdIHRvX3RkeCgnMDk4ODAuSEsnKSAgPSIsIHRv"
    "X3RkeCgiMDk4ODAuSEsiKSkKICAgIHByaW50KCJbbW9vdGR4X2RhdGFdIHRvX3RkeCgnNDMwMDQ3LkJKJykgPSIs"
    "IHRvX3RkeCgiNDMwMDQ3LkJKIikpCiAgICBpZiBhdmFpbGFibGUoKToKICAgICAgICB0cnk6CiAgICAgICAgICAg"
    "IHJvd3MgPSBnZXRfcHJpY2VzX2RpY3RzKCI2MDA1MTkuU0giLCAiMjAyNi0wNi0wMSIsICIyMDI2LTA2LTE4IikK"
    "ICAgICAgICAgICAgcHJpbnQoIlttb290ZHhfZGF0YV0gNjAwNTE5IHJvd3M6IiwgbGVuKHJvd3MpKQogICAgICAg"
    "ICAgICBpZiByb3dzOgogICAgICAgICAgICAgICAgcHJpbnQoIiAgICAgICAgICAgICAgZmlyc3Q6Iiwgcm93c1sw"
    "XSkKICAgICAgICAgICAgICAgIHByaW50KCIgICAgICAgICAgICAgIGxhc3QgOiIsIHJvd3NbLTFdKQogICAgICAg"
    "IGV4Y2VwdCBFeGNlcHRpb24gYXMgZXhjOiAgIyBub3FhOiBCTEUwMDEKICAgICAgICAgICAgcHJpbnQoIlttb290"
    "ZHhfZGF0YV0gbGl2ZSBmZXRjaCBmYWlsZWQgKOacjeWKoeWZqC/nvZHnu5wpOiIsIGV4YykK"
)

OVERRIDE_B64 = (
    "CgojIG1vb3RkeC9weXRkeCDku7fmoLzmupAgKFBoYXNlIDMsIHB5dGR4IOebtOi/niBUQ1AsIHYyLjAuMCwgMjAy"
    "Ni0wNi0yMykgIFttYXJrZXI6bW9vdGR4X3YzXQojIC0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0t"
    "LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLQojIOWcqOaXouaciSBEYXRhU291cmNlUm91dGVyKGRh"
    "dGFzb3VyY2VfdjEp55qEIFBSSUNFIOmTvumHjCzmioogcHl0ZHgg55u06L+e5rqQ5o+S5YiwCiMgYmFvc3RvY2sg"
    "5LmL5ZCO44CBYWtzaGFyZSDkuYvliY0g4oaSIGJhb3N0b2NrIOKGkiBtb290ZHgg4oaSIGFrc2hhcmXjgIIKIyDl"
    "j6rmnI3liqEgUFJJQ0U7bmVlZHNfY25faXA9VHJ1ZSjmtbflpJYgUm91dGVyIOiHquWKqOi3s+i/hyk75LiN5aSN"
    "5p2DLOS7heWFnOW6leOAggojIOWkjeeUqCBkYXRhc291cmNlX3YxIOW3suaehOW7uueahCBfcm91dGVyKOS4jemH"
    "jeWumuS5iSBnZXRfcHJpY2VzIOKAlOKAlCDlroPlt7Lnu4/otbAgX3JvdXRlci5yZXNvbHZlKeOAggojIOW5guet"
    "iTptYXJrZXIg5bey5a2Y5Zyo5YiZ5LiN6YeN5aSN5a6J6KOFO+aXoCBkYXRhc291cmNlX3YxIC8g5pyq6KOFIHB5"
    "dGR4IOKGkiDmg7DmgKfot7Pov4fjgIIKIyA9PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09"
    "PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0KdHJ5OgogICAgZnJvbSB0b29scyBpbXBvcnQgbW9vdGR4X2Rh"
    "dGEgYXMgX21keApleGNlcHQgSW1wb3J0RXJyb3I6CiAgICB0cnk6CiAgICAgICAgZnJvbSBzcmMudG9vbHMgaW1w"
    "b3J0IG1vb3RkeF9kYXRhIGFzIF9tZHgKICAgIGV4Y2VwdCBJbXBvcnRFcnJvcjoKICAgICAgICBfbWR4ID0gTm9u"
    "ZQoKaWYgKF9tZHggaXMgbm90IE5vbmUKICAgICAgICBhbmQgZ2xvYmFscygpLmdldCgiX0RBVEFTT1VSQ0VfVjFf"
    "SU5TVEFMTEVEIikKICAgICAgICBhbmQgIl9yb3V0ZXIiIGluIGdsb2JhbHMoKQogICAgICAgIGFuZCBub3QgZ2xv"
    "YmFscygpLmdldCgiX01PT1REWF9WM19JTlNUQUxMRUQiKSk6CiAgICBnbG9iYWxzKClbIl9NT09URFhfVjNfSU5T"
    "VEFMTEVEIl0gPSBUcnVlCiAgICBfRkdfbSA9IGdsb2JhbHMoKVsiX0ZHIl0KICAgIF9kc19tID0gZ2xvYmFscygp"
    "WyJfZHMiXQogICAgX1RJRVJfQV9tID0gZ2xvYmFscygpLmdldCgiX1RJRVJfQSIsIF9kc19tLmJhc2UuVElFUl9B"
    "KQoKICAgIGRlZiBfbW9vdGR4X3ByaWNlcyhub3JtLCBzdGFydF9kYXRlLCBlbmRfZGF0ZSwgKiprdyk6CiAgICAg"
    "ICAgIyDov57mjqXnuqfmlYXpmpwg4oaSIOS4iuaKmyhSb3V0ZXIg6K6w54aU5pat6ZqU56a75pys5rqQKTvmn6Xm"
    "l6Av5LiN5pSv5oyBIOKGkiBbXShSb3V0ZXIg6L2s5LiL5LiA5rqQKQogICAgICAgIHJldHVybiBfbWR4LmdldF9w"
    "cmljZXNfZGljdHMobm9ybSwgc3RhcnRfZGF0ZSwgZW5kX2RhdGUpCgogICAgX3JvdXRlci5yZWdpc3RlcihfZHNf"
    "bS5DYWxsYWJsZVByb3ZpZGVyKAogICAgICAgICJtb290ZHgiLCBfVElFUl9BX20sCiAgICAgICAgW19GR19tLlBS"
    "SUNFXSwKICAgICAgICB7InByaWNlcyI6IF9tb290ZHhfcHJpY2VzfSwKICAgICAgICBhdmFpbGFibGVfZm49X21k"
    "eC5hdmFpbGFibGUsCiAgICAgICAgbmVlZHNfY25faXA9VHJ1ZSkpCgogICAgIyDmioogbW9vdGR4IOaPkuWIsCBi"
    "YW9zdG9jayDkuYvlkI4oYWtzaGFyZSDmsLjov5zlhZzlupXlnKjmnIDlkI4pCiAgICBfY3VyX2NoYWluID0gbGlz"
    "dChfcm91dGVyLmNoYWlucy5nZXQoX0ZHX20uUFJJQ0UsIFsiYmFvc3RvY2siLCAiYWtzaGFyZSJdKSkKICAgIGlm"
    "ICJtb290ZHgiIG5vdCBpbiBfY3VyX2NoYWluOgogICAgICAgIF9uZXdfY2hhaW4gPSBbXQogICAgICAgIGZvciBf"
    "bm0gaW4gX2N1cl9jaGFpbjoKICAgICAgICAgICAgX25ld19jaGFpbi5hcHBlbmQoX25tKQogICAgICAgICAgICBp"
    "ZiBfbm0gPT0gImJhb3N0b2NrIjoKICAgICAgICAgICAgICAgIF9uZXdfY2hhaW4uYXBwZW5kKCJtb290ZHgiKQog"
    "ICAgICAgIGlmICJtb290ZHgiIG5vdCBpbiBfbmV3X2NoYWluOiAgICAgICAgIyDpk77ph4zmnKzmnaXmsqEgYmFv"
    "c3RvY2sg55qE5YWc5bqV5oOF5b2iCiAgICAgICAgICAgIF9uZXdfY2hhaW4uaW5zZXJ0KDAsICJtb290ZHgiKQog"
    "ICAgICAgIF9yb3V0ZXIuc2V0X2NoYWluKF9GR19tLlBSSUNFLCBfbmV3X2NoYWluKQo="
)


def _log(msg): print("[deploy_mootdx_v2] " + msg)


def find_root():
    cwd = os.getcwd()
    here = os.path.dirname(os.path.abspath(__file__))
    for root in (cwd, here):
        if os.path.exists(os.path.join(root, "src", "tools", "api_china.py")):
            return root
        if os.path.exists(os.path.join(root, "tools", "api_china.py")):
            return root
    for root in (cwd, here):
        if os.path.exists(os.path.join(root, "pyproject.toml")):
            return root
    return cwd


def tools_dir(root):
    for sub in (("src", "tools"), ("tools",)):
        d = os.path.join(root, *sub)
        if os.path.isdir(d):
            return d
    d = os.path.join(root, "src", "tools")
    os.makedirs(d, exist_ok=True)
    return d


def _atomic_write_bytes(path, data, tdir):
    fd, tmp = tempfile.mkstemp(dir=tdir, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_module(tdir):
    path = os.path.join(tdir, MODULE_NAME)
    data = base64.b64decode(MODULE_B64)
    _atomic_write_bytes(path, data, tdir)
    py_compile.compile(path, doraise=True)  # 语法自检
    _log("写入模块: %s (%d bytes)" % (path, len(data)))
    return path


def append_override(tdir):
    path = os.path.join(tdir, "api_china.py")
    if not os.path.exists(path):
        raise SystemExit("找不到 api_china.py: %s" % path)
    src = open(path, "r", encoding="utf-8").read()
    if OVERRIDE_MARKER in src:
        _log("override 已存在(幂等),跳过追加。")
        return path, False
    block = base64.b64decode(OVERRIDE_B64).decode("utf-8")
    if not src.endswith("\n"):
        src += "\n"
    new = src + block + "\n"
    fd, tmp = tempfile.mkstemp(dir=tdir, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new)
    try:
        py_compile.compile(tmp, doraise=True)  # 追加后整体语法自检,坏则回滚
    except Exception as e:
        os.remove(tmp)
        raise SystemExit("追加后 api_china.py 语法检查失败,已回滚未改动原文件: %s" % e)
    os.replace(tmp, path)
    _log("已追加 override 到 api_china.py。")
    return path, True


def verify(module_path):
    spec = importlib.util.spec_from_file_location("mootdx_data_probe", module_path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.to_tdx("600519.SH") == (1, "600519"), "SH 映射错误"
    assert m.to_tdx("000333.SZ") == (0, "000333"), "SZ 映射错误"
    assert m.to_tdx("09880.HK") is None, "HK 应返回 None"
    assert m.to_tdx("430047.BJ") is None, "BJ 应返回 None"
    _log("模块自检通过(to_tdx 映射正确)。available(pytdx 已装)= %s" % m.available())
    if not m.available():
        _log("⚠️ pytdx 未装 → Router 会跳过 mootdx。装包: poetry add pytdx")


def main():
    root = find_root()
    tdir = tools_dir(root)
    _log("项目根: %s" % root)
    _log("tools : %s" % tdir)
    mpath = write_module(tdir)
    _path, changed = append_override(tdir)
    verify(mpath)
    _log("完成。PRICE 链现为 baostock → mootdx → akshare(pytdx 已装且国内 IP 时生效)。")
    _log("下一步:确认 poetry add pytdx 已装,重启进程让 api_china 重新 import 生效。")


if __name__ == "__main__":
    main()
