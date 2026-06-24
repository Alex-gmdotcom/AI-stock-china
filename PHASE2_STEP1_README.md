# Phase 2 — 进度 & Step 1 交付

> 日期:2026-06-18
> 状态:Step 1 (`tools/api_china.py`) 在 sandbox 验证完成,等本机部署 + 真网络 smoke test

---

## 1. Phase 2 全局进度看板

| Step | 任务 | 状态 | 备注 |
|------|------|------|------|
| **1** | **`tools/api_china.py`** A 股 + 港股报价统一接口 (fallback chain) | ✅ **本回合完成** | 见 §2 |
| 2 | 删 `strategy/briefing_generator.py` (1500 行旧早晚报生成器) | ⏳ 阻塞 | 需要当前文件内容 |
| 3 | 删 `web_app.py` 里早晚报路由 | ⏳ 阻塞 | 需要当前 web_app.py |
| 4 | `briefings_archive/ingest.py` + `storage.py` (消费 openclaw 早晚报) | ⏳ 阻塞 | 需要 1-2 份 openclaw 早晚报真实样本 |
| 5 | `hk_news/schema.py` + `ingest.py` (消费 openclaw 港股新闻) | ⏳ 阻塞 | 需要 openclaw_hk_news_prompt 实测产出样本 |
| 0 | v3.5 遗留三文件复用评估 (`markets/ticker.py` / `markets/config.py` / `tools/api_bridge.py`) | ⏳ 阻塞 | 需要这三个文件代码 |

---

## 2. Step 1 — `tools/api_china.py` v1.0.0

### 2.1 设计要点

完全按照 0617 总结 §3 的真实探测结果钉死 fallback chain:

```
quote("600519")
    │
    ├─ 1. tencent_qt        http://qt.gtimg.cn/q=sh600519       (首选)
    ├─ 2. sina_hq           http://hq.sinajs.cn/list=sh600519   (备用,需 Referer)
    └─ 3. eastmoney_spot    http://82.push2.eastmoney.com/...   (兜底,spot 全表查找)
```

**显式排除** `push2.eastmoney.com/api/qt/stock/get` —— Alex 网络下 RemoteDisconnected,
已知不可用,根本不放进 chain。

核心 API:
```python
from tools.api_china import quote, batch_quote, Quote, NoDataSourceAvailable

q = quote("600519")              # 任意常见形式都吃
print(q.price, q.source)          # 1240.0  tencent_qt
print(q.ticker, q.market, q.name) # 600519.SH  SH  贵州茅台

# 批量(部分失败不影响其他位置)
qs = batch_quote(["600519", "00700.HK", "300750"])
```

支持的 ticker 输入形式:
```
"600519"   "600519.SH"   "sh600519"   "SH600519"     →  600519.SH
"000001"   "000001.SZ"   "sz000001"                  →  000001.SZ
"300750"                                              →  300750.SZ  (创业板自动识别)
"688008"                                              →  688008.SH  (科创板自动识别)
"00700"    "00700.HK"    "0700.HK"   "hk00700"       →  00700.HK   (港股,自动补齐 5 位)
"430047"   "830799"                                   →  *.BJ       (北交所自动识别)
```

### 2.2 不变量对齐

| 不变量 | 实现 |
|--------|------|
| I1.2 数据可追溯实际使用源 | `Quote.source` 字段,chain key 强制覆盖,审计不可被 mock 篡改 |
| I6.4 代理配置不上传 | 入口 `quote()` 调 `ensure_no_proxy()` (Phase 1 markets.proxy) |
| fail-loud (Phase 1 共识) | 三源全挂抛 `NoDataSourceAvailable`,消息含全部 source name + 各自错误 |

### 2.3 Sandbox 验证 (本回合已完成)

`self-test` 内置在模块尾部,**纯 mock,无网络**,跑通后才允许部署:

```
[T1]     ticker normalize (15 cases): PASS
[T1.neg] bad tickers reject correctly: PASS
[T2]     tencent_qt parser: PASS (贵州茅台 @ 1240.0, src=tencent_qt)
[T2.neg] tencent empty payload rejects: PASS
[T3]     sina_hq A-share parser: PASS
[T3.neg] sina empty payload rejects: PASS
[T4]     eastmoney_spot parser: PASS
[T5]     fallback chain (tencent down → sina ok): PASS (used=sina_hq)
[T5.neg] all sources fail raises NoSourceAvailable with full audit: PASS
[T6]     batch_quote partial failure isolated + chain-key-override: PASS
[T7]     HK ticker pipeline (normalize → tencent code → em fs_key): PASS

[api_china v1.0.0] self-test PASS (7 groups + 4 negative checks)
```

同时跑了一次模拟仓库部署 dry-run —— `deploy_phase2_step1.py` → 备份 →
base64 解码 → 写入 → __pycache__ 清理 → self-test 全过 → returncode 0。

### 2.4 本机操作 (按顺序跑)

#### Step 1.A — 部署

```cmd
cd E:\AI-tool\Stock\ai-hedge-fund
python deploy_phase2_step1.py
```

预期看到:
```
[BACKUP] 无需备份 (新建)
[WRITE] ...\src\tools\api_china.py  (32,996 bytes)
[CLEAN] 删除 N 个 __pycache__
[TEST]  ... self-test PASS (7 groups + 4 negative checks)
[SUCCESS] Phase 2 Step 1 (tools/api_china.py) 部署完成
```

#### Step 1.B — boot 横幅确认挂载

```cmd
python -m src.boot
```

预期新增一行:
```
tools.api_china                      v1.0.0         src\tools\api_china.py
```

并且 `WARN: N/16 module(s) missing` 应该减少 1 (从 9 降到 8)。

#### Step 1.C — 真网络 smoke test

```cmd
python smoke_test_phase2_step1.py
```

预期(基于 §3 探测,你的家用 IP):
```
[OK]  600519.SH    贵州茅台    price=  1240.000  chg= -1.43%  src=tencent_qt    (xxxms)
[OK]  000001.SZ    平安银行    price=    12.500  chg= +0.40%  src=tencent_qt    (xxxms)
[OK]  300750.SZ    宁德时代    price=   245.000  chg= +0.81%  src=tencent_qt    (xxxms)
[OK]  688008.SH    澜起科技    price=    65.000  chg= +0.31%  src=tencent_qt    (xxxms)
[OK]  00700.HK     腾讯控股    price=   615.000  chg= +0.49%  src=tencent_qt    (xxxms)

Source 使用分布:
  tencent_qt           5 / 5

汇总: 5 OK, 0 WARN, 0 FAIL
[DIAG] fallback chain 工作正常,建议作为 Phase 2 后续模块的数据底座
```

**如果 src 分布不是 5×tencent_qt** → 把汇总贴回来,我据此调整 chain 顺序或 endpoint。

---

## 3. 下一步:需要你提供的 inputs

按优先级(按完成 Phase 2 顺序):

### 3.1 最高优先级 — 解锁 Step 0 (v3.5 三文件评估)

请把这三个文件的内容贴出来 (或上传):

```
src/markets/ticker.py
src/markets/config.py
src/tools/api_bridge.py
```

每个文件 `head -100`(或全文)都行。我会判断:
- 哪些直接复用(写进 boot 横幅 + 删掉 (no version) 标签)
- 哪些重写(版本升级为 v1.0.0,与 Phase 2 风格对齐)
- 哪些删除(被 `tools/api_china.py` / `tools/data_fallback.py` 取代)

预计省 1-2 天工作量。

### 3.2 次高优先级 — 解锁 Step 4 (briefings_archive)

请提供:
- **openclaw 早报真实产出样本 1 份** (上午刚出炉的或者昨天的)
- **openclaw 晚报真实产出样本 1 份**

我会根据真实样本确定:
- `BriefingDocument` schema (字段、嵌套结构)
- `ingest.py` 的 parser 兼容度 (markdown vs json vs 混合)
- `storage.py` 的索引策略 (按日期? 按主题?)

### 3.3 次高优先级 — 解锁 Step 5 (hk_news)

请提供:
- 用 `openclaw_hk_news_prompt.md` 在 openclaw 跑出来的 **真实 JSON 输出 3-5 份** (不同港股)

我会根据真实输出 codify `hk_news/schema.py`。

### 3.4 解锁 Step 2 + Step 3 (删除旧路由)

请提供:
- `src/strategy/briefing_generator.py` (整个文件即可,1500 行)
- `src/web_app.py` (整个文件即可)

我会:
- 给出精确的 `delete` 列表
- 给出精确的 `str_replace` 列表 (对 web_app.py)
- 生成 `deploy_phase2_step2.py` 一键执行 + 回滚备份

---

## 4. 交付清单 (本回合 4 个文件)

| 文件 | 用途 | 大小 |
|------|------|------|
| `tools/api_china.py` | 模块本体 v1.0.0 + 内置 self-test | 33 KB |
| `deploy_phase2_step1.py` | 一键部署 (base64 嵌入,绕开 Windows 编辑器陷阱) | 47 KB |
| `smoke_test_phase2_step1.py` | 真网络端到端测试 + 诊断提示 | 5 KB |
| `PHASE2_STEP1_README.md` | 本文件 | — |

---

## 5. 回滚 (如本机部署后发现问题)

```cmd
cd E:\AI-tool\Stock\ai-hedge-fund
:: 列出可用备份
dir phase2_backup
:: 回滚 (替换 TIMESTAMP)
copy /Y phase2_backup\TIMESTAMP\api_china.py.bak src\tools\api_china.py
```

---

**Phase 2 Step 1 完成。等你提供 §3 列出的 inputs 后,我继续推 Step 0 / Step 4 / Step 5。**
