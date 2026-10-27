#!/usr/bin/env python3
"""
DOWNLOAD COMMODITY DATA.py

Daily candles for the MCX commodities, as far back as Zerodha will give them,
saved into  DATA\\mcx_daily.json

WHAT THIS TRIES TO GET, AND WHY IT ASKS RATHER THAN ASSUMES

MM, 22/09: "you can backtest everything. patterns, intraday, smc and sp."

He was right to push back. I had said the intraday engines could not be
tested on commodities, and that came from Zerodha forum posts rather than
from asking the API. Forum posts go stale. So this asks.

Three passes, in order, and it prints exactly what each one returns:

  1. DAILY, continuous. Stitches the expired contracts automatically. This
     is the one Kite documents, and it feeds the swing engine.

  2. FIFTEEN-MINUTE, continuous. The documentation does not promise this and
     the forum says it does not exist - so the request is made anyway and
     whatever comes back is reported. If it works, all four engines can be
     tested and the forum was out of date.

  3. FIFTEEN-MINUTE, per contract. If pass 2 fails there is still a way:
     MCX lists a contract months before it expires, so every LIVE contract
     already carries its own intraday history back to its listing day. Gold
     is listed as far out as February, so the far contracts alone may hold a
     year or more. This fetches every live contract for each commodity and
     stitches them by date, newest contract winning where they overlap.

Whatever the three passes yield is what we can honestly test on. The file
says which pass each series came from.

WHAT THIS FETCHES

    GOLD GOLDM GOLDGUINEA GOLDPETAL          bullion
    SILVER SILVERM SILVERMIC
    CRUDEOIL CRUDEOILM NATURALGAS NATGASMINI energy
    COPPER ZINC ALUMINIUM LEAD NICKEL        base metals

Open interest comes with it, which the equity download never had.

HOW TO RUN IT
    1. Run  connect.py  in "5 LIVE FEED" first.
    2. Double-click this file. It takes about a minute.
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
OUT = os.path.join(DATA, "mcx_daily.json")

YEARS_BACK = 8           # kept for the intraday probe only - see DAY_SPAN
# WHY THE DAILY PART CAME BACK EMPTY, BOTH TIMES (23/09).
# Zerodha serves at most 2,000 days in one DAY-candle request. Asking for
# eight years is 2,928 days, so every single request was rejected and the file
# was written with nothing in it. The fix I wrote after the first failure never
# reached this machine, so the second run failed for the identical reason.
# 1,990 days is just inside the limit and reaches back to about April 2021.
DAY_SPAN = 1990
PACE = 0.36
INTRA = "15minute"
INTRA_CHUNK = 180        # days per request at 15 minutes
OUT_I = os.path.join(DATA, "mcx_intraday.json")

WANT = ["GOLD", "GOLDM", "GOLDGUINEA", "GOLDPETAL",
        "SILVER", "SILVERM", "SILVERMIC",
        "CRUDEOIL", "CRUDEOILM", "NATURALGAS", "NATGASMINI",
        "COPPER", "ZINC", "ALUMINIUM", "LEAD", "NICKEL"]


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
    instr = kite.instruments("MCX")
except Exception as e:
    print(f"  Could not read the MCX instrument list: {e}")
    hold(1)

# Every live contract per commodity, nearest expiry first. The nearest one is
# what the continuous passes ride on; the whole list is what pass 3 stitches.
chain = {}
for r in instr:
    if r.get("instrument_type") != "FUT":
        continue
    nm = (r.get("name") or "").upper()
    if nm not in WANT:
        continue
    ex = r.get("expiry")
    if not ex:
        continue
    ex = ex if isinstance(ex, date) else datetime.fromisoformat(str(ex)).date()
    if ex < date.today():
        continue
    chain.setdefault(nm, []).append(
        (ex, r["instrument_token"], r["tradingsymbol"],
         r.get("lot_size"), r.get("tick_size")))
for nm in chain:
    chain[nm].sort()
live = {nm: v[0] for nm, v in chain.items()}

missing = [w for w in WANT if w not in live]
if missing:
    print(f"  not listed right now: {', '.join(missing)}")
print(f"\n  {len(live)} commodities · daily candles · {DAY_SPAN} days back "
      f"(Zerodha will not serve more than 2,000 in one request)\n")

frm = (date.today() - timedelta(days=DAY_SPAN)).isoformat()
to = date.today().isoformat()
out, t0 = {}, time.time()

for n, (nm, (ex, tok, tsym, lot, tick)) in enumerate(sorted(live.items()), 1):
    rows = []
    # Open interest is asked for SECOND, not first. If Zerodha will not serve
    # OI together with the stitched-contract request, the candles still arrive
    # - which is the part a backtest actually needs.
    err = None
    for with_oi in (False, True):
        try:
            raw = kite.historical_data(tok, frm, to, "day",
                                       continuous=True, oi=with_oi) or []
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            if not with_oi:
                break
            continue
        if raw:
            rows = [[str(x["date"])[:10], float(x["open"]), float(x["high"]),
                     float(x["low"]), float(x["close"]),
                     float(x.get("volume") or 0), float(x.get("oi") or 0)]
                    for x in raw]
            err = None
    if err and not rows:
        print(f"    {nm}: {err}")
    if len(rows) > 200:
        out[nm] = {"symbol": tsym, "lot_size": lot, "tick_size": tick,
                   "front_expiry": str(ex), "bars": rows}
        print(f"  {n:>2}. {nm:<12}{len(rows):>6} days   "
              f"{rows[0][0]} -> {rows[-1][0]}   lot {lot}")
    else:
        print(f"  {n:>2}. {nm:<12}  only {len(rows)} days - skipped")
    time.sleep(PACE)

json.dump({"saved": datetime.now().isoformat(timespec="seconds"),
           "interval": "day", "continuous": True, "data": out},
          open(OUT, "w"))
tot = sum(len(v["bars"]) for v in out.values())
print(f"\n  DAILY DONE. {len(out)} commodities, {tot:,} candles "
      f"({time.time()-t0:.0f}s)\n")


# ── pass 2: does continuous work at fifteen minutes? ──────────────────────
def fetch_intra(tok, frm_d, to_d, cont):
    """Walk the range in chunks. Returns the rows, or raises."""
    rows, cur = [], frm_d
    while cur < to_d:
        nxt = min(cur + timedelta(days=INTRA_CHUNK), to_d)
        raw = kite.historical_data(tok, cur.isoformat(), nxt.isoformat(),
                                   INTRA, continuous=cont, oi=True) or []
        for x in raw:
            rows.append([str(x["date"])[:16], float(x["open"]),
                         float(x["high"]), float(x["low"]), float(x["close"]),
                         float(x.get("volume") or 0), float(x.get("oi") or 0)])
        cur = nxt
        time.sleep(PACE)
    return rows


print("  ── now the intraday, which is the part nobody could tell me about ──")
t1 = time.time()
probe_nm, probe = None, None
for nm in ("CRUDEOIL", "GOLDM", "SILVERM"):
    if nm in live:
        probe_nm, probe = nm, live[nm][1]
        break
cont_works = False
if probe:
    try:
        r = fetch_intra(probe, date.today() - timedelta(days=360),
                        date.today(), True)
        days = len({x[0][:10] for x in r})
        print(f"  continuous 15-minute on {probe_nm}: {len(r):,} bars over "
              f"{days} days  ({r[0][0] if r else '-'} -> "
              f"{r[-1][0] if r else '-'})")
        cont_works = days > 200
        if not cont_works:
            print("    -> that is only the live contract, not a stitched "
                  "history. Falling back to pass 3.")
    except Exception as e:
        print(f"  continuous 15-minute refused: {e}")
        print("    -> falling back to pass 3.")

outi, t2 = {}, time.time()
for nm in sorted(chain):
    merged = {}
    used = []
    contracts = chain[nm] if not cont_works else [chain[nm][0]]
    for ex, tok, tsym, lot, tick in contracts:
        try:
            r = fetch_intra(tok, date.today() - timedelta(days=int(YEARS_BACK*366)),
                            date.today(), cont_works)
        except Exception:
            continue
        if not r:
            continue
        used.append(f"{tsym}:{len(r)}")
        for row in r:
            # a later-expiring contract wins where two overlap, because it is
            # the one still being traded at that moment
            merged[row[0]] = row
    if len(merged) > 500:
        rows = [merged[k] for k in sorted(merged)]
        outi[nm] = {"lot_size": chain[nm][0][3], "tick_size": chain[nm][0][4],
                    "how": "continuous" if cont_works else "contracts stitched",
                    "from": used, "bars": rows}
        d0, d1 = rows[0][0][:10], rows[-1][0][:10]
        print(f"  {nm:<12}{len(rows):>8,} bars   {d0} -> {d1}   "
              f"{len({x[0][:10] for x in rows})} sessions")
    else:
        print(f"  {nm:<12}  only {len(merged)} bars - not enough to test")

json.dump({"saved": datetime.now().isoformat(timespec="seconds"),
           "interval": INTRA, "continuous": cont_works, "data": outi},
          open(OUT_I, "w"))

toti = sum(len(v["bars"]) for v in outi.values())
print(f"""
  DONE.
    DATA\\mcx_daily.json      {len(out)} commodities, {tot:,} daily candles
    DATA\\mcx_intraday.json   {len(outi)} commodities, {toti:,} 15-minute candles

  Tell Claude it has finished. He will run whichever engines the intraday
  history actually reaches - and say plainly which ones it does not.
  ({time.time()-t0:.0f}s)
""")
hold(0)
