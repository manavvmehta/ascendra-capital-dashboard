"""ASCENDRA - SMC, THE TWO BOOKS THAT PASSED TESTING

Leave this window open beside the desk. It does not touch any other engine and
it does not touch your existing SMC tab - it keeps its own record and serves it
on its own port.

WHY THIS EXISTS
  The SMC engine as it stands loses money: over three years and 176,483
  sweeps it gives up about Rs 180 a trade whichever way you exit it, and the
  positional side of it is the worse half. Two versions survived a proper
  test - split by stock, so a rule found on one half of the market had to
  prove itself on stocks it had never seen.

SHORT TERM  (15-minute chart, closed the same day, cash market)
  Price runs through YESTERDAY'S high or low and closes back the other side
  inside one candle - that candle is the sweep. It does NOT have to be any
  particular shape; testing showed demanding one cost two thirds of the trades
  and earned nothing.
  The stop sits at that candle's low, and it must be 1.5% to 3% below the
  candle's high. Outside that band this loses; the target ends up too far away
  to ever be reached.
  Price must then BREAK above that candle's high - that is what says the sweep
  was real. You do not chase the break: you wait for price to come back into
  the sweep candle's own body and buy there.
  Target twice the risk. Out if it CLOSES below the stop, not if it merely
  touches it. Long only - shorts are flat.
      Tested: +Rs 877 a trade on Rs 2 lakh, 1,210 trades, 60% right,
      +864 on one half of the stocks and +893 on the other.

LONG TERM  (daily chart, held up to ten days, traded as a FUTURE)
  Same shape, one chart up: price runs through LAST WEEK'S high or low and
  closes back inside, on a daily candle. Same entry - wait for the pullback
  into that candle's body. Same 2x target, same close-based stop, long only.
  It must be traded as a stock future. Delivery costs 0.222% the round trip
  against 0.062% on a future, and that Rs 320 a trade is the difference
  between this making money and losing it. So only F&O names.
      Tested: +Rs 1,188 a trade on Rs 2 lakh, 5,715 trades, 66% right,
      +1,071 on one half of the stocks and +1,322 on the other.

WHAT IT DOES NOT DO
  It places no orders. It records what the rules would have done, with the
  same honesty as the rest of the desk: an entry that never filled is marked
  as never filled, and a trade that is still open is marked at the last price
  and never added to the booked figure.
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
def _shape(b):
    o, h, l, c = b["open"], b["high"], b["low"], b["close"]
    rng = max(h - l, 1e-9)
    return {"o": o, "h": h, "l": l, "c": c, "body": abs(c - o), "rng": rng,
            "up": h - max(o, c), "dn": min(o, c) - l, "bull": c > o,
            "mid": (o + c) / 2}


def bullish_reversal(prev, cur):
    p, b = _shape(prev), _shape(cur)
    hammer = b["dn"] >= 2 * b["body"] and b["up"] <= 0.25 * b["rng"] \
        and b["body"] <= 0.4 * b["rng"]
    engulf = (not p["bull"]) and b["bull"] and b["c"] >= p["o"] and b["o"] <= p["c"]
    pierce = (not p["bull"]) and b["bull"] and b["o"] < p["l"] and b["c"] > p["mid"]
    return ("hammer" if hammer else "bullish engulfing" if engulf
            else "piercing" if pierce else None)


def swings(bars):
    hi, lo = {}, {}
    n = SMC_PIVOT
    for i in range(n, len(bars) - n):
        w = bars[i - n:i + n + 1]
        if bars[i]["high"] == max(x["high"] for x in w):
            hi.setdefault(i + n, []).append(bars[i]["high"])
        if bars[i]["low"] == min(x["low"] for x in w):
            lo.setdefault(i + n, []).append(bars[i]["low"])
    return hi, lo


def week_of(dstr):
    y, w, _ = date.fromisoformat(dstr[:10]).isocalendar()
    return (y, w)


def prev_levels(bars, by_week=False):
    """For each bar, the PREVIOUS day's (or week's) high and low."""
    ph, pl = [], []
    cur, h, l, lh, ll = None, None, None, None, None
    for b in bars:
        k = week_of(str(b["date"])) if by_week else str(b["date"])[:10]
        if k != cur:
            if h is not None:
                lh, ll = h, l
            cur, h, l = k, b["high"], b["low"]
        else:
            h = max(h, b["high"]); l = min(l, b["low"])
        ph.append(lh); pl.append(ll)
    return ph, pl


def find_sweep(bars, by_week):
    """A sweep on the LAST COMPLETED candle only. Long side only."""
    if len(bars) < 3 * SMC_PIVOT + 4:
        return None
    hi, lo = swings(bars)
    ph, pl = prev_levels(bars, by_week)
    i = len(bars) - 1
    if pl[i] is None:
        return None
    known = []
    for k in range(i + 1):
        known += lo.get(k, [])
    levels = known[-KEEP_LEVELS:] + [pl[i]]
    cur, prev = bars[i], bars[i - 1]
    hit = [x for x in levels if cur["low"] < x <= cur["close"]]
    if not hit:
        return None
    level = min(hit)
    # the rule that passed testing is the PREVIOUS period's level, nothing else
    if abs(level - pl[i]) > 1e-9:
        return None
    # NO SHAPE REQUIREMENT (25/09). The engine used to insist the sweep candle
    # be a hammer, a bullish engulfing or a piercing candle. Tested against a
    # control that asks for no shape at all, over three years: the control made
    # Rs 877 a trade on 1,210 short-term trades where the hammer made Rs 876 on
    # 508, and Rs 1,188 on 5,715 long-term trades where the shape-restricted
    # version made Rs 456 on 1,790. The filter was throwing away two thirds of
    # the trades and buying nothing with them. The shape is still NAMED, because
    # it is worth seeing on the card - it just no longer decides anything.
    shape = bullish_reversal(prev, cur) or "no particular shape"
    trig = cur["high"]
    sl = cur["low"]
    if trig <= sl:
        return None
    return {"at": str(cur["date"]), "level": round(level, 2), "shape": shape,
            "trigger": round(trig, 2), "stop": round(sl, 2),
            "body_low": round(min(cur["open"], cur["close"]), 2),
            "risk_pct": round(100 * (trig - sl) / trig, 2)}


# ── the two books ─────────────────────────────────────────────────────────
BOOKS = {
    "SHORT TERM": {"chart": "15-minute", "by_week": False, "cost": COST_CASH,
                   "band": (1.5, 3.0), "hold_days": 0,
                   "note": "yesterday's level swept, squared off the same day, "
                           "cash market"},
    "LONG TERM":  {"chart": "daily", "by_week": True, "cost": COST_FUT,
                   "band": (0.3, 6.0), "hold_days": 10,
                   "note": "last week's level swept, held up to ten days, "
                           "traded as a stock future"},
}


def make_call(book, sym, s, fo):
    cfg = BOOKS[book]
    lo_b, hi_b = cfg["band"]
    if not (lo_b <= s["risk_pct"] < hi_b):
        return None
    if book == "LONG TERM" and sym not in fo:
        return None
    entry = s["body_low"]
    if entry <= s["stop"]:
        return None
    if not (0.3 <= 100 * (entry - s["stop"]) / entry < hi_b):
        return None
    risk = entry - s["stop"]
    return {"book": book, "sym": sym, "at": s["at"], "shape": s["shape"],
            "level": s["level"], "entry": round(entry, 2),
            "trigger": s["trigger"],
            "stop": s["stop"], "target": round(entry + 2 * risk, 2),
            "risk_pct": s["risk_pct"], "state": "WAITING",
            "first_seen": datetime.now().isoformat(timespec="seconds"),
            "broke_at": None,
            "filled_at": None, "closed_at": None, "exit_px": None,
            "result_pct": None, "status": "WAITING", "last": None}


def key_of(c):
    return f"{c['book']}|{c['sym']}|{c['at']}"


def step(c, bars):
    """Move one call forward using the candles that have closed since."""
    # A CALL WRITTEN BY THE OLDER RULE (25/09). Before the break-then-pullback
    # fix, calls were saved without a trigger price. There is no honest way to
    # carry one of those forward under the new rule, and reading its missing
    # trigger would throw and kill the whole sweep - so it is retired, kept in
    # the record, and counted nowhere.
    if not c.get("trigger"):
        c["state"] = "GONE"
        c["status"] = "VOID - written under the older rule"
        return
    cfg = BOOKS[c["book"]]
    after = [b for b in bars if str(b["date"]) > c["at"]]
    if not after:
        return
    c["last"] = round(after[-1]["close"], 2)
    # TWO STEPS, NOT ONE (25/09). The rule that was tested waits for price to
    # BREAK the sweep candle's high first - that is what says the sweep was
    # real - and only then buys the pullback back into the candle's body. The
    # first version of this file skipped the break and bought the pullback
    # straight away, which is a different rule and a worse one: on a sample it
    # made -0.10% a trade where the tested rule makes +0.62%.
    if c["state"] == "WAITING":
        for b in after:
            if b["high"] >= c["trigger"]:
                c["state"] = "BROKE"
                c["status"] = "BROKE OUT"
                c["broke_at"] = str(b["date"])
                break
            if b["close"] <= c["stop"]:
                c["state"] = "GONE"; c["status"] = "MISSED"
                return
        else:
            limit = 1 if cfg["hold_days"] == 0 else 3
            if len({str(b["date"])[:10] for b in after}) > limit:
                c["state"] = "GONE"; c["status"] = "MISSED"
            return
    if c["state"] == "BROKE":
        # ONE CANDLE CANNOT DO THREE THINGS (29/09, from MM asking why
        # FINCABLES never rang the bell).
        #
        # A candle has one high and one low, and no order between them. This
        # used to start the pullback search AT the breakout candle, so a single
        # wide 15-minute candle could satisfy the break (its high >= trigger),
        # the pullback (its low <= entry) and the target (the same high again)
        # all at once. The call was written already finished - never open for a
        # second, so the bell had no live call to ring and nothing could have
        # been traded. FINCABLES on 29/09 broke, filled and hit target all at
        # 09:30, and every row on the short-term book looked like that.
        #
        # The pullback must now come from a LATER candle than the break, which
        # is the only version of the sequence the data can actually prove.
        back = [b for b in after if str(b["date"]) > c["broke_at"]]
        for b in back:
            if b["low"] <= c["entry"]:
                c["state"] = "IN"
                c["status"] = "OPEN"
                c["filled_at"] = str(b["date"])
                break
        else:
            limit = 1 if cfg["hold_days"] == 0 else 6
            if len({str(b["date"])[:10] for b in back}) > limit:
                c["state"] = "GONE"
                c["status"] = "NEVER CAME BACK"
            return
    if c["state"] != "IN":
        return
    run = [b for b in after if str(b["date"]) >= c["filled_at"]]
    day0 = c["filled_at"][:10]
    for b in run:
        # Same reasoning on the way out: on the candle that filled us we cannot
        # know whether the high or the low came first, so the target is not
        # claimable there. The stop still is - that is the conservative side,
        # and it is the convention the calls engine already uses.
        _fill_bar = str(b["date"]) == c["filled_at"]
        d = str(b["date"])[:10]
        if cfg["hold_days"] == 0 and d != day0:
            break
        if cfg["hold_days"] and len({str(x["date"])[:10] for x in run
                                     if str(x["date"]) <= str(b["date"])}) > cfg["hold_days"]:
            break
        if b["close"] <= c["stop"]:
            c["exit_px"] = round(b["close"], 2); c["status"] = "STOP"
            c["closed_at"] = str(b["date"]); break
        if b["high"] >= c["target"] and not _fill_bar:
            c["exit_px"] = c["target"]; c["status"] = "TARGET"
            c["closed_at"] = str(b["date"]); break
    else:
        if cfg["hold_days"] == 0 and run and str(run[-1]["date"])[:10] == day0 \
           and str(run[-1]["date"])[11:16] >= "15:15":
            c["exit_px"] = round(run[-1]["close"], 2); c["status"] = "SQUARED OFF"
            c["closed_at"] = str(run[-1]["date"])
    if c["exit_px"] is not None and c["result_pct"] is None:
        gross = 100 * (c["exit_px"] - c["entry"]) / c["entry"]
        c["result_pct"] = round(gross - cfg["cost"], 2)
        c["state"] = "DONE"


# ── data ──────────────────────────────────────────────────────────────────
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
            print(f"  daily candles: using today's cache ({len(old['bars'])} names)")
            return old["bars"]
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
            "waiting": [c for c in rows if c["state"] == "WAITING"],
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
  ASCENDRA - SMC, THE TWO BOOKS

  SHORT TERM   15-minute chart, yesterday's level swept, closed the same day
  LONG TERM    daily chart, last week's level swept, held up to ten days

  Both enter on the pullback into the sweep candle's body, stop on a CLOSE
  past the stop, target twice the risk, long only. Nothing is ordered - this
  records what the rules do, and keeps every call forever.
""")
    kite = load_kite()
    print("  connected to Zerodha")
    try:
        fo = json.load(open(os.path.join(DATA, "fo_universe.json"), encoding="utf8"))
        fo = set(fo["map"].keys()) if isinstance(fo, dict) and "map" in fo else set(fo)
    except Exception:
        fo = set()
    syms_all = set(fo)
    p = os.path.join(DATA, "ind_niftytotalmarket_list.csv")
    if os.path.exists(p):
        import csv as _csv
        for row in _csv.DictReader(open(p, encoding="utf-8-sig")):
            if row.get("Symbol"):
                syms_all.add(row["Symbol"].strip())
    inst = kite.instruments("NSE")
    tokens = {i["tradingsymbol"]: i["instrument_token"] for i in inst
              if i.get("segment") == "NSE" and i.get("instrument_type") == "EQ"}
    syms = sorted(s for s in syms_all if s in tokens)
    print(f"  watching {len(syms)} stocks · {len(fo)} of them have futures")

    L = load_log()
    print(f"  record holds {len(L)} calls so far")
    publish(L)
    threading.Thread(target=serve, daemon=True).start()
    print(f"  serving on http://127.0.0.1:{PORT}   (your desk stays on 5055)\n")

    last_slow = last_fast = last_say = 0.0
    while True:
        now = datetime.now()
        # THE SQUARE-OFF COULD BE MISSED ENTIRELY (29/09, from MM: MANAPPURAM
        # still OPEN after the close while AVANTIFEED and RALLIS squared off).
        #
        # An intraday call is squared off from the 15:15 candle, which is not
        # readable until 15:30. The book only stepped while `trading` was true,
        # and that ended at 15:40 - so the square-off happened only if a sweep
        # happened to land in that ten-minute window. Sweeps are fifteen minutes
        # apart, so it was missed about a third of the time, and the position
        # sat open overnight holding a slot.
        #
        # The window now runs to 16:00, which always contains a sweep after the
        # 15:15 candle has closed. Nothing else changes: the square-off itself
        # still needs that candle, so it cannot fire early.
        trading = now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) <= (16, 0)
        try:
            # LONG TERM - daily candles, once an hour is more than enough
            if time.time() - last_slow > SLOW_EVERY:
                last_slow = time.time()
                D = fetch_daily(kite, tokens, [s for s in syms if s in fo])
                for sym, bars in D.items():
                    for c in [c for c in L.values()
                              if c["book"] == "LONG TERM" and c["sym"] == sym
                              and c["state"] in ("WAITING", "IN")]:
                        step(c, bars)
                    s = find_sweep(bars, True)
                    if s:
                        c = make_call("LONG TERM", sym, s, fo)
                        if c and key_of(c) not in L:
                            L[key_of(c)] = c
                            print(f"  LONG TERM  {sym:12s} {s['shape']:18s} "
                                  f"buy {c['entry']}  stop {c['stop']}  "
                                  f"target {c['target']}")
                save_log(L); publish(L)

            # SHORT TERM - the 15-minute book, only while the market is open
            if trading and time.time() - last_fast > FAST_EVERY:
                last_fast = time.time()
                B = fetch_15m(kite, tokens, syms)
                for sym, bars in B.items():
                    for c in [c for c in L.values()
                              if c["book"] == "SHORT TERM" and c["sym"] == sym
                              and c["state"] in ("WAITING", "IN")]:
                        step(c, bars)
                    s = find_sweep(bars, False)
                    if s:
                        c = make_call("SHORT TERM", sym, s, fo)
                        if c and key_of(c) not in L:
                            L[key_of(c)] = c
                            print(f"  SHORT TERM {sym:12s} {s['shape']:18s} "
                                  f"buy {c['entry']}  stop {c['stop']}  "
                                  f"target {c['target']}")
                save_log(L); publish(L)
                print(f"  {now:%H:%M}  swept {len(B)} names · "
                      f"{sum(1 for c in L.values() if c['state']=='WAITING')} waiting · "
                      f"{sum(1 for c in L.values() if c['state']=='IN')} open", flush=True)

            # THE PRICE, ON ITS OWN CLOCK (28/09). The sweeps are slow because
            # they read 1,900 stocks; the price is one request. Separating them
            # is what stops the desk showing a price from half an hour ago.
            quote_all(kite, tokens, syms)
            mark(kite, tokens, L); publish(L)
            if time.time() - last_say > 900:
                last_say = time.time()
                print(f"  {now:%H:%M}  "
                      + ("prices updated" if trading
                         else "market closed - the daily book still runs"),
                      flush=True)
        except Exception as e:
            print(f"  (sweep failed: {e} - carrying on)", flush=True)
        time.sleep(PRICE_EVERY if trading else 300)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  stopped.")
    except Exception as e:
        print(f"\n  stopped: {e}\n")
        hold(1)
