"""ASCENDRA - SMC, THE NEW BOOK.

One setup, two books. Built 22/10/2026 after three weeks of getting it wrong,
and every one of those mistakes is named in the comments so none of them comes
back. The connection, the price feed, the record and the dashboard writing are
taken unchanged from SMC TWO BOOKS.py because those parts were already right;
only the rules are new.
"""

import json
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = 5057
SMC_PIVOT = 3
KEEP_LEVELS = 8
SLOT = 200000.0                     # money per position, for the record only
COST_CASH = 0.059                   # intraday round trip, per cent
COST_FUT = 0.062                    # stock future round trip, per cent
FAST_EVERY = 15 * 60                # the 15-minute book
SLOW_EVERY = 60 * 60                # the daily book
PRICE_EVERY = 60                    # the live price on the calls already written


# ── where everything lives ────────────────────────────────────────────────
def _root():
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        if os.path.isdir(os.path.join(d, "WEBSITE")) and \
           os.path.isdir(os.path.join(d, "DATA")):
            return d
        p = os.path.dirname(d)
        if p == d:
            break
        d = p
    return os.path.dirname(os.path.abspath(__file__))


HERE = _root()
FEED = os.path.join(HERE, "5 LIVE FEED")
DATA = os.path.join(HERE, "DATA")
LOGF = os.path.join(DATA, "smc2_log.json")
SITE = os.path.join(HERE, "WEBSITE", "ascendra_desk.html")
DAILYF = os.path.join(DATA, "smc2_daily.json")


def hold(code=0):
    try:
        input("\n  Press Enter to close this window. ")
    except Exception:
        pass
    sys.exit(code)


def ensure_kiteconnect():
    try:
        import kiteconnect  # noqa
        return
    except ImportError:
        pass
    print("  The Zerodha library is not installed for this Python.")
    print("  Installing it now - about twenty seconds...\n")
    import subprocess
    for args in (["-m", "pip", "install", "--quiet", "kiteconnect"],
                 ["-m", "pip", "install", "--quiet", "--user", "kiteconnect"]):
        try:
            subprocess.run([sys.executable] + args, check=True)
        except Exception:
            continue
        try:
            import importlib
            importlib.invalidate_caches()
            import kiteconnect  # noqa
            print("  Installed.\n")
            return
        except ImportError:
            continue
    print("\n  COULD NOT INSTALL IT. Open a Command Prompt and run:\n"
          f"      {sys.executable} -m pip install kiteconnect\n")
    hold(1)


def load_kite():
    ensure_kiteconnect()
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
        print(f"\n  TODAY'S LOGIN IS MISSING (the saved token is from "
              f"{tok.get('date')}).\n  Run connect.py in '5 LIVE FEED', then "
              f"start this again.\n")
        hold(1)
    from kiteconnect import KiteConnect
    k = KiteConnect(api_key=api_key)
    k.set_access_token(tok["access_token"])
    k.quote(["NSE:RELIANCE"])
    return k


# ── the sweep, exactly as the desk defines it ─────────────────────────────

# ── the rules, as MM wrote them and as they were tested ───────────────────
#
# This is the whole strategy. It is deliberately short and deliberately dull,
# because every version of it that was clever turned out to be wrong.
#
# The long road here, so nobody repeats it:
#
#   * a swing used to mean one candle with one lower candle either side. On a
#     chart with two candles a day that makes almost every candle a swing, so
#     "structure broke" fired on noise. MM: "are you taking major high's and
#     low's? if not then take that." Three candles either side now.
#   * structure used to be read on a 4-hour chart that was built by counting
#     sixteen 15-minute candles from the start of the file, so one "4-hour"
#     candle could run from Monday afternoon to Wednesday morning. Tested on
#     honest candles that version made Rs 12 a trade. Structure is read on the
#     DAILY chart now, where a candle is a day and cannot be grouped wrongly.
#   * the level being swept used to be only last week's low. His note also
#     lists liquidity pools - equal lows - and those are the commonest thing
#     to get swept. Adding them tripled the number of trades.
#   * the order block used to be whatever sat behind the sweep candle. His
#     note says price sweeps the low AND THEN enters a demand block, which is
#     two events. Two events now.
#
# Confirmed over three years and 1,281 stocks, both halves separately.

MAJOR_SIDE = 3          # candles either side for a high or low to count
EMA_N = 50              # the trend filter
CONF_WITHIN = 10        # days allowed between the sweep and the turn
POOL_TOL = 0.0025       # two lows within a quarter per cent are one pool
POOL_BACK = 120         # how far back to look for pools


def swings_major(b, side=MAJOR_SIDE):
    """A high needs `side` lower candles on BOTH sides. Same for a low."""
    out = []
    for i in range(side, len(b) - side):
        hi, lo = b[i]["high"], b[i]["low"]
        if all(b[j]["high"] < hi for j in range(i - side, i)) and \
           all(b[j]["high"] < hi for j in range(i + 1, i + 1 + side)):
            out.append((i, "H", hi))
        if all(b[j]["low"] > lo for j in range(i - side, i)) and \
           all(b[j]["low"] > lo for j in range(i + 1, i + 1 + side)):
            out.append((i, "L", lo))
    out.sort()
    return out


def structure(b, side=MAJOR_SIDE):
    """Trend, and whether a break continues it (BOS) or reverses it (CHoCH).

    A swing is only published `side` candles after it prints, because that is
    when its right-hand shoulder exists. Nothing here can see the future.
    """
    by_confirm = {}
    for idx, kind, px in swings_major(b, side):
        by_confirm.setdefault(idx + side, []).append((idx, kind, px))
    H, L, state = [], [], []
    trend = 0
    broken_h, broken_l = set(), set()
    for i in range(len(b)):
        for idx, kind, px in by_confirm.get(i, []):
            (H if kind == "H" else L).append((idx, px))
        ev = lvl = None
        c = b[i]["close"]
        if H and H[-1][0] not in broken_h and c > H[-1][1]:
            ev = "CHoCH" if trend < 0 else "BOS"
            lvl = H[-1][1]; broken_h.add(H[-1][0]); trend = 1
        elif L and L[-1][0] not in broken_l and c < L[-1][1]:
            ev = "CHoCH" if trend > 0 else "BOS"
            lvl = L[-1][1]; broken_l.add(L[-1][0]); trend = -1
        state.append({"trend": trend, "event": ev, "level": lvl})
    return state


def bias(b, side=MAJOR_SIDE):
    """+1 when the chart is making higher highs AND higher lows."""
    by_confirm = {}
    for idx, kind, px in swings_major(b, side):
        by_confirm.setdefault(idx + side, []).append((kind, px))
    H, L, out = [], [], []
    for i in range(len(b)):
        for kind, px in by_confirm.get(i, []):
            (H if kind == "H" else L).append(px)
        v = 0
        if len(H) >= 2 and len(L) >= 2:
            if H[-1] > H[-2] and L[-1] > L[-2]:
                v = 1
            elif H[-1] < H[-2] and L[-1] < L[-2]:
                v = -1
        out.append(v)
    return out


def to_weekly(daily):
    out, cur, wk = [], [], None
    for b in daily:
        y, w, _ = date.fromisoformat(str(b["date"])[:10]).isocalendar()
        if (y, w) != wk:
            if cur:
                out.append({"date": cur[0]["date"],
                            "open": cur[0]["open"],
                            "high": max(x["high"] for x in cur),
                            "low": min(x["low"] for x in cur),
                            "close": cur[-1]["close"]})
            cur, wk = [], (y, w)
        cur.append(b)
    if cur:
        out.append({"date": cur[0]["date"], "open": cur[0]["open"],
                    "high": max(x["high"] for x in cur),
                    "low": min(x["low"] for x in cur),
                    "close": cur[-1]["close"]})
    return out


def pools(d, i, wk_lo):
    """Every level a sweep could be reaching for: last week's low, and any
    cluster of equal lows. These are where the stops are."""
    out = [wk_lo]
    sw = [s for s in swings_major(d[:i]) if s[1] == "L" and s[0] >= i - POOL_BACK]
    vals = [p for _, _, p in sw]
    for a in range(len(vals)):
        for b in range(a + 1, len(vals)):
            if abs(vals[a] - vals[b]) / max(vals[b], 1e-9) <= POOL_TOL:
                out.append(min(vals[a], vals[b]))
                break
    return sorted(set(round(x, 2) for x in out))


def swept(bar, level):
    """Took the stops and came back: the low went under, the close did not."""
    return bar["low"] < level <= bar["close"]


def demand_blocks(d, upto, back=60):
    """Blocks that already existed at `upto` - a down candle immediately
    followed by an up candle that closes above its high."""
    out = []
    for j in range(max(1, upto - back), upto):
        if d[j]["close"] < d[j]["open"] and d[j + 1]["close"] > d[j + 1]["open"] \
           and d[j + 1]["close"] > d[j]["high"]:
            out.append((j, d[j]["low"], d[j]["high"]))
    return out


def ema_series(d, n=EMA_N):
    out, e, k = [], d[0]["close"], 2.0 / (n + 1)
    for b in d:
        out.append(b["close"] > e)
        e = (b["close"] - e) * k + e
    return out


# ── the two books ─────────────────────────────────────────────────────────
#
# One setup, two ways out. MM asked for exactly this: "one with all the rules
# and regulations i told you about, it has a good success ratio. one is with
# smaller targets but with full conviction."
BOOKS = {
    "SHORT TERM": {"chart": "daily structure, hourly entry",
                   "target": "pct2.0", "hold_days": 10, "cost": COST_FUT,
                   "note": "the conviction book - out at a 2% move, "
                           "right about two times in three"},
    "LONG TERM":  {"chart": "daily structure, hourly entry",
                   "target": "3x", "hold_days": 10, "cost": COST_FUT,
                   "note": "the full rules - out at three times the risk, "
                           "right about four times in ten"},
}


def find_setup(d):
    """One stock's daily candles in, a setup or None out.

    Only the most recent qualifying setup is returned, and only if its turn
    happened on one of the last few candles - an older one is not news.
    """
    if len(d) < EMA_N + 60:
        return None
    wk = to_weekly(d)
    if len(wk) < 3:
        return None
    wb, db = bias(wk), bias(d)
    above = ema_series(d)
    S = structure(d)

    # which weekly bar each daily bar belongs to
    wmap, seen = {}, {}
    for n, b in enumerate(wk):
        y, w, _ = date.fromisoformat(str(b["date"])[:10]).isocalendar()
        seen[(y, w)] = n
    for i, b in enumerate(d):
        y, w, _ = date.fromisoformat(str(b["date"])[:10]).isocalendar()
        wmap[i] = seen.get((y, w))

    for conf in range(len(d) - 1, max(len(d) - 4, EMA_N + 20), -1):
        if S[conf]["event"] != "CHoCH" or S[conf]["trend"] != 1:
            continue
        for i in range(conf - 1, max(conf - 1 - CONF_WITHIN, EMA_N + 10), -1):
            wn = wmap.get(i)
            if wn is None or wn < 1 or i < 1:
                continue
            if wb[wn - 1] != 1 or db[i - 1] != 1 or not above[i - 1]:
                continue
            wk_lo = wk[wn - 1]["low"]
            hit = [x for x in pools(d, i, wk_lo) if swept(d[i], x)]
            if not hit:
                continue
            blocks = demand_blocks(d, i)
            if not blocks:
                continue
            touched = None
            for j in range(i, conf + 1):
                for (bi, blo, bhi) in reversed(blocks):
                    if d[j]["low"] <= bhi and d[j]["high"] >= blo:
                        touched = (bi, blo, bhi)
                        break
                if touched:
                    break
            if not touched:
                continue
            bi, blo, bhi = touched
            entry, stop = bhi, min(blo, d[i]["low"])
            if not (stop < entry):
                continue
            risk = entry - stop
            if not (0.3 <= risk / entry * 100 <= 8):
                continue
            return {"at": str(d[conf]["date"])[:10],
                    "swept": str(d[i]["date"])[:10],
                    "level": round(min(hit), 2),
                    "entry": round(entry, 2), "stop": round(stop, 2),
                    "risk": risk,
                    "shape": "swept %s, demand block, change of character"
                             % round(min(hit), 2)}
    return None


def make_call(book, sym, s, fo):
    cfg = BOOKS[book]
    entry, risk = s["entry"], s["risk"]
    if cfg["target"] == "3x":
        target = entry + 3 * risk
    else:
        want = float(cfg["target"][3:]) + cfg["cost"]
        target = entry * (1 + want / 100)
    return {"book": book, "sym": sym, "at": s["at"], "shape": s["shape"],
            "level": s["level"], "entry": entry, "trigger": entry,
            "stop": s["stop"], "target": round(target, 2),
            "risk_pct": round(risk / entry * 100, 2), "state": "WAITING",
            "first_seen": datetime.now().isoformat(timespec="seconds"),
            "broke_at": s["at"], "filled_at": None, "closed_at": None,
            "exit_px": None, "result_pct": None, "status": "WAITING",
            "last": None, "broke_seen": datetime.now().isoformat(timespec="seconds")}


def live_already(L, book, sym):
    return any(c["book"] == book and c["sym"] == sym
               and c["state"] in ("WAITING", "BROKE", "IN")
               for c in L.values())


def key_of(c):
    return f"{c['book']}|{c['sym']}|{c['at']}"


def step(c, h1):
    """Move one call forward on the hourly candles.

    The order is live for `hold_days` trading days after the signal and then
    it expires. A fill can only happen on a candle AFTER the signal day, and
    the target is never claimed on the candle that filled us - inside one
    candle there is no way to know whether the high or the low came first.
    """
    cfg = BOOKS[c["book"]]
    if c["state"] in ("DONE", "GONE"):
        return
    after = [b for b in h1 if str(b["date"])[:10] > str(c["at"])[:10]]
    if not after:
        return
    c["last"] = round(after[-1]["close"], 2)

    if c["state"] in ("WAITING", "BROKE"):
        seen = set()
        for b in after:
            seen.add(str(b["date"])[:10])
            if len(seen) > cfg["hold_days"]:
                c["state"] = "GONE"
                c["status"] = "NEVER CAME BACK"
                c["closed_at"] = str(b["date"])
                return
            if b["low"] <= c["entry"]:
                c["state"] = "IN"
                c["status"] = "OPEN"
                c["filled_at"] = str(b["date"])
                c["entry"] = round(min(c["entry"], b["open"]), 2)
                break
        if c["state"] != "IN":
            return

    run = [b for b in after if str(b["date"]) >= str(c["filled_at"])]
    seen = set()
    for n, b in enumerate(run):
        seen.add(str(b["date"])[:10])
        if len(seen) > cfg["hold_days"]:
            c["exit_px"] = round(run[n - 1]["close"], 2)
            c["status"] = "TIME"; c["closed_at"] = str(run[n - 1]["date"])
            break
        if b["close"] <= c["stop"]:
            c["exit_px"] = round(b["close"], 2)
            c["status"] = "STOP"; c["closed_at"] = str(b["date"])
            break
        if n > 0 and b["high"] >= c["target"]:
            c["exit_px"] = c["target"]
            c["status"] = "TARGET"; c["closed_at"] = str(b["date"])
            break
    if c["exit_px"] is not None and c["result_pct"] is None:
        gross = 100 * (c["exit_px"] - c["entry"]) / c["entry"]
        c["result_pct"] = round(gross - cfg["cost"], 2)
        c["state"] = "DONE"


def fetch_15m(kite, tokens, syms, days=40):
    out = {}
    frm = (date.today() - timedelta(days=days)).isoformat()
    to = date.today().isoformat()
    for s in syms:
        t = tokens.get(s)
        if not t:
            continue
        try:
            raw = kite.historical_data(t, f"{frm} 09:00:00", f"{to} 15:40:00",
                                       "15minute")
            bars = [{"date": str(x["date"])[:19], "open": float(x["open"]),
                     "high": float(x["high"]), "low": float(x["low"]),
                     "close": float(x["close"]),
                     "volume": float(x.get("volume") or 0)} for x in raw]
            if bars:
                try:
                    lastb = datetime.fromisoformat(bars[-1]["date"])
                    if (datetime.now() - lastb).total_seconds() < 15 * 60:
                        bars.pop()
                except Exception:
                    bars.pop()
            if len(bars) > 60:
                out[s] = bars
        except Exception:
            pass
        time.sleep(0.05)
    return out


def fetch_daily(kite, tokens, syms, years=2):
    """Daily candles change once a day, so they are cached."""
    today = date.today().isoformat()
    try:
        old = json.load(open(DAILYF, encoding="utf8"))
        # AND DO NOT TRUST AN EMPTY ONE EITHER (28/09). Refusing to WRITE an
        # empty cache was only half the fix - a poisoned cache from earlier in
        # the day was still being read back, so the book stayed dead until
        # midnight. An empty cache is now ignored and the download retried.
        if old.get("date") == today and old.get("bars"):
            # A CACHE BUILT BEFORE THE OPEN HAS NO CANDLE FOR TODAY (06/10).
            #
            # The cache is stamped with the date it was written, so one written
            # at 00:50 counts as "today's" and is handed back all session - and
            # the newest candle in it is YESTERDAY'S. The long-term book then
            # spends the whole day unable to see today at all: it cannot fill,
            # cannot stop out, and cannot retire a call whose target has gone.
            # That is why PNBHOUSING, BHEL, HDFCLIFE, PRESTIGE and ICICIPRULI
            # were still asking to be bought at prices the market had left
            # behind hours earlier.
            #
            # While the market is open the cache is ignored and the candles are
            # pulled again, so the book is reading the day it is trading in.
            # Outside market hours the cache is correct and is used as before.
            now = datetime.now()
            trading = (now.weekday() < 5
                       and (9, 15) <= (now.hour, now.minute) <= (15, 35))
            newest = max((b[-1]["date"] for b in old["bars"].values() if b),
                         default="")
            if not trading or newest[:10] == today:
                print(f"  daily candles: using today's cache "
                      f"({len(old['bars'])} names, newest {newest})")
                return old["bars"]
            print(f"  daily candles: the cache stops at {newest} and the market "
                  f"is open - pulling today's candles")
    except Exception:
        pass
    out = {}
    frm = (date.today() - timedelta(days=365 * years)).isoformat()
    for n, s in enumerate(syms, 1):
        t = tokens.get(s)
        if not t:
            continue
        try:
            raw = kite.historical_data(t, frm, today, "day")
            bars = [{"date": str(x["date"])[:10], "open": float(x["open"]),
                     "high": float(x["high"]), "low": float(x["low"]),
                     "close": float(x["close"]),
                     "volume": float(x.get("volume") or 0)} for x in raw]
            if len(bars) > 120:
                out[s] = bars
        except Exception:
            pass
        if n % 50 == 0:
            print(f"    daily {n}/{len(syms)}", flush=True)
        time.sleep(0.05)
    # NEVER CACHE AN EMPTY DOWNLOAD (28/09). If every request failed - a
    # stale token, a rate limit, a dropped connection - `out` is empty, and
    # writing it as today's cache meant the long-term book had no candles for
    # the whole day and quietly did nothing. Found on 28/09 with a 34-byte
    # cache file and twelve calls frozen since Friday.
    if not out:
        print("  daily candles: NOTHING came back from Zerodha - not caching "
              "that. Check the login and leave this running; it will try again.")
        return {}
    try:
        json.dump({"date": today, "bars": out}, open(DAILYF, "w"))
    except Exception:
        pass
    return out


_PX = {"map": {}, "at": "", "err": ""}


def quote_all(kite, tokens, syms):
    """ONE LIVE PRICE FOR THE WHOLE DESK (28/09).

    MM: "havells price is showing wrong. why is it happening again and again."
    Because every engine on the desk carries its OWN price, taken whenever that
    engine's scan last came round. HAVELLS sat at 1082.70 because the old SMC
    scan had not been back to it. Fixing one tab at a time never ends - there
    is always another engine with another stale number.

    So this reads a real quote for every stock the desk watches, once a minute,
    and publishes it as one table the dashboard can use for any tab. Around
    1,900 names is ten requests, well inside Zerodha's limit.
    """
    out, err = {}, ""
    # A HUNDRED AT A TIME (28/09). Two hundred came back empty every single
    # pass - kite.ltp puts every symbol in the URL, and the request is refused
    # once that gets long. The failure is also carried to the desk now, so a
    # dead price table says why instead of just being blank.
    for i in range(0, len(syms), 100):
        chunk = [x for x in syms[i:i + 100] if x in tokens]
        if not chunk:
            continue
        try:
            q = kite.ltp(["NSE:" + x for x in chunk])
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            continue
        for k, v in q.items():
            pr = v.get("last_price")
            if pr:
                out[k.split(":", 1)[-1]] = round(float(pr), 2)
        time.sleep(0.05)
    _PX["err"] = err
    if out:
        _PX["map"] = out
        _PX["at"] = datetime.now().isoformat(timespec="seconds")
        _PX["err"] = ""
    else:
        print(f"  (live prices: nothing came back{' - ' + err if err else ''})",
              flush=True)
    return _PX["map"]


def mark(kite, tokens, L):
    """A PRICE LABELLED "PRICE NOW" HAS TO BE NOW (28/09).

    MM: "it is showing price now 700.45. cmp is 714 ... and it is showing
    wrong." He was right and the fault was here, not on the dashboard.

    `last` was only ever set inside step(), from the last candle that had
    already closed. Two things followed from that, both bad:

      - the daily book had NO price at all. A long-term call is written on
        today's daily candle, so there is no candle after it, so step() never
        reached the line that sets `last`. All twelve long-term calls showed
        a blank price.
      - the 15-minute book carried whatever the last completed sweep saw. A
        sweep over 1,900 stocks takes most of a quarter of an hour, so the
        price on the desk could be half an hour behind the market. That is
        the 700.45 against a real 714 on RKFORGE.

    A quote is one request to Zerodha for every name on the books - twenty-six
    names today - so it is read every minute and it is what the desk shows.
    Only calls that are still alive are marked; a closed trade keeps the exit
    price it actually got.
    """
    alive = ("WAITING", "BROKE", "IN")
    want = sorted({c["sym"] for c in L.values()
                   if c.get("state") in alive and c["sym"] in tokens})
    if not want:
        return
    px = _PX["map"]
    if not px:
        return                      # no quote yet - leave the prices alone
    for c in L.values():
        if c.get("state") in alive and px.get(c["sym"]):
            c["last"] = px[c["sym"]]

    # SEVENTEEN MINUTES IS TOO LATE TO BE TOLD (06/10). MM: "why did i get
    # notification of voltas so late? why?"
    #
    # He was told at 10:02:36 about a breakout that happened somewhere inside
    # the 09:45 candle - up to seventeen minutes after the fact. The cause is
    # structural: a breakout is only noticed when its 15-minute candle has
    # CLOSED, which is 10:00, and the 400-name sweep then takes until 10:02.
    # Every call today was 17-18 minutes late by the same arithmetic.
    #
    # This engine already asks Zerodha for a live price on every name once a
    # minute. That price knows about the breakout immediately. So a WAITING
    # call whose live price has gone through its trigger is flagged here, and
    # the desk says so straight away.
    #
    # NOTHING ABOUT THE RECORD MOVES. `state` is untouched; the breakout is
    # still confirmed, timed and recorded from the closed candle exactly as
    # before, because a tick is not a candle and the book has to stay provable.
    # This is a heads-up, not a decision - it only changes when he hears.
    now_s = datetime.now().isoformat(timespec="seconds")
    for c in L.values():
        if c.get("state") != "WAITING" or not c.get("trigger"):
            continue
        p = px.get(c["sym"])
        if p and p >= c["trigger"] and not c.get("live_through"):
            c["live_through"] = now_s


# ── the permanent record ──────────────────────────────────────────────────
def load_log():
    try:
        return json.load(open(LOGF, encoding="utf8"))
    except Exception:
        return {}


def save_log(L):
    tmp = LOGF + ".tmp"
    json.dump(L, open(tmp, "w"))
    os.replace(tmp, LOGF)


_SERVE = {"payload": json.dumps({"as_of": None, "books": {}})}


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        b = _SERVE["payload"].encode("utf8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        try:
            self.wfile.write(b)
        except Exception:
            pass


def serve():
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), _H).serve_forever()
    except Exception as e:
        print(f"  (could not open port {PORT}: {e})")


def write_site(payload):
    """WRITE THE CALLS INTO THE PAGE ITSELF (25/09).

    Serving them on a port was not enough: a page loaded from 127.0.0.1:5055
    is refused when it tries to read 127.0.0.1:5057, so the tab sat there
    saying "not running" while this program was running perfectly. The
    formations scanner solved the same problem long ago by writing its calls
    straight into the HTML between two markers, and that is why the Patterns
    tab has always had data. Same thing here. The port stays, for the browsers
    where it does work - this is the belt.
    """
    start, end = "/*SMC2_START*/", "/*SMC2_END*/"
    block = f"{start}const SMC2LIVE = {payload};{end}"
    try:
        h = open(SITE, encoding="utf8").read()
    except Exception as e:
        print(f"  (could not read the dashboard: {e})")
        return
    if start in h and end in h:
        a = h.index(start); b = h.index(end) + len(end)
        h = h[:a] + block + h[b:]
    else:
        try:
            k = h.rindex("</script>")
        except ValueError:
            return
        h = h[:k] + "\n" + block + "\n" + h[k:]
    try:
        tmp = SITE + ".smc2.tmp"
        open(tmp, "w", encoding="utf8").write(h)
        os.replace(tmp, SITE)
    except Exception as e:
        print(f"  (could not write the dashboard: {e})")


def publish(L):
    books = {}
    for b in BOOKS:
        rows = [c for c in L.values() if c["book"] == b]
        done = [c for c in rows if c["result_pct"] is not None]
        booked = sum(c["result_pct"] / 100.0 * SLOT for c in done)
        openr = [c for c in rows if c["state"] == "IN"]
        marked = sum(((c["last"] - c["entry"]) / c["entry"] * SLOT)
                     for c in openr if c.get("last") and c.get("entry"))
        books[b] = {
            "chart": BOOKS[b]["chart"], "note": BOOKS[b]["note"],
            # A CALL THAT HAS BROKEN OUT MUST STILL BE ON THE DESK (06/10).
            #
            # This line read `state == "WAITING"`, so the moment a call broke
            # out it left the page completely - it was not waiting, not held,
            # not closed. It only reappeared if it filled. That is exactly the
            # state where MM needs to be watching it: the level has gone, the
            # pullback is what he has to put the order into.
            #
            # It is also why the desk kept looking empty, and why the twelve
            # calls put back this morning were nowhere to be seen even though
            # the record had them. BROKE belongs here.
            "waiting": [c for c in rows if c["state"] in ("WAITING", "BROKE")],
            "open": openr,
            "closed": sorted(done, key=lambda c: c["closed_at"] or "",
                             reverse=True)[:400],
            "booked": round(booked), "closed_n": len(done),
            "marked": round(marked), "open_n": len(openr),
            "missed_n": sum(1 for c in rows if c["status"] == "MISSED"),
            "wins": sum(1 for c in done if c["result_pct"] > 0),
        }
    _SERVE["payload"] = json.dumps(
        {"as_of": datetime.now().isoformat(timespec="seconds"),
         "slot": SLOT, "books": books,
         # the whole desk's live prices - see quote_all()
         "px": _PX["map"], "px_at": _PX["at"], "px_err": _PX["err"]})
    write_site(_SERVE["payload"])



def main():
    print("""
  ASCENDRA - SMC, THE NEW BOOK

  One setup, two ways out. The setup is read on the DAILY chart and the
  trade is placed on the hourly:

     weekly up, daily up, price above its 50-day average
     a pool of stops below the market gets taken and price closes back above
     price comes back into a demand block that was already there
     the chart turns - a change of character
     buy at the block, stop under it

  SHORT TERM   out at a 2% move           - right about 2 times in 3
  LONG TERM    out at three times the risk - right about 4 times in 10

  Nothing is ordered. This records what the rules do and keeps every call.
""")
    kite = load_kite()
    print("  connected to Zerodha")
    try:
        fo = json.load(open(os.path.join(DATA, "fo_universe.json"), encoding="utf8"))
        fo = set(fo["map"].keys()) if isinstance(fo, dict) and "map" in fo else set(fo)
    except Exception:
        fo = set()
    syms_all = set(fo)
    # EVERY COMPANY OVER A THOUSAND CRORE, if that list has been built.
    # BUILD UNIVERSE.py writes it; without it we fall back to the index list.
    mc = os.path.join(DATA, "mcap_1000cr.json")
    if os.path.exists(mc):
        try:
            syms_all |= set(json.load(open(mc, encoding="utf8"))["caps"].keys())
            print(f"  using the market-cap list ({len(syms_all)} names)")
        except Exception:
            pass
    p = os.path.join(DATA, "ind_niftytotalmarket_list.csv")
    if os.path.exists(p):
        import csv as _csv
        for row in _csv.DictReader(open(p, encoding="utf-8-sig")):
            if row.get("Symbol"):
                syms_all.add(row["Symbol"].strip())
    inst = kite.instruments("NSE")
    # TRADE-TO-TRADE STOCKS WERE BEING THROWN AWAY HERE (23/10).
    #
    # MM spotted a double bottom in HFCL that never showed up on the desk. It
    # was not the detector disagreeing with him - HFCL was never looked at at
    # all. It trades in the BE series, and this filter kept only "EQ", so five
    # of the six BE names on the list (DIACABS, HEG, HFCL, LOTUSDEV, STLTECH)
    # never had a single candle downloaded. BE is delivery only - no intraday
    # squaring off - so those names belong in the swing and positional books.
    tokens = {i["tradingsymbol"]: i["instrument_token"] for i in inst
              if i.get("segment") == "NSE"
              and i.get("instrument_type") in ("EQ", "BE")}
    syms = sorted(s for s in syms_all if s in tokens)
    print(f"  watching {len(syms)} stocks")

    L = load_log()
    print(f"  record holds {len(L)} calls so far")
    publish(L)
    threading.Thread(target=serve, daemon=True).start()
    print(f"  serving on http://127.0.0.1:{PORT}   (your desk stays on 5055)\n")

    last_scan = last_px = 0.0
    while True:
        now = datetime.now()
        trading = now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) <= (16, 0)
        try:
            # the live price on everything already on the books, once a minute
            if trading and time.time() - last_px > PRICE_EVERY:
                last_px = time.time()
                quote_all(kite, tokens, syms)
                mark(kite, tokens, L)
                publish(L)

            # THE SCAN. A daily candle cannot change until the session closes,
            # so once an hour is more than enough - and the entry is on the
            # hourly chart, which is the fastest anything here moves.
            if time.time() - last_scan > SLOW_EVERY:
                last_scan = time.time()
                print(f"  {now:%H:%M:%S}  scanning {len(syms)} stocks ...",
                      flush=True)
                D = fetch_daily(kite, tokens, syms)
                found = 0
                for sym, dbars in D.items():
                    if len(dbars) < 120:
                        continue
                    live = [c for c in L.values() if c["sym"] == sym
                            and c["state"] in ("WAITING", "BROKE", "IN")]
                    if live:
                        # these need hourly candles to move forward
                        h = fetch_15m(kite, tokens, [sym], days=30).get(sym)
                        if h:
                            h1 = by_hour(h)
                            for c in live:
                                step(c, h1)
                    s = find_setup(dbars)
                    if not s:
                        continue
                    for book in BOOKS:
                        if live_already(L, book, sym):
                            continue
                        c = make_call(book, sym, s, fo)
                        if key_of(c) in L:
                            continue
                        L[key_of(c)] = c
                        found += 1
                        print(f"  {book:11s} {sym:12s} buy {c['entry']}  "
                              f"stop {c['stop']}  target {c['target']}",
                              flush=True)
                save_log(L); publish(L)
                print(f"  {datetime.now():%H:%M:%S}  done - {found} new call"
                      f"{'' if found == 1 else 's'}, "
                      f"{sum(1 for c in L.values() if c['state'] == 'IN')} open",
                      flush=True)
        except Exception as e:
            print(f"  (scan failed, carrying on: {e})", flush=True)
        time.sleep(30)


def by_hour(bars15):
    """15-minute candles into hourly ones, anchored to the clock.

    Anchored, not counted. Counting sixteen candles from the start of a file
    is what produced "4-hour" candles that ran across two days and a backtest
    result that fell apart the moment it was checked.
    """
    OPEN = 9 * 60 + 15
    buckets = {}
    order = []
    for b in bars15:
        t = str(b["date"])
        day = t[:10]
        try:
            mins = int(t[11:13]) * 60 + int(t[14:16])
        except (ValueError, IndexError):
            continue
        slot = max(0, mins - OPEN) // 60
        k = (day, slot)
        if k not in buckets:
            buckets[k] = []
            order.append(k)
        buckets[k].append(b)
    out = []
    for k in order:
        c = buckets[k]
        out.append({"date": c[0]["date"], "open": c[0]["open"],
                    "high": max(x["high"] for x in c),
                    "low": min(x["low"] for x in c),
                    "close": c[-1]["close"],
                    "volume": sum(x.get("volume") or 0 for x in c)})
    return out


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  stopped.")
    except Exception:
        import traceback
        traceback.print_exc()
        hold(1)
