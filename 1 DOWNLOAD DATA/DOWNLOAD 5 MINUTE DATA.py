#!/usr/bin/env python3
"""
DOWNLOAD 5 MINUTE DATA.py

Five-minute candles for the F&O stocks, one year back, saved into
    DATA\\5m_parts\\part01.json, part02.json ...

WHY THIS EXISTS

MM, 21/09: "why are we not using shorter time frame?" - and the honest answer
was that we could not. The download so far is FIFTEEN-minute candles, so the
fastest pattern that can be built from it is thirty minutes and the fastest
entry is fifteen. Testing a genuinely quick intraday setup needs five-minute
bars, and this is that download.

WHY ONLY THE F&O NAMES, AND ONLY A YEAR

Because that is what the intraday side actually trades, and because the
arithmetic of the full job is silly: three years of five-minute candles on
1,281 stocks is about 67 million bars and several gigabytes, an hour and a
half of downloading, for a question that 216 liquid stocks over one year
answers just as well. If the answer is promising, widening it later is easy.

HOW TO RUN IT
    1. Run  connect.py  in "5 LIVE FEED" first, so today's login is fresh.
    2. Double-click this file.
    3. Leave it for about six minutes. It prints its progress.

    If it stops early - laptop asleep, connection dropped - just run it
    again. It keeps what it already has and carries on.

WHAT IT COSTS
    Nothing. It makes about 900 requests, paced under Zerodha's limit, on
    the same historical access the desk already uses.
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "DATA")
FEED = os.path.join(ROOT, "5 LIVE FEED")
OUTDIR = os.path.join(DATA, "5m_parts")

MONTHS_BACK = 12
INTERVAL = "5minute"
CHUNK = 90           # days per request - Kite serves 100 for 5minute
PACE = 0.36          # seconds between requests
PER_PART = 60        # symbols per file, so nothing gets too big to open


def hold(code=0):
    try:
        input("\n  Press Enter to close. ")
    except Exception:
        pass
    sys.exit(code)


def load_kite():
    keys = os.path.join(FEED, "kite_keys.txt")
    tokf = os.path.join(FEED, "kite_token.json")
    if not (os.path.exists(keys) and os.path.exists(tokf)):
        print("\n  NOT CONNECTED. Run connect.py in '5 LIVE FEED' first.\n")
        hold(1)
    api_key = None
    for line in open(keys, encoding="utf8"):
        if line.strip().startswith("api_key"):
            api_key = line.split("=", 1)[-1].strip()
    tok = json.load(open(tokf, encoding="utf8"))
    if tok.get("date") != date.today().isoformat():
        print(f"\n  TODAY'S LOGIN IS MISSING (the saved one is from "
              f"{tok.get('date')}).\n  Run connect.py, then start this "
              f"again.\n")
        hold(1)
    try:
        import kiteconnect  # noqa
    except ImportError:
        print("  Installing the Zerodha library, about twenty seconds...\n")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                        "kiteconnect"])
    from kiteconnect import KiteConnect
    k = KiteConnect(api_key=api_key)
    k.set_access_token(tok["access_token"])
    k.quote(["NSE:RELIANCE"])
    return k


def symbols():
    """The F&O names. Falls back to the sector list, then to nothing."""
    p = os.path.join(DATA, "fo_universe.json")
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf8"))
            s = set(d["map"].keys()) if isinstance(d, dict) and "map" in d \
                else set(d if isinstance(d, list) else d.keys())
            if s:
                return sorted(s)
        except Exception:
            pass
    p = os.path.join(DATA, "ind_niftytotalmarket_list.csv")
    if os.path.exists(p):
        import csv
        return sorted({r["Symbol"].strip()
                       for r in csv.DictReader(open(p, encoding="utf-8-sig"))
                       if r.get("Symbol")})
    print("\n  Cannot find fo_universe.json or the Nifty Total Market csv "
          "in the DATA folder.\n")
    hold(1)


print(__doc__)
os.makedirs(OUTDIR, exist_ok=True)
kite = load_kite()
print("  connected to Zerodha")
SYMS = symbols()
print(f"  {len(SYMS)} stocks to fetch · {INTERVAL} candles · "
      f"{MONTHS_BACK} months\n")

try:
    instr = kite.instruments("NSE")
except Exception as e:
    print(f"  Could not read the instrument list: {e}")
    hold(1)
TOK = {r["tradingsymbol"]: r["instrument_token"] for r in instr
       if r.get("instrument_type") == "EQ"}

# what is already on disk, so a second run carries on rather than starting over
have = {}
for f in sorted(os.listdir(OUTDIR)):
    if f.endswith(".json"):
        try:
            have[f] = json.load(open(os.path.join(OUTDIR, f),
                                     encoding="utf8"))["bars"]
        except Exception:
            have[f] = {}
done_syms = {s for d in have.values() for s in d}
if done_syms:
    print(f"  {len(done_syms)} already downloaded, carrying on from there.\n")

end = datetime.now()
start = end - timedelta(days=int(MONTHS_BACK * 30.5))
todo = [s for s in SYMS if s in TOK and s not in done_syms]
print(f"  {len(todo)} to go.\n", flush=True)

t0 = time.time()
bucket, part_no, got, failed = {}, len(have) + 1, 0, 0


def flush(bucket, part_no):
    if not bucket:
        return
    p = os.path.join(OUTDIR, f"part{part_no:02d}.json")
    json.dump({"interval": INTERVAL,
               "saved": datetime.now().isoformat(timespec="seconds"),
               "bars": bucket}, open(p, "w"))
    n = sum(len(v) for v in bucket.values())
    print(f"    -> wrote {os.path.basename(p)}  "
          f"{len(bucket)} stocks, {n:,} candles", flush=True)


for n, sym in enumerate(todo, 1):
    tk = TOK[sym]
    rows, cur = [], start
    while cur < end:
        nxt = min(cur + timedelta(days=CHUNK), end)
        try:
            raw = kite.historical_data(tk, cur, nxt, INTERVAL) or []
            for x in raw:
                rows.append([str(x["date"])[:16], round(float(x["open"]), 2),
                             round(float(x["high"]), 2),
                             round(float(x["low"]), 2),
                             round(float(x["close"]), 2),
                             int(x.get("volume") or 0)])
        except Exception:
            time.sleep(2.0)
        cur = nxt
        time.sleep(PACE)
    if len(rows) > 500:
        bucket[sym] = rows
        got += 1
    else:
        failed += 1
    if len(bucket) >= PER_PART:
        flush(bucket, part_no)
        bucket, part_no = {}, part_no + 1
    if n % 20 == 0:
        el = time.time() - t0
        left = (len(todo) - n) * el / n
        print(f"  {n}/{len(todo)}  ·  {got} saved  ·  "
              f"{el/60:.1f} min gone, about {left/60:.0f} left", flush=True)

flush(bucket, part_no)
print(f"""
  DONE.  {got} stocks saved, {failed} had too little data.
  Files are in  DATA\\5m_parts\\

  Tell Claude it has finished and he will run the short-timeframe test.
""")
hold(0)
