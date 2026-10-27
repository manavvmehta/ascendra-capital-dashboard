#!/usr/bin/env python3
"""
DOWNLOAD OI DATA.py

OPEN INTEREST for every F&O stock, three years, into one new file:

    DATA\\oi_daily.json

WHY THIS ONE IS DIFFERENT FROM EVERY OTHER DOWNLOAD

Every indicator tested so far - all thirty-two of TradingView's built-ins -
is arithmetic on the same open, high, low and close. None of them can add
information that is not already in the price, which is why almost none of
them helped.

Open interest is not. It counts how many contracts are actually open, so it
says whether a move is being backed by new positions or is just old ones
being closed. That is genuinely new information, and it is the only such
input Zerodha gives us. It is worth a proper test.

WHAT IT COLLECTS, per F&O name, per day:
    open, high, low, close, volume  - from the futures contract
    open interest                    - the number this whole file is for

Futures, not the cash stock, because open interest only exists on the
derivative. It stitches across expired contracts the way the commodity
download does, using the continuous flag, which Zerodha allows on DAY
candles.

HOW TO RUN IT
    1. Run  connect.py  in "5 LIVE FEED" first, so today's login is fresh.
    2. Double-click this file.
    3. It takes about four minutes for roughly 200 names. It prints progress.

    Safe to stop and run again: it keeps whatever it already downloaded.
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
OUT = os.path.join(DATA, "oi_daily.json")

DAY_SPAN = 1990          # Zerodha serves at most 2,000 days of DAY candles
PACE = 0.36


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
              f"{tok.get('date')}).\n  Run connect.py, then start this again.\n")
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
    return k


print(__doc__)
os.makedirs(DATA, exist_ok=True)
kite = load_kite()
print("  connected to Zerodha")

try:
    instr = kite.instruments("NFO")
except Exception as e:
    print(f"  Could not read the F&O instrument list: {e}")
    hold(1)

# the NEAREST live futures contract for every underlying
chain = {}
for r in instr:
    if r.get("instrument_type") != "FUT":
        continue
    nm = (r.get("name") or "").upper()
    ex = r.get("expiry")
    if not nm or not ex:
        continue
    ex = ex if isinstance(ex, date) else datetime.fromisoformat(str(ex)).date()
    if ex < date.today():
        continue
    cur = chain.get(nm)
    if cur is None or ex < cur[0]:
        chain[nm] = (ex, r["instrument_token"], r["tradingsymbol"],
                     r.get("lot_size"))

print(f"\n  {len(chain)} F&O underlyings with a live futures contract")
print(f"  asking for {DAY_SPAN} days of daily candles WITH open interest\n")

out = {}
try:
    out = json.load(open(OUT, encoding="utf8")).get("data") or {}
    if out:
        print(f"  {len(out)} already downloaded, carrying on from there.\n")
except Exception:
    pass

frm = (date.today() - timedelta(days=DAY_SPAN)).isoformat()
to = date.today().isoformat()
todo = [nm for nm in sorted(chain) if nm not in out]
t0 = time.time()
kept = skipped = 0

for n, nm in enumerate(todo, 1):
    ex, tok, tsym, lot = chain[nm]
    rows = []
    err = None
    try:
        raw = kite.historical_data(tok, frm, to, "day",
                                   continuous=True, oi=True) or []
        for x in raw:
            rows.append([str(x["date"])[:10], float(x["open"]), float(x["high"]),
                         float(x["low"]), float(x["close"]),
                         float(x.get("volume") or 0), float(x.get("oi") or 0)])
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    # a row with no open interest at all is no use to the test this is for
    has_oi = sum(1 for r in rows if r[6] > 0)
    if len(rows) > 250 and has_oi > 100:
        out[nm] = {"symbol": tsym, "lot_size": lot, "front_expiry": str(ex),
                   "bars": rows}
        kept += 1
        print(f"  {n:>3}/{len(todo)} {nm:<14}{len(rows):>5} days  "
              f"{rows[0][0]} -> {rows[-1][0]}  OI on {has_oi} of them")
    else:
        skipped += 1
        print(f"  {n:>3}/{len(todo)} {nm:<14}  {len(rows)} days, OI on {has_oi}"
              f" - not enough{(' (' + err + ')') if err else ''}")
    if n % 25 == 0:
        json.dump({"saved": datetime.now().isoformat(timespec="seconds"),
                   "interval": "day", "continuous": True, "data": out},
                  open(OUT, "w"))
    time.sleep(PACE)

json.dump({"saved": datetime.now().isoformat(timespec="seconds"),
           "interval": "day", "continuous": True, "data": out},
          open(OUT, "w"))
tot = sum(len(v["bars"]) for v in out.values())
print(f"""
  DONE.  {len(out)} stocks, {tot:,} daily candles with open interest.
  Saved to DATA\\oi_daily.json   ({time.time()-t0:.0f}s)

  Tell Claude it has finished.
""")
hold(0)
