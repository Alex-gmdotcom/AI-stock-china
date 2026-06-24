# Phase 2 完整收尾 — 交付清单

> 日期:2026-06-19
> 状态:全套 Phase 2 工作 sandbox 验证完成,等本机部署

---

## 1. 收官清单

| Step | 任务 | 状态 |
|------|------|------|
| 0 | v3.5 三文件复用评估 + ticker 加 BJ | ✅ |
| 1 | `tools/api_china.py` (上回合 已 ship) | ✅ |
| 2 | 删 `strategy/briefing_generator.py` (1071 行) | ✅ |
| 3 | 删 `web_app.py` 早晚报路由 + HTML + JS | ✅ |
| 4 | `briefings_archive/` (4 文件) | ✅ |
| 5 | `hk_news/` (4 文件) | ✅ |

**Phase 2 全部完成。**

---

## 2. 关键决定

### 2.1 v3.5 三文件评估结果

| 文件 | 决定 | 说明 |
|------|------|------|
| `markets/ticker.py` | ✅ 复用 + 扩展 (v1.0.1) | 加 BJ 北交所、加 prefix 形式 (sh600519)、HK 短码自动补齐、加 `__version__` |
| `markets/config.py` | ✅ 复用 + 扩展 (v1.0.0) | 加 BJ trading rules (±30% 涨跌幅、注册制)、加 `__version__` |
| `tools/api_bridge.py` | ✅ 直接复用 (v1.0.0) | 仅加 `__version__`,代码体不动。原版 monkey-patch 设计 OK |

**省了 1-2 天工作量**,完全按 0617 总结 §8.1 预期实现。

### 2.2 早晚报 schema 选择

采用「**存原文 + 抽 metadata**」而非硬结构化。原因:
- 真早晚报样本是半结构化中文 markdown,emoji + 分隔线 + 章节编号都是装饰性的
- 强行 parse 出 "今日上证收盘价 = 4090.41" 之类只会让任何 prompt 微调都 break
- 改为索引型:文件名 `2026-06-18_morning.md` 直接存原文,sidecar JSON 存抽出来的 (日期、类型、评分、自选股提及、模式告警、茧房信号)

抽取正则在真早报 (290 行) + 真晚报 (98 行) 上回归全过,字段如下:
- 早报:`tickers=['T6', '候选', 'T7', 'N1', 'N2', ...]`,`patterns=['主线疲劳触发', '主线疲劳警告', ...]`
- 晚报:`grade=B/69`,`faults=['C类-信源盲区', 'A类正向超预期', 'B类右侧验证']`,`cocoons=5 个茧房信号`

### 2.3 hk_news schema 严格 codify

3 份真实样本 (00148/01810/02513) 字段结构高度一致,所以严格 typed dataclass + enum + `coerce()` 容错:
- `Sentiment` / `AnnouncementType` / `RatingChange` / `RiskEventType` / `RiskSeverity` 全 enum 化
- 真样本里发现一个 typo `"down grade"`(01810.HK),`RatingChange.coerce()` 容错为 `DOWNGRADE`
- 真样本里发现 2513_hk.txt 中间夹杂 `Agent: xiaodian1 | Model: deepseek-v4-flash` 元数据噪音,`_strip_noise()` 自动剥离

**3 份真样本全部 parse 通过**:
```
00148_hk.txt → news=8, ann=7, reports=3, risks=3, peers=2
01810_hk.txt → news=10, ann=6, reports=5, risks=5, peers=2
2513_hk.txt  → news=10, ann=1, reports=2, risks=3, peers=2
```

### 2.4 `api_china.py` v1.0.1 升级

`_normalize` 改用 `markets.ticker.parse_ticker` 作为单一真理源。Sandbox 两种模式都测过:
- 真集成模式 (markets.ticker 可达) → `_HAVE_REAL_TICKER = True`,走真模块
- 独立 stub 模式 (sandbox 单跑 api_china.py self-test) → 退化到本地最小实现,15 个 ticker case 仍 PASS

### 2.5 web_app.py 6 处 patch

精确删除清单(行号基于原 418 行版本):
1. L54-56 `class BriefingRequest`
2. L70-95 `/api/history` + `/api/history/{filename}` (两个 GET)
3. L98-116 `/api/briefing` (POST)
4. L227-245 早晚报 HTML 卡片(整个 div.row)
5. L279-284 历史报告 HTML 卡片(保留观察池)
6. L380-403 `loadHistory()` + `viewHist()` JS + 末尾 `loadHistory()` 调用

**保留**:`@app.get("/api/pool")` + `@app.post("/api/analyze")` + 根路径 HTML + 个股分析卡片 + 观察池卡片。

Patch 后 `web_app.py` 从 418 → 300 行,路由从 6 个 → 3 个,无残留 briefing 引用。

---

## 3. 本机部署

```cmd
cd E:\AI-tool\Stock\ai-hedge-fund
python deploy_phase2_full.py
```

预期输出末尾:
```
[SUCCESS] Phase 2 全套部署完成
  9/9 self-test PASS
  备份位置: phase2_full_backup\20260619_HHMMSS
```

部署做的事:
1. 备份所有将被改/删的文件到 `phase2_full_backup/TIMESTAMP/`
2. 解压 base64 zip payload → 12 个新/改文件
3. 删除 `src/strategy/briefing_generator.py`(1071 行)
4. 对 `src/web_app.py` 应用 6 处 patch (CRLF / LF 自适应)
5. 清 `__pycache__`
6. 跑 9 个模块 self-test (mock-based, 无网络)

### 部署后验证

```cmd
:: 1. boot 横幅应新增 6 行
python -m src.boot
```

应看到:
```
markets.ticker                       v1.0.1         src\markets\ticker.py
markets.config                       v1.0.0         src\markets\config.py
tools.api_bridge                     v1.0.0         src\tools\api_bridge.py
tools.api_china                      v1.0.1         src\tools\api_china.py
briefings_archive                    v1.0.0         src\briefings_archive\__init__.py
hk_news                              v1.0.0         src\hk_news\__init__.py
```

并且原来标 `(no version)` 的 ticker.py / config.py / api_bridge.py 现在都有 version 了。`(missing)` 计数应从 9 降到 ~5(剩 api_china 的 agent 接口 / line_items_china / analysis.* / hk_news 高级查询接口 / 早晚报生成 — 这些都不再需要)。

```cmd
:: 2. 跑真网络 smoke test 验证 api_china (上回合已交付)
python smoke_test_phase2_step1.py
```

```cmd
:: 3. 启动 web_app,确认早晚报卡片消失但观察池+个股分析仍 work
poetry run python src/web_app.py
```

### 回滚

```cmd
xcopy /Y /E /I phase2_full_backup\TIMESTAMP\src src
```

---

## 4. 9 个模块 sandbox self-test 全部 PASS

```
✓ src.markets.ticker             v1.0.1   3 groups, 19 ticker cases
✓ src.markets.config             v1.0.0   4 groups
✓ src.tools.api_china            v1.0.1   7 groups + 4 negative
✓ src.briefings_archive.schema   v1.0.0   3 groups
✓ src.briefings_archive.storage  v1.0.0   7 groups (含索引自愈)
✓ src.briefings_archive.ingest   v1.0.0   含真早晚报回归
✓ src.hk_news.schema             v1.0.0   5 groups
✓ src.hk_news.storage            v1.0.0   7 groups (含索引自愈)
✓ src.hk_news.ingest             v1.0.0   含 3 份真 JSON 样本回归
```

---

## 5. Phase 2 留给 Phase 3 的事

主动留下,不在本回合做:

1. **`api_china.py` agent 兼容接口**:`get_prices` / `get_financial_metrics` / `get_company_news` / `get_market_cap` / `get_price_data` — 这些是 `api_bridge.py` 期待 cn_api 暴露的,实现要靠 AKShare,字段映射 sandbox 测不了,等真机跑过你说"OK"再做
2. **`tools/line_items_china.py`** (`search_line_items_china`) — 同上,buffett / taleb agent 需要,要 AKShare 三表实测
3. **briefings_archive ingest CLI** — 让 Alex 从 clipboard / 文件粘贴入库的命令行入口 (核心 lib 已就绪,只差一个 5 行的 CLI wrapper)
4. **hk_news 高级查询** — 跨 ticker 趋势对比、按时间窗口聚合等

---

## 6. 交付清单

| 文件 | 用途 | 大小 |
|------|------|------|
| `deploy_phase2_full.py` | 一键部署(zip+base64 嵌入 12 文件 + web_app patch) | 80 KB |
| `PHASE2_FINAL_README.md` | 本文件 | — |
| `payload.zip` | 备用 zip(若想手工 review 12 个新文件) | 47 KB |

**Phase 2 全套完成,等部署反馈。**
