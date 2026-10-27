#!/usr/bin/env python3
"""
intraday_data.py  —  one-off download of 15-minute bars.

WHY THIS EXISTS
    Everything tested so far used DAILY prices. An intraday study needs
    intraday prices, and Zerodha only gives them on request.

WHAT IT DOES
    Picks the most liquid stocks, downloads a year of 15-minute candles for
    each, and writes them to  NSE FO BACKTEST\\intraday_15m.csv.gz.

WHEN TO RUN IT
    After the market closes. It shares Zerodha's rate limit with the live
    desk, so running both at once slows the desk down.

HOW LONG
    Roughly five minutes. It saves as it goes, so if it stops halfway you can
    run it again and it picks up where it left off.
"""

import csv
import gzip
import json
import os
import statistics
import sys
import time
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))


def _project_root():
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


ROOT = _project_root()
FEED = os.path.join(ROOT, "5 LIVE FEED")
DATA = os.path.join(ROOT, "DATA")
# All the NSE F&O study work lives in one folder, apart from the trading
# record. DATA is the desk's own working memory - the permanent call log and
# the engine's state files - and nothing experimental belongs next to it,
# because that is how someone eventually deletes the wrong thing.
#
# NOTE: this data is for the F&O study ONLY. Jenil's five setups are NOT to be
# re-tested or re-tuned with it. If that is ever wanted it is a separate job,
# run separately, on MM's explicit say-so.
STUDY = os.path.join(ROOT, "NSE FO BACKTEST")
OUT = os.path.join(STUDY, "intraday_15m.csv.gz")
PART = os.path.join(STUDY, ".intraday_15m.partial.json")

DAYS = 365                 # how much history
INTERVAL = "15minute"
CHUNK = 190                # Kite allows 200 days per request at this interval
MIN_TURNOVER_CR = 25       # only stocks that genuinely trade
MAX_STOCKS = 300

G, R, Y, DIM, OFF = "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[0m"
if os.name == "nt":
    try:
        import ctypes
        h = ctypes.windll.kernel32.GetStdHandle(-11)
        m = ctypes.c_ulong()
        ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(m))
        ctypes.windll.kernel32.SetConsoleMode(h, m.value | 0x0004)
    except Exception:
        G = R = Y = DIM = OFF = ""


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
        print(f"\n  {R}Not connected to Zerodha.{OFF}")
        print("  Open ASCENDRA.py and press 1 first.\n")
        hold(1)
    api_key = None
    for line in open(keys, encoding="utf8"):
        if line.strip().startswith("api_key"):
            api_key = line.split("=", 1)[-1].strip()
    tok = json.load(open(tokf, encoding="utf8"))
    if tok.get("date") != date.today().isoformat():
        print(f"\n  {Y}Today's Zerodha login is missing.{OFF}")
        print("  Open ASCENDRA.py and press 1, then run this again.\n")
        hold(1)
    try:
        import kiteconnect
    except ImportError:
        print(f"\n  {R}Zerodha's library is not installed.{OFF}")
        print("  Start the desk once (ASCENDRA.py, option 2) - it installs it.\n")
        hold(1)
    k = kiteconnect.KiteConnect(api_key=api_key)
    k.set_access_token(tok["access_token"])
    try:
        k.quote(["NSE:RELIANCE"])
    except Exception as e:
        print(f"\n  {R}Zerodha refused the connection: {e}{OFF}\n")
        hold(1)
    print(f"  Connected as {tok.get('user_name','')}.")
    return k


def liquid_universe(kite):
    """The stocks worth studying.

    A study of stocks you could never trade tells you nothing. The daily cache
    the desk already keeps has the volume history, so the shortlist costs no
    extra requests. If it is missing, fall back to asking Zerodha.
    """
    cache_path = os.path.join(DATA, ".tech_daily_cache.json")
    turnover = {}
    if os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path, encoding="utf8"))
            for sym, blob in cache.items():
                bars = blob.get("d") if isinstance(blob, dict) else blob
                if not bars or len(bars) < 40:
                    continue
                vals = [(b.get("close") or 0) * (b.get("volume") or 0)
                        for b in bars[-20:]]
                t = statistics.median(vals) / 1e7          # crores per day
                if t >= MIN_TURNOVER_CR:
                    turnover[sym] = t
            print(f"  {len(turnover)} stocks trade more than "
                  f"Rs {MIN_TURNOVER_CR} crore a day.")
        except Exception as e:
            print(f"  (could not read the daily cache: {e})")

    tokens = {}
    for row in kite.instruments("NSE"):
        if row.get("segment") == "NSE" and row.get("instrument_type") == "EQ":
            tokens[row["tradingsymbol"]] = row["instrument_token"]

    if not turnover:
        print(f"  {Y}No volume history cached - taking the whole equity list.{OFF}")
        picks = sorted(tokens)[:MAX_STOCKS]
        return [(s, tokens[s]) for s in picks]

    ranked = sorted(turnover.items(), key=lambda kv: -kv[1])
    out = []
    for sym, _t in ranked:
        if sym in tokens:
            out.append((sym, tokens[sym]))
        if len(out) >= MAX_STOCKS:
            break
    return out


def fetch(kite, token, frm, to):
    """15-minute candles, in chunks Kite will accept."""
    bars, cur = [], frm
    while cur < to:
        end = min(cur + timedelta(days=CHUNK), to)
        try:
            bars += kite.historical_data(token, cur, end, INTERVAL)
        except Exception:
            pass
        time.sleep(0.36)                    # 3 requests a second is the limit
        cur = end + timedelta(days=1)
    return bars


def main():
    print("=" * 64)
    print("  INTRADAY DATA — 15-minute candles for the liquid stocks")
    print("=" * 64)
    os.makedirs(STUDY, exist_ok=True)
    kite = load_kite()

    print("\n  Choosing which stocks to study ...")
    stocks = liquid_universe(kite)
    print(f"  {G}{len(stocks)} stocks selected.{OFF}")

    done = {}
    if os.path.exists(PART):
        try:
            done = json.load(open(PART, encoding="utf8"))
            print(f"  {len(done)} already downloaded - carrying on from there.")
        except Exception:
            done = {}

    todo = [(s, t) for s, t in stocks if s not in done]
    if not todo:
        print("  Everything is already downloaded.")
    else:
        mins = len(todo) * 2 * 0.36 / 60
        print(f"\n  Downloading {DAYS} days of 15-minute candles for "
              f"{len(todo)} stocks")
        print(f"  — about {mins:.0f} minutes. Leave the window open.\n")

    to = datetime.now()
    frm = to - timedelta(days=DAYS)
    t0 = time.time()
    for i, (sym, tok) in enumerate(todo, 1):
        rows = fetch(kite, tok, frm, to)
        done[sym] = [[str(c["date"])[:16], c["open"], c["high"], c["low"],
                      c["close"], c.get("volume", 0)] for c in rows]
        if i % 25 == 0 or i == len(todo):
            pct = 100 * i / len(todo)
            left = (time.time() - t0) / i * (len(todo) - i) / 60
            print(f"     {pct:5.1f}%   {i} of {len(todo)}   "
                  f"{DIM}about {left:.0f} min left{OFF}", flush=True)
            json.dump(done, open(PART, "w", encoding="utf8"))
    json.dump(done, open(PART, "w", encoding="utf8"))

    print(f"\n  Writing {OUT} ...")
    n = 0
    with gzip.open(OUT, "wt", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "ts", "open", "high", "low", "close", "volume"])
        for sym, rows in sorted(done.items()):
            for r in rows:
                w.writerow([sym] + r)
                n += 1
    size = os.path.getsize(OUT) / 1e6
    print(f"\n  {G}Done.{OFF}  {n:,} candles across {len(done)} stocks, "
          f"{size:.0f} MB.")
    print(f"  Saved to  NSE FO BACKTEST\\intraday_15m.csv.gz")
    print("\n  Tell Claude it has finished and he will run the study.\n")
    hold(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Stopped. Run it again and it will carry on.\n")
        hold(0)
    except Exception as e:
        print(f"\n  {R}Something went wrong: {type(e).__name__}: {e}{OFF}")
        import traceback
        traceback.print_exc()
        hold(1)
