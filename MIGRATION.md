# AI Hedge Fund 中国版 — 设备迁移指南（v3.3-full 完整包）

> 本包 = v3.1 + v3.2 + v3.3 全部修复与增强的合集，用于在新设备上一次性部署。
> 各版本变更明细见 docs/ 目录；本文只讲迁移步骤。

## 〇、包内容

```
ai-hedge-fund-china-v3.3-full/
├── MIGRATION.md            ← 本文
├── apply_all.py            ← 一键打补丁（按序 v3.1→v3.2→v3.3）
├── doctor_china.py         ← 数据链路自检
├── src/                    ← 直接覆盖到仓库（新增模块 + 整文件替换）
│   ├── main_china.py           整文件替换（港股路由/模型自选/结论层/代理守卫）
│   ├── strategy/briefing_generator.py 整文件替换 ⭐（v3.1+v3.2+v3.3 全量打入：
│   │       数据快照注入/_llm_text 直连模型[修坑#10]/fail-fast/评分护栏/
│   │       指数温度计/电报+个股新闻/外部简报桥）
│   ├── tools/proxy_guard.py    NO_PROXY 注入（修系统代理劫持东财）
│   ├── tools/quotes_fallback.py 腾讯行情：个股兜底 + 全球指数温度计
│   ├── tools/news_collector.py  财联社电报 + 个股新闻（多级回退）
│   ├── tools/external_brief.py  openclaw 外部信源文件桥
│   ├── tools/line_items_china.py 财报line items中国实现（buffett/taleb数据腿）
│   ├── tools/api_bridge.py     整文件替换（kwargs 签名过滤）
│   └── utils/decision_summary.py 个股强制结论层
├── patches/                ← 四个补丁脚本（briefing/api_china/两个agent/web_app）
└── docs/                   ← PATCH_NOTES(v3.1) / V32 / V33 / openclaw_task
```

## 一、前置条件

1. 新设备已有基础代码：原版 virattt/ai-hedge-fund 仓库 + 你的中国版 v3 包
   （ai-hedge-fund-china-v3.tar.gz，22 文件）已解压就位
2. Python 3.13 + Poetry 可用（注意旧坑：关闭 Windows 应用执行别名 python.exe；
   numpy 版本须 `>=1.26,<3.0`，详见 SESSION_SUMMARY 踩坑表 #1-#4）
3. `.env` 在仓库根目录（文件名就叫 `.env`），含 `DEEPSEEK_API_KEY=...`
   （可选 `ANTHROPIC_API_KEY` 启用 Claude）

## 二、安装（三步）

```bash
# 1. 本包内容复制到仓库根目录（src/ 与现有目录合并，遇同名文件【覆盖】——
#    main_china.py 和 api_bridge.py 是有意的整文件替换）

# 2. 一键打补丁（幂等，失败自动中止不改坏文件，可修复后重跑）
poetry run python apply_all.py

# 3. 代理客户端加直连(DIRECT)规则 —— 每台新设备都要做，
#    TUN/全局模式下这步是必须的（NO_PROXY 拦不住透明代理）：
#    *.eastmoney.com   qt.gtimg.cn   *.sinajs.cn   *.sina.com.cn   *.10jqka.com.cn
```

⭐ briefing_generator.py 采用整文件替换（基于 2026-06-12 实际部署版重建，
保留其 BriefingScorer/BriefingHistory/快速版早晚报原文，重写 LLM 区段），
三个 applier 检测到标记后会对它自动全部跳过 —— 所以即使先覆盖文件再跑
apply_all.py 也安全。补丁脚本只实际作用于 api_china.py 和两个 agent 文件；
若这些文件锚点匹配失败，applier 会明确报出且不动文件，发回会话即可重出补丁。

⚠️ 历史教训（幽灵版本）：上一台机器上补丁打在了 v3 包的 899 行版文件上，
而运行时导入的是 760 行旧部署版（无数据注入、call_llm 调用方式错误），
导致增强版早报从未生效。本包整文件替换从根上消除此问题，但装完仍要跑
第四节第 4 条验证 bg.__file__ 路径。

## 三、已知陷阱（迁移高发）

1. **幽灵旧版本**：补丁打在 A 文件上、运行时导入的是 B 副本（上一台机器就
   发生了）。装完务必跑第四节第 4 条验证命令，确认 `bg.__file__` 指向打过
   补丁的那份；发现多余副本直接删除。
2. **系统代理**：Windows 注册表级代理不体现在环境变量里，doctor 的代理检测
   查不到 TUN 模式。凡见 ProxyError → 回到第二节第 3 步。
3. **池状态不随代码走**：观察池/历史报告在 `~/.ai-hedge-fund/`
   （Windows: `C:\Users\<用户>\.ai-hedge-fund\`）。迁移设备时把整个目录拷过去，
   否则三分法池回到预装状态、早晚报反馈闭环从零开始。
4. **模型 ID**：本包已切到 deepseek-v4-flash（deepseek-chat 2026-07-24 弃用）。
   若舆情/政策 agent 仍偶发 schema 校验失败，改用 deepseek-v4-pro。

## 四、验证清单（按序）

```bash
# 1. 数据链路自检 —— 预期 A股日线/实时/资金流/板块全通（无 ProxyError）
poetry run python doctor_china.py

# 2. 腾讯源自测 —— 预期打印个股报价 + 全球指数（含隔夜美股三大）
poetry run python src/tools/quotes_fallback.py

# 3. 新闻源自测 —— 预期打印电报快讯 + 个股新闻
poetry run python src/tools/news_collector.py

# 4. 幽灵版本检查 —— 预期打印补丁目标路径 + OK
poetry run python -c "import src.strategy.briefing_generator as bg; print(bg.__file__); print('OK' if hasattr(bg, '_snapshot_gate') else '旧版本!')"

# 5. 个股分析 —— 预期: risk层拿到真实价格、PM产生真实决策（不再
#    'No valid trade available'）、buffett/taleb 有依据非中性信号、末尾【结论】段
poetry run python src/main_china.py --ticker 00148.HK
# 5b. 财报数据腿自测 —— 预期打印 600519 与 00148.HK 各期关键财务字段
poetry run python src/tools/line_items_china.py

# 6. Web 控制台 —— 生成 AI 增强版早报：预期含指数温度计/电报/个股新闻段落，
#    或数据不足时输出明确的"生成中止+逐项错误"诊断（绝不再有占位符假报告）
poetry run python src/web_app.py
```

## 五、可选：openclaw 信源桥与飞书推送

按 docs/openclaw_task.md 在 openclaw 侧配置 taskflow 定时任务
（agent-reach 跨平台搜索 → 写 `~/.ai-hedge-fund/external_brief.md`），
早晚报快照会自动多出【外部信源简报】段；飞书推送给同事也在该文档（任务三）。
openclaw 不在线时系统照常运行，安全降级。

## 六、开源发布提醒

本包的 doctor_china.py / patches / docs 适合随仓库开源；发布前按
DEPLOYMENT_GUIDE 第十二章清单执行：删除个人持仓与观察池预装数据、
检查 key 硬编码、补 CHANGELOG（可直接引用 docs/ 三份 NOTES）。
