# probe_0620.py  -- ASCII only, save & run: poetry run python probe_0620.py
import os, glob, json

print("=== 1. ENV PROXY ===")
for k in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","NO_PROXY"):
    print(f"  {k} = {os.environ.get(k)}")

print("\n=== 2. AKShare hist WITH vs WITHOUT proxy ===")
import akshare as ak
def try_hist(label):
    try:
        df = ak.stock_zh_a_hist(symbol="600519", period="daily", adjust="qfq")
        print(f"  [{label}] rows={len(df)}  last_close={df['收盘'].iloc[-1] if len(df) else 'EMPTY'}")
    except Exception as e:
        print(f"  [{label}] ERROR: {type(e).__name__}: {str(e)[:120]}")
try_hist("with system proxy")
saved = {k: os.environ.pop(k, None) for k in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY")}
try_hist("NO proxy (stripped)")
for k,v in saved.items():
    if v is not None: os.environ[k]=v

print("\n=== 3. northbound (eastmoney) ===")
try:
    df = ak.stock_hsgt_hist_em(symbol="北向资金")
    print(f"  rows={len(df)} cols={list(df.columns)[:6]}")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {str(e)[:120]}")

print("\n=== 4. api_china.get_prices (app wrapper) ===")
try:
    from src.tools.api_china import get_prices
    ps = get_prices("600519.SH", "2026-01-01", "2026-06-20")
    print(f"  type={type(ps).__name__}  len={len(ps) if ps else 0}")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {str(e)[:160]}")

print("\n=== 5. pool state file(s) on disk ===")
hits = glob.glob("**/pool_state*.json", recursive=True) + glob.glob("**/three_categor*state*.json", recursive=True)
print("  candidates:", hits or "NONE FOUND")
for h in hits[:3]:
    try:
        d = json.load(open(h, encoding="utf-8"))
        print(f"  {h}: keys={list(d.keys())[:8]}")
    except Exception as e:
        print(f"  {h}: UNREADABLE {e}")

print("\n=== 6. web_app version check (kline route + i18n) ===")
wa = glob.glob("**/web_app.py", recursive=True)
for f in wa[:2]:
    src = open(f, encoding="utf-8", errors="replace").read()
    print(f"  {f}: /api/kline={'/api/kline' in src}  i18n_import={'i18n' in src}  conclusions_v2={'conclusions_v2' in src}")