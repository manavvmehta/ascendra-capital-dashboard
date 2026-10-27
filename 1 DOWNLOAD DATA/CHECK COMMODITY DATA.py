#!/usr/bin/env python3
"""
CHECK COMMODITY DATA.py

The download came back empty - DATA\\mcx_daily.json has no commodities in it -
so this asks Zerodha the same questions one at a time and prints the raw
answer to each, including the exact error text.

It downloads nothing and writes nothing. Twenty seconds.

Run connect.py first, then double-click this, then send Claude a photo of
this window.
"""
import json
import os
import sys
import traceback
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FEED = os.path.join(ROOT, "5 LIVE FEED")


def hold(code=0):
    try:
        input("\n  Press Enter to close. ")
    except Exception:
        pass
    sys.exit(code)


print(__doc__)
try:
    keys = os.path.join(FEED, "kite_keys.txt")
    tok = json.load(open(os.path.join(FEED, "kite_token.json"), encoding="utf8"))
    api_key = None
    for line in open(keys, encoding="utf8"):
        if line.strip().startswith("api_key"):
            api_key = line.split("=", 1)[-1].strip()
    print(f"  saved login is from : {tok.get('date')}")
    print(f"  today is            : {date.today().isoformat()}")
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(tok["access_token"])
    print("  connected\n")
except Exception as e:
    print(f"  COULD NOT CONNECT: {e}")
    hold(1)

# 1. what does the MCX instrument list actually contain?
try:
    instr = kite.instruments("MCX")
    print(f"  1. MCX instruments returned : {len(instr):,}")
    futs = [r for r in instr if r.get("instrument_type") == "FUT"]
    print(f"     of which FUT             : {len(futs):,}")
    names = sorted({(r.get("name") or "").upper() for r in futs})
    print(f"     distinct names           : {len(names)}")
    print(f"     first 25                 : {', '.join(names[:25])}")
    if futs:
        r = futs[0]
        print(f"\n     one row, in full:")
        for k in sorted(r):
            print(f"       {k:<20}{r[k]}")
except Exception:
    print("  1. INSTRUMENT LIST FAILED:")
    traceback.print_exc()
    hold(1)

# 2. pick a real crude contract and ask for its history four ways
pick = None
for want in ("CRUDEOIL", "GOLDM", "SILVERM", "NATGASMINI"):
    c = [r for r in futs if (r.get("name") or "").upper() == want]
    if c:
        c.sort(key=lambda r: str(r.get("expiry")))
        pick = (want, c[0])
        break

if not pick:
    print("\n  2. none of CRUDEOIL / GOLDM / SILVERM / NATGASMINI are listed.")
    print("     The names above are what MCX is actually offering.")
    hold(0)

want, row = pick
tokn = row["instrument_token"]
print(f"\n  2. testing on {row['tradingsymbol']}  (token {tokn}, "
      f"expiry {row.get('expiry')})\n")

tries = [
    ("day,      60 days,  plain",       "day",      60,  False),
    ("day,      60 days,  continuous",  "day",      60,  True),
    ("day,    2000 days,  continuous",  "day",      2000, True),
    ("15minute, 60 days,  plain",       "15minute", 60,  False),
    ("15minute, 60 days,  continuous",  "15minute", 60,  True),
    ("15minute,180 days,  continuous",  "15minute", 180, True),
    ("60minute,365 days,  continuous",  "60minute", 365, True),
]
for label, interval, days, cont in tries:
    frm = (date.today() - timedelta(days=days)).isoformat()
    to = date.today().isoformat()
    try:
        r = kite.historical_data(tokn, frm, to, interval, continuous=cont)
        if r:
            d0, d1 = str(r[0]["date"])[:16], str(r[-1]["date"])[:16]
            sess = len({str(x["date"])[:10] for x in r})
            print(f"     {label:<34}{len(r):>7,} bars  {sess:>4} sessions  "
                  f"{d0} -> {d1}")
        else:
            print(f"     {label:<34}      0 bars  (empty, no error)")
    except Exception as e:
        print(f"     {label:<34}ERROR: {type(e).__name__}: {e}")

print("""
  WHAT TO DO NEXT
     Send Claude a photo of this whole window. The lines above say exactly
     which requests Zerodha honours and which it refuses, and that decides
     how much of the commodity work is possible.
""")
hold(0)
