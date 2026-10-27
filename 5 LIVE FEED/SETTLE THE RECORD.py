#!/usr/bin/env python3
"""
SETTLE THE RECORD.py  -  DOUBLE-CLICK ME, after you have connected to Zerodha.

WHAT THIS IS FOR
  Every call the SMC engine threw away is unanswerable from the files on this
  machine, because the engine keeps only DAILY candles. The question "did price
  really come back to my entry after the breakout?" needs 15-MINUTE candles, and
  those are downloaded every quarter hour and then discarded.

  Zerodha still has them. This pulls them once, for the handful of stocks that
  matter, and settles every thrown-away call exactly - filled or not, at what
  price, for how much.

WHAT IT DOES NOT DO
  It places no orders. It does not modify smc2_log.json, the desk, or any engine.
  It only READS your log and WRITES two new files:

      DATA/smc2_15m.json      the raw 15-minute candles it downloaded, kept so
                              this never has to be guessed at again
      DATA/smc2_settled.json  one verdict per thrown-away call

HOW IT JUDGES
  It does not re-implement the rules. It imports step() and BOOKS straight out of
  SMC TWO BOOKS.py, so the rules applied here are the rules being traded. The one
  thing it deliberately changes: broke_seen is cleared, so the pullback is looked
  for across the call's WHOLE window rather than only from the moment the engine
  happened to notice the breakout. That is the difference between "could the
  engine see it" and "did it happen" - and the second is what you asked.

  Run it any time. It needs today's Zerodha login, same as the engine.
"""

import importlib.util
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))


def hold(code=0):
    try:
        input("\n  Press Enter to close this window. ")
    except Exception:
        pass
    sys.exit(code)


def load_engine():
    """Import SMC TWO BOOKS.py by path - its name has spaces in it."""
    path = os.path.join(HERE, "SMC TWO BOOKS.py")
    if not os.path.exists(path):
        print(f"\n  CANNOT FIND the engine next to this script:\n    {path}\n")
        hold(1)
    spec = importlib.util.spec_from_file_location("smc_two_books", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["smc_two_books"] = mod
    spec.loader.exec_module(mod)          # safe: main() is behind __main__
    return mod


print("\n  SETTLING THE RECORD - the calls the engine threw away\n")
E = load_engine()
DATA = E.DATA
LOGF = E.LOGF
SLOT = 200000.0

# ── which calls need answering ────────────────────────────────────────────
# Everything that was given up on or voided, but only if it ever broke out -
# a call that never broke out was never a trade and is left alone.
WANTED = (
    "NEVER CAME BACK",
    "VOID - filled outside its window",
    "VOID - filled on its own breakout candle",
    "VOID - target taken on its own fill candle",
    "VOID - settled from history, never alerted",
)

try:
    L = json.load(open(LOGF, encoding="utf8"))
except Exception as e:
    print(f"  COULD NOT READ {LOGF}\n    {e}\n")
    hold(1)

jobs = {k: v for k, v in L.items()
        if str(v.get("status")) in WANTED and v.get("broke_at")}
if not jobs:
    print("  Nothing to settle - no thrown-away call has a breakout on it.\n")
    hold(0)

syms = sorted({v["sym"] for v in jobs.values()})
first = min(str(v["broke_at"])[:10] for v in jobs.values())
need_days = (date.today() - date.fromisoformat(first)).days + 5
print(f"  {len(jobs)} calls to settle across {len(syms)} stocks, "
      f"back to {first} ({need_days} days of candles)\n")

# ── the candles ───────────────────────────────────────────────────────────
kite = E.load_kite()
print("  connected. reading the instrument list...")
inst = kite.instruments("NSE")
tokens = {i["tradingsymbol"]: i["instrument_token"] for i in inst
          if i.get("segment") == "NSE"}
missing = [s for s in syms if s not in tokens]
if missing:
    print(f"  no instrument token for: {', '.join(missing)}")

print(f"  downloading 15-minute candles for {len(syms)} stocks...")
BARS = E.fetch_15m(kite, tokens, syms, days=max(need_days, 45))
print(f"  got candles for {len(BARS)} of {len(syms)} stocks")

barf = os.path.join(DATA, "smc2_15m.json")
json.dump({"built": datetime.now().isoformat(timespec="seconds"),
           "from": first, "bars": BARS}, open(barf, "w"))
print(f"  saved the candles -> {os.path.basename(barf)}\n")

# ── settle ────────────────────────────────────────────────────────────────
def settle(call, bars):
    """Replay the engine's own step() over real 15-minute candles."""
    c = dict(call)
    c["state"] = "BROKE"
    c["status"] = "BROKE OUT"
    c["broke_seen"] = None            # look across the whole window, not from
    c["filled_at"] = None             # the moment the engine happened to look
    c["closed_at"] = None
    c["exit_px"] = None
    c["result_pct"] = None
    for _ in range(6):                # step() advances one state at a time
        before = (c["state"], c.get("filled_at"), c.get("exit_px"))
        try:
            E.step(c, bars)
        except Exception as ex:
            return None, f"could not be judged: {ex}"
        if (c["state"], c.get("filled_at"), c.get("exit_px")) == before:
            break
    return c, None


rows = []
for key, call in sorted(jobs.items(), key=lambda kv: str(kv[1]["broke_at"])):
    bars = BARS.get(call["sym"])
    if not bars:
        rows.append({"key": key, "sym": call["sym"], "book": call["book"],
                     "verdict": "NO CANDLES", "money": None})
        continue
    c, err = settle(call, bars)
    if err:
        rows.append({"key": key, "sym": call["sym"], "book": call["book"],
                     "verdict": err, "money": None})
        continue
    filled = c.get("filled_at") is not None
    pct = c.get("result_pct")
    rows.append({
        "key": key, "sym": call["sym"], "book": call["book"],
        "was": str(call.get("status")),
        "at": call.get("at"), "broke_at": call.get("broke_at"),
        "entry": call.get("entry"), "stop": call.get("stop"),
        "target": call.get("target"),
        "filled_at": c.get("filled_at"), "closed_at": c.get("closed_at"),
        "exit_px": c.get("exit_px"), "result_pct": pct,
        "money": (pct / 100 * SLOT) if pct is not None else None,
        "verdict": ("REAL TRADE - " + str(c.get("status"))) if filled
                   else "never filled - " + str(c.get("status")),
    })

out = os.path.join(DATA, "smc2_settled.json")
json.dump({"built": datetime.now().isoformat(timespec="seconds"),
           "slot": SLOT, "rows": rows}, open(out, "w"), indent=1)

# ── what it found ─────────────────────────────────────────────────────────
def rupees(v):
    return ("-" if v < 0 else "+") + "Rs " + f"{abs(round(v)):,}"

real = [r for r in rows if r.get("filled_at")]
none_ = [r for r in rows if not r.get("filled_at") and r.get("money") is None
         and "NO CANDLES" not in str(r["verdict"])]
nobar = [r for r in rows if "NO CANDLES" in str(r["verdict"])]

print("  " + "-" * 74)
print(f"  {'STOCK':12}{'BOOK':6}{'ENTRY':>9}{'EXIT':>9}{'RESULT':>9}"
      f"{'MONEY':>12}  WHAT HAPPENED")
print("  " + "-" * 74)
for r in sorted(real, key=lambda r: -(r["money"] or 0)):
    pct = "%+.2f%%" % r["result_pct"] if r["result_pct"] is not None else "-"
    what = r["verdict"].replace("REAL TRADE - ", "")
    print("  %-12s%-6s%9s%9s%9s%12s  %s" % (
        r["sym"], r["book"][:5], r["entry"], r["exit_px"], pct,
        rupees(r["money"]), what))
tot = sum(r["money"] for r in real if r["money"] is not None)
wins = sum(1 for r in real if (r["result_pct"] or 0) > 0)
print("  " + "-" * 74)
print(f"\n  {len(real)} of these {len(rows)} thrown-away calls WERE REAL TRADES")
if real:
    print(f"  {wins} made money, {len(real)-wins} lost     net {rupees(tot)}"
          f"     Rs {round(tot/len(real)):,} a trade")
print(f"  {len(none_)} genuinely never filled - price did not come back")
if nobar:
    print(f"  {len(nobar)} could not be judged - no candles came back for them")
print(f"\n  written -> DATA\\{os.path.basename(out)}")
print(f"  candles -> DATA\\{os.path.basename(barf)}\n")
print("  Nothing else was changed. Your log, your desk and the engines are untouched.\n")
hold(0)
