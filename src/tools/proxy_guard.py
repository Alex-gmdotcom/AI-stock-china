"""
v3.1 代理绕行守卫 — 修复 Windows 系统代理劫持国内行情接口的问题。

背景（2026-06-11 doctor 诊断结论）：
环境变量中无代理，但 push2his.eastmoney.com / 82.push2.eastmoney.com /
17.push2.eastmoney.com 等东财子域全部 ProxyError。原因：requests 在
Windows 上会自动读取注册表里的系统代理（Clash/v2rayN 等"系统代理"模式，
不体现在环境变量中），且代理规则只放行了 push2.eastmoney.com 主域，
子域走代理节点失败 —— 这正是早晚报满屏【数据缺口】的根因。

机制：进程内注入 NO_PROXY 环境变量。requests 的代理判定中 no_proxy
优先于一切代理来源（包括 Windows 注册表代理），命中域名后缀即直连。
本机直连东财链路已验证可通（doctor: 主域直连 1.1s）。

⚠️ 局限：若代理客户端使用 TUN/虚拟网卡全局模式（透明代理），NO_PROXY
无效，必须在代理客户端规则中手动添加直连（DIRECT）：
  *.eastmoney.com / qt.gtimg.cn / *.sinajs.cn / *.sina.com.cn / *.10jqka.com.cn

用法：在任何发起行情请求的入口模块顶部 `from src.tools import proxy_guard`
即可（import 即生效）。
"""
from __future__ import annotations

import os

BYPASS_DOMAINS = [
    "eastmoney.com",    # 东财全部子域（push2 / push2his / 82.push2 / 17.push2 ...）
    "gtimg.cn",         # 腾讯行情 qt.gtimg.cn（v3.1 备用数据源）
    "sinajs.cn",        # 新浪行情
    "sina.com.cn",      # 新浪（AKShare 港股日线等备用接口）
    "10jqka.com.cn",    # 同花顺
    "csindex.com.cn",   # 中证指数
    "sse.com.cn",       # 上交所
    "szse.cn",          # 深交所
]


def install() -> None:
    """把直连域名注入 NO_PROXY / no_proxy（追加，不覆盖已有配置）。"""
    for var in ("NO_PROXY", "no_proxy"):
        existing = [x.strip() for x in os.environ.get(var, "").split(",") if x.strip()]
        for d in BYPASS_DOMAINS:
            if d not in existing:
                existing.append(d)
        os.environ[var] = ",".join(existing)


# ════════ v3.4.1 网络策略说明 ════════
# 策略A（默认，适合国内网络/代理可直连东财）: 注入 NO_PROXY 让行情域名绕过代理直连
# 策略B（适合海外网络: 东财拒绝海外直连IP，doctor 表现为 502/RemoteDisconnected）:
#   设置环境变量 AHF_DISABLE_NO_PROXY=1 跳过注入，并在代理客户端把
#   *.eastmoney.com 单独指向【中国大陆方向节点】—— 即让东财走国内代理回程。
#   新浪/腾讯对海外IP友好，无论哪种策略都可直连，系统价格/快照已自动用其兜底。
import os as _os

if _os.getenv("AHF_DISABLE_NO_PROXY", "").lower() not in ("1", "true", "yes"):
    install()  # import 即生效（策略A）
