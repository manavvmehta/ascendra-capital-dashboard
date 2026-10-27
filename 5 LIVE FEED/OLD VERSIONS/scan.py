#!/usr/bin/env python3
"""
scan.py — scan the whole market right now and write today's swing calls.

DOUBLE-CLICK THIS after the market closes. Takes about ten minutes the first
time, seconds afterwards.

WHY THIS EXISTS
    The live desk hunts intraday setups, which need the market open. This runs
    the SWING setup - BREAKOUT-20D - which works on daily bars and therefore
    works after the close, when the day's candle is final.

    It does not invent anything. It calls the SAME functions in the same engine
    file that the live desk calls. Same rules, same levels, same thresholds.

WHAT IT PRODUCES
    Calls written into DATA/calls_log.csv, with entry, stop loss and both
    targets. Restart the desk afterwards and they appear on your dashboard.

WHAT IT FILTERS OUT
    Anything under Rs 20 a share, or trading less than Rs 5 crore a day
    averaged over 20 days. Those are the engine's own tradability limits - a
    call on a stock you cannot get filled in is not a call.
"""

import csv
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))


def project_root():
    d = HERE
    for _ in range(4):
        if os.path.isdir(os.path.join(d, "WEBSITE")) and \
           os.path.isdir(os.path.join(d, "5 LIVE FEED")):
            return d
        p = os.path.dirname(d)
        if p == d:
            break
        d = p
    return HERE


ROOT = project_root()
FEED = os.path.join(ROOT, "5 LIVE FEED")
ENGINE = os.path.join(ROOT, "3 CALL ENGINE")
DATA = os.path.join(ROOT, "DATA")
LOG = os.path.join(DATA, "calls_log.csv")
CACHE = os.path.join(DATA, "daily_bars.json")

MIN_PRICE = 20              # engine's own tradability floor
MIN_TURNOVER_CR = 5         # 20-day average, in crores
HIST_DAYS = 200

os.makedirs(DATA, exist_ok=True)
os.environ.setdefault("TE_DATA_DIR", DATA)
sys.path.insert(0, ENGINE)

FIELDS = ["date", "created_hm", "symbol", "seg", "setup", "side", "tf", "score",
          "conviction", "entry", "sl", "t1", "t2", "rr", "risk0", "status", "r",
          "triggered_at", "closed_at", "horizon", "level", "reasons"]


def hold(code=0):
    try:
        input("\n  Press Enter to close this window. ")
    except Exception:
        pass
    sys.exit(code)


def load_kite():
    keys = os.path.join(FEED, "kite_keys.txt")
    tokf = os.path.join(FEED, "kite_token.json")
    if not (os.path.exists(keys) and os.path.exists(tokf)):
        print("\n  Not connected to Zerodha. Run connect.py in 5 LIVE FEED first.\n")
        hold(1)
    api_key = None
    for line in open(keys, encoding="utf8"):
        if line.strip().startswith("api_key"):
            api_key = line.split("=", 1)[-1].strip()
    tok = json.load(open(tokf, encoding="utf8"))
    if tok.get("date") != date.today().isoformat():
        print(f"\n  Today's login is missing (saved token is from {tok.get('date')}).")
        print("  Run connect.py in 5 LIVE FEED, then try again.\n")
        hold(1)
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        print("\n  Zerodha's library is missing. Run desk.py once first.\n")
        hold(1)
    k = KiteConnect(api_key=api_key)
    k.set_access_token(tok["access_token"])
    return k


def main():
    print("=" * 62)
    print("  SCAN THE WHOLE MARKET — swing setup, today's closing data")
    print("=" * 62)
    kite = load_kite()
    import tech_calls as T

    print("\n  Loading the instrument list ...")
    stocks = []
    for row in kite.instruments("NSE"):
        if row.get("segment") != "NSE" or row.get("instrument_type") != "EQ":
            continue
        sym = row["tradingsymbol"]
        if T._is_etfish(sym, row.get("name", "")):
            continue                       # ETFs pass volume screens but do not move
        if sym.isalpha() or "-" not in sym:
            stocks.append((sym, row["instrument_token"]))
    print(f"  {len(stocks)} equities to look at.")

    cache = {}
    if os.path.exists(CACHE):
        try:
            c = json.load(open(CACHE, encoding="utf8"))
            if c.get("date") == date.today().isoformat():
                cache = c.get("bars", {})
                print(f"  {len(cache)} already have today's bars cached.")
        except Exception:
            pass

    todo = [(s, t) for s, t in stocks if s not in cache]
    if todo:
        mins = len(todo) * 0.36 / 60
        print(f"  Fetching daily bars for {len(todo)} — about {mins:.0f} minutes.")
        print("  (Zerodha limits how fast this can go. Leave it running.)\n")
    now = datetime.now()
    frm = now - timedelta(days=HIST_DAYS)
    fetched = failed = 0
    for i, (sym, tok) in enumerate(todo, 1):
        try:
            cd = kite.historical_data(tok, frm, now, "day")
            cache[sym] = [{"date": str(c["date"])[:10], "open": c["open"],
                           "high": c["high"], "low": c["low"], "close": c["close"],
                           "volume": c.get("volume", 0)} for c in cd[-140:]]
            fetched += 1
        except Exception:
            failed += 1
        if i % 100 == 0:
            print(f"     {100*i/len(todo):5.1f}%   {i} of {len(todo)}", flush=True)
            json.dump({"date": date.today().isoformat(), "bars": cache},
                      open(CACHE, "w", encoding="utf8"))
        time.sleep(0.36)
    if todo:
        json.dump({"date": date.today().isoformat(), "bars": cache},
                  open(CACHE, "w", encoding="utf8"))
        print(f"\n  Got {fetched}, failed {failed}.")

    print("\n  Running the breakout rules ...")
    fo_names = set()
    try:
        for row in kite.instruments("NFO"):
            if row.get("instrument_type") == "FUT":
                fo_names.add(row.get("name", ""))
    except Exception:
        pass

    calls, thin, checked = [], 0, 0
    for sym, bars in cache.items():
        if len(bars) < 60:
            continue
        last = bars[-1]
        if not last.get("close") or last["close"] < MIN_PRICE:
            continue
        v20 = bars[-20:]
        turn_cr = sum(b["close"] * b.get("volume", 0) for b in v20) / len(v20) / 1e7
        if turn_cr < MIN_TURNOVER_CR:
            thin += 1
            continue
        checked += 1
        try:
            reg = T.daily_regime(bars)
            c = T.setup_breakout20(sym, "EQ", reg, bars, can_short=(sym in fo_names))
        except Exception:
            continue
        if c:
            c["date"] = last["date"]
            c["turnover_cr"] = round(turn_cr, 1)
            calls.append(c)

    calls.sort(key=lambda c: -c["score"])
    print(f"  Checked {checked} tradable stocks ({thin} too thin to trade).")
    print(f"  {len(calls)} calls.\n")

    if not calls:
        print("  Nothing broke out today that passed every rule.")
        print("  That is the engine being selective, not broken — his own")
        print("  record averages a handful of swing calls a day.\n")
        hold()

    rows = []
    stamp = datetime.now().strftime("%H:%M")
    for c in calls:
        why = c.get("reasons")
        rows.append({
            "date": c["date"], "created_hm": stamp, "symbol": c["symbol"],
            "seg": "EQ", "setup": c["setup"], "side": c["side"], "tf": "SWING",
            "score": c["score"], "conviction": c["conviction"],
            "entry": c["entry"], "sl": c["sl"], "t1": c["t1"], "t2": c["t2"],
            "rr": c["rr"], "risk0": c["risk0"], "status": "WAITING", "r": "",
            "triggered_at": "", "closed_at": "",
            "horizon": c.get("horizon", "3-10 sessions"), "level": c.get("level", ""),
            "reasons": " · ".join(why) if isinstance(why, (list, tuple)) else (why or ""),
        })

    seen = set()
    if os.path.exists(LOG):
        for r in csv.DictReader(open(LOG, encoding="utf8")):
            seen.add((r.get("date"), r.get("symbol"), r.get("setup")))
    fresh = [r for r in rows if (r["date"], r["symbol"], r["setup"]) not in seen]

    new_file = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for r in fresh:
            w.writerow({k: r.get(k, "") for k in FIELDS})

    print("  " + "-" * 58)
    print(f"  {'SYMBOL':<12}{'SIDE':<7}{'ENTRY':>10}{'STOP':>10}"
          f"{'TARGET 1':>11}{'TARGET 2':>11}  SCORE")
    print("  " + "-" * 58)
    for r in rows[:30]:
        print(f"  {r['symbol']:<12}{r['side']:<7}{r['entry']:>10.2f}{r['sl']:>10.2f}"
              f"{r['t1']:>11.2f}{r['t2']:>11.2f}   {r['score']:.1f}")
    if len(rows) > 30:
        print(f"  ... and {len(rows)-30} more, all written to the log.")
    print("  " + "-" * 58)
    print(f"""
  {len(fresh)} new calls written to DATA\\calls_log.csv
  ({len(rows)-len(fresh)} were already there from an earlier run today.)

  Every one is a LONG or SHORT with an entry, a stop loss and two
  targets. Entry is today's close — you would act at tomorrow's open.

  NEXT: restart desk.py and they appear on your dashboard.
""")
    hold()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Stopped. Bars fetched so far are cached — run it again "
              "and it carries on.\n")
        hold(1)
    except Exception as e:
        print(f"\n  Something went wrong: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        print("\n  Screenshot this window and send it to Claude.\n")
        hold(1)
