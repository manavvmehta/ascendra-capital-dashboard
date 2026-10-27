#!/usr/bin/env python3
"""
backtest.py — run the swing rules over history and record what actually happened.

DOUBLE-CLICK THIS. First run takes about twenty minutes (it fetches three years
of daily bars for every stock). After that it reuses them and takes a minute.

WHAT IT DOES
    For every NSE stock, for every day in the past three years, it asks the
    SAME engine function the live desk asks: is this a BREAKOUT-20D signal?
    When the answer is yes, it takes the trade the way the engine would and
    follows it until it wins, loses, or times out.

    Nothing is re-implemented. It calls setup_breakout20 and daily_regime out
    of Jenil's file, so the rules being tested are the rules being traded.

HOW A TRADE IS SCORED
    entry     the NEXT day's open      (the signal only exists once the day has
                                        closed, so the close was not available)
    stop      the engine's stop
    T1        2R      - half comes off here, stop moves to breakeven
    T2        3.5R    - the rest comes off here, so a full winner is 2.75R
    stopped after T1                   -> +1.0R   (half at 2R, half at zero)
    stopped before T1                  -> -1.0R
    still open after 10 sessions       -> closed at that day's close

    WITHIN ONE DAY THE STOP IS ASSUMED TO HIT FIRST. A daily bar does not say
    whether the high or the low came first, and assuming the good one would
    flatter every single result.

WHAT IT WRITES
    DATA/backtest.json   — read by the BACKTEST tab on your dashboard
"""

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
BARS = os.path.join(DATA, "backtest_bars.json")
OUT = os.path.join(DATA, "backtest.json")

YEARS = 3
MIN_PRICE = 20
MIN_TURNOVER_CR = 5
MAX_SESSIONS = 10          # the engine holds a swing at most this long

os.makedirs(DATA, exist_ok=True)
os.environ.setdefault("TE_DATA_DIR", DATA)
sys.path.insert(0, ENGINE)


def hold(code=0):
    try:
        input("\n  Press Enter to close this window. ")
    except Exception:
        pass
    sys.exit(code)


def load_kite():
    keys, tokf = os.path.join(FEED, "kite_keys.txt"), os.path.join(FEED, "kite_token.json")
    if not (os.path.exists(keys) and os.path.exists(tokf)):
        print("\n  Not connected to Zerodha. Run connect.py in 5 LIVE FEED first.\n")
        hold(1)
    api_key = None
    for line in open(keys, encoding="utf8"):
        if line.strip().startswith("api_key"):
            api_key = line.split("=", 1)[-1].strip()
    tok = json.load(open(tokf, encoding="utf8"))
    if tok.get("date") != date.today().isoformat():
        print(f"\n  Today's login is missing (token is from {tok.get('date')}).")
        print("  Run connect.py in 5 LIVE FEED, then try again.\n")
        hold(1)
    from kiteconnect import KiteConnect
    k = KiteConnect(api_key=api_key)
    k.set_access_token(tok["access_token"])
    return k


def simulate(bars, i, call):
    """Take the call at bar i+1's OPEN and follow it to its end."""
    if i + 1 >= len(bars):
        return None
    entry = bars[i + 1]["open"]
    long = call["side"] == "LONG"
    sl0 = call["sl"]
    # The engine's levels are computed off the signal close. Entry is the next
    # open, which may gap. Risk is measured from the ACTUAL entry against the
    # ORIGINAL stop - never re-derived later, or every winner is mis-scaled.
    risk = (entry - sl0) if long else (sl0 - entry)
    if risk <= 0:
        return {"outcome": "skipped_gap_through_stop", "r": None}
    t1 = entry + 2.0 * risk if long else entry - 2.0 * risk
    t2 = entry + 3.5 * risk if long else entry - 3.5 * risk
    stop = sl0
    half = False

    for j in range(i + 2, min(i + 2 + MAX_SESSIONS, len(bars))):
        b = bars[j]
        hit_stop = (b["low"] <= stop) if long else (b["high"] >= stop)
        hit_t2 = (b["high"] >= t2) if long else (b["low"] <= t2)
        hit_t1 = (b["high"] >= t1) if long else (b["low"] <= t1)

        if hit_stop:                       # adverse side first, always
            return {"outcome": "T1_THEN_BE" if half else "SL_HIT",
                    "r": 1.0 if half else -1.0, "days": j - i - 1,
                    "exit": round(stop, 2)}
        if hit_t2:
            return {"outcome": "T2_HIT", "r": 2.75, "days": j - i - 1,
                    "exit": round(t2, 2)}
        if hit_t1 and not half:
            half = True
            stop = entry                   # breakeven on the remainder
    j = min(i + 1 + MAX_SESSIONS, len(bars) - 1)
    last = bars[j]["close"]
    rest = (last - entry) / risk if long else (entry - last) / risk
    return {"outcome": "TIME_EXIT", "r": round((2.0 + rest) / 2 if half else rest, 4),
            "days": j - i - 1, "exit": round(last, 2)}


def main():
    print("=" * 64)
    print("  BACKTEST — the swing rules, over three years of real prices")
    print("=" * 64)
    kite = load_kite()
    import tech_calls as T

    print("\n  Loading instruments ...")
    stocks = []
    for row in kite.instruments("NSE"):
        if row.get("segment") != "NSE" or row.get("instrument_type") != "EQ":
            continue
        s = row["tradingsymbol"]
        if T._is_etfish(s, row.get("name", "")):
            continue
        if s.isalpha() or "-" not in s:
            stocks.append((s, row["instrument_token"]))
    print(f"  {len(stocks)} equities.")

    cache = {}
    if os.path.exists(BARS):
        try:
            c = json.load(open(BARS, encoding="utf8"))
            if c.get("years") == YEARS and c.get("date", "")[:7] == date.today().isoformat()[:7]:
                cache = c.get("bars", {})
                print(f"  {len(cache)} already have history cached.")
        except Exception:
            pass

    todo = [(s, t) for s, t in stocks if s not in cache]
    if todo:
        print(f"  Fetching {YEARS} years of daily bars for {len(todo)} stocks")
        print(f"  — about {len(todo)*0.36/60:.0f} minutes. Leave it running.\n")
    now = datetime.now()
    frm = now - timedelta(days=365 * YEARS + 60)
    for i, (sym, tok) in enumerate(todo, 1):
        try:
            cd = kite.historical_data(tok, frm, now, "day")
            cache[sym] = [{"date": str(c["date"])[:10], "open": c["open"],
                           "high": c["high"], "low": c["low"], "close": c["close"],
                           "volume": c.get("volume", 0)} for c in cd]
        except Exception:
            pass
        if i % 100 == 0:
            print(f"     {100*i/len(todo):5.1f}%   {i} of {len(todo)}", flush=True)
            json.dump({"years": YEARS, "date": date.today().isoformat(), "bars": cache},
                      open(BARS, "w", encoding="utf8"))
        time.sleep(0.36)
    if todo:
        json.dump({"years": YEARS, "date": date.today().isoformat(), "bars": cache},
                  open(BARS, "w", encoding="utf8"))

    print(f"\n  Replaying {len(cache)} stocks day by day ...\n")
    trades, sigs, done = [], 0, 0
    t0 = time.time()

    for sym, bars in cache.items():
        done += 1
        if done % 250 == 0:
            print(f"     {100*done/len(cache):5.1f}%   {len(trades)} trades so far",
                  flush=True)
        if len(bars) < 90:
            continue
        highs = [b["high"] for b in bars]
        for i in range(60, len(bars) - 2):
            # CHEAP PRE-CHECK. Running the full regime on every stock-day would
            # be a million ADX calculations. A 20-day breakout is a hard
            # requirement of the setup, so days that fail it cannot produce a
            # call and are skipped. This changes no rule - only what gets asked.
            c_ = bars[i]["close"]
            hi20 = max(highs[i-20:i])
            if not (c_ > hi20 and (c_ - hi20) <= 0.015 * c_):
                continue
            win = bars[:i+1]
            if c_ < MIN_PRICE:
                continue
            v20 = win[-20:]
            turn = sum(b["close"] * b.get("volume", 0) for b in v20) / 20 / 1e7
            if turn < MIN_TURNOVER_CR:
                continue
            try:
                reg = T.daily_regime(win)
                call = T.setup_breakout20(sym, "EQ", reg, win)
            except Exception:
                continue
            if not call:
                continue
            sigs += 1
            res = simulate(bars, i, call)
            if not res or res.get("r") is None:
                continue
            trades.append({"symbol": sym, "date": bars[i]["date"],
                           "entry_date": bars[i+1]["date"],
                           "side": call["side"], "score": call["score"],
                           "entry": round(bars[i+1]["open"], 2), "sl": call["sl"],
                           "t1": call["t1"], "t2": call["t2"],
                           "outcome": res["outcome"], "r": res["r"],
                           "days": res.get("days"), "exit": res.get("exit")})

    if not trades:
        print("  No trades. That is suspicious rather than informative — tell Claude.")
        hold(1)

    rs = [t["r"] for t in trades]
    n = len(rs)
    wins = [r for r in rs if r > 0]
    mean = sum(rs) / n
    sd = (sum((r - mean) ** 2 for r in rs) / (n - 1)) ** 0.5 if n > 1 else 0
    half = 1.96 * sd / (n ** 0.5) if n else 0

    by_year, by_out, by_score = {}, {}, {}
    for t in trades:
        y = t["entry_date"][:4]
        by_year.setdefault(y, []).append(t["r"])
        by_out[t["outcome"]] = by_out.get(t["outcome"], 0) + 1
        b = f"{int(t['score'])}"
        by_score.setdefault(b, []).append(t["r"])

    def stat(v):
        if not v:
            return None
        m = sum(v) / len(v)
        s = (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5 if len(v) > 1 else 0
        h = 1.96 * s / (len(v) ** 0.5)
        return {"n": len(v), "win": round(100 * len([x for x in v if x > 0]) / len(v), 1),
                "mean": round(m, 4), "lo": round(m - h, 4), "hi": round(m + h, 4),
                "total": round(sum(v), 1)}

    eq, cum = [], 0.0
    for t in sorted(trades, key=lambda x: x["entry_date"]):
        cum += t["r"]
        eq.append({"d": t["entry_date"], "c": round(cum, 3)})
    peak, dd = 0, 0
    for p in eq:
        peak = max(peak, p["c"])
        dd = min(dd, p["c"] - peak)

    out = {
        "built": datetime.now().isoformat(timespec="seconds"),
        "years": YEARS, "stocks": len(cache), "signals": sigs,
        "from": min(t["entry_date"] for t in trades),
        "to": max(t["entry_date"] for t in trades),
        "overall": stat(rs), "max_drawdown_r": round(dd, 1),
        "avg_days": round(sum(t["days"] or 0 for t in trades) / n, 1),
        "by_year": {k: stat(v) for k, v in sorted(by_year.items())},
        "by_score": {k: stat(v) for k, v in sorted(by_score.items())},
        "outcomes": by_out,
        "equity": eq[::max(1, len(eq)//600)],
        "trades": sorted(trades, key=lambda t: t["entry_date"], reverse=True)[:500],
    }
    json.dump(out, open(OUT, "w", encoding="utf8"), separators=(",", ":"))

    # EVERY TRADE, IN ITS OWN FILE (29/09). backtest.json keeps only the most
    # recent 500 trades because the BACKTEST tab has to load it in a browser.
    # That is the right call for the dashboard and the wrong one for studying
    # the record - joining open interest, or anything else, to the run needs
    # all of them, with the date and symbol intact.
    #
    # This is written SEPARATELY and read by nothing on the desk. backtest.json
    # above is untouched, so the tab renders exactly as before.
    try:
        json.dump({"built": date.today().isoformat(), "years": YEARS,
                   "n": len(trades),
                   "trades": sorted(trades, key=lambda t: t["entry_date"])},
                  open(os.path.join(DATA, "backtest_trades.json"), "w",
                       encoding="utf8"), separators=(",", ":"))
        print(f"   All {len(trades):,} trades also written to "
              f"DATA\\backtest_trades.json (nothing on the desk reads it).")
    except Exception as e:
        print(f"   Could not write backtest_trades.json: {e}")

    o = out["overall"]
    print(f"""
  ----------------------------------------------------------------
   {n:,} trades   {out['from']} to {out['to']}   {len(cache)} stocks
  ----------------------------------------------------------------

     win rate        {o['win']}%     ({len(wins)} of {n})
     average         {o['mean']:+.3f}R per trade
     95% interval    {o['lo']:+.3f}R to {o['hi']:+.3f}R
     total           {o['total']:+.0f}R
     worst drawdown  {out['max_drawdown_r']:.0f}R
     held on average {out['avg_days']} sessions

  ----------------------------------------------------------------""")
    # An interval that misses zero means the result is RELIABLE, not GOOD.
    # The sign of the mean decides which. Saying "the edge is real" over a
    # negative average - as this did - reads as an endorsement of a losing
    # system, which is the worst thing this script could print.
    if o["lo"] <= 0 <= o["hi"]:
        print("""
   NOT PROVEN. The interval crosses zero, so the edge is not
   established. The average may look positive but the data cannot
   rule out that the true value is zero or worse.
""")
    elif o["mean"] > 0:
        print("""
   THESE RULES MADE MONEY. The interval sits entirely above zero,
   so this is unlikely to be chance. That is a statement about the
   past three years, not a promise about the next one.
""")
    else:
        print(f"""
   THESE RULES LOST MONEY.

   The interval sits entirely BELOW zero. This is not too small a
   sample and not an unlucky run - on {n:,} trades the loss is the
   result. Trading these rules as they stand would have cost money,
   not made it.

   Before concluding the setup is worthless, note that entry here is
   the NEXT DAY'S OPEN - the earliest price a daily-bar test can
   honestly claim. The live engine enters intraday, the moment the
   breakout happens. That difference is real and untested.
""")
    print("   Written to DATA\\backtest.json — restart desk.py to see it on")
    print("   your dashboard under BACKTEST.\n")
    hold()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Stopped. Bars fetched so far are cached.\n")
        hold(1)
    except Exception as e:
        print(f"\n  Something went wrong: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        print("\n  Screenshot this window and send it to Claude.\n")
        hold(1)
