# Phase 1 部署 README

> 5 个文件，对应 TECH.md §13 Phase 1 的 Step 1-5。
> 所有文件已在沙箱本地跑通自测。Alex 直接放到本机仓库即可。

---

## 文件清单 & 部署位置

```
你的仓库根目录：D:\AI-tool\Stock\ai-hedge-fund\src\
└── （把下面 5 个文件放到对应位置）

phase1/                                  →  src/
├── boot.py                              →  src/boot.py                     [新]
├── llm_text.py                          →  src/llm_text.py                 [新]
├── markets/
│   └── proxy.py                         →  src/markets/proxy.py            [新]
├── tools/
│   └── data_fallback.py                 →  src/tools/data_fallback.py      [新]
└── strategy/
    └── three_categories.py              →  src/strategy/three_categories.py [覆盖现有！]
```

**`strategy/three_categories.py` 是覆盖**：v3.5 已有同名文件，本版本是加固重写。
覆盖前先 `git add . && git commit -m "snapshot v3.5 before phase1"` 留个底。

其他 4 个文件是**新增**，不冲突。

---

## 每个文件对应的不变量

| 文件 | 解决的 v3.5 问题 | 不变量 |
|------|----------------|-------|
| `markets/proxy.py` | TUN 模式代理拦截 AKShare 502 | I6.4 |
| `boot.py` | Ghost Version（patch 没生效却以为生效） | I5.1, I5.2, I5.3 |
| `llm_text.py` | 静默回退到占位符模板 + DeepSeek thinking 空返回 | I6.1, I6.3, I8.1 |
| `tools/data_fallback.py` | 单数据源失败导致整页空白 | I1.2 |
| `strategy/three_categories.py` | 池状态损坏 + Pydantic v2 中文乱码 | I4.1-I4.5 |

---

## 启动顺序（关键）

任何入口（`web_app.py` / `main_china.py` / Streamlit 入口等）的**第 1-3 行**必须是：

```python
# 1. 进程级 NO_PROXY 注入（必须在 import requests 之前）
from markets.proxy import inject_no_proxy
inject_no_proxy()

# 2. 把 api_china 的数据接口 patch 到 agents 模块（如果用 monkey-patch）
#    Phase 2 实施 tools/api_bridge.py 后此行才有效；现在可以暂时省略。
# import tools.api_bridge

# 3. 启动横幅 — 让 Alex 一眼看出运行的是哪份代码
from boot import boot_print_versions
boot_print_versions(strict=False)

# 4. 之后才能 import 其他业务模块
from strategy.three_categories import load_pool_state
pool = load_pool_state()
# ...
```

**顺序原因**：
- `NO_PROXY` 必须在 `requests` 第一次 import 前注入（环境变量是 import 时读的）
- 横幅必须在所有核心模块加载后打印，才能拍到 `__version__` 和文件路径
- 业务代码必须在前两步完成后才 import，避免拿到错误的 monkey-patch 状态

---

## 验证步骤

放好文件后，cd 到 `src/` 目录，逐个跑：

```bash
# 必跑（不依赖外部网络 / API key）
python -m markets.proxy
python -m tools.data_fallback
python -m strategy.three_categories

# 横幅（需要其他模块都在位置）
python -m boot

# LLM 配置测试（不需要真实 key）
python -m llm_text

# 含真实 LLM 调用（需要 .env 配 DEEPSEEK_API_KEY）
DEEPSEEK_API_KEY=sk-xxx python -m llm_text
```

每个测试都应该以 `ALL TESTS PASSED` 或 `Self-test done` 结尾。

---

## 沙箱已跑通的测试清单

| 模块 | 测试数 | 全过 |
|------|-------|------|
| `markets.proxy` | 4 | ✓ |
| `tools.data_fallback` | 5 | ✓ |
| `strategy.three_categories` | 10 | ✓ |
| `boot` | 1（横幅 + Phase 1 探测） | ✓ |
| `llm_text` | 3（不含真实调用） | ✓ |

`strategy.three_categories` 的 10 个测试覆盖：
- 空池加载、save+load 闭环、5+5+5 不变量校验
- 7 天 backup 创建 + 损坏文件恢复
- 配对迁移 + pair_id 共享
- 月度配额耗尽
- 非互补配对（V→T + N→T）被池规模约束拒绝
- 重复 ticker 拒绝、批量初始化 + sub_id 自动分配

---

## Phase 1 自测中暴露的设计修正

T6 暴露：单步 `execute_migration` API 与 5+5+5 不变量冲突。
修正：改为 `execute_migration_pair(exit_leg, enter_leg, ...)` 一次原子操作两条腿，
保持池规模。月度配额按 `pair_id` 去重计数。

这一改动反映在 `__version__ = "v1.0.1"`（其他 4 个文件是 v1.0.0）。

**对你日常工作流的影响**：
- 老的"单步迁移"想法（"我想把 V3 转到 T 池"）现在必须**配对**（"我想把 V3 转到 T，同时把 T2 转到 V"）
- 这反而更符合"2 进 2 出"的真实节奏
- UI 层（Phase 4 的总览页 C）会显示"迁移面板"要求一次填两个标的

---

## 接下来要做的（不属于 Phase 1）

Phase 2（TECH.md §13）：
1. 删除 `strategy/briefing_generator.py` 全部 1500 行
2. 删除 web_app.py 里早晚报相关的路由
3. 新建 `briefings_archive/ingest.py` + `briefings_archive/storage.py`
4. 新建 `hk_news/schema.py` + `hk_news/ingest.py`

预计 1.5 天。

Phase 1 + Phase 2 完成后，旧的 v3.5 早晚报功能完全被替换为"粘贴消费 + 投研工作台"模式。

---

## 已知限制（不阻塞实施）

- `llm_text.py` 暂未支持 Gemini（v1.1 添加，结构上是加一个 provider 配置）
- `tools/data_fallback.py` 的 `quote_chain()` / `news_chain()` / `kline_chain()` 是占位，
  实际数据源接入在 Phase 2 的 `api_china.py` 里
- `boot.py` 的 16 个模块清单包含 Phase 2-4 模块，跑时显示 12 个 missing 是预期行为
