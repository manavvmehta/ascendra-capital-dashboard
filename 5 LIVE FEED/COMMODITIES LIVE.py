"""ASCENDRA - COMMODITIES, LIVE

The Commodities tab has been showing a backtest result and nothing else,
because nothing on this machine was watching MCX. This watches it.

THE RULE, as it came out of three years of testing on sixteen MCX commodities:

    the day closes above its 20-day high
    AND TRIX is positive
    AND the WEEKLY Choppiness reading is under 50
    AND that breakout candle is a strong one - its body at least 70% of its
        range, and green

    buy the next day's open
    stop at the 20-day low
    target twice the risk
    give up after 40 days
    long only - shorts lost on every version tested

  306 signals in five and a half years, 72% reached the target, +8.35% a
  trade, and +3.03% once the commodity's own rise is taken out of it. MCX
  costs about 0.02% the round trip, which is already deducted.

It places no orders. It writes what the rule does into its own record and
into the dashboard, exactly the way the formations scanner does.

WHAT IT NEEDS
  Today's Zerodha login - run connect.py in "5 LIVE FEED" first.
  Daily candles only, so it asks Zerodha once an hour and no more.
"""
import json
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = 5058
COST = 0.02                 # MCX round trip, per cent
LOOKBACK = 20               # the breakout window, in days
HOLD = 40                   # give up after this many days
SLOT = 200000.0             # money per position, for the record only
EVERY = 60 * 60
PRICE_EVERY = 60            # the live price on the calls already written


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
LOGF = os.path.join(DATA, "cmd_log.json")
BARSF = os.path.join(DATA, "cmd_bars.json")
SITE = os.path.join(HERE, "WEBSITE", "ascendra_desk.html")

# The sixteen MCX commodities the backtest ran on, with the lot each one
# trades in. VERIFY THESE ON A CONTRACT NOTE before trading real money - the
# exchange changes lot sizes and this file cannot know when it does.
# FOUR COMMODITIES, NOT SIXTEEN (28/09). MM: "can you just flash trades on
# Gold, Silver, Crude and Copper." The mini and micro contracts are the same
# commodity in a smaller lot, so watching them as well produced three calls on
# one move - three GOLDPETAL positions were open at once on the same breakout.
# The big contract of each is kept and the rest are off.
LOTS = {"GOLD": 100,
        "SILVER": 30,
        "CRUDEOIL": 100,
        "COPPER": 2500}


def hold_window(code=0):
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
    print("  Installing the Zerodha library - about twenty seconds...\n")
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
    print(f"\n  COULD NOT INSTALL IT. In a Command Prompt run:\n"
          f"      {sys.executable} -m pip install kiteconnect\n")
    hold_window(1)


def load_kite():
    ensure_kiteconnect()
    keys = os.path.join(FEED, "kite_keys.txt")
    tokf = os.path.join(FEED, "kite_token.json")
    if not (os.path.exists(keys) and os.path.exists(tokf)):
        print("\n  NOT CONNECTED. Run connect.py in '5 LIVE FEED' first.\n")
        hold_window(1)
    api_key = None
    for line in open(keys, encoding="utf8"):
        if line.strip().startswith("api_key"):
            api_key = line.split("=", 1)[-1].strip()
    tok = json.load(open(tokf, encoding="utf8"))
    if tok.get("date") != date.today().isoformat():
        print(f"\n  TODAY'S LOGIN IS MISSING (the saved token is from "
              f"{tok.get('date')}).\n  Run connect.py in '5 LIVE FEED' first.\n")
        hold_window(1)
    from kiteconnect import KiteConnect
    k = KiteConnect(api_key=api_key)
    k.set_access_token(tok["access_token"])
    return k


# ── the readings the rule needs ───────────────────────────────────────────
def ema(v, n):
    k = 2.0 / (n + 1)
    e, out = None, []
    for x in v:
        e = x if e is None else (x - e) * k + e
        out.append(e)
    return out


def trix(bars, n=18):
    cl = [b["close"] for b in bars]
    e3 = ema(ema(ema(cl, n), n), n)
    out = [None]
    for i in range(1, len(e3)):
        p = e3[i - 1]
        out.append(100 * (e3[i] - p) / p if p else None)
    return out


def choppiness(bars, n=14):
    """TradingView's Choppiness Index. Under 50 means trending."""
    import math
    out = [None] * len(bars)
    tr = []
    for i, b in enumerate(bars):
        if i == 0:
            tr.append(b["high"] - b["low"])
        else:
            p = bars[i - 1]["close"]
            tr.append(max(b["high"] - b["low"], abs(b["high"] - p), abs(b["low"] - p)))
    for i in range(n, len(bars)):
        s = sum(tr[i - n + 1:i + 1])
        hh = max(x["high"] for x in bars[i - n + 1:i + 1])
        ll = min(x["low"] for x in bars[i - n + 1:i + 1])
        if hh > ll and s > 0:
            out[i] = 100 * math.log10(s / (hh - ll)) / math.log10(n)
    return out


def weekly(daily):
    """Daily candles rolled into weeks, plus, for each day, the index of the
    LAST COMPLETED week - never the week it is standing in."""
    out, cur, key, idx = [], None, None, []
    for b in daily:
        y, w, _ = date.fromisoformat(str(b["date"])[:10]).isocalendar()
        if (y, w) != key:
            if cur:
                out.append(cur)
            key = (y, w)
            cur = {"date": b["date"], "open": b["open"], "high": b["high"],
                   "low": b["low"], "close": b["close"]}
        else:
            cur["high"] = max(cur["high"], b["high"])
            cur["low"] = min(cur["low"], b["low"])
            cur["close"] = b["close"]
        idx.append(len(out) - 1)
    if cur:
        out.append(cur)
    return out, idx


def signal(bars):
    """Does the LAST COMPLETED day fire the rule? Returns a call or None."""
    if len(bars) < 80:
        return None
    i = len(bars) - 1
    x = bars[i]
    hh = max(b["high"] for b in bars[i - LOOKBACK:i])
    ll = min(b["low"] for b in bars[i - LOOKBACK:i])
    if not (x["close"] > hh):
        return None
    tr = trix(bars)
    if not (tr[i] and tr[i] > 0):
        return None
    wk, widx = weekly(bars)
    j = widx[i] - 1                    # the last week that has CLOSED
    if j < 14:
        return None
    wch = choppiness(wk)
    if j >= len(wch) or wch[j] is None or wch[j] >= 50:
        return None
    rng = x["high"] - x["low"]
    if not (rng > 0 and (x["close"] - x["open"]) / rng >= 0.70
            and x["close"] > x["open"]):
        return None
    stop = ll
    if stop >= x["close"]:
        return None
    return {"at": str(x["date"])[:10], "close": round(x["close"], 2),
            "breakout_of": round(hh, 2), "stop": round(stop, 2),
            "trix": round(tr[i], 4), "week_chop": round(wch[j], 1)}


def make_call(sym, s, lot):
    """Entry is the NEXT day's open, so the call is written with the entry
    unknown and filled in when tomorrow opens. Nothing is assumed."""
    return {"sym": sym, "at": s["at"], "state": "WAITING", "status": "WAITING",
            "breakout_of": s["breakout_of"], "close": s["close"],
            "stop": s["stop"], "entry": None, "target": None,
            "week_chop": s["week_chop"], "lot": lot,
            "first_seen": datetime.now().isoformat(timespec="seconds"),
            "filled_at": None, "closed_at": None, "exit_px": None,
            "result_pct": None, "last": s["close"], "bars_held": 0}


def step(c, bars):
    after = [b for b in bars if str(b["date"])[:10] > c["at"]]
    if not after:
        return
    c["last"] = round(after[-1]["close"], 2)
    if c["status"] == "WAITING":
        b = after[0]
        c["entry"] = round(b["open"], 2)
        if c["entry"] <= c["stop"]:
            c["status"] = "VOID"; c["state"] = "GONE"
            c["why"] = "the next open was already below the stop"
            return
        c["target"] = round(c["entry"] + 2 * (c["entry"] - c["stop"]), 2)
        c["status"] = "OPEN"; c["state"] = "IN"
        c["filled_at"] = str(b["date"])[:10]
    if c["status"] != "OPEN":
        return
    run = [b for b in bars if str(b["date"])[:10] >= (c["filled_at"] or "")]
    c["bars_held"] = len(run)
    for q, b in enumerate(run):
        if q >= HOLD:
            c.update(status="GAVE UP", closed_at=str(b["date"])[:10],
                     exit_px=round(b["close"], 2)); break
        if b["low"] <= c["stop"]:
            px = min(b["open"], c["stop"])
            c.update(status="STOP", closed_at=str(b["date"])[:10],
                     exit_px=round(px, 2)); break
        if b["high"] >= c["target"]:
            px = max(b["open"], c["target"])
            c.update(status="TARGET", closed_at=str(b["date"])[:10],
                     exit_px=round(px, 2)); break
    if c.get("exit_px") is not None and c.get("result_pct") is None:
        g = 100 * (c["exit_px"] - c["entry"]) / c["entry"]
        c["result_pct"] = round(g - COST, 2)
        c["state"] = "DONE"


# ── data ──────────────────────────────────────────────────────────────────
def fetch(kite, years=3):
    """Daily candles for every MCX commodity, continuous across expiries.
    Cached for the day - a daily candle cannot change until the close."""
    today = date.today().isoformat()
    try:
        old = json.load(open(BARSF, encoding="utf8"))
        if old.get("date") == today and old.get("bars"):
            print(f"  candles: today's cache ({len(old['bars'])} commodities)")
            return old["bars"]
    except Exception:
        pass
    inst = kite.instruments("MCX")
    fut = [i for i in inst if i.get("instrument_type") == "FUT"]
    pick = {}
    for i in fut:
        n = i.get("name")
        if n in LOTS and (n not in pick or i["expiry"] < pick[n]["expiry"]):
            pick[n] = i
    frm = (date.today() - timedelta(days=365 * years)).isoformat()
    out = {}
    for n, i in sorted(pick.items()):
        try:
            raw = kite.historical_data(i["instrument_token"], frm, today,
                                       "day", continuous=True)
            bars = [{"date": str(x["date"])[:10], "open": float(x["open"]),
                     "high": float(x["high"]), "low": float(x["low"]),
                     "close": float(x["close"])} for x in raw]
            if len(bars) > 120:
                out[n] = bars
                print(f"    {n:12s} {len(bars)} days")
        except Exception as e:
            print(f"    {n:12s} could not download: {e}")
        time.sleep(0.4)
    # NEVER CACHE AN EMPTY DOWNLOAD (28/09) - see the same note in
    # SMC TWO BOOKS.py. An empty cache silently kills the whole day.
    if not out:
        print("  NOTHING came back from Zerodha - not caching that. Check the "
              "login; this will try again on the next pass.")
        return {}
    try:
        json.dump({"date": today, "bars": out}, open(BARSF, "w"))
    except Exception:
        pass
    return out


_NEAR = {}


def near_contracts(kite):
    """The front-month contract for each commodity - what a quote needs."""
    today = date.today().isoformat()
    if _NEAR.get("date") == today and _NEAR.get("map"):
        return _NEAR["map"]
    m = {}
    try:
        for i in kite.instruments("MCX"):
            n = i.get("name")
            if i.get("instrument_type") == "FUT" and n in LOTS:
                if n not in m or i["expiry"] < m[n][1]:
                    m[n] = (i["tradingsymbol"], i["expiry"])
    except Exception as e:
        print(f"  (could not list the MCX contracts: {e})", flush=True)
        return _NEAR.get("map") or {}
    _NEAR.update(date=today, map={k: v[0] for k, v in m.items()})
    return _NEAR["map"]


def mark(kite, L):
    """A PRICE LABELLED "PRICE NOW" HAS TO BE NOW (28/09).

    Same fault as the one found in SMC TWO BOOKS.py on the same day. `last`
    was only ever the close of the last DAILY candle, and a daily candle does
    not change until the close - so every open commodity showed yesterday's
    price for the whole session, on a market that trades until 11:30 at night.
    The front-month quote is read every minute instead. Closed trades are left
    alone; they keep the exit they actually got.
    """
    near = near_contracts(kite)
    want = sorted({c["sym"] for c in L.values()
                   if c.get("status") in ("WAITING", "OPEN") and c["sym"] in near})
    if not want:
        return
    back = {near[s]: s for s in want}
    try:
        q = kite.ltp(["MCX:" + near[s] for s in want])
    except Exception as e:
        print(f"  (could not read live prices: {e})", flush=True)
        return
    px = {}
    for k, v in q.items():
        s = back.get(k.split(":", 1)[-1])
        if s and v.get("last_price"):
            px[s] = round(float(v["last_price"]), 2)
    for c in L.values():
        if c.get("status") in ("WAITING", "OPEN") and px.get(c["sym"]):
            c["last"] = px[c["sym"]]
    if len(px) < len(want):
        print(f"  (live price came back for {len(px)} of {len(want)} "
              f"commodities)", flush=True)


def load_log():
    try:
        return json.load(open(LOGF, encoding="utf8"))
    except Exception:
        return {}


def save_log(L):
    tmp = LOGF + ".tmp"
    json.dump(L, open(tmp, "w"))
    os.replace(tmp, LOGF)


_SERVE = {"payload": json.dumps({"as_of": None, "calls": []})}


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
    """Into the page itself - a page served from one port cannot read another
    on this desk, which is why the SMC tab sat empty while its program ran."""
    start, end = "/*CMD_START*/", "/*CMD_END*/"
    block = f"{start}const CMDLIVE = {payload};{end}"
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
        tmp = SITE + ".cmd.tmp"
        open(tmp, "w", encoding="utf8").write(h)
        os.replace(tmp, SITE)
    except Exception as e:
        print(f"  (could not write the dashboard: {e})")


def publish(L):
    rows = list(L.values())
    done = [c for c in rows if c["result_pct"] is not None]
    booked = sum(c["result_pct"] / 100.0 * SLOT for c in done)
    openr = [c for c in rows if c["status"] == "OPEN"]
    marked = sum(((c["last"] - c["entry"]) / c["entry"] * SLOT)
                 for c in openr if c.get("entry") and c.get("last"))
    _SERVE["payload"] = json.dumps({
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "slot": SLOT,
        "rule": ("closes above the 20-day high, TRIX positive, the weekly "
                 "Choppiness under 50, and a strong breakout candle"),
        "waiting": [c for c in rows if c["status"] == "WAITING"],
        "open": openr,
        "closed": sorted(done, key=lambda c: c["closed_at"] or "", reverse=True)[:400],
        "booked": round(booked), "closed_n": len(done),
        "marked": round(marked), "open_n": len(openr),
        "wins": sum(1 for c in done if c["result_pct"] > 0)})
    write_site(_SERVE["payload"])


def main():
    print("""
  ASCENDRA - COMMODITIES, LIVE

  Sixteen MCX commodities, daily candles. A call is written when the day
  closes above its 20-day high with TRIX positive, the weekly Choppiness
  under 50, and a strong breakout candle. Buy the next open, stop at the
  20-day low, target twice the risk, give up after forty days. Long only.

  Nothing is ordered. This records what the rule does and keeps it.
""")
    kite = load_kite()
    print("  connected to Zerodha\n")
    L = load_log()
    print(f"  record holds {len(L)} calls so far")
    publish(L)
    threading.Thread(target=serve, daemon=True).start()
    print(f"  serving on http://127.0.0.1:{PORT}   (your desk stays on 5055)\n")

    # NO PAST TRADES ON THIS TAB (28/09). MM: "commodity tab should not have
    # previous trades, record from today."
    #
    # When this was switched on, the rule was replayed over the downloaded
    # history so the tab would not sit empty for weeks. Those were paper
    # trades, flagged `replayed`, and they made the tab claim 26 lakh of
    # profit nobody made. They are dropped here, once, and the replay is
    # gone for good - this record now starts the day it starts.
    gone = [k for k, c in L.items() if c.get("replayed")]
    for k in gone:
        del L[k]
    if gone:
        save_log(L)
        print(f"  dropped {len(gone)} replayed paper trades - this record now "
              f"starts from today\n")
    last_full = 0.0
    while True:
        try:
            # THE FULL CHECK - daily candles, once an hour is more than enough.
            if time.time() - last_full > EVERY:
                last_full = time.time()
                B = fetch(kite)
                for sym, bars in B.items():
                    for c in [c for c in L.values() if c["sym"] == sym
                              and c["status"] in ("WAITING", "OPEN")]:
                        step(c, bars)
                    s = signal(bars)
                    if s:
                        key = f"{sym}|{s['at']}"
                        if key not in L:
                            L[key] = make_call(sym, s, LOTS.get(sym, 1))
                            print(f"  {sym:12s} closed {s['close']} above its "
                                  f"20-day high {s['breakout_of']}  ·  stop "
                                  f"{s['stop']}  ·  buy tomorrow's open")
                save_log(L)
                n_w = sum(1 for c in L.values() if c["status"] == "WAITING")
                n_o = sum(1 for c in L.values() if c["status"] == "OPEN")
                print(f"  {datetime.now():%H:%M}  {len(B)} commodities checked"
                      f" · {n_w} waiting · {n_o} open", flush=True)

            # THE PRICE, ON ITS OWN CLOCK (28/09). The candles cannot change
            # until the close, but the price can, and MCX trades until 11:30
            # at night. So the quote is read every minute and published, which
            # is what stops the desk showing yesterday's price all day.
            mark(kite, L); publish(L)
        except Exception as e:
            print(f"  (check failed: {e} - carrying on)", flush=True)
        time.sleep(PRICE_EVERY)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  stopped.")
    except Exception as e:
        print(f"\n  stopped: {e}\n")
        hold_window(1)
