# -*- coding: utf-8 -*-
"""smoke_step15.py — Step 15 真机冒烟(仓库根目录运行,不改仓库)
①合法导入 → 平铺文件 ②舆情 agent Tier-1 真函数读回 ③status ④四路拒绝
注: I3.3 端到端(source_tier=openclaw 进决议)留给下次 HK 批跑前导入验证,
   避免冒烟烧 hk_daily 预算。
"""
import json, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv; load_dotenv()
from fastapi.testclient import TestClient
from src.web_app import app

VALID = json.dumps({
  "schema_version": "1.0", "ticker": "09880.HK", "company_name_zh": "优必选",
  "market": "HK", "snapshot_at": "2026-07-06T09:00:00", "data_window_days": 7,
  "news": [{"title": "优必选获批量人形机器人订单", "published_at": "2026-07-05T10:00:00",
            "source": "证券时报", "sentiment": "positive",
            "summary": "冒烟样例(导入真 openclaw JSON 前可先跑本脚本验通路)"}],
  "announcements": [], "analyst_reports": [], "sentiment_signals": {},
  "risk_events": [], "peer_events": [], "data_gaps": []}, ensure_ascii=False)

c = TestClient(app)
r = c.post("/stock/09880.HK/hk-news/import", json={"raw_json": VALID}).json()
print("① import:", r.get("status"), "| flat:", r.get("flat_path"), "| archived:", r.get("archived"))
from src.agents.china_public_opinion import _load_openclaw_bundle
b = _load_openclaw_bundle("09880.HK")
print("② agent Tier-1 读回:", b is not None, "| news:", b and len(b.get("news", [])))
print("③ status:", c.get("/stock/09880.HK/hk-news/status").json())
codes = [c.post("/stock/09880.HK/hk-news/import", json={"raw_json": "{broken"}).status_code,
         c.post("/stock/09880.HK/hk-news/import", json={"raw_json": VALID.replace('"1.0"', '"2.0"', 1)}).status_code,
         c.post("/stock/09660.HK/hk-news/import", json={"raw_json": VALID}).status_code,
         c.post("/stock/600519/hk-news/import", json={"raw_json": VALID}).status_code]
print("④ 拒绝四路:", codes, "| 全400:", all(x == 400 for x in codes))
print("\n[判读] ①ok ②True ③fresh ④全400 = Step 15 验收通过")
