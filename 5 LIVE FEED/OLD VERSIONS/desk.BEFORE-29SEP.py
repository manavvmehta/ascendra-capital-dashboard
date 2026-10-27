#!/usr/bin/env python3
"""
START THE DESK.py

DOUBLE-CLICK THIS EACH MORNING, after you have connected to Zerodha.

It does three things at once:
    1. runs the call engine against live Zerodha prices
    2. serves your dashboard at  http://localhost:5055
    3. keeps a permanent record of every call it ever makes

Leave the black window open all day. Closing it stops the engine - open calls
stop being watched, and a stop that is not being watched is not a stop.

TO STOP IT
    Close the window, or press Ctrl+C.

WHAT IT WRITES  (all under the DATA folder)
    calls_log.csv        every call ever issued, permanent, never overwritten
    engine_log.txt       what the engine did, for when something looks wrong
    .tech_*.json         the engine's own working files
"""

import csv
import json
import os
import sys
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

def _project_root():
    """Find the ASCENDRA CAPITAL folder, wherever this file was put.

    Checks where it is sitting, then walks upward. Being copied into a
    subfolder should not stop it working - that is a filing decision, not a
    reason to fail.
    """
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        if os.path.isdir(os.path.join(d, "WEBSITE")) and \
           os.path.isdir(os.path.join(d, "5 LIVE FEED")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.dirname(os.path.abspath(__file__))


HERE = _project_root()
FEED = os.path.join(HERE, "5 LIVE FEED")
ENGINE = os.path.join(HERE, "3 CALL ENGINE")
SITE = os.path.join(HERE, "WEBSITE", "ascendra_desk.html")
DATA = os.path.join(HERE, "DATA")
LOG = os.path.join(DATA, "calls_log.csv")
PORT = 5055

os.makedirs(DATA, exist_ok=True)
os.environ["TE_DATA_DIR"] = DATA          # keep the engine's files out of Desktop
sys.path.insert(0, ENGINE)

STATE = {}          # the engine publishes its snapshot here
STARTED = datetime.now()


def hold(code=0):
    try:
        input("\n  Press Enter to close this window. ")
    except Exception:
        pass
    sys.exit(code)


def ensure_deps():
    try:
        import kiteconnect  # noqa
        return
    except ImportError:
        pass
    print("  Installing Zerodha's library (one time) ...\n")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                    "--disable-pip-version-check", "--no-warn-script-location",
                    "kiteconnect"], check=False)
    try:
        import importlib, site
        try:
            site.main()
        except Exception:
            pass
        importlib.invalidate_caches()
        import kiteconnect  # noqa
    except ImportError:
        print("\n  Installed, but this window cannot see it yet.")
        print("  Close this window and double-click the file again.\n")
        hold(1)


def load_kite():
    """Build an authenticated Kite client, or explain exactly what is missing."""
    keys = os.path.join(FEED, "kite_keys.txt")
    tokf = os.path.join(FEED, "kite_token.json")
    if not os.path.exists(keys) or not os.path.exists(tokf):
        print(f"""
  NOT CONNECTED TO ZERODHA YET.

  Open the "5 LIVE FEED" folder and run connect.py first.
  That saves your keys and today's login.
""")
        hold(1)

    api_key = None
    for line in open(keys, encoding="utf8"):
        if line.strip().startswith("api_key"):
            api_key = line.split("=", 1)[-1].strip()
    tok = json.load(open(tokf, encoding="utf8"))

    if tok.get("date") != date.today().isoformat():
        print(f"""
  TODAY'S LOGIN IS MISSING.

  The saved token is from {tok.get('date')}. Zerodha's tokens expire
  at the end of each day, so it will not work now.

  Open "5 LIVE FEED" and run connect.py, then start this again.
  It takes twenty seconds.
""")
        hold(1)

    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(tok["access_token"])
    try:
        kite.quote(["NSE:RELIANCE"])          # prove it before trusting it
    except Exception as e:
        print(f"""
  Zerodha refused the connection: {e}

  Usually today's login has expired or was not completed.
  Run connect.py in "5 LIVE FEED" again.
""")
        hold(1)
    print(f"  Connected to Zerodha as {tok.get('user_name','')}.")
    return kite


# ── converting the engine's calls into what the page reads ─────────────────
# triggered_on / closed_on are DATES to go with triggered_at / closed_at, which
# are only ever HH:MM. For an intraday trade the day is obvious - it is the day
# the call was made. For a SWING trade it is not: a position opened on Wednesday
# and closed the following Tuesday has closed_at "14:20" and nothing anywhere
# saying which Tuesday. Without the date the book cannot know when the money
# came back, so it frees the capital on the next morning and the position stops
# occupying a slot it is genuinely still holding.
FIELDS = ["date", "created_hm", "symbol", "seg", "setup", "side", "tf", "score",
          "conviction", "entry", "sl", "t1", "t2", "rr", "risk0", "status", "r",
          "triggered_at", "triggered_on", "closed_at", "closed_on",
          "horizon", "level", "reasons", "blocked",
          # HOW FAR IT ACTUALLY WENT (MM, 10/09). NOVARTIND closed at +0.5 when
          # the breakeven stop caught it, and then ran to 2550 — past a target
          # of 2377 that the record has no way of showing. Without the best
          # price the trade reached, there is no way to ask whether an exit
          # rule is giving away more than it protects. The engine already
          # tracks these; they were simply never carried into the record.
          "hi_seen", "lo_seen"]

OPEN_STATUSES = {"WAITING", "LIVE", "T1_HIT"}


def OPEN_ST(r):
    return (r.get("status") or "") in OPEN_STATUSES


def hhmm(v):
    if not v:
        return ""
    s = str(v)
    if "T" in s:
        s = s.split("T", 1)[1]
    return s[:5]


def to_row(c):
    reasons = c.get("reasons")
    if isinstance(reasons, (list, tuple)):
        reasons = " · ".join(str(x) for x in reasons)
    return {
        "date": c.get("date") or "",
        "created_hm": hhmm(c.get("created") or c.get("ts")),
        "symbol": c.get("symbol") or "",
        "seg": c.get("seg") or "EQ",
        "setup": c.get("setup") or "",
        "side": c.get("side") or "",
        "tf": c.get("tf") or "",
        "score": c.get("score"),
        "conviction": c.get("conviction") or "",
        "entry": c.get("entry"), "sl": c.get("sl"),
        "t1": c.get("t1"), "t2": c.get("t2"),
        "rr": c.get("rr"), "risk0": c.get("risk0"),
        "status": c.get("status") or "",
        "r": c.get("r"),
        "triggered_at": hhmm(c.get("triggered_at") or c.get("triggered")),
        "closed_at": hhmm(c.get("closed_at") or c.get("closed")),
        # filled in by merge(), which is the only place that sees a call change
        "triggered_on": c.get("triggered_on") or "",
        "closed_on": c.get("closed_on") or "",
        "horizon": c.get("horizon") or "",
        "level": c.get("level") or "",
        "reasons": reasons or "",
        # the highest and lowest the price reached while the call was alive
        "hi_seen": c.get("hi_seen"),
        "lo_seen": c.get("lo_seen"),
    }


_ltp = {"at": None, "px": {}}


def price_watcher(kite):
    """Live price for whatever the book is holding.

    Only open calls are quoted - a handful of symbols, so this is cheap and can
    run far more often than the market scan. Without it a card shows where a
    trade started and where it is going, but not where it actually is, which is
    the one number you look at first.
    """
    # Names that have been proven, one at a time, to be unquotable. Without
    # this the splitting below would rediscover the same dead symbol every six
    # seconds - about twenty requests instead of five, which is slower than
    # the loop itself. Each is left out for ten minutes and then tried again,
    # because a name can go quiet for a session and come back.
    dead = {}
    while True:
        try:
            # Every open call gets a price, wherever it lives. The engine's own
            # list is not enough: a call the engine has dropped still shows on
            # the page, and a card with no price on it looks broken even when
            # the trade is real (MM, 08/09 - the intraday cards were blank).
            syms = sorted({c.get("symbol") for c in STATE.get("calls", [])
                           if c.get("symbol")}
                          | {r.get("symbol") for r in HISTORY
                             if OPEN_ST(r) and r.get("symbol")}
                          | {c.get("symbol") for c in SMC
                             if c.get("symbol") and c.get("status")
                             in ("WAITING", "LIVE")})
            # Quoted in batches rather than truncated at 300. The old cap threw
            # away everything past the three hundredth name ALPHABETICALLY and
            # said nothing, so a stock late in the alphabet silently lost its
            # price the moment the book grew.
            # ONE BAD NAME MUST NOT COST TWO HUNDRED PRICES.
            #
            # MM, 18/09: "in grse cmp is not showing". It was not GRSE. It was
            # 105 stocks at once, spread from AASTHA to XTRANET - 107 of the
            # 251 open calls had no live price at all, TCS and INFY among them.
            #
            # The book has grown: 819 distinct symbols are open across the
            # tabs now, mostly from SMC's 1,170 calls. That is five quote
            # requests fired back to back every six seconds. Kite answers a
            # batch or raises for the whole batch - one delisted name, or one
            # rate-limit rejection, and the old `except: continue` threw away
            # all two hundred prices without a word.
            #
            # So: the calls are PACED like every other Kite call in this file,
            # and a batch that fails is retried in small pieces and then one
            # at a time. A name that genuinely cannot be quoted then costs
            # exactly itself.
            def quote_many(exch, names):
                """Quote these names, losing at most the ones truly unquotable."""
                got = {}
                now = time.time()
                live = [s for s in names
                        if now - dead.get((exch, s), 0) > 600]
                todo = [live[i:i+200] for i in range(0, len(live), 200)]
                while todo:
                    chunk = todo.pop(0)
                    try:
                        q = kite.quote([exch + ":" + s for s in chunk]) or {}
                        for k, v in q.items():
                            sym = k.split(":", 1)[1]
                            oh = v.get("ohlc") or {}
                            got[sym] = {"ltp": v.get("last_price"),
                                        "high": oh.get("high"),
                                        "low": oh.get("low"),
                                        "close": oh.get("close"),
                                        "open": oh.get("open")}
                    except Exception:
                        # split and retry rather than lose the whole batch
                        if len(chunk) > 1:
                            mid = len(chunk) // 2
                            todo[:0] = [chunk[:mid], chunk[mid:]]
                        else:
                            # a single name that will not quote costs itself,
                            # and is set aside so it is not rediscovered on
                            # every pass
                            dead[(exch, chunk[0])] = now
                    time.sleep(0.36)          # the same pacing as everywhere else
                return got

            px = quote_many("NSE", syms)
            # A name NSE does not answer for. Found 08/09: RBZJEWEL was the one
            # open position out of 130 with no price on it, so its card showed
            # no live number and its money was missing from the paper P&L - the
            # strip said 50 open but only 49 were marked, and nothing said why.
            # Kite returns nothing (not an error) for a symbol NSE will not
            # quote, so the miss is silent. Ask BSE for those few; the same
            # company trades there and the last price is within a tick or two.
            # Anything still unanswered is left with no price rather than
            # guessed at, and the page shows a dash.
            missing = [s for s in syms if s not in px]
            if missing:
                px.update(quote_many("BSE", missing))
            # A name neither exchange answered for keeps whatever price it had
            # a moment ago rather than going blank. A stock does not stop
            # having a price because one request was refused, and a card that
            # blinks between a number and a dash is worse than a number that
            # is six seconds old. Anything never quoted at all still shows a
            # dash, which is honest.
            old = _ltp.get("px") or {}
            for s in syms:
                if s not in px and s in old:
                    px[s] = old[s]
            if px:
                _ltp["px"] = px
                _ltp["at"] = datetime.now().strftime("%H:%M:%S")
                # Record the low and high of every open call HERE, at the
                # six-second sample, not once a minute in the watcher. A dip
                # through the stop that lasts forty seconds is a real exit and
                # a minute-by-minute check would walk straight past it.
                try:
                    track_since_fill()
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(6)


# ── the index strip ────────────────────────────────────────────────────────
# Nifty, Bank Nifty and Sensex, live, at the top of the page. Three numbers
# that set the context for every call underneath them.
#
# Quoted by INSTRUMENT TOKEN, not by name. Kite spells these differently in
# different places ("NIFTY 50", "NIFTY_50", "Nifty 50") and a name that is
# almost right returns nothing at all rather than an error. The token is the
# exchange's own identifier and does not move.
INDEX_TOKENS = []                 # [(display name, token)] - filled in main()
_idx = {"at": None, "rows": []}


def index_watcher(kite):
    """Refresh the three index quotes every 5 seconds.

    The last good reading is HELD on failure rather than blanked. A momentary
    API hiccup should not make the top of the dashboard flash "—" at someone
    who is in a trade; a number four seconds stale is honest, an empty box is
    alarming and wrong.
    """
    while True:
        try:
            if INDEX_TOKENS:
                q = kite.quote([t for _n, t in INDEX_TOKENS]) or {}
                rows = []
                for name, tok in INDEX_TOKENS:
                    v = q.get(str(tok)) or q.get(tok) or {}
                    oh = v.get("ohlc") or {}
                    ltp = v.get("last_price")
                    prev = oh.get("close")          # yesterday's close
                    if not ltp:
                        continue
                    chg = (float(ltp) - float(prev)) if prev else None
                    rows.append({
                        "name": name,
                        "ltp": round(float(ltp), 2),
                        "chg": round(chg, 2) if chg is not None else None,
                        "pct": (round(100.0 * chg / float(prev), 2)
                                if chg is not None and prev else None),
                        "open": oh.get("open"), "high": oh.get("high"),
                        "low": oh.get("low"), "prev": prev,
                    })
                if rows:
                    _idx["rows"] = rows
                    _idx["at"] = datetime.now().strftime("%H:%M:%S")
        except Exception:
            pass
        time.sleep(5)


def with_price(row):
    """Attach the live price and how far the trade has travelled.

    TWO THINGS HERE ARE EASY TO GET WRONG AND BOTH WERE, ON DAY ONE.

    1. RISK COMES FROM risk0, THE ORIGINAL STOP - never from entry minus the
       current stop. After a call hits T1 the engine moves the stop up to the
       entry price, so those two are then EQUAL and the risk is zero. Dividing
       by it turned a Rs 65 move on VOLTAMP into "+65R". Jenil's own notes warn
       about exactly this; it is the same mistake wearing a different hat.

    2. A WAITING CALL HAS NO POSITION. Marking an unfilled order to market
       counts profit on something you do not own. Only calls that actually
       filled get a running number.
    """
    p = _ltp["px"].get(row.get("symbol")) or {}
    ltp = p.get("ltp")
    if not ltp:
        return row
    row["ltp"] = round(float(ltp), 2)
    row["day_high"] = p.get("high")
    row["day_low"] = p.get("low")
    row["prev_close"] = p.get("close")

    if row.get("status") not in ("LIVE", "T1_HIT"):
        return row                       # unfilled: no position, no P&L

    e = row.get("entry")
    risk = row.get("risk0") or 0         # the ORIGINAL per-share risk
    if not risk:                         # only if the engine never stored one
        sl = row.get("sl")
        risk = abs(e - sl) if (e and sl) else 0
    if e and risk > 0:
        long = row.get("side") == "LONG"
        move = (ltp - e) if long else (e - ltp)
        row["r_now"] = round(move / risk, 2)
        row["pct"] = round(100 * move / e, 2)
    return row


_log_seen = set()


def _px(v):
    """A price as ONE string, always.

    The 09/09 duplicate: HEROMOTOCO was flashed twice at the identical entry,
    because one copy came back off the CSV as the float 5313.0 and wrote
    "5313.0", while the fresh scan held the int 5313 and wrote "5313". Same
    price, two different keys, so the dedupe never saw them as the same call.
    Every key that contains a price goes through here."""
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return str(v)


def log_key(r):
    return (r["date"], r["created_hm"], r["symbol"], r["setup"], _px(r["entry"]))


def call_ident(r):
    """The identity of a call as a TRADE, independent of when it was noticed.

    The swing sweep rebuilds its setups every ten minutes from the same daily
    closing prices, so it re-emits an identical call with a fresh timestamp.
    log_key() includes the minute, so each sweep looked like a brand-new call:
    on 3 September five swing ideas became two hundred rows, one file, one day.

    Two rows sharing a day, a stock, a setup, a side, an entry AND its original
    risk are the same idea seen twice. Levels are recomputed from live data
    every cycle, so a genuinely different call will differ in at least one.

    THE RISK, NOT THE STOP (fixed 09/09). This used to key on r["sl"] - which
    MOVES. The moment a trade reached T1 its stop was pulled to breakeven, the
    key changed, and the evening scan filed the very same trade a second time:
    MASTERTR on 07/09 appears twice, once at 09:24 with its real stop and once
    at 18:04 with the stop sitting on the entry. A trade recorded twice is
    counted twice in the money the moment it closes. risk0 is the ORIGINAL
    distance from entry to stop and is never rewritten, so it identifies the
    call for its whole life.
    """
    try:
        risk = round(float(r.get("risk0") or 0), 4)
    except Exception:
        risk = 0.0
    if not risk:                       # older rows with no risk0 keep the old key
        risk = str(r.get("sl"))
    return (r["date"], r["symbol"], r["setup"], r.get("side"),
            _px(r["entry"]), _px(risk))


def fill_ident(r):
    """The identity of a FILLED trade, ignoring what day the call is dated.

    MM, 10/09: "on my dashboard it is still showing 2 calls in the record."
    The audit was clean, but underneath it seven trades were in the file twice
    — once dated 3 September and once dated 4 September, with the SAME entry,
    the SAME stop and the SAME fill minute. EDELWEISS filled at 15:31 on both
    days at 134.60. SOUTHBANK at 11:43 on both, at 48.15. That cannot happen:
    a trade fills once.

    They are left over from the repair that moved ten mis-dated swing rows from
    4 September back to 3 September in an earlier session — the moved copy was
    written and the original stayed. call_ident() could never see them because
    the first thing it compares is the date, which is the one field that
    differs.

    So this is a second identity with the date taken out and the fill minute
    put in. It only applies to rows that actually filled, and requires the
    stock, the setup, the side, the entry, the original risk AND the minute of
    the fill all to match. Two genuinely separate entries into the same stock
    would have to agree on every one of those to collide, which in practice
    does not happen; a re-entry days later fills at a different price at a
    different minute.
    """
    at = r.get("triggered_at") or ""
    if not at:
        return None                    # nothing to compare - never deduped here
    try:
        risk = round(float(r.get("risk0") or 0), 4)
    except Exception:
        risk = 0.0
    if not risk:
        risk = str(r.get("sl"))
    return (r["symbol"], r["setup"], r.get("side"),
            _px(r["entry"]), _px(risk), at)


def load_log():
    """The permanent record. Read back on start so history survives restarts."""
    rows = []
    if os.path.exists(LOG):
        try:
            for r in csv.DictReader(open(LOG, encoding="utf8")):
                for k in ("score", "entry", "sl", "t1", "t2", "rr", "risk0", "r"):
                    try:
                        r[k] = float(r[k]) if r.get(k) not in ("", None) else None
                    except Exception:
                        r[k] = None
                rows.append(r)
                _log_seen.add(log_key(r))
        except Exception as e:
            print(f"  (could not read the old log: {e})")
    # Backfill the two new date columns on rows written before they existed.
    # An intraday call fills and closes on the day it was made, so its own date
    # is the right answer. No swing position has closed yet, so nothing here is
    # being guessed.
    for r in rows:
        if r.get("triggered_at") and not r.get("triggered_on"):
            r["triggered_on"] = r.get("date") or ""
        if r.get("closed_at") and not r.get("closed_on"):
            r["closed_on"] = r.get("date") or ""

    # Clean out duplicates already sitting in the file from before the fix.
    # Keep the FIRST sighting of each trade, but carry forward the most
    # resolved copy of it - a later row may hold the outcome.
    keep, seen = [], {}
    rank = {"WAITING": 0, "LIVE": 1, "T1_HIT": 2}
    for r in rows:
        i = seen.get(call_ident(r))
        if i is None:
            seen[call_ident(r)] = len(keep)
            keep.append(r)
            continue
        old = keep[i]
        if rank.get(r.get("status"), 9) >= rank.get(old.get("status"), 9):
            r = dict(r)
            r["created_hm"] = old.get("created_hm") or r.get("created_hm")
            keep[i] = r
    if len(keep) != len(rows):
        print(f"  Log: {len(rows)} rows collapsed to {len(keep)} real calls "
              f"({len(rows) - len(keep)} duplicates from the swing sweep).")

    # NOTE - no date-rewriting happens here any more, deliberately.
    #
    # An earlier version of this function shifted swing rows forward a day to
    # repair calls that had been filed under the candle's date. It could not
    # tell those apart from the engine's own swing calls, which were already
    # correct, so it pushed ten of them a day into the future - it made the
    # problem it was written to fix. Dates are now stamped correctly when a
    # call is created, and history is left alone.
    return keep


def append_log(rows):
    """Append-only. A call already written is UPDATED in place, never doubled -
    keyed on the day, minute, symbol, setup and entry, which together cannot
    collide within one session."""
    if not rows:
        return
    new = os.path.exists(LOG)
    with _HIST_LOCK, open(LOG, "a", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not new:
            w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in FIELDS})


HISTORY = []

# THE CAUSE OF EVERY DUPLICATE WE CHASED ON 09/09 — MASTERTR, NBCC, NAVA,
# KOTAKBANK, KEC. Found from MM asking, for the fourth time, what the red bar
# was about.
#
# The dashboard is served by a ThreadingHTTPServer, so EVERY browser request
# runs in its own thread. /api/calls calls merge(), and merge() reads the whole
# of HISTORY, decides a call is new, and appends it. Two pages open — MM's tab
# and any other — poll every ten seconds, so two threads run merge() at the
# same moment. Both build their index of HISTORY before either has appended.
# Both conclude the call is new. Both append it. The record gets two identical
# rows, and merge() can never remove them afterwards because it only ever
# updates in place.
#
# That is why the duplicates were always EXACT pairs of brand-new calls, why
# they appeared in twos and threes rather than steadily, and why they came back
# after every fix aimed at the KEY rather than at the race. The keys were fine.
#
# One lock. HISTORY is changed by one thread at a time, and the file is written
# by one thread at a time, which also stops two writers interleaving rows.
_HIST_LOCK = threading.RLock()



def stamp_days(new, old=None):
    """Record WHICH DAY a call filled and which day it closed.

    The engine writes only a clock time. This is the one place that sees a call
    change from one state to the next, so it is the only place that can know
    the date those times belong to. An existing date is never overwritten - the
    day a trade opened does not change because it is still open tomorrow.
    """
    today = date.today().isoformat()
    o = old or {}
    if new.get("triggered_at"):
        new["triggered_on"] = o.get("triggered_on") or new.get("triggered_on") or today
    if new.get("closed_at"):
        new["closed_on"] = o.get("closed_on") or new.get("closed_on") or today
    # The day cap's verdict, carried forward. merge() REPLACES a row with the
    # engine's newer copy on every status change, and the engine knows nothing
    # about the cap - so without this line the mark would be wiped the first
    # time the call moved from WAITING to LIVE, and a blocked trade would
    # quietly rejoin the book. Whether the cap was full at the moment a call
    # appeared is a fact about that moment; it cannot be recomputed later.
    if o.get("blocked") and not new.get("blocked"):
        new["blocked"] = o["blocked"]
    return new


# ── the day's loss cap ─────────────────────────────────────────────────────
# MM, 08/09: "I don't mind making lesser profits but I don't want such big
# losses." Monday 07/09 cost Rs 66,493 across 248 intraday calls, against a
# best day of Rs 31,567. The losing days are twice the size of the winning
# ones, and that asymmetry - not the win rate - is what the account feels.
#
# THE RULE, and nothing more than this: once the intraday book has LOST
# DAY_LOSS_CAP rupees today, stop taking new intraday trades until tomorrow.
#
#   · Positions already open are untouched. They run to their own stop,
#     target or 15:10 close exactly as before.
#   · Swing calls are untouched. They hold for days, so "today's loss" is not
#     a meaningful budget for them, and the version of this rule that covers
#     them has not been backtested. MM chose intraday-only on 08/09.
#   · Nothing about how calls are FOUND changes. No score, no threshold, no
#     parameter of the engine is touched. This decides only whether a call
#     that has already been found is taken.
#   · Every blocked call is still RECORDED, still tracked and still graded -
#     MM asked for this explicitly. It is marked blocked="DAY_CAP" and left
#     out of the money, so the cost of the rule is visible rather than
#     assumed. If the blocked trades would have made money, the record says so.
#
# Backtested over three years and 4,285 intraday trades: total -Rs 7,17,278
# becomes -Rs 63,495, and the worst single day -Rs 89,706 becomes -Rs 20,274.
DAY_LOSS_CAP = 15000.0            # rupees of realised intraday loss per day
BOOK_CAPITAL = 5000000.0          # the same figures the dashboard books on
BOOK_SLOT    = 100000.0


def _mins(t):
    p = str(t or "").split(":")
    try:
        return int(p[0]) * 60 + int(p[1]) if len(p) >= 2 else None
    except Exception:
        return None


def _opened_at(r):
    return ((r.get("triggered_on") or r.get("date") or "") + " "
            + (r.get("triggered_at") or ""))


def _closed_at(r):
    return (((r.get("closed_on") or r.get("date") or "") + " "
             + r.get("closed_at")) if r.get("closed_at") else None)


def taken_rows():
    """Replay the book and return the calls that actually got capital.

    This is the SAME allocation the dashboard runs, deliberately: fifty slots
    of one lakh, first come first served, one position per stock at a time, a
    slot freed only when that trade genuinely closes. The cap has to count the
    money MM sees on the page, not a different population - the last time two
    figures were computed from two different sets of trades he was shown
    +Rs 19,469 for a book that was actually down Rs 14,259, and he was right
    to call it out.

    Checked against the dashboard on 08/09: both give -Rs 15,589 to the rupee.
    """
    slots = max(1, int(BOOK_CAPITAL // BOOK_SLOT))
    filled = [r for r in HISTORY
              if r.get("triggered_at") and _mins(r.get("triggered_at")) is not None
              and not r.get("blocked")]
    filled.sort(key=_opened_at)
    held, out = [], []
    for r in filled:
        now = _opened_at(r)
        held = [h for h in held if not (h[1] and h[1] <= now)]
        try:
            e = float(r.get("entry") or 0)
        except Exception:
            e = 0.0
        qty = int(BOOK_SLOT // e) if e > 0 else 0
        if any(h[0] == r.get("symbol") for h in held):
            continue                                   # already holding it
        if len(held) >= slots:
            continue                                   # every slot was full
        if qty < 1:
            continue                                   # one share costs too much
        out.append((r, qty))
        held.append((r.get("symbol"), _closed_at(r)))
    return out


_cap = {"at": 0.0, "used": 0.0, "n": 0, "halted": False, "day": ""}


def cap_state(force=False):
    """Rupees the intraday book has LOST today, and whether that is over the cap.

    Realised only. A position still running is not money lost - it can still
    come back, and stopping the day on a paper number would halt trading on a
    dip that recovers by lunch. The counter therefore moves only when a trade
    actually closes.

    A trade counts on the day it CLOSED, which for intraday is always the day
    it opened - there is no such thing as an intraday trade that closes on a
    different day, so the ambiguity MM asked about cannot arise here. That is
    exactly why the swing book is left out of this.
    """
    today = date.today().isoformat()
    now = time.time()
    if _cap["day"] != today:                  # new day, fresh budget
        _cap.update({"at": 0.0, "used": 0.0, "n": 0, "halted": False,
                     "day": today, "said": False})
    if not force and now - _cap["at"] < 15:   # recomputed at most 4x a minute
        return _cap
    used, n = 0.0, 0
    for r, qty in taken_rows():
        if (r.get("tf") or "").upper() == "SWING":
            continue
        if (r.get("closed_on") or r.get("date") or "") != today:
            continue
        try:
            rr, rk = float(r.get("r")), float(r.get("risk0"))
        except (TypeError, ValueError):
            continue
        if not rk:
            continue
        used += rr * rk * qty
        n += 1
    _cap.update({"at": now, "used": used, "n": n,
                 "halted": used <= -DAY_LOSS_CAP, "day": today})
    return _cap




def merge(rows):
    """Fold this cycle's calls into the permanent history."""
    with _HIST_LOCK:
        return _merge_locked(rows)


_merge_said = {}


def settled_wins(old, new):
    """Once a trade is settled, the live feed may not un-settle it.

    MM, 10/09: "check about all the stocks." Comparing the engine's live book
    against the record found ELEVEN disagreements, and they were not harmless:

        GARUDA      record T2_HIT     engine VOID_RESTART
        AURIONPRO   record TIME_EXIT  engine VOID_RESTART
        BIRLACABLE  record SL_HIT     engine LIVE
        MANAKCOAT   record SL_HIT     engine LIVE

    Both are the same fault seen from two sides. Jenil's engine keeps its own
    call book, and on restart it drops anything not dated today and marks it
    VOID_RESTART so the history "updates". That is right when the desk never
    settled the call. It is WRONG here, because the desk had already settled
    these from the exchange's own candles hours earlier - so a real, graded
    result was about to be overwritten with a void. GARUDA was a WINNER.

    The other direction is worse still: an engine that comes back up thinking
    BIRLACABLE is LIVE would REOPEN a trade the record has already closed as a
    stop loss - the money counted twice, once as a loss and again as an open
    position.

    So: an OPEN row still belongs to the live feed, which sees every tick and
    is the right authority while a trade is running. A SETTLED row belongs to
    the record. The feed may not void it, and may not reopen it. Anything
    else - a normal status change on a live trade - passes through untouched.

    Repairs are unaffected: they rewrite HISTORY directly, not through merge.
    """
    if OPEN_ST(old):
        return False                    # still running - the feed decides
    ns = (new.get("status") or "")
    if ns.startswith("VOID"):
        return True                     # a restart-void cannot erase a result
    if ns in OPEN_STATUSES:
        return True                     # a closed trade is never reopened
    return False


def _merge_locked(rows):
    global HISTORY
    kept_settled = 0
    idx = {log_key(r): i for i, r in enumerate(HISTORY)}
    ident = {call_ident(r): i for i, r in enumerate(HISTORY)}
    # THE SAME FILL, whatever date the row carries. MM, 11/09: three rows kept
    # coming back after every restart. The engine's book still holds MANALIPETC,
    # IDFCFIRSTB and EDELWEISS as open swings - phantoms from the old date
    # repair - and republishes them every scan. dedupe_history() strips them on
    # each save, merge() adds them straight back, and round it goes.
    #
    # It goes round because the phantom is dated 4 September and the real,
    # settled row is dated the 3rd, so neither log_key nor call_ident matches
    # and the phantom is treated as a brand-new call. fill_ident does match -
    # same price, same stop, same fill minute - so it is checked here too,
    # before anything is appended.
    byfill = {}
    for i, r in enumerate(HISTORY):
        k2 = fill_ident(r)
        if k2 is not None and k2 not in byfill:
            byfill[k2] = i
    fresh = []
    for r in rows:
        k = log_key(r)
        if k in idx:
            if settled_wins(HISTORY[idx[k]], r):
                kept_settled += 1
                continue
            HISTORY[idx[k]] = stamp_days(r, HISTORY[idx[k]])   # status/R may have changed
            continue
        j = ident.get(call_ident(r))
        if j is not None:
            if settled_wins(HISTORY[j], r):
                kept_settled += 1
                continue
            # Same trade, seen again on a later sweep. Keep the FIRST sighting
            # time - that is when the idea appeared, and it is what the day's
            # timeline is built from - but take the newer status and result.
            r = dict(r)
            r["created_hm"] = HISTORY[j].get("created_hm") or r.get("created_hm")
            HISTORY[j] = stamp_days(r, HISTORY[j])
            idx[log_key(r)] = j
            continue
        k2 = fill_ident(r)
        if k2 is not None and k2 in byfill:
            if settled_wins(HISTORY[byfill[k2]], r):
                kept_settled += 1
                continue                 # the settled row stands; drop the copy
        r = stamp_days(r)
        # A brand-new call, seen for the first time. This is the only moment
        # the cap has any say - and it only marks the row, it never discards
        # it. The call is recorded, tracked and graded exactly as any other;
        # it simply does not get capital, so it stays out of the P&L and
        # appears under "Trades not taken" with the reason.
        if (not r.get("blocked")) and (r.get("tf") or "").upper() != "SWING":
            st = cap_state()
            if st["halted"]:
                r["blocked"] = "DAY_CAP"
                if not _cap.get("said"):
                    _cap["said"] = True
                    print(f"\n  DAY CAP REACHED - intraday is down "
                          f"Rs {abs(st['used']):,.0f} on {st['n']} closed trades. "
                          f"No new intraday trades today; open ones run on. "
                          f"Calls are still being recorded and graded.\n")
        HISTORY.append(r)
        fresh.append(r)
        idx[k] = len(HISTORY) - 1
        ident[call_ident(r)] = len(HISTORY) - 1
    if fresh:
        append_log(fresh)
    if kept_settled and not _merge_said.get(kept_settled):
        _merge_said[kept_settled] = True
        print(f"  ({kept_settled} settled call{'s' if kept_settled > 1 else ''} "
              f"kept as recorded — the engine tried to void or reopen them "
              f"after its restart)")
    return fresh


def log_saver():
    """Rewrite the record every two minutes, not only at shutdown.

    append_log() writes a call ONCE, at first sighting - when it is still
    WAITING. Every status change after that lived only in memory, so an
    ungraceful exit (window closed with the X, power cut, PC asleep) left a
    file in which nothing ever resolved. A day of outcomes would read as a day
    of unfilled orders, and no error would have said so.
    """
    while True:
        time.sleep(120)
        try:
            rewrite_log()
        except Exception:
            pass


def audit_record(quiet=False):
    """Check every call for every kind of impossible state, at every startup.

    MM found three record bugs in two days, each by noticing one stock. That is
    the wrong way round: he should not be the thing that catches these. Every
    statement below must be true of an honest record, and any row that breaks
    one is printed the moment the desk starts."""
    today = date.today().isoformat()
    bad = {}

    def flag(name, r):
        bad.setdefault(name, []).append(r)

    def num(r, k):
        try:
            return float(r[k]) if r.get(k) not in ("", None) else None
        except Exception:
            return None

    seen = {}
    for r in HISTORY:
        st = r.get("status") or ""
        tf = (r.get("tf") or "").upper()
        d = r.get("date") or ""
        e, sl, t1, t2 = (num(r, "entry"), num(r, "sl"), num(r, "t1"), num(r, "t2"))
        rk, rr = num(r, "risk0"), num(r, "r")
        ta, ca = r.get("triggered_at") or "", r.get("closed_at") or ""
        td = r.get("triggered_on") or d
        cd = r.get("closed_on") or d
        long = (r.get("side") or "LONG") == "LONG"
        op = st in OPEN_STATUSES

        if op and tf != "SWING" and d < today:
            flag("intraday calls left open from an earlier day", r)
        if op and rr is not None:
            flag("still open but already graded", r)
        if (not op) and st != "EXPIRED" and not st.startswith("VOID") and rr is None:
            flag("closed but never graded", r)
        if (not op) and not ca:
            flag("closed with no closing time", r)
        if ta and ca and (cd + ca) < (td + ta):
            flag("closed before it was filled", r)
        if op and st != "WAITING" and not ta:
            flag("live with no fill time", r)
        if st == "WAITING" and ta:
            flag("waiting but carries a fill time", r)
        if rk is not None and rk <= 0:
            flag("zero or negative risk", r)
        if e and sl and t1 and t2:
            if long and not (t2 >= t1 > e):
                flag("targets not above the entry on a long", r)
            if (not long) and not (t2 <= t1 < e):
                flag("targets not below the entry on a short", r)
        # WHAT A STOPPED-OUT TRADE IS ALLOWED TO BE GRADED.
        #
        # This used to demand that every SL_HIT graded exactly -1R, and it
        # raised a red "THE MONEY BELOW IS WRONG" banner whenever one did not.
        # The banner came back day after day and MM restarted the desk each
        # time, because a restart cannot fix something that was never broken.
        #
        # It was never broken. The engine pulls the stop to BREAKEVEN once a
        # trade reaches T1. A trade that then turns round is stopped at its
        # entry price, so it books a few paise of slippage, not a full stop.
        # Checked against the record on 17/09: every single SL_HIT not graded
        # -1R had sl EXACTLY equal to entry - all 23 of them, GOKEX -0.01,
        # ASTRAZEN -0.09, ATGL -0.34 and so on. Correct rows, every one.
        #
        # A gap can also carry a trade PAST its stop overnight, which books
        # worse than -1R and is equally correct.
        #
        # So the rule is now what is genuinely impossible: a stop that made
        # money, or a full stop that somehow cost less than it should have.
        if st == "SL_HIT" and rr is not None:
            at_be = (e is not None and sl is not None and abs(sl - e) < 1e-9)
            if rr > 0.02:
                flag("stopped out but booked a profit", r)
            elif (not at_be) and rr > -0.9:
                flag("stopped at its original stop but graded better than -1R", r)
        if st == "T2_HIT" and rr is not None and rr <= 0:
            flag("target hit but graded a loss", r)
        if rr is not None and abs(rr) > 10:
            flag("an implausibly large result", r)
        k = (d, r.get("symbol"), r.get("setup"), r.get("side"), str(r.get("entry")))
        if k in seen:
            flag("the same call recorded more than once", r)
        seen[k] = 1

    if not bad:
        if not quiet:
            print(f"  Record check: {len(HISTORY)} calls, nothing impossible found.")
        return {}
    print(f"\n  RECORD CHECK FOUND PROBLEMS in {len(HISTORY)} calls:")
    for name, rows in sorted(bad.items(), key=lambda kv: -len(kv[1])):
        days = sorted({(x.get('date') or '') for x in rows})
        print(f"     {len(rows):>5}  {name}"
              f"   ({days[0]}{' to ' + days[-1] if len(days) > 1 else ''})")
        for x in rows[:3]:
            print(f"            {x.get('date')} {x.get('symbol')} "
                  f"{x.get('setup')} {x.get('status')}")
    print()
    return bad


_GAPS = {"day": "", "rows": []}
BOOK_SLOT_RS = 100000.0        # what the book puts into one stock


def opening_fill(kite, sym, qty, long):
    """The price you would REALLY have got, selling into the open.

    MM, 09/09: "if you book at the opening price then make sure volume is there
    and if we sell at market then i need net sell avg."

    He is right and it changes the answer. The opening price is ONE print. If
    two hundred shares trade there and the position is two thousand, that price
    was never available for the whole position - the rest fills lower, into
    whatever the book offers. Recording the opening tick would flatter the
    result in exactly the situation that hurts most.

    So this walks the first minutes of trading, adds up real volume until it
    covers the quantity, and returns the volume-weighted average of those
    prices. That is the net average a market order would actually have got.

    Returns (price, minutes_needed) or (None, 0) if the candles are unavailable
    - in which case nothing is guessed and the position is left alone.
    """
    today = date.today().isoformat()
    try:
        raw = kite.historical_data(_token_of(sym), f"{today} 09:15:00",
                                   f"{today} 09:45:00", "minute") or []
    except Exception:
        return None, 0
    if not raw:
        return None, 0
    got, cost, mins = 0.0, 0.0, 0
    for c in raw:
        v = float(c.get("volume") or 0)
        if v <= 0:
            continue
        mins += 1
        # the typical price traded in that minute, weighted by how much of it
        # the position actually needs
        px = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3
        take = min(v, qty - got)
        got += take
        cost += take * px
        if got >= qty:
            break
    if got <= 0:
        return None, 0
    return cost / got, mins


def open_gap_check(kite, quiet=False):
    """At the open, find every position the gap has already decided.

    MM, 09/09: "I know we can't do anything about gap up or gap down, but at
    least I need you to flash entry or exit at that time."

    Nothing here prevents a gap - nothing can. A stop is a price you ask for.
    If a stock closes at 100 with a stop at 95 and opens at 85, there was no
    trade between those prices and nobody was ever going to sell you out at 95.
    What this does is make sure MM KNOWS within a minute of the open, and that
    the money says what actually happened.

    The exit price is NOT the opening tick. It is the volume-weighted average
    of the opening minutes for the size actually held - see opening_fill().
    """
    today = date.today().isoformat()
    if _GAPS["day"] != today:
        _GAPS.update({"day": today, "rows": []})
    watch = [r for r in HISTORY if OPEN_ST(r) and r.get("entry")]
    if not watch or kite is None:
        return 0
    syms = sorted({r["symbol"] for r in watch})
    _load_instruments(kite, set(syms))
    px = {}
    for i in range(0, len(syms), 200):
        try:
            q = kite.quote(["NSE:" + s for s in syms[i:i+200]]) or {}
        except Exception:
            continue
        for k, v in q.items():
            oh = v.get("ohlc") or {}
            if oh.get("open"):
                px[k.split(":", 1)[1]] = float(oh["open"])
        time.sleep(0.36)
    found = 0
    for r in watch:
        op = px.get(r.get("symbol"))
        if not op:
            continue
        try:
            entry = float(r["entry"]); sl = float(r["sl"])
            risk = float(r.get("risk0") or 0) or abs(entry - sl)
        except (TypeError, ValueError, KeyError):
            continue
        if risk <= 0 or entry <= 0:
            continue
        long = (r.get("side") or "LONG") == "LONG"
        st = r.get("status")

        if st in ("LIVE", "T1_HIT"):
            # A position that filled TODAY cannot have been gapped out at
            # today's open — at that moment it did not exist. Same guard as
            # the two settlers; added in the 10/09 sweep for consistency
            # rather than because it had bitten yet.
            if (r.get("triggered_on") or r.get("date")) == today:
                continue
            if (op >= sl) if long else (op <= sl):
                continue                     # the open did not gap through it
            qty = max(int(BOOK_SLOT_RS // entry), 1)
            fill, mins = opening_fill(kite, r["symbol"], qty, long)
            time.sleep(0.36)
            if fill is None:
                fill, mins = op, 0           # no candles - the open is all we have
            loss_per_share = (entry - fill) if long else (fill - entry)
            r["status"] = "SL_HIT"
            r["r"] = round(-loss_per_share / risk, 2)
            r["closed_on"], r["closed_at"] = today, "09:15"
            r["reasons"] = ((r.get("reasons") or "") +
                            f" · GAPPED past the stop at the open. Stop {sl:.2f}, "
                            f"opened {op:.2f}, and selling {qty} shares into the "
                            f"first {max(mins,1)} minute(s) averaged {fill:.2f}")
            _GAPS["rows"].append({
                "kind": "EXIT", "symbol": r["symbol"], "side": r.get("side"),
                "want": round(sl, 2), "open": round(op, 2), "got": round(fill, 2),
                "qty": qty, "mins": mins,
                "rupees": round(-loss_per_share * qty),
                "planned": round(-risk * qty),
                "setup": r.get("setup"), "tf": r.get("tf")})
            found += 1
        elif st == "WAITING":
            if (op <= entry) if long else (op >= entry):
                continue
            slip = 100 * abs(op - entry) / entry
            if slip < 0.25:
                continue                     # a tick or two is not a gap
            _GAPS["rows"].append({
                "kind": "ENTRY", "symbol": r["symbol"], "side": r.get("side"),
                "want": round(entry, 2), "open": round(op, 2),
                "got": round(op, 2), "slip": round(slip, 2),
                "setup": r.get("setup"), "tf": r.get("tf")})
            found += 1
    if found:
        rewrite_log()
        if not quiet:
            ex = sum(1 for g in _GAPS["rows"] if g["kind"] == "EXIT")
            en = len(_GAPS["rows"]) - ex
            print(f"\n  GAP AT THE OPEN — {ex} position(s) stopped out on the "
                  f"opening trade, {en} order(s) filling worse than the card says.")
            for g in _GAPS["rows"]:
                if g["kind"] == "EXIT":
                    print(f"     EXIT  {g['symbol']:<12} stop {g['want']:>9.2f}"
                          f"  opened {g['open']:>9.2f}  sold at {g['got']:>9.2f}"
                          f"   Rs {g['rupees']:>+8,} (planned Rs {g['planned']:+,})")
                else:
                    print(f"     ENTRY {g['symbol']:<12} wanted {g['want']:>9.2f}"
                          f"  opened {g['open']:>9.2f}   {g['slip']:.1f}% worse")
    return found


def open_watcher(kite):
    """Run the gap check once, just after the market opens."""
    done = ""
    while True:
        time.sleep(20)
        try:
            now = datetime.now()
            today = now.date().isoformat()
            if now.weekday() > 4 or done == today:
                continue
            if (now.hour, now.minute) < (9, 16):
                continue
            if now.hour >= 15:
                done = today          # missed it today; do not run at the close
                continue
            done = today
            open_gap_check(kite)
        except Exception as e:
            print(f"  (open gap check failed: {e})")


def _swing_fill_day(kite, tok, r, day, entry, risk, t1, t2, long):
    """What the fill day itself did, AFTER the trade actually entered.

    A daily candle is a single high and low for a whole session, with no way to
    say when either happened. On the day a swing trade enters, that makes it
    useless and worse than useless: the day's low is very often from the
    morning, hours before a trade that filled in the afternoon, and reading it
    as a stop hit books a loss that never happened.

    So the fill day is replayed from its own 15-minute candles, starting at the
    candle the trade filled on, exactly as the intraday settler does.

    Returns (status, r, "HH:MM"), or ("T1_HIT", None, hm) when the day only
    reached target one, or None when the day decided nothing at all — which is
    the normal answer for a swing position and leaves it open.
    """
    at = r.get("triggered_at") or ""
    if not at:
        return None
    try:
        seq = kite.historical_data(tok, f"{day} 09:00:00",
                                   f"{day} 15:40:00", "15minute") or []
        time.sleep(0.36)
    except Exception:
        return None
    stop = (entry - risk) if long else (entry + risk)
    r1 = abs(t1 - entry) / risk
    r2 = abs(t2 - entry) / risk
    tier1 = r.get("status") == "T1_HIT"
    if tier1:
        stop = entry
    out = None
    for c in seq:
        hm = str(c["date"])[11:16]
        if hm < at:
            continue                       # before the trade existed
        hi, lo = float(c["high"]), float(c["low"])
        hit_sl = (lo <= stop) if long else (hi >= stop)
        hit_t1 = (hi >= t1) if long else (lo <= t1)
        hit_t2 = (hi >= t2) if long else (lo <= t2)
        if hit_sl:
            # No gap branch here, deliberately. A gap is an OPENING price, and
            # inside a session there is no opening price to gap from — the
            # trade filled at a price that was trading. The stop is the exit.
            if tier1:
                return ("T1_TRAIL_OUT", r1 / 2, hm)
            return ("SL_HIT", -1.0, hm)
        if hit_t2:
            return ("T2_HIT", (r1 + r2) / 2 if tier1 else (r1 + r2) / 2, hm)
        if hit_t1 and not tier1:
            tier1 = True
            stop = entry
            out = ("T1_HIT", None, hm)
    return out


def settle_swing(kite, quiet=False):
    """Settle open SWING positions from their own daily candles.

    THE GAP, found 09/09 after MM restarted and eleven rows still showed open
    with the price far past their stop — IFCI 11% below, RBZJEWEL 6.8% below,
    some since 4 September.

    settle_stale() and the 15:45 sweep both skip SWING on purpose: a swing
    trade is MEANT to run for days, so it must not be force-closed at 15:10.
    But that left them with no candle-based settlement at all. Their only
    guardian was the live tracker, and when a restart orphaned them nothing
    ever noticed again. An intraday call that slips through is caught the same
    afternoon; a swing call that slips through can sit wrong for weeks.

    So this does for them what the 15:45 sweep does for intraday, without ever
    forcing an exit: walk each position's DAILY candles from the day it filled,
    and close it only where it genuinely hit something —

        stop      the low (or high) of a day reaches the stop
        T1        target one reached, and the stop moves to breakeven after
        T2        target two reached
        neither   left open, which is the correct answer for a live swing

    The stop is checked before the target within a day, so a day that reached
    both is recorded as the loss. That is the pessimistic reading and it is the
    same convention every other settlement in this file uses.
    """
    today = date.today().isoformat()
    open_sw = [r for r in HISTORY
               if OPEN_ST(r) and (r.get("tf") or "").upper() == "SWING"
               and r.get("status") in ("LIVE", "T1_HIT")
               and r.get("triggered_at")]
    if not open_sw:
        return 0
    if kite is None:
        if not quiet:
            print("  (no Kite session — swing positions not settled)")
        return 0
    _load_instruments(kite, {r.get("symbol") for r in open_sw})
    closed = 0
    for r in open_sw:
        try:
            tok = _token_of(r.get("symbol"))
            if not tok:
                continue
            frm = (r.get("triggered_on") or r.get("date") or today)
            raw = kite.historical_data(tok, f"{frm} 00:00:00",
                                       f"{today} 23:59:59", "day") or []
            time.sleep(0.36)
            if not raw:
                continue
            entry = float(r["entry"]); risk = float(r.get("risk0") or 0)
            t1, t2 = float(r["t1"]), float(r["t2"])
            long = (r.get("side") or "LONG") == "LONG"
            if risk <= 0:
                continue
            # start from the ORIGINAL stop and re-derive T1, so a row already
            # marked T1_HIT is not stopped out by a dip that happened BEFORE
            # its target was reached
            stop = (entry - risk) if long else (entry + risk)
            tier1 = False
            r1 = abs(t1 - entry) / risk
            r2 = abs(t2 - entry) / risk
            # THE FILL DAY CANNOT BE JUDGED BY A DAILY CANDLE (MM, 09/09).
            #
            # This is the 210-row bug again, in the daily settler, and I did
            # not carry the fix across when I found it in the intraday one.
            #
            # EDELWEISS filled at 15:31 on 03/09. The daily candle for 03/09
            # opened hours EARLIER, at 09:15, below the stop — because the
            # trade did not exist yet. The gap branch below read that open as
            # "it gapped through the stop", closed the position at 09:15 and
            # booked -1.42 instead of -1.00. Nine more rows the same way, all
            # of them recorded as closing before they opened.
            #
            # A daily candle is one number for a whole session. It cannot say
            # WHEN in that session the low happened, so on the day a trade
            # enters it cannot decide anything: the low may be from before the
            # entry. So the daily walk now starts the day AFTER the fill, and
            # the fill day itself is settled from that day's 15-minute candles
            # from the fill time onward — the exact thing _settle_one does.
            # Nothing is guessed and nothing is skipped.
            #
            # BUT ONLY WHEN IT COULD MATTER (MM, 09/09: "why is it taking so
            # much time"). The extra 15-minute fetch is one more Kite call per
            # open position, and with forty-odd swings open that is forty more
            # round trips at every startup. It is only ever needed if the fill
            # DAY got near something: if that day's high never reached target
            # one and its low never reached the stop, no arrangement of the
            # minutes inside it can have hit anything, so the minutes are not
            # worth asking for. This skips the fetch for the large majority of
            # positions and changes no answer.
            fd = next((c for c in raw if str(c["date"])[:10] == frm), None)
            near = True
            if fd:
                fh, fl = float(fd["high"]), float(fd["low"])
                near = ((fl <= (entry - risk) or fh >= t1) if long
                        else (fh >= (entry + risk) or fl <= t1))
            hit = (_swing_fill_day(kite, tok, r, frm, entry, risk, t1, t2, long)
                   if near else None)
            if hit:
                st, rr, at = hit
                if st == "T1_HIT":
                    tier1 = True
                    stop = entry
                    r["status"] = "T1_HIT"
                    r["sl"] = round(entry, 2)
                else:
                    r["status"] = st
                    r["r"] = round(rr, 2)
                    r["closed_on"], r["closed_at"] = frm, at
                    closed += 1
                    continue
            for c in raw:
                d = str(c["date"])[:10]
                if d <= frm:
                    continue          # the fill day was handled above
                hi, lo = float(c["high"]), float(c["low"])
                op = float(c.get("open") or 0)
                hit_sl = (lo <= stop) if long else (hi >= stop)
                hit_t1 = (hi >= t1) if long else (lo <= t1)
                hit_t2 = (hi >= t2) if long else (lo <= t2)
                if hit_sl:
                    # A GAP THROUGH THE STOP (MM, 09/09). If the day OPENED
                    # already beyond the stop, the position was never sold at
                    # the stop - it was sold at the open, and the loss is
                    # whatever that open was. Booking it as a flat -1R would
                    # hide the exact trade that costs the most money: a stock
                    # that opens 8% down on news. The stop is a price you ask
                    # for, not a price you are guaranteed.
                    gapped = op > 0 and ((op < stop) if long else (op > stop))
                    exit_px = op if gapped else stop
                    rr = ((exit_px - entry) if long else (entry - exit_px)) / risk
                    r["status"] = "T1_TRAIL_OUT" if (tier1 and not gapped) else "SL_HIT"
                    r["r"] = round(r1 / 2, 2) if (tier1 and not gapped) else round(rr, 2)
                    r["closed_on"] = d
                    r["closed_at"] = "09:15" if gapped else "15:30"
                    if gapped:
                        r["reasons"] = ((r.get("reasons") or "") +
                                        f" · GAPPED through the stop: opened at "
                                        f"{op:.2f} against a stop of {stop:.2f}")
                    closed += 1
                    break
                if hit_t2:
                    # the same in your favour - a gap THROUGH the target pays
                    # the open, not the target
                    gapped = op > 0 and ((op > t2) if long else (op < t2))
                    exit_px = op if gapped else t2
                    rr = ((exit_px - entry) if long else (entry - exit_px)) / risk
                    r["status"] = "T2_HIT"
                    r["r"] = round(rr if gapped else (r1 + r2) / 2, 2)
                    r["closed_on"] = d
                    r["closed_at"] = "09:15" if gapped else "15:30"
                    closed += 1
                    break
                if hit_t1 and not tier1:
                    tier1 = True
                    stop = entry
                    r["status"] = "T1_HIT"
                    r["sl"] = round(entry, 2)
        except Exception:
            continue
    if closed:
        rewrite_log()
        if not quiet:
            print(f"  {closed} swing position{'s' if closed > 1 else ''} settled "
                  f"from daily candles.")
    elif not quiet:
        print(f"  {len(open_sw)} swing positions checked, none had hit anything.")
    return closed


def sync_from_engine_book():
    """Read the engine's OWN call file and take the truth from it.

    MM, 10/09: "novarti is still there in live calls? what was the point of
    restarting?" A fair question, and the answer is that my last fix addressed
    the wrong thing. Here is the real one.

    The engine hands the desk a snapshot each scan. Inside it:

        "calls":           every call still WAITING / LIVE / T1_HIT
        "resolved_today":  done[-10:]      <- the last TEN closed calls

    That ten is the whole problem. A call that closes is dropped from "calls"
    the moment it closes, and it sits in "resolved_today" only until ten more
    close behind it. After that the desk NEVER SEES IT AGAIN — not late, not
    eventually. Never. The row stays open in the record for ever.

    NOVARTIND closed at 09:33 with a result of +0.5. Seventeen calls have
    closed today, so it fell out of the window long ago, and no amount of
    restarting brings it back: every restart replays the same ten.

    But the engine also PERSISTS its whole book to DATA\\.tech_calls.json, and
    that file has every call with its true status - NOVARTIND included, marked
    T1_TRAIL_OUT with r=0.5 and a closing time of 09:33. Nothing is missing
    from it. So the desk stops relying on the ten-item window and reads the
    book itself.

    This changes nothing about Jenil's engine and touches none of his
    parameters - it only reads a file he already writes.
    """
    p = os.path.join(DATA, ".tech_calls.json")
    if not os.path.exists(p):
        return 0
    try:
        book = json.load(open(p, encoding="utf8")).get("calls") or []
    except Exception as e:
        print(f"  (could not read the engine's call book: {e})")
        return 0
    rows = []
    for c in book:
        try:
            rows.append(to_row(c))
        except Exception:
            continue
    if not rows:
        return 0
    before = sum(1 for r in HISTORY if OPEN_ST(r))
    merge(rows)
    after = sum(1 for r in HISTORY if OPEN_ST(r))
    return before - after


def record_watcher():
    """Keep the record up to date whether or not anyone is looking at the page.

    MM, 10/09, after a restart that did not clear the warning: "you said all
    these problems will be cleared. what's going on?"

    He was right to push. NOVARTIND had hit target one, its stop moved to
    breakeven at 2323.80, and the engine watched price come back through it and
    decided T1_TRAIL_OUT — correctly. That decision never reached the record.
    The desk restarted, HISTORY was reloaded from the file, and the trade came
    back as still open. By then price was 2550.80, far past its target, so the
    red bar flagged it — which is the bar doing its job.

    THE CAUSE, and it is structural, not a typo. merge() is what carries a
    status change from the live engine into the permanent record, and the only
    thing that called it with the live feed was the /api/calls handler. That
    runs when the BROWSER asks for it. So the record only learned that a trade
    had closed if MM happened to have the page open at that moment. Close the
    tab, or restart before the next page load, and the decision was gone.

    I have said twice this week that the record no longer depends on anything
    being watched. This path still did.

    So the same merge now runs on its own clock, every fifteen seconds, for as
    long as the desk is up. The page can be shut all day and the record still
    keeps up.
    """
    n = 0
    while True:
        time.sleep(15)
        n += 1
        try:
            snap = dict(STATE)
            rows = ([to_row(c) for c in snap.get("calls", [])] +
                    [to_row(c) for c in snap.get("resolved_today", [])])
            if rows:
                merge(rows)
            # And once a minute, the engine's whole book rather than the ten
            # closed calls it happens to be showing. This is the part that
            # catches a call which closed and then fell out of that window.
            if n % 4 == 0:
                sync_from_engine_book()
        except Exception as e:
            print(f"  (record keeper failed: {e})")


# THE LOW SINCE THE TRADE WAS FILLED, kept by the desk itself.
#
# MM, 18/09: "after taking the trade if it makes that low then pos should be
# closed, this is wrong for me" - and he is right. Checking only the price at
# this instant misses a trade that dipped through its stop at 11:00 and
# bounced back by 11:05. That trade was over at 11:00.
#
# The reason the row's own lo_seen cannot be used is unchanged: it is the
# DAY's low and carries no time, so it may be from before the fill. That is
# the 09/09 bug, 210 rows closed before they opened.
#
# So the desk keeps its own. The moment a call is first seen filled, it starts
# recording the lowest and highest price IT has watched, from that moment on.
# Nothing before the fill is in it, by construction, so it is safe to act on -
# and it catches the dip that the live price alone would miss.
#
# Its one limit, stated rather than hidden: it starts when the desk first sees
# the call, so a restart mid-trade begins a fresh window, and a spike between
# two six-second samples is not in it. The 15:45 settler reads the exchange's
# own candles and remains the final word on both.
_SINCE_FILL = {}


def _fill_key(r):
    return (r.get("date"), r.get("symbol"), r.get("setup"), r.get("side"),
            str(r.get("entry")))


def track_since_fill():
    """Update the desk's own low/high for every open call. Cheap, no API."""
    for r in HISTORY:
        if r.get("status") not in ("LIVE", "T1_HIT"):
            continue
        px = (_ltp["px"].get(r.get("symbol")) or {}).get("ltp")
        if not px:
            continue
        k = _fill_key(r)
        seen = _SINCE_FILL.get(k)
        if seen is None:
            _SINCE_FILL[k] = {"lo": px, "hi": px}
        else:
            seen["lo"] = min(seen["lo"], px)
            seen["hi"] = max(seen["hi"], px)


def close_passed_stop():
    """Close an open call whose price has been through its stop since the fill.

    MM, 18/09: "after hitting the target or sl, it is still showing in live
    calls." He was right, and it was 33 calls at once.

    Every one had the same shape: status T1_HIT, which means target one was
    reached and the stop was pulled to breakeven - and then price came back
    through that breakeven stop and nothing closed the row. BEML: entry
    2,061.70, stop moved to 2,061.70, and the stock was trading at 2,013.30
    with the row still saying it was open. The 15:45 settler gets these right
    from the exchange's candles, but that is hours later; all session MM was
    looking at 33 positions that were already finished.

    THE TRACKING IS THE ENGINE'S, AND THE ENGINE IS NOT TOUCHED. This does not
    change a single one of its rules; it only closes a row the desk can
    already see is over.

    WHY THE CURRENT PRICE AND NOT lo_seen / hi_seen. Those two fields are
    recorded on every row and used nowhere, and they look like the obvious
    answer - BEML's lo_seen was 2,053.90, below the stop. But a recorded
    extreme carries no TIME. A low below the stop could be from before the
    fill, or from between the fill and target one, and closing on it would
    recreate the 09/09 bug exactly: 210 rows shut half an hour before they
    opened, a +5.67R winner filed as -1.00. The LAST TRADED PRICE has no such
    ambiguity - if the stock is below the stop right now, the stop is behind
    us, whatever route price took. So that is the only thing tested here.

    The fill is booked AT THE STOP, not at the current price: a resting stop
    triggers as price passes through it, and claiming the worse price would
    overstate the loss.
    """
    today = date.today().isoformat()
    n = 0
    for r in HISTORY:
        if r.get("status") not in ("LIVE", "T1_HIT"):
            continue
        if (r.get("triggered_on") or r.get("date")) != today:
            continue          # only today's; older ones are the settler's job
        px = (_ltp["px"].get(r.get("symbol")) or {}).get("ltp")
        try:
            entry, sl = float(r["entry"]), float(r["sl"])
            risk = float(r.get("risk0") or 0) or abs(entry - sl)
        except (TypeError, ValueError, KeyError):
            continue
        if not px or risk <= 0:
            continue
        long = (r.get("side") or "LONG") == "LONG"
        # Two tests, and either one closes the trade.
        #   NOW       price is through the stop at this instant
        #   SINCE     price went through it at some point after the fill and
        #             came back. That trade ended when it touched, not when
        #             MM happened to look.
        seen = _SINCE_FILL.get(_fill_key(r)) or {}
        worst = seen.get("lo") if long else seen.get("hi")
        now_through = (px < sl) if long else (px > sl)
        was_through = worst is not None and ((worst <= sl) if long
                                             else (worst >= sl))
        if not (now_through or was_through):
            continue
        got = ((sl - entry) if long else (entry - sl)) / risk
        r["status"] = "SL_HIT"
        r["r"] = round(got, 2)
        r["closed_on"] = today
        r["closed_at"] = datetime.now().strftime("%H:%M")
        why = (f"price {px:,.2f} is through the stop at {sl:,.2f}"
               if now_through else
               f"price reached {worst:,.2f} after the fill, through the stop "
               f"at {sl:,.2f}, before coming back")
        r["reasons"] = ((r.get("reasons") or "") +
                        f" · closed by the desk: {why}"
                        + (" (breakeven, after target one)"
                           if abs(sl - entry) < 1e-9 else ""))
        _SINCE_FILL.pop(_fill_key(r), None)
        n += 1
    if n:
        rewrite_log()
        print(f"  {n} call{'s' if n != 1 else ''} closed - price was already "
              f"through the stop and the row still said open.")
    return n


def settle_watcher(kite):
    """Close the day's book from the exchange's own candles, every day, at 15:45.

    MM, 09/09: "what is the solution to this problem, it affects my pnl data."

    He is right that fixing causes one at a time is not a solution. Every P&L
    problem this week has the same shape: the record's truth depended on a live
    process watching every price of every open trade, continuously, without
    interruption. That process can be interrupted in more ways than I can
    enumerate — a restart, a dropped connection, a call the engine could not
    reconstruct, a gap between two price samples, a bug I have not found. When
    it is, a trade sits open for ever, and the money is silently wrong.

    So the record no longer depends on it.

    At 15:45 every trading day, every intraday call still showing open is
    settled from Zerodha's own 15-minute candles for that session. Not from
    what the tracker saw — from what the exchange printed. The candle contains
    every tick; a five-second sample does not. Whatever happened during the
    day, and whatever the desk was doing at the time, the book is closed
    correctly by a quarter to four.

    The live tracker still does its job and still closes trades the moment they
    hit, which is what makes the page useful during the session. It just is not
    the last word any more. If it misses something, this catches it the same
    day rather than whenever MM happens to notice.

    Deliberately NOT done here: re-grading trades that already closed. The
    tracker watched those live and the candle replay cannot know the order of
    ticks inside a candle, so overruling it could make a correct result worse.
    This only settles what is still open, which is the thing that has actually
    been going wrong.
    """
    done = None
    while True:
        time.sleep(60)
        try:
            now = datetime.now()
            today = now.date().isoformat()
            if now.weekday() > 4 or done == today:
                continue
            if (now.hour, now.minute) < (15, 45):
                continue
            left = [r for r in HISTORY
                    if OPEN_ST(r) and (r.get("date") or "") == today
                    and (r.get("tf") or "").upper() != "SWING"]
            done = today
            if not left:
                print(f"  {now:%H:%M}  end of day: every intraday call is "
                      f"already closed.")
                continue
            print(f"\n  {now:%H:%M}  END OF DAY — {len(left)} intraday call"
                  f"{'s' if len(left) > 1 else ''} still open. Settling "
                  f"{'them' if len(left) > 1 else 'it'} from the exchange's "
                  f"own candles.")
            settle_stale(kite, include_today=True)
            settle_swing(kite)          # swing positions too, from daily candles
            rewrite_log()
        except Exception as e:
            print(f"  (end-of-day settlement failed: {e})")


def audit_watcher():
    """Run the record check every 30 minutes while the market is open.

    Checking only at startup catches yesterday's mess. It does not catch a row
    going wrong at 11:20 and sitting there all afternoon - which is exactly how
    MM found TEGA and PARAGMILK, by reading his own dashboard. He asked for
    this to run through the day (08/09) so the desk notices first.

    Silent when the record is clean; it prints only when something is actually
    wrong, so a quiet screen means a good record and not a dead thread.
    """
    while True:
        time.sleep(1800)
        try:
            now = datetime.now()
            if now.weekday() > 4 or not (9 <= now.hour < 16):
                continue
            bad = audit_record(quiet=True)
            if bad:
                print(f"     ^ record check, {now:%H:%M} - fix these before "
                      f"trusting the day's numbers")
        except Exception:
            pass


def settle_stale(kite, include_today=False):
    """Close out intraday calls that were left open when the desk stopped.

    THE BUG, found 08/09 from MM's question about PARAGMILK still showing LIVE
    the morning after its trade.

    An intraday call must be out by 15:10. If the desk is not running at 15:10
    - it was closed for the evening, or restarted after the close - nothing
    settles it. Next morning the engine reloads only calls dated TODAY, so
    yesterday's are dropped and correctly marked VOID_RESTART inside the
    engine. But the engine only PUBLISHES calls dated today, so that void never
    reaches the permanent record, and the row sits at LIVE for ever.

    Measured on 08/09: 172 intraday calls frozen open, 169 of them from the
    previous session alone. That is not just an untidy record - the dashboard
    counts them as open positions, so 172 ghosts were holding slots in a fifty
    slot book and blocking the day's real calls from being taken.

    Nothing here is invented. Each stale call is settled from that day's own
    15-minute candles, using the engine's own rules: the stop first if a candle
    reaches both, T1 moves the stop to breakeven, and whatever is left is taken
    at the 15:10 close. A call that never reached its entry is EXPIRED, not
    graded. If the candles cannot be fetched the call is voided rather than
    guessed at.
    """
    today = date.today().isoformat()
    # include_today is used by the 15:45 sweep, which settles the session that
    # has just ended. At startup only earlier days are touched, because a call
    # made this morning is still legitimately running.
    cutoff = today if not include_today else "9999-99-99"
    stale = [r for r in HISTORY
             if OPEN_ST(r) and (r.get("date") or "") < cutoff
             and (r.get("tf") or "").upper() != "SWING"]
    if not stale:
        return
    print(f"  {len(stale)} intraday calls were left open when the desk last "
          f"stopped. Settling them from the exchange's own candles ...")
    want = sorted({r["symbol"] for r in stale})
    toks = {}
    try:
        for row in kite.instruments("NSE"):
            if row.get("tradingsymbol") in want and row.get("instrument_type") == "EQ":
                toks[row["tradingsymbol"]] = row["instrument_token"]
    except Exception as e:
        print(f"  (could not read the instrument list: {e})")

    by_sym = {}
    for r in stale:
        by_sym.setdefault(r["symbol"], []).append(r)
    oldest = min((r.get("date") or today) for r in stale)
    frm = datetime.fromisoformat(oldest) - timedelta(days=2)
    to = datetime.now()
    done = voided = 0
    for sym, rows in by_sym.items():
        tok = toks.get(sym)
        bars = []
        if tok:
            try:
                raw = kite.historical_data(tok, frm, to, "15minute")
                bars = [{"ts": str(c["date"])[:16].replace("T", " "),
                         "o": c.get("open"),          # needed to spot a GAP
                         "h": c["high"], "l": c["low"], "c": c["close"]} for c in raw]
            except Exception:
                bars = []
            time.sleep(0.36)
        for r in rows:
            if not bars:
                r["status"] = "VOID_NO_DATA"
                r["r"] = None
                r["closed_on"] = r.get("date")
                r["closed_at"] = "15:10"
                voided += 1
                continue
            day = r.get("date")
            seq = [b for b in bars if b["ts"][:10] == day]
            if not seq:
                r["status"] = "VOID_NO_DATA"; r["r"] = None
                r["closed_on"] = day; r["closed_at"] = "15:10"
                voided += 1
                continue
            _settle_one(r, seq)
            done += 1
    print(f"  settled {done} from candles"
          + (f", voided {voided} with no data (not graded)" if voided else ""))
    rewrite_log()


def _settle_one(r, seq):
    """One stale call, walked through its own day, on the engine's own rules."""
    try:
        entry = float(r["entry"]); sl0 = float(r["sl"])
        t1 = float(r["t1"]); t2 = float(r["t2"])
        risk = float(r.get("risk0") or 0) or abs(entry - sl0)
    except Exception:
        r["status"] = "VOID_NO_DATA"; r["r"] = None
        r["closed_on"] = r.get("date"); r["closed_at"] = "15:10"
        return
    long = (r.get("side") or "LONG") == "LONG"
    if risk <= 0:
        r["status"] = "VOID_NO_DATA"; r["r"] = None
        r["closed_on"] = r.get("date"); r["closed_at"] = "15:10"
        return
    r1 = abs(t1 - entry) / risk
    r2 = abs(t2 - entry) / risk
    filled = r.get("status") in ("LIVE", "T1_HIT")
    day = r.get("date")

    # THE BUG MM FOUND ON 09/09, and it was a bad one.
    #
    # For a call that was ALREADY LIVE this walk used to start at the first
    # candle of the day - 09:15 - even though the trade did not enter until
    # 09:47. The 09:15 candle sits entirely BEFORE the entry, so its low is
    # almost always through the stop of a long, and the very first test
    # recorded an instant SL_HIT stamped 09:15. Every such row therefore
    # "closed" half an hour before it opened, and a winner was booked as a
    # full -1R loss. UNIMECH was +5.67R on the screen at 14:29 and went into
    # the record at -1.00R. Measured across the file: 210 rows, 194 of them
    # stamped 09:15, together worth -Rs 75,694 of a -Rs 60,016 P&L.
    #
    # The trade is now replayed from the candle it actually entered on. That
    # single line is the whole fix; everything else here is unchanged, on
    # purpose. A row already marked T1_HIT keeps its breakeven stop, because
    # T1 genuinely happened before the desk stopped and the recorded status is
    # better evidence than anything re-derived from a partial day - the repair
    # below handles the rows whose history was lost by reopening them as plain
    # LIVE with their original stop, so they replay from scratch.
    start_hm = (r.get("triggered_at") or "") if filled else ""
    tier1 = r.get("status") == "T1_HIT"
    stop = entry if tier1 else sl0

    def close(st, rr, hm):
        r["status"] = st
        r["r"] = None if rr is None else round(rr, 2)
        r["closed_on"] = day
        r["closed_at"] = hm

    for b in seq:
        hm = b["ts"][11:16]
        # Nothing before the trade existed can decide the trade.
        if start_hm and hm < start_hm:
            continue
        if not filled:
            through = b["h"] >= entry if long else b["l"] <= entry
            if through:
                filled = True
                r["triggered_on"] = r.get("triggered_on") or day
                r["triggered_at"] = r.get("triggered_at") or hm
                # the same candle can also reach the stop - assume it did
                if (long and b["l"] <= stop) or ((not long) and b["h"] >= stop):
                    return close("SL_HIT", -1.0, hm)
            else:
                if hm >= "15:10":
                    return close("EXPIRED", None, hm)
                continue
        hit_sl = (b["l"] <= stop) if long else (b["h"] >= stop)
        hit_t1 = (b["h"] >= t1) if long else (b["l"] <= t1)
        hit_t2 = (b["h"] >= t2) if long else (b["l"] <= t2)
        if hit_sl:
            # A candle that OPENS beyond the stop was never filled at the stop.
            # Booking a flat -1R there understates exactly the trades that hurt
            # most - a gap on news. Same reasoning as settle_swing().
            op = float(b.get("o") or b.get("open") or 0)
            gapped = op > 0 and ((op < stop) if long else (op > stop))
            if gapped:
                rr = ((op - entry) if long else (entry - op)) / risk
                return close("SL_HIT", round(rr, 2), hm)
            return close("T1_TRAIL_OUT", r1 / 2, hm) if tier1 else close("SL_HIT", -1.0, hm)
        if hit_t2:
            op = float(b.get("o") or b.get("open") or 0)
            if op > 0 and ((op > t2) if long else (op < t2)):
                rr = ((op - entry) if long else (entry - op)) / risk
                return close("T2_HIT", round(rr, 2), hm)
            return close("T2_HIT", (r1 + r2) / 2, hm)
        if hit_t1 and not tier1:
            tier1 = True
            stop = entry
            r["sl"] = round(entry, 2)
        if hm >= "15:10":
            move = (b["c"] - entry) if long else (entry - b["c"])
            return close("TIME_EXIT", move / risk, hm)
    # the day's candles ran out before 15:10 - take the last one honestly
    b = seq[-1]
    move = (b["c"] - entry) if long else (entry - b["c"])
    close("TIME_EXIT" if filled else "EXPIRED",
          (move / risk) if filled else None, b["ts"][11:16])


def sweep_passed(kite):
    """Settle, during the session, any open call the price has already left.

    MM, 18/09: "why everyday it is showing like this, 65 calls in the record
    are impossible. what is going on?"

    Nothing was impossible. His record was checked rule by rule the same
    morning - 2,852 rows, not one broken. The 65 were a SECOND kind of warning
    printed under the same red heading: an open call whose live price has
    already gone past its target or its stop, with nothing closing the row.

    Those are not corrupt rows. They are UNSETTLED rows. And the desk already
    knew how to settle them - settle_watcher does exactly this at 15:45 from
    the exchange's own candles, which is why the banner disappeared each
    evening and was back the next morning. MM was being shown a problem that
    the desk was going to fix by itself in a few hours, in the language of a
    catastrophe, every single day.

    So it is fixed when it happens instead. Every two minutes, any open call
    the live price has clearly left is replayed through that day's 15-minute
    candles by the same _settle_one the end-of-day sweep uses - same rules,
    same gap handling, same breakeven-after-T1.

    Two deliberate brakes, because closing a trade that is still running is a
    far worse bug than a banner:

      * a call must be past its level on TWO consecutive passes, two minutes
        apart, before it is touched. A single wild print cannot close a trade.
      * the replay must find a REAL hit. _settle_one falls back to closing at
        the last candle when it finds nothing, which mid-session would book a
        live trade out at whatever the clock says. If it comes back TIME_EXIT
        or EXPIRED the row is put back exactly as it was and left open.
    """
    flagged = {}
    while True:
        time.sleep(120)
        try:
            now = datetime.now()
            if now.weekday() > 4:
                continue
            if not ((9, 20) <= (now.hour, now.minute) <= (15, 40)):
                continue
            today = date.today().isoformat()
            px = _ltp.get("px") or {}
            hot = []
            for r in HISTORY:
                if not OPEN_ST(r) or r.get("status") == "WAITING":
                    continue
                if (r.get("date") or "") != today:
                    continue          # older days belong to settle_stale
                if (r.get("tf") or "").upper() == "SWING":
                    continue
                p = (px.get(r.get("symbol")) or {}).get("ltp")
                if not p:
                    continue
                try:
                    t2, sl = float(r["t2"]), float(r["sl"])
                except (TypeError, ValueError, KeyError):
                    continue
                long = (r.get("side") or "LONG") == "LONG"
                gone = ((p >= t2 * 1.0025) if long else (p <= t2 * 0.9975)) or \
                       ((p <= sl * 0.9975) if long else (p >= sl * 1.0025))
                k = _fill_key(r)
                if not gone:
                    flagged.pop(k, None)
                    continue
                flagged[k] = flagged.get(k, 0) + 1
                if flagged[k] >= 2:
                    hot.append(r)
            if not hot:
                continue
            by_sym = {}
            for r in hot:
                by_sym.setdefault(r["symbol"], []).append(r)
            toks = {}
            try:
                want = set(by_sym)
                for row in kite.instruments("NSE"):
                    if row.get("tradingsymbol") in want \
                            and row.get("instrument_type") == "EQ":
                        toks[row["tradingsymbol"]] = row["instrument_token"]
            except Exception:
                pass
            frm = datetime.fromisoformat(today) - timedelta(days=1)
            done = 0
            for sym, rows in by_sym.items():
                tok = toks.get(sym)
                if not tok:
                    continue
                try:
                    raw = kite.historical_data(tok, frm, now, "15minute")
                except Exception:
                    continue
                time.sleep(0.36)
                seq = [{"ts": str(c["date"])[:16].replace("T", " "),
                        "o": c.get("open"), "h": c["high"], "l": c["low"],
                        "c": c["close"]} for c in raw
                       if str(c["date"])[:10] == today]
                if not seq:
                    continue
                for r in rows:
                    before = dict(r)
                    _settle_one(r, seq)
                    if r.get("status") in ("SL_HIT", "T2_HIT", "T1_TRAIL_OUT"):
                        done += 1
                        flagged.pop(_fill_key(before), None)
                    else:
                        r.clear()
                        r.update(before)      # still running - leave it alone
            if done:
                rewrite_log()
                print(f"  {now:%H:%M}  settled {done} call"
                      f"{'s' if done > 1 else ''} the price had already left.")
        except Exception as e:
            print(f"  (mid-session settlement failed: {e})")


def repair_swing_fill_day(kite=None):
    """The same repair, for the rows the DAILY settler closed before they opened.

    MM, 09/09 evening: "21 calls in the record are impossible, why is it
    showing like this." The earlier repair fixed 210 rows and was correct as
    far as it went — but it only fixed the intraday settler. settle_swing()
    had the identical flaw and I did not carry the fix across, so ten more
    rows appeared with the same fingerprint.

    They are unmistakable: a closing moment earlier than the opening moment,
    every one stamped 09:15, every one a SWING call, and every one graded worse
    than a full stop loss (-1.42, -1.31, -1.75 ...) because the "gap" it
    thought it saw was simply the morning of a day the trade entered in the
    afternoon.

    Each is reopened with its original stop and replayed properly: the fill day
    from its own 15-minute candles from the fill time, later days from daily
    candles. A row whose candles cannot be fetched is left alone, not guessed.

    Runs once. Gated on DATA\\.swing_fill_day_repaired.
    """
    # v2: the first pass used one fingerprint and left CEIGALL behind, so the
    # flag name is bumped to let the widened pass run once on machines that
    # already ran the first one.
    #
    # v3 (23/09): CENTRALBK, 18/09. Filled 15:05, "closed" 09:15 the SAME day,
    # graded +0.99. Its own candles say the exit cannot have happened that day
    # - the last candle of 18/09 ran 31.59 to 32.40 and the breakeven stop was
    # 31.58, so the stop was never touched before the close. The row matches
    # gapped_on_its_own_fill_day() exactly and would have been repaired, but
    # the v2 flag was already on disk and the repair never ran again. So the
    # flag is bumped rather than the date being guessed at: the exchange's own
    # candles decide which later session it actually closed on, not me.
    flag = os.path.join(DATA, ".swing_fill_day_repaired_v3")
    if os.path.exists(flag):
        return

    def moment(r, which):
        d = r.get(f"{which}_on") or r.get("date") or ""
        return d + " " + (r.get(f"{which}_at") or "")

    def gapped_on_its_own_fill_day(r):
        """The second fingerprint, and CEIGALL is why it exists.

        MM, 09/09, after the first repair took 21 down to 3: one row survived.
        CEIGALL carried a fill time of 09:09 — six minutes BEFORE the market
        opens, so its closing stamp of 09:15 is LATER than its fill and the
        "closed before it opened" test never saw it. The row is still wrong:
        it was closed by a gap, on the same day it was filled.

        That combination is impossible however the clocks read. A gap is an
        OPENING price. You cannot be gapped out of a position at the open of
        the very day you entered it — at that moment you did not hold it.
        """
        return (r.get("closed_at") == "09:15"
                and (r.get("closed_on") or r.get("date"))
                    == (r.get("triggered_on") or r.get("date")))

    bad = [r for r in HISTORY
           if (r.get("tf") or "").upper() == "SWING"
           and r.get("triggered_at") and r.get("closed_at")
           and (moment(r, "closed") < moment(r, "triggered")
                or gapped_on_its_own_fill_day(r))]
    if not bad:
        open(flag, "w").write("nothing to repair\n")
        return
    print(f"\n  REPAIRING {len(bad)} swing calls closed before they opened.")
    print("  (the daily settler read the morning of the fill day as a gap)")
    if kite is None:
        print("  No Kite session — leaving them alone until the next login.")
        return
    _load_instruments(kite, {r.get("symbol") for r in bad})
    fixed = failed = 0
    for r in bad:
        try:
            if not _token_of(r.get("symbol")):
                failed += 1
                continue
            # put it back exactly as it stood before the bad settlement
            r["status"] = "LIVE"
            r["r"] = None
            r["closed_at"] = ""
            r["closed_on"] = ""
            try:
                e = float(r["entry"]); k = float(r.get("risk0") or 0)
                if k > 0:
                    r["sl"] = round(e - k if (r.get("side") or "LONG") == "LONG"
                                    else e + k, 2)
            except Exception:
                pass
            fixed += 1
        except Exception:
            failed += 1
    # and settle them again, properly, through the fixed path
    settle_swing(kite, quiet=True)
    rewrite_log()
    open(flag, "w").write(f"reopened {fixed}, could not fetch {failed}\n")
    print(f"  Reopened and re-settled {fixed}. Could not fetch {failed} "
          f"(left untouched, not guessed at).\n")


def repair_settle_bug(kite=None):
    """Re-settle every row the old settle_stale() closed before it opened.

    Found by MM on 09/09 from one number: the all-time figure moved from
    -Rs 15,589 to -Rs 60,016 overnight and he said it looked wrong. It was.

    The fingerprint is exact and needs no guessing: a row whose CLOSING moment
    is earlier than its OPENING moment is impossible, full stop. 210 rows in
    the file match - 151 from 07/09 and 59 from 08/09, 194 of them stamped
    09:15 - and together they are worth -Rs 75,694, so the true record is far
    better than the one on the page.

    Each is reopened and replayed from its own trigger candle with its ORIGINAL
    stop, by the fixed _settle_one. A row whose candles cannot be fetched is
    left exactly as it is rather than guessed at, and is reported.

    Runs once. Gated on DATA\\.settle_time_bug_repaired.
    """
    flag = os.path.join(DATA, ".settle_time_bug_repaired")
    if os.path.exists(flag):
        return

    def moment(r, which):
        d = r.get(f"{which}_on") or r.get("date") or ""
        return d + " " + (r.get(f"{which}_at") or "")

    bad = [r for r in HISTORY
           if r.get("triggered_at") and r.get("closed_at")
           and moment(r, "closed") < moment(r, "triggered")]
    if not bad:
        open(flag, "w").write("nothing to repair\n")
        return
    print(f"\n  REPAIRING {len(bad)} calls that were recorded as closing before "
          f"they opened.")
    print("  (the old settler started at 09:15 regardless of when the trade "
          "actually entered)")
    if kite is None:
        print("  No Kite session - leaving them alone until the next login.")
        return

    _load_instruments(kite, {r.get("symbol") for r in bad})
    fixed = failed = 0
    by_day = {}
    for r in bad:
        by_day.setdefault(r.get("date"), []).append(r)
    for day, rows in sorted(by_day.items()):
        for r in rows:
            try:
                tok = _token_of(r.get("symbol"))
                if not tok:
                    failed += 1; continue
                seq = kite.historical_data(
                    tok, f"{day} 09:00:00", f"{day} 15:40:00", "15minute") or []
                seq = [{"ts": str(x["date"])[:16].replace("T", " "),
                        "o": float(x.get("open") or 0),   # needed to spot a GAP
                        "h": float(x["high"]), "l": float(x["low"]),
                        "c": float(x["close"])} for x in seq]
                if not seq:
                    failed += 1; continue
                # reopen it, exactly as it stood before the bad settlement
                r["status"] = "LIVE"
                r["r"] = None
                r["closed_at"] = ""
                r["closed_on"] = ""
                try:
                    e = float(r["entry"]); k = float(r.get("risk0") or 0)
                    if k > 0:
                        r["sl"] = round(e - k if (r.get("side") or "LONG") == "LONG"
                                        else e + k, 2)
                except Exception:
                    pass
                _settle_one(r, seq)
                fixed += 1
            except Exception:
                failed += 1
            time.sleep(0.36)                 # Kite's rate limit
    rewrite_log()
    open(flag, "w").write(f"repaired {fixed}, could not fetch {failed}\n")
    print(f"  Repaired {fixed}. Could not fetch candles for {failed} "
          f"(left untouched, not guessed at).\n")


_INSTR = {}


def _load_instruments(kite, want):
    """NSE equity tokens for the symbols asked for, read once and cached."""
    missing = [s for s in want if s not in _INSTR]
    if not missing or kite is None:
        return
    try:
        for row in kite.instruments("NSE"):
            if (row.get("tradingsymbol") in want
                    and row.get("instrument_type") == "EQ"):
                _INSTR[row["tradingsymbol"]] = row["instrument_token"]
    except Exception as e:
        print(f"  (could not read the instrument list: {e})")


def _token_of(sym):
    return _INSTR.get(sym)


def dedupe_history(quiet=True):
    """Make a duplicate impossible to SURVIVE, whatever created it.

    MM, 09/09, after the fourth duplicate of the day: "why is it happening
    again and again, can you come up with a good solution."

    Fair. Three times today I found a real cause and fixed it — the key that
    moved with the stop, a price written as 5313 and as 5313.0, and finally
    two browser refreshes writing at the same instant. Every fix was right.
    But each one only closed the door I had just found open, and the record
    could still hold a duplicate the moment anything else went wrong.

    This is the difference. It does not care what caused it. Every call is
    reduced to its identity — day, stock, setup, side, entry, original risk —
    and if two rows share one, they are the same trade seen twice: the first
    sighting keeps its time, the more resolved copy keeps its outcome, and one
    row survives. It runs on every save and at every startup, so a duplicate
    can live for at most one save cycle and can NEVER reach the file or the
    money. If some path I have not thought of creates one tomorrow, it heals
    itself and MM never sees it.
    """
    with _HIST_LOCK:
        rank = {"WAITING": 0, "LIVE": 1, "T1_HIT": 2}
        keep, seen = [], {}
        for r in HISTORY:
            k = call_ident(r)
            i = seen.get(k)
            if i is None:
                seen[k] = len(keep)
                keep.append(r)
                continue
            old = keep[i]
            # the further-along copy wins; the first sighting keeps its clock
            if rank.get(r.get("status"), 9) >= rank.get(old.get("status"), 9):
                r = dict(r)
                r["created_hm"] = old.get("created_hm") or r.get("created_hm")
                r["triggered_at"] = r.get("triggered_at") or old.get("triggered_at")
                r["triggered_on"] = r.get("triggered_on") or old.get("triggered_on")
                keep[i] = r
        # SECOND PASS: the same fill written under two different dates.
        # A settled copy beats an unsettled one - the settled one is the trade
        # that actually happened and carries its result. Otherwise the earlier
        # date wins, because that is the day the call was really made.
        seen2, keep2 = {}, []
        for r in keep:
            k = fill_ident(r)
            if k is None:
                keep2.append(r)
                continue
            i = seen2.get(k)
            if i is None:
                seen2[k] = len(keep2)
                keep2.append(r)
                continue
            old = keep2[i]
            new_done = not OPEN_ST(r)
            old_done = not OPEN_ST(old)
            better = (new_done and not old_done) or (
                new_done == old_done and (r.get("date") or "") < (old.get("date") or ""))
            if better:
                keep2[i] = r
        keep = keep2
        gone = len(HISTORY) - len(keep)
        if gone:
            HISTORY[:] = keep
            if not quiet:
                print(f"  ({gone} duplicate row{'s' if gone > 1 else ''} removed "
                      f"from the record)")
        return gone


def rewrite_log():
    """Statuses change all day, so the file is rewritten once at shutdown with
    the final state of every call rather than only its first appearance."""
    dedupe_history()          # nothing crooked ever reaches the file
    try:
        with _HIST_LOCK, open(LOG, "w", newline="", encoding="utf8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for r in list(HISTORY):
                w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in FIELDS})
    except Exception as e:
        print(f"  (could not rewrite the log: {e})")


# ── basket order flow ──────────────────────────────────────────────────────
# The engine takes an optional flow bias. Two setups use it:
#     PULLBACK    +1.0 if the big money is leaning the same way
#     VWAP-FLIP   +1.5 for the same
# A call needs 6.5 to fire, so without this those two setups are quietly a
# point or more short and some of their calls never appear at all.
#
# HONEST NOTE: Jenil's number comes from his own institutional-flow tracker,
# which we do not have. This is a rebuild from the same idea using the data
# Kite already gives us, not a copy of his. The engine only ever looks at the
# SIGN of this number - leaning up, leaning down, or neither - so the exact
# scale does not have to match his for the scoring to behave the same way.
# Fifty names, not eight. Kite takes up to 500 symbols in one request, so a
# wider basket costs exactly the same as a narrow one and is far harder to
# distort: on eight names a single block trade or an ex-dividend can tilt the
# whole reading, and the engine would act on it.
FLOW_BASKET = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "BHARTIARTL", "SBIN",
    "LICI", "ITC", "HINDUNILVR", "LT", "BAJFINANCE", "HCLTECH", "MARUTI",
    "SUNPHARMA", "KOTAKBANK", "AXISBANK", "M&M", "ULTRACEMCO", "NTPC",
    "TITAN", "ADANIENT", "ONGC", "TATAMOTORS", "POWERGRID", "ADANIPORTS",
    "COALINDIA", "BAJAJFINSV", "WIPRO", "NESTLEIND", "JSWSTEEL", "TATASTEEL",
    "ASIANPAINT", "GRASIM", "HINDALCO", "TECHM", "INDUSINDBK", "DRREDDY",
    "CIPLA", "BAJAJ-AUTO", "BRITANNIA", "EICHERMOT", "APOLLOHOSP", "DIVISLAB",
    "HEROMOTOCO", "SBILIFE", "HDFCLIFE", "TATACONSUM", "BPCL", "SHRIRAMFIN",
]
FLOW_EDGE = 0.06          # below this the book is balanced and we say nothing
_flow = {"bias": 0, "score": 0.0, "at": None, "detail": ""}


def flow_once(kite):
    """One reading of buy-versus-sell pressure across the large-cap basket.

    Every Kite quote carries the total quantity resting on the buy side and on
    the sell side of the order book. The imbalance between them is what the
    'B:S' figures on his screen are showing.

    Each name contributes (buy - sell) / (buy + sell), which sits between -1
    and +1, so one huge stock cannot drown out the rest. The average is the
    basket's lean.
    """
    try:
        q = kite.quote(["NSE:" + s for s in FLOW_BASKET]) or {}
    except Exception:
        return _flow["bias"]                    # keep the last reading, do not flip
    vals = []
    for k, v in q.items():
        b = float(v.get("buy_quantity") or 0)
        s = float(v.get("sell_quantity") or 0)
        if b + s <= 0:
            continue
        vals.append((b - s) / (b + s))
    if len(vals) < 10:
        # Too few names answered to call a market. Say nothing rather than
        # read a whole market's direction off a handful of order books.
        return _flow["bias"]
    vals.sort()
    mid = len(vals) // 2
    # The MEDIAN, not the mean. One name with a huge resting block would drag
    # an average around on its own; the median asks how the basket as a whole
    # is leaning, which is the question the score is actually for.
    score = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
    bias = 1 if score > FLOW_EDGE else (-1 if score < -FLOW_EDGE else 0)
    up = sum(1 for v in vals if v > 0)
    _flow.update({"bias": bias, "score": round(score, 3),
                  "at": datetime.now().strftime("%H:%M:%S"),
                  "detail": f"{up} of {len(vals)} bid-heavy"})
    return bias


def flow_watcher(kite):
    """Refresh the reading on its own clock so a slow quote never stalls a scan."""
    while True:
        flow_once(kite)
        time.sleep(45)


# ── full-market coverage ───────────────────────────────────────────────────
# As shipped, the engine looks closely at 25 stocks per cycle. Everything else
# is invisible - not rejected, never examined, so its misses never appear in
# any record and cannot be measured.
#
# The limit is real but narrower than it looks. It comes from ONE thing: the
# 15-minute candle fetch, which Zerodha rate-limits to about 3 symbols a
# second. Two consequences the original 25 does not exploit:
#
#   1. BREAKOUT-20D reads DAILY bars only. The engine already holds daily bars
#      for every stock in the universe and merges today's live quote into
#      them. So the swing setup can run across the WHOLE market every cycle at
#      no API cost whatsoever. That is done below, and it is the bigger win.
#
#   2. The four intraday setups do need candles - but they read 15-MINUTE
#      candles, which only produce new information four times an hour. Sweeping
#      the market in ~15 minutes therefore loses no signal, only latency on the
#      entry price. So instead of ignoring 2,300 stocks, we rotate through them.
FAST_LANE = 60           # re-examined every cycle: the most active names
ROLLING = 200            # plus this many more each cycle, rotating through the rest


def widen_coverage(scanner, T):
    """Fast lane every cycle, plus a rotating slice of everything else."""
    T.SHORTLIST_N = FAST_LANE
    original = scanner._screen
    pos = {"i": 0, "passes": 0}

    def screen(quotes, now):
        short = list(original(quotes, now))
        eq = sorted(s for s, m in scanner.universe.items()
                    if m.get("seg") == "EQ" and s in scanner.daily)
        n = len(eq)
        if n:
            i = pos["i"] % n
            sl = eq[i:i + ROLLING]
            if len(sl) < ROLLING:                    # wrap round the end
                sl += eq[:ROLLING - len(sl)]
                pos["passes"] += 1
            pos["i"] = (i + ROLLING) % n
            short = list(dict.fromkeys(short + sl))
        return short

    scanner._screen = screen
    return pos


def adopt(scanner, T, c):
    """Hand a call the SWEEP found to the engine, so the engine watches it.

    THE BUG THIS FIXES (found 07/09 from MM's question about MAHSEAMLES).
    The sweep used to build its calls and write them straight into the
    permanent log. Nothing else ever saw them. The engine keeps its own list of
    open calls and only watches what is on that list, so a swept call had no
    trigger, no stop and no target - it sat at WAITING for ever. Measured that
    afternoon: 97 of 229 open calls were being watched by nothing at all, and
    six had already run clean through their entry. MAHSEAMLES said WAITING with
    its entry at 688.10 and the stock at 693, having touched 717.80.

    Worse than the frozen rows: a call that never resolves never enters any
    statistic. The whole-market sweep is the widest part of MM's coverage and
    it was silently absent from every result he has looked at.

    The fix is not a second tracker. It is to give the call to the one that
    already exists. Every guard below is the engine's own, read from the
    engine's own state - a call arriving this way is held to exactly the same
    rules as one the engine found itself, or it is not taken at all.
    """
    sym, today = c["symbol"], str(date.today())
    now = datetime.now()
    mins = now.hour * 60 + now.minute
    with getattr(scanner, "life_lock", threading.Lock()):
        calls = list(scanner.calls)
    open_syms = {x["symbol"] for x in calls
                 if x.get("status") in ("WAITING", "LIVE", "T1_HIT")}
    # 1. the engine's post-stop lockout for the day
    if getattr(scanner, "_sl_block", {}).get((sym, c["side"])) == today:
        return None
    # 2. one open call per stock
    if sym in open_syms:
        return None
    # 3. the engine's re-fire gap
    key = (sym, c["setup"], c["side"])
    if time.time() - getattr(scanner, "_cooldown", {}).get(key, 0) < getattr(T, "COOLDOWN_S", 2700):
        return None
    # 4. the same signal must never fire twice in one day
    if any(x.get("symbol") == sym and x.get("setup") == c["setup"]
           and x.get("side") == c["side"] and x.get("date") == today
           and abs((x.get("entry") or 0) - c["entry"]) / max(c["entry"], 1e-9) < 0.003
           for x in calls):
        return None
    # 5. the engine skips 20-day breakouts in the opening half hour
    if c["setup"] == "BREAKOUT-20D" and mins < 9 * 60 + 45:
        return None
    # 6. the engine's own tradability gate
    try:
        why = scanner._untradable(sym)
        if why:
            return None
    except Exception:
        pass
    c.update(status="WAITING", created=now.isoformat(timespec="seconds"),
             created_hm=now.strftime("%H:%M"), date=today, tf="SWING",
             seg="EQ", mkt_flow=_flow.get("bias", 0), ltp=c["entry"])
    # entry_side is deliberately NOT set. The engine stamps it from the price at
    # issue, and the sweep has no quote - it reads cached daily bars. Left
    # unset, the engine falls back to breakout semantics (fill when price rises
    # INTO a long entry), which is correct for a 20-day breakout: the entry is a
    # fresh high, so it always sits above the market. Guessing would be worse
    # than the correct default.
    try:
        scanner._cooldown[key] = time.time()
    except Exception:
        pass
    with getattr(scanner, "life_lock", threading.Lock()):
        scanner.calls.append(c)
    return c


def reattach_open(scanner):
    """Give every still-open call back to the engine after a restart.

    THE BUG, found 09/09 from MM: GRAPHITE hit its target and stayed on Live
    calls. Price was 858.90 against a target of 828.17 - 3.7% past it - and the
    row still said T1_HIT hours later.

    Nothing was wrong with the target test. The call was simply not being
    WATCHED. The engine tracks only what is on its own list, and that list is
    rebuilt from scratch every time the desk starts. Anything it cannot
    reconstruct is dropped. The permanent record still holds the call as open,
    the page still shows it on Live calls, and no code anywhere is checking its
    stop or its target ever again. It can never close, so it can never reach
    Past calls either.

    The desk restarted three times on 09/09 - 10:03, 12:43 and once before -
    and every open position at each of those moments was quietly orphaned.
    Measured right after: of 159 open calls carrying a price, 5 were already
    past their target and 19 past their stop.

    This is NOT adopt(). adopt() decides whether a NEW call may be issued, and
    applies the engine's six guards for that - cooldown, one-per-stock, no
    breakout before 09:45. None of those apply here. These positions were
    opened long ago under those same rules; the only question is whether the
    engine is currently watching them. So the single test is: does the engine
    already have this call? If not, hand it back, with its status and its
    fill time exactly as recorded, so it resumes rather than restarts.
    """
    if not scanner:
        return 0
    try:
        with getattr(scanner, "life_lock", threading.Lock()):
            have = {(x.get("symbol"), x.get("setup"), x.get("side"),
                     str(x.get("entry"))) for x in scanner.calls}
        back = []
        for r in HISTORY:
            if not OPEN_ST(r):
                continue
            k = (r.get("symbol"), r.get("setup"), r.get("side"), str(r.get("entry")))
            if k in have:
                continue
            c = {"date": r.get("date"), "symbol": r.get("symbol"),
                 "seg": r.get("seg") or "EQ", "setup": r.get("setup"),
                 "side": r.get("side"), "tf": r.get("tf") or "INTRADAY",
                 "score": r.get("score"), "conviction": r.get("conviction"),
                 "entry": r.get("entry"), "sl": r.get("sl"),
                 "t1": r.get("t1"), "t2": r.get("t2"),
                 "rr": r.get("rr"), "risk0": r.get("risk0"),
                 "status": r.get("status"), "r": r.get("r"),
                 "triggered_at": r.get("triggered_at") or "",
                 "closed_at": r.get("closed_at") or "",
                 "created_hm": r.get("created_hm"),
                 "horizon": r.get("horizon") or "", "level": r.get("level") or "",
                 "reasons": r.get("reasons") or "",
                 "ltp": r.get("entry")}
            back.append(c)
            have.add(k)
        if back:
            with getattr(scanner, "life_lock", threading.Lock()):
                scanner.calls.extend(back)
            n_open = sum(1 for c in back if c["status"] in ("LIVE", "T1_HIT"))
            print(f"  {len(back)} open calls handed back to the engine to be "
                  f"watched again ({n_open} with money at risk).")
        return len(back)
    except Exception as e:
        print(f"  (could not re-attach open calls: {e})")
        return 0


def swing_sweep(scanner, T, quiet=False):
    """BREAKOUT-20D across the ENTIRE universe, from cached daily bars.

    No candle fetch, no rate limit, no shortlist. Every tradable stock, every
    time. This is the coverage the 25-name funnel was silently costing.

    Everything found here is handed to the engine by adopt(), which is what
    makes it a real call rather than a line in a file - see the note there.
    """
    cache = dict(getattr(scanner, "daily", {}) or {})
    rows, checked = [], 0
    stamp = datetime.now().strftime("%H:%M")
    for sym, blob in cache.items():
        bars = blob.get("d") if isinstance(blob, dict) else blob
        if not bars or len(bars) < 60:
            continue
        last = bars[-1]
        if not last.get("close") or last["close"] < 20:
            continue
        v20 = bars[-20:]
        if sum(b["close"] * (b.get("volume") or 0) for b in v20) / 20 / 1e7 < 5:
            continue
        checked += 1
        try:
            reg = T.daily_regime(bars)
            c = T.setup_breakout20(sym, "EQ", reg, bars)
        except Exception:
            continue
        if not c:
            continue
        if adopt(scanner, T, c) is None:
            continue          # the engine's own rules turned it down
        why = c.get("reasons")
        rows.append({
            # The day the CALL was made, not the day of the candle it was read
            # from. A 20-day breakout is computed from the previous close, and
            # filing it under that date put today's live calls into yesterday's
            # record - the dashboard showed nothing for today while quietly
            # growing a day that had already finished.
            "date": date.today().isoformat(),
            "created_hm": stamp, "symbol": sym, "seg": "EQ",
            "setup": c["setup"], "side": c["side"], "tf": "SWING",
            "score": c["score"], "conviction": c["conviction"],
            "entry": c["entry"], "sl": c["sl"], "t1": c["t1"], "t2": c["t2"],
            "rr": c["rr"], "risk0": c["risk0"], "status": "WAITING", "r": None,
            "triggered_at": "", "closed_at": "",
            "horizon": c.get("horizon", "3-10 sessions"), "level": c.get("level", ""),
            "reasons": " · ".join(why) if isinstance(why, (list, tuple)) else (why or ""),
        })
    fresh = merge(rows)
    if not quiet:
        print(f"  [Swing] whole market: {checked} tradable, {len(rows)} setups, "
              f"{len(fresh)} new")
    return rows, fresh, checked


LATE_SWEEP_AT = ["12:30", "14:00", "15:05"]


def late_sweep(scanner, T, kite, quiet=False):
    """The 20-day breakout, whole market, on TODAY's bar — in the afternoon.

    MM, 09/09: Jenil got CORDSCABLE and he did not. The reason turned out to be
    the volume test, and the way it is measured.

    setup_breakout20 requires the breakout bar to carry 2.5x the average daily
    volume. During the session the "breakout bar" is today so far, built from
    the live quote — so at 09:32 it is seventeen minutes of volume being judged
    against twenty COMPLETE days. Measured across 10,232 of the desk's own
    volume rejections: in the 09:00 hour only 0.8% of stocks got anywhere near
    the 2.5x line; by 12:00 it was 11.5%. The test is not wrong, it is simply
    unanswerable early and answerable later.

    The existing whole-market sweep does not close this gap: it reads the
    CACHED daily bars, which end at yesterday's close, so it tests yesterday's
    breakout on yesterday's volume. The engine's own funnel does test today's
    bar, but only for the twenty-five most active names each cycle - and
    CORDSCABLE, checked once at 09:32 and never again, was never one of them.

    So this pass takes the whole tradable universe, quotes it, builds today's
    forming bar exactly as the engine does, and runs the ENGINE'S OWN setup
    function on it - three times in the afternoon, when a real day's volume
    exists to measure. Nothing about the rule is altered: not the 2.5x floor,
    not the 1.5% freshness limit, not the score. The only change is WHEN the
    question gets asked, and of how many stocks.

    Everything found is handed to adopt(), which re-applies the engine's own
    six guards, so a call arriving this way is held to the same standard as one
    the engine found itself.
    """
    uni = dict(getattr(scanner, "universe", {}) or {})
    cache = dict(getattr(scanner, "daily", {}) or {})
    syms = [s for s, m in uni.items()
            if m.get("seg") == "EQ" and s in cache]
    if not syms:
        return [], [], 0
    today = date.today().isoformat()
    stamp = datetime.now().strftime("%H:%M")
    quotes = {}
    for i in range(0, len(syms), 200):
        chunk = syms[i:i+200]
        try:
            q = kite.quote(["NSE:" + s for s in chunk]) or {}
        except Exception:
            continue
        for k, v in q.items():
            quotes[k.split(":", 1)[1]] = v
        time.sleep(0.36)
    rows, checked = [], 0
    for sym in syms:
        blob = cache.get(sym)
        bars = list((blob.get("d") if isinstance(blob, dict) else blob) or [])
        q = quotes.get(sym)
        if not bars or len(bars) < 60 or not q:
            continue
        oh = q.get("ohlc") or {}
        ltp = q.get("last_price") or 0
        op = oh.get("open") or 0
        # today's forming bar, built exactly as the engine's _merged_daily does
        if op > 0 and str(bars[-1].get("date"))[:10] != today:
            lo = oh.get("low") or 0
            bars.append({"date": today, "open": op,
                         "high": max(oh.get("high") or 0, ltp),
                         "low": min(lo, ltp) if lo > 0 else ltp,
                         "close": ltp,
                         "volume": q.get("volume") or q.get("volume_traded") or 0})
        if bars[-1]["close"] < 20:
            continue
        v20 = bars[-21:-1]
        if sum(b["close"] * (b.get("volume") or 0) for b in v20) / 20 / 1e7 < 5:
            continue
        checked += 1
        try:
            reg = T.daily_regime(bars)
            c = T.setup_breakout20(sym, "EQ", reg, bars)
        except Exception:
            continue
        if not c:
            continue
        if adopt(scanner, T, c) is None:
            continue
        why = c.get("reasons")
        rows.append({
            "date": today, "created_hm": stamp, "symbol": sym, "seg": "EQ",
            "setup": c["setup"], "side": c["side"], "tf": "SWING",
            "score": c["score"], "conviction": c["conviction"],
            "entry": c["entry"], "sl": c["sl"], "t1": c["t1"], "t2": c["t2"],
            "rr": c["rr"], "risk0": c["risk0"], "status": "WAITING", "r": None,
            "triggered_at": "", "closed_at": "",
            "horizon": c.get("horizon", "3-10 sessions"),
            "level": c.get("level", ""),
            "reasons": " · ".join(why) if isinstance(why, (list, tuple)) else (why or ""),
        })
    fresh = merge(rows)
    if not quiet:
        print(f"  [Late] whole market on today's bar: {checked} tradable, "
              f"{len(rows)} setups, {len(fresh)} new")
    return rows, fresh, checked


def late_watcher(scanner, T, kite):
    """Run the late sweep at each time in LATE_SWEEP_AT, once each per day."""
    done = set()
    while True:
        time.sleep(45)
        try:
            now = datetime.now()
            if now.weekday() > 4:
                continue
            hm = now.strftime("%H:%M")
            for t in LATE_SWEEP_AT:
                key = (now.date().isoformat(), t)
                if hm >= t and key not in done:
                    done.add(key)
                    print(f"\n  {hm}  late whole-market breakout pass ...")
                    late_sweep(scanner, T, kite)
        except Exception as e:
            print(f"  (late sweep failed: {e})")


SMC_UNIVERSE = 350       # names scanned for 15-minute sweeps each bar
#   Every stock that has a futures contract goes in first - those are the ones
#   MM can trade either way. The rest of the list is filled with the most
#   liquid cash-only names, largest turnover first. 350 x 0.36s = about two
#   minutes, which fits comfortably inside the fifteen-minute gap between bars.
#   Below this level of turnover an intraday sweep is not reliably fillable,
#   which is MM's own objection, so widening further would add noise not calls.


# ── which stocks have a futures contract, and what one lot costs ───────────
# Stock futures track the underlying almost exactly, so a sweep found on the
# cash chart is the SAME sweep on the futures chart. Rather than scan NFO
# separately and produce a duplicate of every call, each call is TAGGED: does
# this stock have a future, what is the lot size, and what does one lot cost.
# One list per day, cached, so it costs one request a day.
_FO = {"on": None, "map": {}}
FO_CACHE = os.path.join(DATA, "fo_universe.json")


def fo_map(kite=None):
    """{symbol: {"lot": n, "expiry": "YYYY-MM-DD"}} for the nearest expiry."""
    today = date.today().isoformat()
    if _FO["on"] == today and _FO["map"]:
        return _FO["map"]
    try:
        if os.path.exists(FO_CACHE):
            blob = json.load(open(FO_CACHE, encoding="utf8"))
            if blob.get("on") == today and blob.get("map"):
                _FO["on"], _FO["map"] = today, blob["map"]
                return _FO["map"]
    except Exception:
        pass
    if kite is None:
        return _FO["map"]
    try:
        rows = kite.instruments("NFO")
    except Exception as e:
        print(f"  [SMC] could not read the futures list: {e}")
        return _FO["map"]
    best = {}
    for r in rows:
        if r.get("instrument_type") != "FUT" or r.get("segment") != "NFO-FUT":
            continue
        name = r.get("name") or ""
        exp = str(r.get("expiry") or "")[:10]
        lot = int(r.get("lot_size") or 0)
        if not name or not exp or lot <= 0:
            continue
        if name not in best or exp < best[name]["expiry"]:
            best[name] = {"lot": lot, "expiry": exp}
    if best:
        _FO["on"], _FO["map"] = today, best
        try:
            json.dump({"on": today, "map": best}, open(FO_CACHE, "w", encoding="utf8"))
        except Exception:
            pass
        print(f"  [SMC] {len(best)} stocks have a futures contract "
              f"(nearest expiry).")
    return _FO["map"]


def fo_tag(c):
    """Stamp a call with its futures details, if it has any."""
    f = _FO["map"].get(c["symbol"])
    if f:
        c["fo"] = "YES"
        c["lot"] = f["lot"]
        c["expiry"] = f["expiry"]
        try:
            c["lot_value"] = int(round(float(c["entry"]) * f["lot"]))
        except Exception:
            c["lot_value"] = None
    else:
        c["fo"], c["lot"], c["expiry"], c["lot_value"] = "", None, "", None
    return c


# ── SMC: liquidity sweeps ──────────────────────────────────────────────────
# A SEPARATE BOOK, deliberately. These calls never touch the Rs 50,00,000
# equity book, never take a slot, and never appear in its P&L. They are
# recorded so that MM's own live data can judge them, because the backtests
# were not encouraging:
#
#   INTRADAY, 15-minute, 718,512 sweeps tested
#       every sweep                      -0.024% before costs
#       + reversal candle                -0.012%
#       + previous day's extreme swept   +0.006%   <- the best version
#       This is the ONLY structure tested all week that passed a permutation
#       test. The effect is real. It is also about a twelfth of the size
#       needed to cover brokerage.
#
#   POSITIONAL, daily, 123,541 sweeps tested
#       every target from 0.75R to 2R    -0.33% to -0.43% before costs
#       Loses clearly, and the reason is structural: a daily sweep bar is
#       huge, so the stop sits ~4.25% away and the entry is at the bar's
#       high. Bad price, wide risk.
#
# Both are shown anyway. A losing backtest on one year is not a verdict, and
# recording them costs nothing.
SMC = []                      # every SMC call this session
SMC_LOG = os.path.join(DATA, "smc_log.csv")
SMC_FIELDS = ["date", "created_hm", "symbol", "kind", "side", "level", "swept",
              "entry", "sl", "t1", "t2", "risk0", "status", "r",
              "triggered_on", "triggered_at", "closed_on", "closed_at",
              "fo", "lot", "expiry", "lot_value", "bar_ts", "reasons",
              # added 09/09 with the CHoCH rule: which way price must move to
              # fill the order, and what the target is actually worth in R
              "fill_dir", "rr"]
SMC_PIVOT = 3
_smc_seen = set()


def _b(x, k):
    """Bars arrive in two shapes - Kite's dicts and the cache's dicts."""
    return x.get(k) if k in x else x.get(k[0], 0)


def _shape(b):
    o, h, l, c = b["open"], b["high"], b["low"], b["close"]
    rng = max(h - l, 1e-9)
    return {"o": o, "h": h, "l": l, "c": c, "body": abs(c - o), "rng": rng,
            "up": h - max(o, c), "dn": min(o, c) - l, "bull": c > o,
            "mid": (o + c) / 2}


def smc_bullish_reversal(prev, cur):
    """Hammer, bullish engulfing or piercing - the three shapes MM's own
    documents define for a bullish reversal."""
    p, b = _shape(prev), _shape(cur)
    hammer = b["dn"] >= 2 * b["body"] and b["up"] <= 0.25 * b["rng"] \
        and b["body"] <= 0.4 * b["rng"]
    engulf = (not p["bull"]) and b["bull"] and b["c"] >= p["o"] and b["o"] <= p["c"]
    pierce = (not p["bull"]) and b["bull"] and b["o"] < p["l"] and b["c"] > p["mid"]
    return ("hammer" if hammer else "bullish engulfing" if engulf
            else "piercing" if pierce else None)


def smc_bearish_reversal(prev, cur):
    p, b = _shape(prev), _shape(cur)
    star = b["up"] >= 2 * b["body"] and b["dn"] <= 0.25 * b["rng"] \
        and b["body"] <= 0.4 * b["rng"]
    engulf = p["bull"] and (not b["bull"]) and b["c"] <= p["o"] and b["o"] >= p["c"]
    cloud = p["bull"] and (not b["bull"]) and b["o"] > p["h"] and b["c"] < p["mid"]
    return ("shooting star" if star else "bearish engulfing" if engulf
            else "dark cloud cover" if cloud else None)


def smc_swings(bars):
    """Swing highs and lows, recorded against the bar where they became KNOWN -
    three bars after they happened. Using them any earlier is look-ahead."""
    hi, lo = {}, {}
    n = SMC_PIVOT
    for i in range(n, len(bars) - n):
        w = bars[i - n:i + n + 1]
        if bars[i]["high"] == max(x["high"] for x in w):
            hi.setdefault(i + n, []).append(bars[i]["high"])
        if bars[i]["low"] == min(x["low"] for x in w):
            lo.setdefault(i + n, []).append(bars[i]["low"])
    return hi, lo


def smc_hour(bars):
    """Four 15-minute bars into one hour, from the open, last stub folded in.

    MM's method is specified on the 1-hour chart. The NSE session is 6h15m, so
    an exact hourly grid does not exist; folding the final 15 minutes into the
    sixth bar keeps every bar inside one session, which a rolling window would
    not. Six bars a day.
    """
    out, cur, day = [], [], None
    def flush(grp, d):
        if grp:
            out.append({"date": grp[0]["date"], "day": d,
                        "open": grp[0]["open"], "close": grp[-1]["close"],
                        "high": max(x["high"] for x in grp),
                        "low": min(x["low"] for x in grp),
                        "volume": sum(x.get("volume") or 0 for x in grp),
                        "n": len(grp)})
    for b in bars:
        d = str(b.get("date", ""))[:10]
        if d != day:
            for i in range(0, len(cur), 4):
                g = cur[i:i+4]
                if len(cur) - (i + 4) == 1:
                    g = cur[i:i+5]
                flush(g, day)
                if len(g) == 5:
                    break
            cur, day = [], d
        cur.append(b)
    for i in range(0, len(cur), 4):
        g = cur[i:i+4]
        if len(cur) - (i + 4) == 1:
            g = cur[i:i+5]
        flush(g, day)
        if len(g) == 5:
            break
    return out


SMC_TOUCH_TOL = 0.005       # two swings within 0.5% are the same level
# THREE, not two, and this is the only filter that has earned its place.
# Tested 09/09 on a year of 1-hour bars, chosen on the first half and judged on
# the second, which the choosing never saw. "Held 3 times or more" beat taking
# every level in BOTH halves - +0.124% against +0.083% in the first, +0.092%
# against +0.027% in the second - and at Rs 8,00,000 a trade it turned +Rs 218
# a trade into +Rs 735 while still leaving about 8 calls a day.
# Four and five touches looked better still in the second half but were WORSE
# in the first, so they are not trusted. A minimum reward filter was tested at
# the same time and REJECTED: skipping calls under 2R made more per trade and
# less than half the money, and could not be told from a random pick (p=0.39).
SMC_MIN_TOUCH = 3           # the level must have held at least three times
# How long a POSITIONAL call is allowed to live. Both numbers come from the
# daily SMC study, not from taste: it gave each setup ten sessions to fill and
# judged the survivors on holds up to forty.
SMC_POS_FILL_DAYS = 10      # unfilled after this many sessions -> EXPIRED
SMC_POS_HOLD_DAYS = 40      # still running after this many -> closed and graded
SMC_CHOCH_BARS = 10         # bars allowed between the grab and the CHoCH


def smc_levels(points):
    """Cluster confirmed swings into levels and count how often price has
    already turned at each. MM's method only sweeps a level price has
    "historically reversed multiple times" from — a level with a single touch
    is just a swing point and does not qualify.

    This is the one part of the method that reproduced out of sample: win rate
    climbed 37.0% -> 40.1% -> 42.3% with the touch count on the first half of
    the year, and 38.6% -> 40.8% -> 43.3% on the second half, independently."""
    lv = []
    for px in points:
        for c in lv:
            if abs(px - c[0]) <= SMC_TOUCH_TOL * px:
                c[1] += 1
                c[0] = (c[0] * (c[1] - 1) + px) / c[1]
                break
        else:
            lv.append([px, 1])
    return [(p, n) for p, n in lv if n >= SMC_MIN_TOUCH]


def smc_fvg(bars, a, z, side):
    """The most recent fair value gap between bars a and z.

    Three candles: a bullish gap is the untraded span between candle 1's HIGH
    and candle 3's LOW — price moved so fast it left a hole. Returns
    (low, high, index-of-candle-3)."""
    best = None
    for i in range(max(0, a), min(z, len(bars) - 1) - 1):
        c1, c3 = bars[i], bars[i + 2]
        if side > 0 and c3["low"] > c1["high"]:
            best = (c1["high"], c3["low"], i + 2)
        elif side < 0 and c3["high"] < c1["low"]:
            best = (c3["high"], c1["low"], i + 2)
    return best


def smc_scan_choch(sym, hours, prev_day=None):
    """MM's 'BOS and CHoCH' method, exactly as he wrote it out on 08/09.

        1. map levels price has reversed from MORE THAN ONCE
        2. wait for a LIQUIDITY GRAB — price sweeps one, then closes back inside
        3. require a CHoCH — a close beyond the last opposing swing.
           His rule, verbatim: "Do not enter the trade if a clear CHoCH does
           not occur after the liquidity grab."
        4. find the FAIR VALUE GAP left by the move that caused the CHoCH
        5. enter on the retrace, stop beyond the sweep, target the next
           opposing pool of liquidity

    Where the backtest settled a choice his text leaves open, it is noted:

      ENTRY — the gap FULLY FILLED, not its midpoint and not first touch.
        Seven depths were tested; full fill was best (+0.100% against +0.075%
        at the midpoint) and it is the only one still positive before costs
        when you count only fills a resting order would certainly get.

      STOP — the sweep bar's own extreme, not the far side of the gap. His
        text says "safely below the FVG"; measured, that stop is too tight
        (+0.018% against +0.100%).

      TARGET — YESTERDAY'S HIGH OR LOW. His text says "the next opposing key
        level or major swing point"; nine targets were tested and the previous
        day's extreme won clearly (+0.123% gross, 41% win, out in about two
        hours). The nearest swing level made it a scalp at 2R; the second and
        third levels out both LOST. Only the previous day's pool, or a very
        distant 8R, made money — the middle ground is the worst place to be.

    Runs on the LAST COMPLETED hourly bar and returns one call or None."""
    if len(hours) < 3 * SMC_PIVOT + 8 or not prev_day:
        return None
    hi, lo = smc_swings(hours)
    i = len(hours) - 1
    known_lo, known_hi = [], []
    for k in range(SMC_PIVOT, i + 1):
        known_lo += lo.get(k, [])
        known_hi += hi.get(k, [])
    if len(known_lo) < 3 or len(known_hi) < 3:
        return None
    lv_lo = smc_levels(known_lo[-40:])
    lv_hi = smc_levels(known_hi[-40:])

    # THE CHoCH MUST HAVE HAPPENED ALREADY. The grab is looked for on a bar far
    # enough back that the confirmation can have arrived; a grab on the newest
    # bar is not yet a setup, it is half of one.
    for back in range(1, SMC_CHOCH_BARS + 1):
        g = i - back
        if g <= SMC_PIVOT:
            break
        cur = hours[g]
        for side in (1, -1):
            lv = lv_lo if side > 0 else lv_hi
            if not lv:
                continue
            hits = ([(p, n) for p, n in lv if cur["low"] < p <= cur["close"]]
                    if side > 0 else
                    [(p, n) for p, n in lv if cur["close"] <= p < cur["high"]])
            if not hits:
                continue
            level, touches = (min(hits) if side > 0 else max(hits))
            # the last opposing structural point, known before the grab
            opp = [p for k2, pts in (hi if side > 0 else lo).items()
                   if k2 <= g for p in pts]
            if not opp:
                continue
            sp = opp[-1]
            ch = None
            for j in range(g + 1, min(g + 1 + SMC_CHOCH_BARS, i + 1)):
                if (hours[j]["close"] > sp) if side > 0 else (hours[j]["close"] < sp):
                    ch = j
                    break
                if (hours[j]["close"] < cur["low"]) if side > 0 else \
                   (hours[j]["close"] > cur["high"]):
                    break
            if ch is None or ch != i:
                continue          # only act the bar the CHoCH completes on
            fv = smc_fvg(hours, g, ch, side)
            if not fv:
                continue
            glo, ghi, _gb = fv
            if ghi <= glo:
                continue
            entry = glo if side > 0 else ghi          # the gap fully filled
            sl = cur["low"] if side > 0 else cur["high"]
            risk = abs(entry - sl)
            if risk <= 0 or risk > 0.10 * entry:
                continue
            tgt = prev_day[0] if side > 0 else prev_day[1]
            if tgt is None:
                continue
            if (tgt <= entry + risk) if side > 0 else (tgt >= entry - risk):
                continue                              # no room to yesterday's pool
            rr = abs(tgt - entry) / risk
            if rr < 0.5 or rr > 20:
                continue
            t1 = entry + (tgt - entry) / 2
            window = hours[max(0, i - SMC_KEEP + 1):i + 1]
            chart = [{"t": str(b.get("date", ""))[:16].replace("T", " "),
                      "o": round(float(b["open"]), 2), "h": round(float(b["high"]), 2),
                      "l": round(float(b["low"]), 2), "c": round(float(b["close"]), 2)}
                     for b in window]
            why = [f"level at {level:.2f} has held {touches} times before",
                   f"liquidity grab — price swept it and closed back inside",
                   f"CHoCH confirmed: closed {'above' if side > 0 else 'below'} "
                   f"{sp:.2f}, the last {'lower high' if side > 0 else 'higher low'}",
                   f"fair value gap {glo:.2f}–{ghi:.2f}; buy when it fills",
                   f"target is yesterday's {'high' if side > 0 else 'low'} "
                   f"at {tgt:.2f} ({rr:.1f}R)"]
            return {
                "date": date.today().isoformat(),
                "created_hm": datetime.now().strftime("%H:%M"),
                "symbol": sym, "kind": "INTRADAY",
                "side": "LONG" if side > 0 else "SHORT",
                "level": round(level, 2), "swept": f"{touches}x level",
                "entry": round(entry, 2), "sl": round(sl, 2),
                "t1": round(t1, 2), "t2": round(tgt, 2), "risk0": round(risk, 4),
                "status": "WAITING", "r": None,
                "triggered_on": "", "triggered_at": "", "closed_on": "", "closed_at": "",
                "bar_ts": str(cur.get("date", ""))[:16].replace("T", " "),
                "fill_dir": "DOWN",      # a limit back INTO the gap, not a break
                "rr": round(rr, 2),      # the real reward, not a fixed multiple
                "reasons": " · ".join(why),
                "_chart": chart,
            }
    return None


def smc_scan(sym, bars, kind, prev_day=None):
    """Find a sweep on the LAST COMPLETED bar. Returns a call or None.

    A sweep is: the bar trades through a level where stops are sitting, and
    closes back on the other side of it. The break happened and was rejected
    inside one bar.
    """
    if len(bars) < 3 * SMC_PIVOT + 4:
        return None
    hi, lo = smc_swings(bars)
    i = len(bars) - 1
    cur, prev = bars[i], bars[i - 1]
    known_lo, known_hi = [], []
    for k in range(SMC_PIVOT, i + 1):
        known_lo += lo.get(k, [])
        known_hi += hi.get(k, [])
    known_lo, known_hi = known_lo[-8:], known_hi[-8:]
    if prev_day:
        known_hi = known_hi + [prev_day[0]]
        known_lo = known_lo + [prev_day[1]]

    for side in (1, -1):
        if side > 0:
            hit = [L for L in known_lo if cur["low"] < L <= cur["close"]]
            shape = smc_bullish_reversal(prev, cur)
        else:
            hit = [L for L in known_hi if cur["close"] <= L < cur["high"]]
            shape = smc_bearish_reversal(prev, cur)
        if not hit or not shape:
            continue
        level = min(hit) if side > 0 else max(hit)
        pday = bool(prev_day and abs(level - (prev_day[1] if side > 0 else prev_day[0])) < 1e-9)
        trig = cur["high"] if side > 0 else cur["low"]
        sl = cur["low"] if side > 0 else cur["high"]
        risk = abs(trig - sl)
        if risk <= 0 or risk > 0.10 * trig:
            continue
        t1 = trig + risk if side > 0 else trig - risk
        t2 = trig + 2 * risk if side > 0 else trig - 2 * risk
        window = bars[max(0, i - SMC_KEEP + 1):i + 1]
        chart = [{"t": str(b.get("date", ""))[:16].replace("T", " "),
                  "o": round(float(b["open"]), 2), "h": round(float(b["high"]), 2),
                  "l": round(float(b["low"]), 2), "c": round(float(b["close"]), 2)}
                 for b in window]
        why = [f"liquidity sweep of {'support' if side > 0 else 'resistance'} "
               f"at {level:.2f}" + (" (previous day's extreme)" if pday else ""),
               f"rejected in one bar - {shape}",
               f"entry on a break of {trig:.2f}, the sweep bar's "
               f"{'high' if side > 0 else 'low'}"]
        return {
            "date": date.today().isoformat(),
            "created_hm": datetime.now().strftime("%H:%M"),
            "symbol": sym, "kind": kind,
            "side": "LONG" if side > 0 else "SHORT",
            "level": round(level, 2), "swept": "prev day" if pday else "swing",
            "entry": round(trig, 2), "sl": round(sl, 2),
            "t1": round(t1, 2), "t2": round(t2, 2), "risk0": round(risk, 4),
            "status": "WAITING", "r": None,
            "triggered_on": "", "triggered_at": "", "closed_on": "", "closed_at": "",
            # the exact candle the sweep happened on - everything after this
            # candle is what decides the trade, and nothing before it counts
            "bar_ts": str(cur.get("date", ""))[:16].replace("T", " "),
            "reasons": " · ".join(why),
            "_chart": chart,
        }


def smc_key(c):
    return (c["date"], c["symbol"], c["kind"], c["side"], _px(c["entry"]))


def smc_kid(c):
    """The same key as one string, safe to put in a web address."""
    return "|".join(smc_key(c))


# ── the picture behind each call ───────────────────────────────────────────
# The forty bars leading up to the sweep are kept with the call, so the chart
# on the page is drawn from the SAME data the sweep was found in - the exchange
# bars Zerodha sends. Nothing is redrawn from anywhere else, so what the chart
# shows and what the call says can never disagree.
SMC_BARS = {}
SMC_BARS_FILE = os.path.join(DATA, "smc_bars.json")
SMC_KEEP = 40


def smc_bars_save():
    try:
        json.dump(SMC_BARS, open(SMC_BARS_FILE, "w", encoding="utf8"))
    except Exception as e:
        print(f"  [SMC] could not save the charts: {e}")


def smc_bars_load():
    global SMC_BARS
    try:
        if os.path.exists(SMC_BARS_FILE):
            SMC_BARS = json.load(open(SMC_BARS_FILE, encoding="utf8"))
            print(f"  {len(SMC_BARS)} SMC charts already on record.")
    except Exception as e:
        print(f"  (could not read the SMC charts: {e})")


def smc_add(c):
    if not c or smc_key(c) in _smc_seen:
        return False
    _smc_seen.add(smc_key(c))
    fo_tag(c)
    ch = c.pop("_chart", None)
    if ch:
        SMC_BARS[smc_kid(c)] = ch
    SMC.append(c)
    tail = f" · F&O lot {c['lot']}" if c.get("fo") == "YES" else " · cash only"
    print(f"  [SMC/{c['kind'][:4]}] {c['side']} {c['symbol']} @ {c['entry']} "
          f"sl {c['sl']} — {c['swept']} sweep{tail}")
    return True


def smc_intraday(kite, scanner):
    """15-minute sweeps. Runs a few minutes after each 15-minute bar closes,
    on the most liquid names. One request per stock, two days of bars."""
    fut = fo_map(kite)
    sp_found = 0
    cache = dict(getattr(scanner, "daily", {}) or {})
    ranked = []
    for sym, blob in cache.items():
        bars = blob.get("d") if isinstance(blob, dict) else blob
        if not bars or len(bars) < 25:
            continue
        v = sorted((b["close"] * (b.get("volume") or 0)) for b in bars[-20:])
        ranked.append((v[len(v) // 2], sym))
    ranked.sort(reverse=True)
    # every F&O stock first - those are the ones MM can take in either book -
    # then the most liquid cash-only names to fill the rest of the budget
    picks = [s for _t, s in ranked if s in fut]
    for _t, s in ranked:
        if len(picks) >= SMC_UNIVERSE:
            break
        if s not in fut:
            picks.append(s)
    picks = picks[:SMC_UNIVERSE]
    # a stock with a call still open is ALWAYS fetched, even if it has dropped
    # out of the list - otherwise that call would never get resolved
    live = [c["symbol"] for c in SMC
            if c.get("kind") == "INTRADAY" and c.get("date") == date.today().isoformat()
            and c.get("status") in ("WAITING", "LIVE")]
    for s in live:
        if s not in picks:
            picks.append(s)
    n_fo = sum(1 for s in picks if s in fut)
    toks = {}
    try:
        for row in kite.instruments("NSE"):
            if row.get("tradingsymbol") in picks and row.get("instrument_type") == "EQ":
                toks[row["tradingsymbol"]] = row["instrument_token"]
    except Exception as e:
        print(f"  [SMC] instrument list failed: {e}")
        return
    # HOW MUCH HISTORY TO PULL, and why it is no longer six days.
    #
    # Six calendar days is about four trading sessions - roughly 24 one-hour
    # candles. The sweep scanner is happy with that. SP is not: sp_scan_one
    # refuses to run on fewer than 60 hours, because RSI, the stochastic, the
    # Bollinger width and the 40-bar range all need history behind them. So
    # from the day SP shipped it returned None on every stock on every scan,
    # and sp_log.csv sat at nothing but its header. MM, 17/09: "there were no
    # trades today in SP tab."
    #
    # 30 calendar days is about 21 sessions, near 126 hourly candles. Kite
    # serves 15-minute data up to 100 days in ONE request, so this is the
    # same number of API calls as before - a longer range, not another call.
    #
    # The sweep scanner is handed exactly the six days it always had, so
    # nothing about its calls changes.
    SMC_DAYS, FETCH_DAYS = 6, 30
    frm = datetime.now() - timedelta(days=FETCH_DAYS)
    to = datetime.now()
    smc_from = (date.today() - timedelta(days=SMC_DAYS)).isoformat()
    found = 0
    for sym in picks:
        tok = toks.get(sym)
        if not tok:
            continue
        try:
            raw = kite.historical_data(tok, frm, to, "15minute")
        except Exception:
            time.sleep(0.36)
            continue
        time.sleep(0.36)
        if len(raw) < 20:
            continue
        bars = [{"date": str(c["date"]), "open": c["open"], "high": c["high"],
                 "low": c["low"], "close": c["close"],
                 "volume": c.get("volume", 0)} for c in raw]
        today = date.today().isoformat()
        # YESTERDAY'S HIGH AND LOW — the target, and the one the backtest
        # picked out of nine. The previous SESSION only, not a 25-bar window:
        # it is a real liquidity pool that every chart on the street can see.
        prev = [b for b in bars if b["date"][:10] < today]
        pd = None
        if prev:
            lastday = prev[-1]["date"][:10]
            d = [b for b in prev if b["date"][:10] == lastday]
            if d:
                pd = (max(x["high"] for x in d), min(x["low"] for x in d))
        # ONLY COMPLETED HOURS. The hour in progress is a bar that is still
        # changing: its high, its low and its close all move as the hour goes
        # on, so a CHoCH can appear on it at 09:32 and be gone by 09:47 - and
        # the same setup gets flashed again on the next scan (HEROMOTOCO,
        # 09/09, twice within a quarter of an hour). A signal is only real
        # once the candle it is read from has closed.
        hours_all = [h for h in smc_hour(bars) if h.get("n", 4) >= 4]
        # The sweep scanner sees the same six days it always saw. SP sees the
        # lot, because it cannot work without the history.
        hours = [h for h in hours_all if h["date"][:10] >= smc_from]
        if smc_add(smc_scan_choch(sym, hours, pd)):
            found += 1
        smc_resolve(sym, hours, "INTRADAY")  # settle anything already open here
        # SP rides on the SAME hourly bars this loop already downloaded, so
        # adding the tab costs no extra Kite calls and cannot slow the desk.
        try:
            if sp_add(sp_scan_one(sym, hours_all, sym in fut), hours_all):
                sp_found += 1
            sp_resolve(sym, hours_all)
        except Exception as e:
            print(f"  [SP] {sym}: {e}")
    print(f"  [SMC] 1-hour CHoCH scan: {len(picks)} stocks "
          f"({n_fo} with futures), {found} new")
    print(f"  [SP]  reversal-at-a-level scan: {sp_found} new, "
          f"{sum(1 for c in SP if c.get('status') == 'LIVE')} running")


def smc_positional(scanner, kite=None):
    """Daily sweeps, from the cache the desk already keeps. No API cost.
    Runs over the WHOLE market - every stock the desk has bars for."""
    fo_map(kite)
    cache = dict(getattr(scanner, "daily", {}) or {})
    found = 0
    for sym, blob in cache.items():
        bars = blob.get("d") if isinstance(blob, dict) else blob
        if not bars or len(bars) < 40:
            continue
        if bars[-1].get("close", 0) < 20:
            continue
        v20 = bars[-20:]
        if sum(b["close"] * (b.get("volume") or 0) for b in v20) / 20 / 1e7 < 5:
            continue
        if smc_add(smc_scan(sym, bars, "POSITIONAL",
                            (bars[-2]["high"], bars[-2]["low"]))):
            found += 1
        smc_resolve(sym, bars, "POSITIONAL")
    print(f"  [SMC] positional sweep scan: {found} new")


def smc_resolve(sym, bars, kind):
    """Decide every open call on this stock from the CANDLES THEMSELVES.

    This replaces an earlier version that read the fill and the stop from the
    live quote's day high and day low. That was wrong, and wrong in the worst
    possible way: the entry is the sweep bar's high and the stop is the sweep
    bar's low, so BOTH already sit inside the day's range the instant the call
    is made. Every call filled and stopped in the same second. Sixteen calls on
    07/09/2026 were marked SL_HIT within two minutes of being found - none of
    those outcomes were real.

    The rule now is exactly the rule the backtest used, so live results and
    tested results mean the same thing:
        fill    only on a LATER candle trading through the entry price
        stop    if that same candle also reaches the stop, assume the stop
                came first - the pessimistic side, never the flattering one
        target  twice the risk
        out     15:10 on INTRADAY, at that candle's close
    """
    if not bars:
        return
    idx = {}
    for n, b in enumerate(bars):
        idx[str(b.get("date", ""))[:16].replace("T", " ")] = n
    for c in SMC:
        if c.get("symbol") != sym or c.get("kind") != kind:
            continue
        if c.get("status") in ("SL_HIT", "T2_HIT", "TIME_EXIT", "EXPIRED"):
            continue
        i = idx.get(c.get("bar_ts") or "")
        if i is None:
            continue
        long = c["side"] == "LONG"
        entry, sl, t2 = c["entry"], c["sl"], c["t2"]
        filled = c["status"] == "LIVE"
        # THE SAME MISTAKE, A THIRD TIME (MM, 10/09: "why are there so many
        # bugs ... do the sweep").
        #
        # The walk starts at the bar AFTER the signal, which is right for a
        # call still waiting to fill. But a call that is ALREADY LIVE filled
        # later - sometimes hours later, because the CHoCH rule puts a limit
        # below the market and waits for price to come back to it. Every bar
        # between the signal and that fill is before the trade existed, and
        # the stop test below was being applied to all of them. One bar
        # dipping through the entry zone on its way down would book an instant
        # stop loss timed before the position opened.
        #
        # It is the 210-row settle bug and the swing fill-day bug wearing a
        # third hat. Same guard as both: nothing before the fill can decide
        # the trade.
        start_at = ""
        if filled:
            start_at = ((c.get("triggered_on") or c.get("date") or "") + " " +
                        (c.get("triggered_at") or ""))
        for j in range(i + 1, len(bars)):
            x = bars[j]
            ts = str(x.get("date", ""))[:16].replace("T", " ")
            d, hm = ts[:10], ts[11:16]
            if start_at.strip() and ts < start_at:
                continue                   # before the trade existed
            if kind == "INTRADAY" and d != c["date"]:
                break                      # a new day - an intraday call is done
            if not filled:
                # WHICH WAY PRICE HAS TO MOVE TO FILL YOU. The old sweep rule
                # bought a BREAK of the sweep bar's high, so the entry sat
                # ABOVE the market and a long filled when the high reached it.
                # The CHoCH rule buys the RETRACE into the fair value gap, so
                # the entry sits BELOW the market and a long fills when the
                # LOW reaches it. Getting this backwards would mean no call
                # ever fills, or every call fills instantly - so the direction
                # is carried on the call itself rather than assumed, and old
                # rows without it keep the old behaviour.
                down = c.get("fill_dir") == "DOWN"
                if down:
                    through = (x["low"] <= entry) if long else (x["high"] >= entry)
                else:
                    through = (x["high"] >= entry) if long else (x["low"] <= entry)
                if through:
                    filled = True
                    c["status"] = "LIVE"
                    c["triggered_on"], c["triggered_at"] = d, hm
                    # the same candle can also reach the stop; assume it did
                    if (long and x["low"] <= sl) or ((not long) and x["high"] >= sl):
                        c["status"], c["r"] = "SL_HIT", -1.0
                        c["closed_on"], c["closed_at"] = d, hm
                        break
                elif kind == "INTRADAY" and hm >= "15:10":
                    c["status"] = "EXPIRED"
                    c["closed_on"], c["closed_at"] = d, hm
                    break
                else:
                    continue
            if (long and x["low"] <= sl) or ((not long) and x["high"] >= sl):
                c["status"], c["r"] = "SL_HIT", -1.0
                c["closed_on"], c["closed_at"] = d, hm
                break
            if (long and x["high"] >= t2) or ((not long) and x["low"] <= t2):
                # The reward is whatever yesterday's extreme was actually
                # worth on this call - anywhere from 0.5R to 20R - not the
                # flat 2.0R the old fixed-target rule could assume.
                c["status"] = "T2_HIT"
                c["r"] = float(c.get("rr") or 2.0)
                c["closed_on"], c["closed_at"] = d, hm
                break
            if kind == "INTRADAY" and hm >= "15:10":
                move = (x["close"] - entry) if long else (entry - x["close"])
                c["status"] = "TIME_EXIT"
                c["r"] = round(move / c["risk0"], 2) if c.get("risk0") else 0.0
                c["closed_on"], c["closed_at"] = d, hm
                break


def smc_track():
    """The only thing the live quote is used for now: closing an INTRADAY call
    that is still open at the end of the day, in case no scan ran after 15:10.
    Fills, stops and targets are decided by smc_resolve, from the candles."""
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")
    if now < "15:25":
        return
    for c in SMC:
        if c.get("kind") != "INTRADAY" or c.get("date") != today:
            continue
        if c.get("status") in ("SL_HIT", "T2_HIT", "TIME_EXIT", "EXPIRED"):
            continue
        if c["status"] == "WAITING":
            c["status"] = "EXPIRED"
            c["closed_on"], c["closed_at"] = today, now
            continue
        ltp = (_ltp["px"].get(c["symbol"]) or {}).get("ltp")
        if ltp is None:
            continue
        move = (ltp - c["entry"]) if c["side"] == "LONG" else (c["entry"] - ltp)
        c["status"] = "TIME_EXIT"
        c["r"] = round(move / c["risk0"], 2) if c.get("risk0") else 0.0
        c["closed_on"], c["closed_at"] = today, now


def smc_expire_positional(quiet=False):
    """Give POSITIONAL SMC calls an ending.

    MM, 15/09: "there are bugs on the dashboard." The audit found 430 positional
    calls still sitting on WAITING, the oldest from 07/09, and 459 more still
    LIVE. Two thirds of every positional call ever made was still open.

    The reason is a hole in the rules rather than a broken function. An INTRADAY
    call has an ending built in - smc_track() expires an unfilled order at 15:25
    and smc_settle_stale() closes anything left over from an earlier day. A
    POSITIONAL call has neither. smc_resolve() will fill it, stop it or target
    it if price ever gets there, and if price never gets there the row simply
    stays open for ever.

    So two endings, both from how the positional side was actually TESTED
    rather than invented here:

      NEVER FILLED     the limit sits at the swept level. The daily study gave
                       each setup ten sessions to fill; past that the structure
                       that created it is stale. An unfilled order has no money
                       in it, so this grades nothing - it is EXPIRED.

      FILLED, STILL RUNNING AFTER 40 SESSIONS
                       forty sessions is the longest hold the daily backtest
                       used, and the number the futures version was judged on.
                       Past it the trade is closed at that day's close and
                       GRADED, exactly as a time exit is graded intraday.

    The second one does change the record - it turns open rows into results.
    That is the point: a trade with no ending is not a trade, and 459 of them
    sitting open is not a record either.
    """
    today = date.today()
    fills = expiries = 0
    for c in SMC:
        if c.get("kind") != "POSITIONAL":
            continue
        st = c.get("status") or ""
        if st not in OPEN_STATUSES:
            continue
        started = c.get("triggered_on") or c.get("date") or ""
        if not started:
            continue
        try:
            age = _sessions_since(started, today)
        except Exception:
            continue
        if st == "WAITING":
            if age >= SMC_POS_FILL_DAYS:
                c["status"] = "EXPIRED"
                c["closed_on"] = today.isoformat()
                c["closed_at"] = "15:30"
                expiries += 1
            continue
        # filled and still running
        if age >= SMC_POS_HOLD_DAYS:
            px = (_ltp["px"].get(c["symbol"]) or {}).get("ltp")
            if px is None:
                continue                  # no price today - leave it, try again
            move = ((px - c["entry"]) if c["side"] == "LONG"
                    else (c["entry"] - px))
            c["status"] = "TIME_EXIT"
            c["r"] = round(move / c["risk0"], 2) if c.get("risk0") else 0.0
            c["closed_on"] = today.isoformat()
            c["closed_at"] = "15:30"
            fills += 1
    if (fills or expiries) and not quiet:
        print(f"  [SMC] positional endings: {expiries} unfilled orders expired "
              f"after {SMC_POS_FILL_DAYS} sessions, {fills} open trades closed "
              f"after {SMC_POS_HOLD_DAYS}")
    return expiries, fills


def _sessions_since(iso_day, today):
    """Trading sessions between a date and today - weekdays only. Holidays are
    not subtracted, so the count errs slightly LONG, which is the safe side:
    a call is never ended sooner than the rule says."""
    try:
        d0 = date.fromisoformat(str(iso_day)[:10])
    except ValueError:
        return 0
    if d0 >= today:
        return 0
    n, d = 0, d0
    while d < today:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def smc_settle_stale(kite, quiet=False):
    """Settle every SMC INTRADAY call still open from an earlier day.

    MM, 10/09: "why is the smc tab showing the same thing after restarting it."

    Because I kept writing one-off repairs. The first found fifteen rows by a
    fingerprint and reopened them. The second was supposed to settle them, but
    it looked for the SAME fingerprint - a closing time earlier than the fill -
    and the first pass had already wiped the closing time. So it found nothing,
    wrote its marker, and eleven rows stayed open. Three restarts, no change.

    A one-off repair only ever fixes the exact shape you thought of. This is
    the rule instead, and it needs no fingerprint at all:

        AN INTRADAY CALL CANNOT STILL BE OPEN AFTER ITS OWN DAY.

    Whatever left it open - the old resolver, a repair of mine, a restart, a
    stock that never appeared on another day's shortlist - it is settled here
    from its own day's candles. It runs at EVERY startup, not once behind a
    marker file, so there is nothing left to skip. This is exactly what
    settle_stale() already does for the main record; the SMC book never had it.
    """
    today = date.today().isoformat()
    stale = [c for c in SMC
             if c.get("kind") == "INTRADAY"
             and c.get("status") in ("WAITING", "LIVE", "T1_HIT")
             and (c.get("date") or "") < today]
    if not stale:
        if not quiet:
            print("  [SMC] no intraday calls left open from earlier days.")
        return 0
    if kite is None:
        print(f"  [SMC] {len(stale)} intraday calls open from earlier days — "
              f"no Kite session, leaving them alone.")
        return 0
    by = {}
    for c in stale:
        by.setdefault((c.get("symbol"), c.get("date")), []).append(c)
    _load_instruments(kite, {s for s, _ in by})
    fixed = stuck = 0
    for (sym, day), rows in sorted(by.items()):
        tok = _token_of(sym)
        if not tok:
            stuck += len(rows); continue
        try:
            raw = kite.historical_data(tok, f"{day} 09:00:00",
                                       f"{day} 15:40:00", "15minute") or []
            time.sleep(0.36)
        except Exception:
            stuck += len(rows); continue
        if not raw:
            stuck += len(rows); continue
        bars = [{"date": str(x["date"]), "open": x["open"], "high": x["high"],
                 "low": x["low"], "close": x["close"],
                 "volume": x.get("volume", 0)} for x in raw]
        hours = [h for h in smc_hour(bars) if h.get("n", 4) >= 4]
        smc_resolve(sym, hours, "INTRADAY")
        for c in rows:
            if c.get("status") in ("WAITING", "LIVE", "T1_HIT"):
                # the candles exist and still decided nothing, which for an
                # intraday call means it simply ran to the close
                c["status"] = "TIME_EXIT" if c.get("triggered_at") else "EXPIRED"
                c["r"] = 0.0 if c.get("triggered_at") else None
                c["closed_on"], c["closed_at"] = day, "15:10"
            fixed += 1
    smc_save()
    if not quiet:
        print(f"  [SMC] settled {fixed} intraday call"
              f"{'s' if fixed != 1 else ''} left open from earlier days"
              + (f"; {stuck} had no candles and were left alone." if stuck else "."))
    return fixed


def repair_smc_before_fill(kite=None):
    """Reopen the SMC calls the old smc_resolve closed before they filled.

    Found by the 10/09 sweep MM asked for, not by MM finding another wrong
    number — which is the point of the sweep.

    smc_resolve walked from the SIGNAL bar. The CHoCH rule places a limit
    BELOW the market and waits for price to come back to it, so a call
    signalled at 09:45 might not fill until 12:00. Every bar in between was
    being tested against the stop, and the bar right after the signal is
    exactly where price is falling towards the limit — so it booked an instant
    stop loss, stamped before the position opened. Fifteen calls, all of them
    -1.0, all closed one bar after their signal.

    The fingerprint is the same as everywhere else: a closing moment earlier
    than the opening moment. These are reopened as LIVE with their result
    cleared; the next scan re-settles them properly from the candles, which is
    how every SMC call is decided anyway. Nothing is guessed.

    The SMC book holds no money, so this changes no P&L. It changes the
    EVIDENCE — the record MM is using to judge whether the strategy works,
    which is the thing those 15 rows were quietly poisoning.

    Runs once. Gated on DATA\\.smc_before_fill_repaired.
    """
    # v2: the first pass only REOPENED the rows and left them for "the next
    # scan", which never comes for a stock that has not flashed since. The
    # fixed pass settles them itself — so the flag name is bumped, or the
    # machines that already ran the broken version would skip the fix.
    flag = os.path.join(DATA, ".smc_before_fill_repaired_v2")
    if os.path.exists(flag):
        return 0

    def moment(c, which):
        d = c.get(f"{which}_on") or c.get("date") or ""
        return d + " " + (c.get(f"{which}_at") or "")

    bad = [c for c in SMC
           if c.get("triggered_at") and c.get("closed_at")
           and moment(c, "closed") < moment(c, "triggered")]
    if not bad:
        open(flag, "w").write("nothing to repair\n")
        return 0
    for c in bad:
        c["status"] = "LIVE"
        c["r"] = None
        c["closed_on"] = ""
        c["closed_at"] = ""

    # AND SETTLE THEM HERE, rather than hoping a scan does it.
    #
    # MM, 10/09: "there is a bug in smc tab ... now it is showing 7 live calls
    # but the prices do not match." That was this repair, half done. The first
    # version reopened the rows and left a note saying the next scan would
    # settle them. It does not: the hourly scan only ever looks at the stocks
    # on TODAY'S shortlist, so a reopened call in a stock that has not flashed
    # since simply sits LIVE for ever, showing a stale entry against a price
    # that has moved on. Eleven of the fifteen were stranded exactly that way.
    #
    # Reopening a row is only half a repair. The other half is closing it
    # again, correctly, from that day's own candles - which is what this does.
    done = stuck = 0
    if kite is not None:
        by = {}
        for c in bad:
            if c.get("kind") == "INTRADAY":
                by.setdefault((c.get("symbol"), c.get("date")), []).append(c)
        _load_instruments(kite, {s for s, _ in by})
        for (sym, day), _rows in sorted(by.items()):
            tok = _token_of(sym)
            if not tok or not day:
                stuck += len(_rows); continue
            try:
                raw = kite.historical_data(tok, f"{day} 09:00:00",
                                           f"{day} 15:40:00", "15minute") or []
                time.sleep(0.36)
            except Exception:
                stuck += len(_rows); continue
            if not raw:
                stuck += len(_rows); continue
            bars = [{"date": str(c2["date"]), "open": c2["open"],
                     "high": c2["high"], "low": c2["low"], "close": c2["close"],
                     "volume": c2.get("volume", 0)} for c2 in raw]
            hours = [h for h in smc_hour(bars) if h.get("n", 4) >= 4]
            smc_resolve(sym, hours, "INTRADAY")
            done += sum(1 for c2 in _rows if c2.get("status") not in
                        ("WAITING", "LIVE", "T1_HIT"))
        stuck += sum(1 for c in bad if c.get("kind") == "INTRADAY"
                     and c.get("status") in ("WAITING", "LIVE", "T1_HIT"))
    smc_save()
    open(flag, "w").write(f"reopened {len(bad)}, re-settled {done}\n")
    print(f"\n  REPAIRED {len(bad)} SMC calls that were closed before they "
          f"filled.\n  (the old resolver walked from the signal bar, not the "
          f"fill bar)\n  Re-settled {done} from their own candles"
          + (f"; {stuck} could not be fetched and are left open."
             if stuck else ".") + "\n")
    return len(bad)


def dedupe_smc(quiet=True):
    """The same protection for the SMC book. Same reasoning as
    dedupe_history(): heal it rather than hunt for the cause each time."""
    rank = {"WAITING": 0, "LIVE": 1, "T1_HIT": 2}
    keep, seen = [], {}
    for c in SMC:
        k = smc_key(c)
        i = seen.get(k)
        if i is None:
            seen[k] = len(keep); keep.append(c); continue
        old_c = keep[i]
        if rank.get(c.get("status"), 9) >= rank.get(old_c.get("status"), 9):
            c = dict(c)
            c["created_hm"] = old_c.get("created_hm") or c.get("created_hm")
            keep[i] = c
    gone = len(SMC) - len(keep)
    if gone:
        SMC[:] = keep
        if not quiet:
            print(f"  ({gone} duplicate SMC row{'s' if gone > 1 else ''} removed)")
    return gone


def smc_save():
    dedupe_smc()              # nothing crooked ever reaches the file
    try:
        with open(SMC_LOG, "w", newline="", encoding="utf8") as f:
            w = csv.DictWriter(f, fieldnames=SMC_FIELDS)
            w.writeheader()
            for c in SMC:
                w.writerow({k: ("" if c.get(k) is None else c.get(k)) for k in SMC_FIELDS})
    except Exception as e:
        print(f"  [SMC] could not save the log: {e}")


# Which status is "further along". A WAITING row and a LIVE row for the same
# call are the same trade seen at two moments; the later one is the truth.
_SMC_RANK = {"WAITING": 0, "LIVE": 1, "T1_HIT": 2}


def smc_load():
    if not os.path.exists(SMC_LOG):
        return
    dropped = 0
    try:
        for r in csv.DictReader(open(SMC_LOG, encoding="utf8")):
            for k in ("entry", "sl", "t1", "t2", "risk0", "r", "level"):
                try:
                    r[k] = float(r[k]) if r.get(k) not in ("", None) else None
                except Exception:
                    r[k] = None
            for k in ("lot", "lot_value"):
                try:
                    r[k] = int(float(r[k])) if r.get(k) not in ("", None) else None
                except Exception:
                    r[k] = None
            # COLLAPSE DUPLICATES ALREADY IN THE FILE (09/09). Stopping new
            # ones from being written was only half the job - HEROMOTOCO was
            # still on the page twice after a restart because this loop
            # appended every row it read, duplicates included. The first
            # sighting keeps its time (that is when the idea appeared) and the
            # most resolved copy wins, which is the same rule the equity log
            # has used since 04/09.
            k = smc_key(r)
            if k in _smc_seen:
                for n, old in enumerate(SMC):
                    if smc_key(old) != k:
                        continue
                    if _SMC_RANK.get(r.get("status"), 9) >= \
                       _SMC_RANK.get(old.get("status"), 9):
                        r["created_hm"] = old.get("created_hm") or r.get("created_hm")
                        SMC[n] = r
                    break
                dropped += 1
                continue
            SMC.append(r)
            _smc_seen.add(k)
        if dropped:
            print(f"  ({dropped} duplicate SMC row{'s' if dropped > 1 else ''} "
                  f"in the log collapsed into the call{'s' if dropped > 1 else ''} "
                  f"already there)")
        print(f"  {len(SMC)} SMC calls already on record.")
        if dropped:
            smc_save()          # write the file back clean, once
    except Exception as e:
        print(f"  (could not read the SMC log: {e})")


def smc_repair_fill_bug(kite=None):
    """One-time repair of the 07/09/2026 SMC fill bug.

    THE BUG. The first tracker read the fill and the stop from the live quote's
    DAY high and DAY low. The entry is the sweep bar's high and the stop is its
    low, so both already sat inside the day's range the moment the call was
    made. Every call filled on one pass of the watcher and stopped on the next.

    THE SIGNATURE, measured rather than guessed. A first attempt looked for
    triggered_at == closed_at and found nothing, because the watcher runs once
    a minute: the fill was stamped on one pass and the stop on the NEXT, always
    exactly one minute later. The real fingerprint, from the 07/09 log:
        55 calls settled before the 11:38 restart
        every one SL_HIT, every one exactly 1 minute from fill to exit,
        every one -1.000R
    Against the 175 settled after the restart by the candle resolver:
        34 targets, 47 never triggered, 73 stops, 21 time exits, +0.054R
    The two populations are not close. Only the first is repaired.

    WHAT HAPPENS TO THEM. Nothing is re-graded from the old numbers - they were
    never real. Each call is reopened, its sweep candle recovered from the chart
    saved beside it, and settled from the exchange's own 15-minute bars by the
    same code that settles every other call. A call whose chart was not kept
    cannot be replayed honestly, so it is voided and left out of the record
    rather than carrying an invented result."""
    mark = os.path.join(DATA, ".smc_fill_bug_repaired_v2")
    if os.path.exists(mark):
        return
    BAD_DAY, BAD_BEFORE = "2026-09-07", 11 * 60 + 38

    def _m(t):
        try:
            h, m = str(t).split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return None

    hit = []
    for c in SMC:
        if c.get("closed_on") != BAD_DAY or not c.get("closed_at"):
            continue
        cm, tm = _m(c["closed_at"]), _m(c.get("triggered_at") or "")
        if cm is None or cm >= BAD_BEFORE:
            continue
        if tm is None or not (0 <= cm - tm <= 2):
            continue
        hit.append(c)
    if not hit:
        try:
            open(mark, "w", encoding="utf8").write(
                f"nothing to repair, checked {datetime.now().isoformat()}\n")
        except Exception:
            pass
        return

    reopened, voided = [], 0
    for c in hit:
        ch = SMC_BARS.get(smc_kid(c)) or []
        if ch and ch[-1].get("t"):
            c["bar_ts"] = ch[-1]["t"]
            c["status"], c["r"] = "WAITING", None
            c["triggered_on"] = c["triggered_at"] = ""
            c["closed_on"] = c["closed_at"] = ""
            reopened.append(c)
        else:
            c["status"], c["r"] = "VOID_NO_CHART", None
            voided += 1
    print(f"  [SMC] {len(hit)} calls were filled and stopped by the old tracker "
          f"in the same instant — {len(reopened)} reopened to be settled from "
          f"their candles" + (f", {voided} voided (no chart kept)" if voided else ""))

    # Settle them NOW, not on the next scan. Reopened calls are dated today, and
    # the end-of-day sweep would mark them EXPIRED before any scan could look at
    # them. One request a stock, the same bars the scan uses.
    if reopened and kite is not None:
        syms = sorted({c["symbol"] for c in reopened})
        toks = {}
        try:
            for row in kite.instruments("NSE"):
                if row.get("tradingsymbol") in syms and row.get("instrument_type") == "EQ":
                    toks[row["tradingsymbol"]] = row["instrument_token"]
        except Exception as e:
            print(f"  [SMC] could not read the instrument list: {e}")
        frm, to = datetime.now() - timedelta(days=6), datetime.now()
        done = 0
        for sym in syms:
            tok = toks.get(sym)
            if not tok:
                continue
            try:
                raw = kite.historical_data(tok, frm, to, "15minute")
            except Exception:
                time.sleep(0.36)
                continue
            time.sleep(0.36)
            bars = [{"date": str(x["date"]), "open": x["open"], "high": x["high"],
                     "low": x["low"], "close": x["close"]} for x in raw]
            smc_resolve(sym, bars, "INTRADAY")
            done += 1
        left = [c for c in reopened if c["status"] == "WAITING"]
        print(f"  [SMC] replayed {done} stocks from the exchange's candles; "
              f"{len(reopened) - len(left)} settled, {len(left)} still open")
        smc_save()

    try:
        open(mark, "w", encoding="utf8").write(
            f"fill-bug repair v2 run {datetime.now().isoformat()} — "
            f"{len(hit)} found, {len(reopened)} reopened, {voided} voided\n")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════
#  SP — the setup MM described, recorded live so it can prove itself
# ══════════════════════════════════════════════════════════════════════════
#
#  MM, 13/09: "when the price reaches a certain level that is when indicators,
#  candlesticks and price action should all be clubbed ... if there is a
#  reversal candle at the top or bottom of the chart and indicator also shows
#  an overbought zone or oversold zone, thats when you place a trade."
#
#  Two weeks of testing settled three things about that idea, and this tab is
#  built on those three and nothing else:
#
#  1. THE LEVEL MATTERS, AND IT IS NOT THE BOLLINGER BAND.
#     Two million tests asked: when price reaches a level, does it turn away
#     without cutting through? Against a random price nearby, which turns 30%
#     of the time -
#         floor pivot S1/R1      50%        the 20 EMA            47%
#         yesterday's high/low   50%        range extreme         45%
#         VWAP                   47%        twice-turned zone     42%
#         Bollinger band         25%   <-  WORSE than random
#     So the tab watches the levels price actually respects. Bollinger, which
#     the first version of this idea was built on, is not one of them.
#
#  2. THE TARGET SHOULD NOT BE CAPPED.
#     Same signal, thirteen exits. Taking profit at twice the risk made
#     Rs 8.5 lakh on Rs 50 lakh; no target at all with a stop trailing 3x ATR
#     made Rs 13.5 lakh. The win rate FELL from 41% to 38% and the money went
#     up, because the biggest winner went from 9.8% to 39.2%.
#
#  3. RECORD EVERYTHING, FILTER LATER.
#     The SMC tab was tightened from two touches to three on a backtest, the
#     calls stopped, and now there is no live data to settle whether three was
#     right. That mistake is not repeated here: every call stores its level,
#     its candle, and the RSI, %B and stochastic readings at the moment it
#     fired - WITHOUT filtering on any of them. In three months MM's own
#     record answers "does the indicator help", "which candle", "which level",
#     instead of another backtest guessing.
#
#  NO MONEY IS DEPLOYED. Nothing here touches the Rs 50 lakh book or any P&L
#  tile. It is a recorder, exactly like the SMC tab, because the 15% a year
#  this showed in testing was the best of seventy-two combinations and that is
#  how the last two false starts began.

SP = []                         # every SP call this session
SP_LOG = os.path.join(DATA, "sp_log.csv")
# PAST CALLS, and why they live in their own file.
#
# MM, 17/09: "you can put past calls and store everything in SP". Fair - the
# tab had nothing in it and nothing to look at.
#
# But they are kept OUT of sp_log.csv, deliberately. The whole reason the SP
# tab exists is that in three months MM's own LIVE record settles whether the
# idea works, instead of another backtest. Pour a year of replayed calls into
# that same file and the live record can never answer anything again: 6,000
# replayed rows would drown the 150 a day that are real, and no one would be
# able to tell which was which.
#
# So history is read-only, generated once from exchange candles, and shown on
# its own. The desk NEVER writes to it.
SP_HIST_LOG = os.path.join(DATA, "sp_history.csv")
SP_HIST = []
SP_FIELDS = ["date", "created_hm", "symbol", "side", "level_kind", "level",
             "candle", "entry", "sl", "sl0", "risk0", "atr", "trail_mult",
             "rsi", "pctb", "stoch", "vol", "hour_of_day", "status", "r",
             "peak_r", "triggered_on", "triggered_at", "closed_on",
             "closed_at", "exit_px", "bar_ts", "fo", "reasons"]

SP_TRAIL = 3.0                  # the stop follows 3x ATR behind the best price
SP_STOP_ATR = 1.5               # and starts no tighter than 1.5x ATR
SP_HOLD_BARS = 30               # hours, then the trade is closed at the close
SP_RANGE_LOOK = 40              # bars that define "the top or bottom"
SP_TOUCH = 0.10                 # how close to the level counts as "at" it

# WHICH LEVELS ARE WATCHED, and why it is three rather than six.
#
# All six beat a random price on the reaction test. But running all six fires
# 433 times a day across the universe, and cutting to the best three gives 216
# for an average of +0.110 against +0.117 - half the noise for a twentieth of
# the edge.
#
# What was NOT done, and the reason matters: tightening further. Keeping only
# the eight strongest candles at the best three levels gives a comfortable 99
# calls a day - and the average COLLAPSES to +0.050. Being choosy makes this
# worse, not better. The edge is thin and spread across many trades rather
# than concentrated in the best-looking ones, which is exactly what the SMC
# work found when the same question was asked there. So the tab records a lot
# of small edges rather than a few large ones, because that is what is there.
SP_LEVELS_ON = ("PIVOT S2/R2", "PREV DAY")

# HOW MANY CALLS A DAY A PERSON CAN ACTUALLY LOOK AT.
#
# MM, 17/09: "i was told i will get approx 6 calls per day. what happened to
# that?" He was right to ask. Measured over a year of real bars, the settings
# as they stood produced 228 a day across the 350 stocks the desk scans. That
# is not a call list, it is a firehose, and nobody can read it.
#
# Two changes bring it to about SEVEN a day:
#
#   the RANGE EDGE level is off. Of the three, it is the weakest in the
#   2-million-touch study - price reverses there 45% of the time against 50%
#   at a floor pivot or yesterday's extreme - and it was producing most of
#   the volume. That study measured how often price REVERSES at a level,
#   which is a fact about levels and is not touched by the fill bug, so it is
#   still safe to lean on.
#
#   the CLOSE has to finish near the level, not merely the wick. SP_TOUCH
#   already asks the bar's low or high to reach the level; this asks the
#   close to end up within a quarter of an ATR of it as well. A bar that
#   spikes through a level and closes far away is not a bar that respected
#   the level, whatever its wick did.
#
# SAID PLAINLY: this is for USABILITY, not performance. Once gap fills are
# honest the setup adds about nothing over simply owning the stock, and every
# selectivity test has moved the average the wrong way. Do not read seven a
# day as seven good trades a day.
SP_CLOSE_NEAR = 0.25            # close must end within 0.25 ATR of the level


def sp_volr(hours, i, look=20):
    """How heavy was this hour against the 20 hours BEFORE it.

    Before, not including - putting the bar into its own average drags the
    average up towards it and makes a big bar look ordinary.

    RECORDED, NOT FILTERED ON, and that is a measurement not an oversight.
    Splitting a year of real calls by this number:

        no filter      239 a day   +0.092 per trade   +22.0 a day in total
        only >= 2x      21 a day   +0.190 per trade    +4.1 a day in total

    The per-trade number looks better and the total is a quarter of the size,
    because 90% of the trades are gone. Worse, it does not hold: >= 2x gives
    +0.329 in the first half of the year and +0.035 in the second. And the
    QUIETEST bucket - under half the average volume - is the best single
    bucket of the six at +0.199, which is the opposite of the volume story.
    Same answer every other filter gave on this setup.

    So it goes on the card as a number MM can watch, and the live record can
    overturn this later if it disagrees.
    """
    prev = [h.get("volume") or 0 for h in hours[max(0, i - look):i]]
    prev = [v for v in prev if v > 0]
    if len(prev) < 10:
        return None
    av = sum(prev) / len(prev)
    if av <= 0:
        return None
    return (hours[i].get("volume") or 0) / av


def sp_ema(vals, n):
    k = 2.0 / (n + 1)
    out, cur = [], None
    for v in vals:
        cur = v if cur is None else (v - cur) * k + cur
        out.append(cur)
    return out


def sp_rsi(vals, n=14):
    """Wilder's RSI — checked against his own published example to 2 decimals."""
    out = [None] * len(vals)
    if len(vals) <= n:
        return out
    g = l_ = 0.0
    for i in range(1, n + 1):
        d = vals[i] - vals[i - 1]
        g += max(d, 0.0)
        l_ += max(-d, 0.0)
    ag, al = g / n, l_ / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(vals)):
        d = vals[i] - vals[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def sp_stoch(bars, n=14, smooth=3):
    raw = [None] * len(bars)
    for i in range(n - 1, len(bars)):
        w = bars[i - n + 1:i + 1]
        hh = max(x["high"] for x in w)
        ll = min(x["low"] for x in w)
        raw[i] = 50.0 if hh == ll else 100 * (bars[i]["close"] - ll) / (hh - ll)
    out = [None] * len(bars)
    for i in range(len(bars)):
        w = [x for x in raw[max(0, i - smooth + 1):i + 1] if x is not None]
        if len(w) == smooth:
            out[i] = sum(w) / smooth
    return out


def sp_bb(vals, n=20, k=2.0):
    import statistics as _st
    up, lo = [None] * len(vals), [None] * len(vals)
    for i in range(n - 1, len(vals)):
        w = vals[i - n + 1:i + 1]
        m = _st.fmean(w)
        s = _st.pstdev(w)
        up[i], lo[i] = m + k * s, m - k * s
    return up, lo


def sp_atr(bars, n=14):
    import statistics as _st
    out = [None] * len(bars)
    tr = []
    for i, b in enumerate(bars):
        if i == 0:
            tr.append(b["high"] - b["low"])
        else:
            pc = bars[i - 1]["close"]
            tr.append(max(b["high"] - b["low"], abs(b["high"] - pc),
                          abs(b["low"] - pc)))
        if i >= n:
            out[i] = _st.fmean(tr[i - n + 1:i + 1])
    return out


def sp_vwap(bars):
    """Rolling VWAP, restarted each session — the line institutions judge
    their own fills against, which is why price keeps coming back to it."""
    out = [None] * len(bars)
    day, pv, vol = None, 0.0, 0.0
    for i, b in enumerate(bars):
        d = str(b["date"])[:10]
        if d != day:
            day, pv, vol = d, 0.0, 0.0
        v = b.get("volume") or 0
        pv += (b["high"] + b["low"] + b["close"]) / 3 * v
        vol += v
        out[i] = (pv / vol) if vol else None
    return out


def sp_pivots(bars):
    """Classic floor pivots from the PREVIOUS session. Computed once a day
    from a day that has already finished, so nothing here is hindsight."""
    out = [None] * len(bars)
    day, cur, levels = None, [], None
    for i, b in enumerate(bars):
        d = str(b["date"])[:10]
        if d != day:
            if cur:
                h = max(x["high"] for x in cur)
                l = min(x["low"] for x in cur)
                c = cur[-1]["close"]
                p = (h + l + c) / 3
                levels = {"P": p, "R1": 2*p - l, "S1": 2*p - h,
                          "R2": p + (h - l), "S2": p - (h - l)}
            day, cur = d, []
        cur.append(b)
        out[i] = levels
    return out


def sp_candle(bars, i, up):
    """Every reversal candle that completes ON bar i, most telling first.
    Returns a name or None. Nothing after bar i is looked at."""
    if i < 2:
        return None
    b, p, q = bars[i], bars[i - 1], bars[i - 2]
    o, h, l, c = b["open"], b["high"], b["low"], b["close"]
    rng = h - l
    if rng <= 0:
        return None
    body = abs(c - o)
    upw = h - max(o, c)
    low = min(o, c) - l
    pbody = abs(p["close"] - p["open"])
    small = body <= 0.35 * rng

    # THE WICK ON THE WRONG SIDE, and why this guard sits above everything.
    #
    # MM, 17/09, on being walked through a real trade: "WHY DO I HAVE THIS
    # FEELING THERE IS SOME MISTAKE IN IT." There was.
    #
    # HDFCBANK, 16/10/25 11:15 — body 0.55, upper wick 0.05, LOWER WICK 2.05.
    # Price fell to 982.40 and buyers drove it back to close at 984.45. That
    # is buyers rejecting lower prices. The desk sold it short and called it a
    # BEARISH BELT HOLD. ICICIBANK, 22/12/25 — body 0.30, lower wick 3.90 —
    # sold short as a TWEEZER TOP.
    #
    # The first attempt at this fix patched the belt hold and the tweezer. It
    # was not enough: HDFCBANK simply fell through to BEARISH HARAMI, which
    # has the same hole. The rule is not about one pattern, so it belongs
    # here, above all of them:
    #
    #     a SHORT signal cannot come from a bar whose LOWER wick dominates
    #     a LONG signal cannot come from a bar whose UPPER wick dominates
    #
    # "Dominates" means longer than the body AND longer than the wick on the
    # other side. That leaves the hammer and the dragonfly doji alone, which
    # ARE defined by a long lower wick and are correctly bullish.
    if up and upw > body and upw > low:
        return None
    if (not up) and low > body and low > upw:
        return None

    if up:
        if (q["close"] < q["open"] and pbody <= 0.4 * (p["high"] - p["low"])
                and p["close"] < q["close"] and c > o
                and c > (q["open"] + q["close"]) / 2):
            return "MORNING STAR"
        if (c > o and p["close"] < p["open"] and c >= p["open"]
                and o <= p["close"] and body > pbody):
            return "BULLISH ENGULFING"
        if low >= 2 * body and upw <= body and small:
            return "HAMMER"
        if (c > o and p["close"] < p["open"] and o < p["low"]
                and c > (p["open"] + p["close"]) / 2 and c < p["open"]):
            return "PIERCING LINE"
        # A BULLISH candle must not be dominated by an UPPER wick. If sellers
        # pushed it back down from a high, that is rejection of higher prices
        # - a shooting-star shape - and buying it is backwards. Found 17/09
        # by tracing a real trade: HDFCBANK's mirror case had a 0.55 body
        # against a 2.05 opposite wick and was traded the wrong way.
        if upw <= body:
            if (p["close"] < p["open"] and abs(l - p["low"]) <= 0.05 * rng
                    and c > o):
                return "TWEEZER BOTTOM"
            # A belt hold is a LONG body opening at the extreme and running.
            # The old rule asked only for a green bar with no lower wick and
            # never checked the body at all.
            if c > o and low <= 0.02 * rng and upw > 0 and body >= 0.5 * rng:
                return "BULLISH BELT HOLD"
        if (c > o and p["close"] < p["open"] and o >= p["close"]
                and c <= p["open"] and body < pbody):
            return "BULLISH HARAMI"
        if body <= 0.08 * rng and low >= 2 * upw:
            return "DRAGONFLY DOJI"
    else:
        if (q["close"] > q["open"] and pbody <= 0.4 * (p["high"] - p["low"])
                and p["close"] > q["close"] and c < o
                and c < (q["open"] + q["close"]) / 2):
            return "EVENING STAR"
        if (c < o and p["close"] > p["open"] and c <= p["open"]
                and o >= p["close"] and body > pbody):
            return "BEARISH ENGULFING"
        if upw >= 2 * body and low <= body and small:
            return "SHOOTING STAR"
        if (c < o and p["close"] > p["open"] and o > p["high"]
                and c < (p["open"] + p["close"]) / 2 and c > p["open"]):
            return "DARK CLOUD COVER"
        # A BEARISH candle must not be dominated by a LOWER wick. If buyers
        # pushed it back up off a low, that is rejection of lower prices - a
        # hammer shape - and shorting it is backwards. This is the bug MM's
        # instinct found: HDFCBANK 16/10/25, body 0.55, lower wick 2.05, sold
        # as a "BEARISH BELT HOLD"; ICICIBANK 22/12/25, body 0.30, lower wick
        # 3.90, sold as a "TWEEZER TOP".
        if low <= body:
            if (p["close"] > p["open"] and abs(h - p["high"]) <= 0.05 * rng
                    and c < o):
                return "TWEEZER TOP"
            # A belt hold is a LONG body opening at the extreme and running.
            # The old rule asked only for a red bar with no upper wick and
            # never checked the body at all.
            if c < o and upw <= 0.02 * rng and low > 0 and body >= 0.5 * rng:
                return "BEARISH BELT HOLD"
        if (c < o and p["close"] > p["open"] and o <= p["close"]
                and c >= p["open"] and body < pbody):
            return "BEARISH HARAMI"
        if body <= 0.08 * rng and upw >= 2 * low:
            return "GRAVESTONE DOJI"
    return None


# How often price turned away at each level rather than cutting through it,
# measured over 2 million touches. A random price scores 30%. These numbers
# are shown on the card so MM can see WHY the level was worth watching.
SP_LEVEL_SCORE = {"PIVOT S2/R2": 50, "PIVOT S1/R1": 50, "PREV DAY": 50,
                  "EMA 20": 47, "VWAP": 47, "RANGE EDGE": 45}


def sp_level(bars, i, ctx, up):
    """Has price ARRIVED at a level worth watching? Returns
    (name, level price, the far side of it) or None.

    Only the levels that beat a random price in testing are here. The
    Bollinger band is deliberately absent - it scored 25% against a random
    price's 30%, which makes it worse than no level at all."""
    b = bars[i]
    a = ctx["atr"][i]
    if not a or a <= 0:
        return None
    # HOW CLOSE IS "AT" THE LEVEL. Measured, not guessed. At a quarter of an
    # ATR the tab produced about 700 calls a day across the universe - useless.
    # A tenth gives 433 and, more to the point, a BETTER average per trade
    # (+0.117 against +0.087), because the loose version was letting in bars
    # that were never really at the level.
    near = SP_TOUCH * a

    def touch(v, name):
        if v is None:
            return None
        if up and abs(b["low"] - v) <= near:
            return (name, v, min(b["low"], v) - 0.25 * a)
        if (not up) and abs(b["high"] - v) <= near:
            return (name, v, max(b["high"], v) + 0.25 * a)
        return None

    def on(nm):
        return nm in SP_LEVELS_ON

    piv = ctx["piv"][i]
    if piv:
        for key, nm in ((("S2" if up else "R2"), "PIVOT S2/R2"),
                        (("S1" if up else "R1"), "PIVOT S1/R1")):
            if not on(nm):
                continue
            got = touch(piv[key], nm)
            if got:
                return got
    pv = ctx["prevday"][i]
    if pv and on("PREV DAY"):
        got = touch(pv[1] if up else pv[0], "PREV DAY")
        if got:
            return got
    if on("VWAP"):
        got = touch(ctx["vwap"][i - 1] if i else None, "VWAP")
        if got:
            return got
    if on("EMA 20"):
        got = touch(ctx["e20"][i - 1] if i else None, "EMA 20")
        if got:
            return got
    if not on("RANGE EDGE"):
        return None
    w = bars[max(0, i - SP_RANGE_LOOK):i]
    if len(w) >= 20:
        hh = max(x["high"] for x in w)
        ll = min(x["low"] for x in w)
        if hh > ll:
            pos = (b["close"] - ll) / (hh - ll)
            if up and pos <= 0.15:
                return ("RANGE EDGE", ll, min(b["low"], ll) - 0.25 * a)
            if (not up) and pos >= 0.85:
                return ("RANGE EDGE", hh, max(b["high"], hh) + 0.25 * a)
    return None


def sp_ctx(bars):
    c = [b["close"] for b in bars]
    up, lo = sp_bb(c)
    prev, day, cur, last = [None] * len(bars), None, [], None
    for i, b in enumerate(bars):
        d = str(b["date"])[:10]
        if d != day:
            if cur:
                last = (max(x["high"] for x in cur), min(x["low"] for x in cur))
            day, cur = d, []
        cur.append(b)
        prev[i] = last
    return {"atr": sp_atr(bars), "rsi": sp_rsi(c), "stoch": sp_stoch(bars),
            "bbu": up, "bbl": lo, "e20": sp_ema(c, 20), "vwap": sp_vwap(bars),
            "piv": sp_pivots(bars), "prevday": prev, "close": c}


def sp_swings(bars, n=3):
    hi, lo = [], []
    for i in range(n, len(bars) - n):
        w = bars[i - n:i + n + 1]
        if bars[i]["high"] == max(x["high"] for x in w):
            hi.append((i + n, bars[i]["high"]))
        if bars[i]["low"] == min(x["low"] for x in w):
            lo.append((i + n, bars[i]["low"]))
    return hi, lo


def sp_key(c):
    # Keyed on the CANDLE it fired from, not on the entry price. Two scans of
    # the same hour drift a few paise apart and used to produce two calls off
    # one shooting star - the same idea counted twice, and double the risk if
    # both were taken. (MM spotted this on PIRAMALFIN, 18/09.)
    return (c["symbol"], c["side"], c["level_kind"],
            str(c.get("bar_ts") or c.get("date")))


# ── the picture behind an SP call (21/09) ─────────────────────────────────
# MM tapped PNB in the SP tab and nothing happened. The SP cards had never
# been given a chart - only SMC and Formations had one - and I had never said
# so. The sixty hourly bars leading up to the call are now kept with it, out
# of the SAME bars the scan ran on, so the picture and the numbers can never
# disagree. Calls made before today have no bars and the card says so rather
# than drawing something invented.
SP_BARS = {}
SP_BARS_FILE = os.path.join(DATA, "sp_bars.json")
SP_KEEP = 60


def sp_kid(c):
    return "|".join(str(x) for x in sp_key(c))


def sp_bars_save():
    try:
        json.dump(SP_BARS, open(SP_BARS_FILE, "w", encoding="utf8"))
    except Exception as e:
        print(f"  [SP] could not save the charts: {e}")


def sp_bars_load():
    global SP_BARS
    try:
        if os.path.exists(SP_BARS_FILE):
            SP_BARS = json.load(open(SP_BARS_FILE, encoding="utf8"))
            print(f"  {len(SP_BARS)} SP charts already on record.")
    except Exception as e:
        print(f"  (could not read the SP charts: {e})")


def sp_keep_bars(c, hours):
    """The run-up to the call, as the exchange sent it."""
    if not c or not hours:
        return
    try:
        i = c.get("i")
        end = (i + 1) if isinstance(i, int) else len(hours)
        end = max(1, min(end, len(hours)))
        seg = hours[max(0, end - SP_KEEP):end]
        SP_BARS[sp_kid(c)] = [[round(float(b["open"]), 2),
                               round(float(b["high"]), 2),
                               round(float(b["low"]), 2),
                               round(float(b["close"]), 2)] for b in seg]
    except Exception:
        pass


def sp_add(c, hours=None):
    if not c:
        return False
    k = sp_key(c)
    for x in SP:
        if sp_key(x) == k:
            return False
    sp_keep_bars(c, hours)
    SP.append(c)
    return True


def sp_scan_one(sym, hours, fo=False):
    """One stock, one look. Returns a call or None.

    Fires on the LAST COMPLETED hour only. An hour still forming has a high,
    a low and a close that are all still moving, so a candle can appear on it
    and be gone twenty minutes later - the same repainting that made the SMC
    tab flash HEROMOTOCO twice in a quarter of an hour on 09/09.
    """
    if len(hours) < 60:
        return None
    ctx = sp_ctx(hours)
    i = len(hours) - 1
    a = ctx["atr"][i]
    if not a or a <= 0:
        return None
    hi, lo = sp_swings(hours)
    for up in (True, False):
        # MM, 21/09: "only short fo names and you can short equites for trades
        # which we can square off intraday." SP does not square off intraday -
        # it carries on a trailing stop - so a short here needs a futures
        # contract behind it. Without one the call is not takeable, and an
        # untakeable call in the record is worse than no call at all.
        if (not up) and not fo:
            continue
        cname = sp_candle(hours, i, up)
        if not cname:
            continue
        got = sp_level(hours, i, ctx, up)
        if not got:
            continue
        lname, lvl, far = got
        px = hours[i]["close"]
        # the CLOSE has to respect the level too, not just the wick
        if abs(px - lvl) > SP_CLOSE_NEAR * a:
            continue
        band = px - SP_STOP_ATR * a if up else px + SP_STOP_ATR * a
        stop = min(far, band) if up else max(far, band)
        # never leave the stop hanging in open space - put it beyond the
        # nearest swing point on that side
        sw = [p for k, p in (lo if up else hi) if k <= i]
        if sw:
            nr = (max([p for p in sw if p < px], default=None) if up
                  else min([p for p in sw if p > px], default=None))
            if nr is not None:
                stop = (min(stop, nr - 0.1 * a) if up else max(stop, nr + 0.1 * a))
        risk = abs(px - stop)
        if risk <= 0 or risk / px > 0.12:
            continue
        r = ctx["rsi"][i]
        s = ctx["stoch"][i]
        bu, bl = ctx["bbu"][i], ctx["bbl"][i]
        pctb = (100 * (px - bl) / (bu - bl)) if (bu and bl and bu > bl) else None
        vr = sp_volr(hours, i)
        ts = str(hours[i].get("date", ""))[:16].replace("T", " ")
        hm = ts[11:16]
        score = SP_LEVEL_SCORE.get(lname, 30)
        # WHAT GOES ON THE CARD, and what does not.
        #
        # MM's rule, 17/09: the dashboard shows the trade. The research behind
        # it - hit rates, backtest percentages, what did or did not survive a
        # split-half test - is a conversation between him and me and belongs
        # in chat, not on a page he looks at while trading. So every line
        # below describes THIS trade: where price is, what closed, where the
        # stop is, what the readings were. No percentages from testing.
        why = [
            f"price {'fell to' if up else 'rose to'} the {lname} at "
            f"{lvl:,.2f}",
            f"a {cname} closed on that hour",
            (f"RSI {r:.0f}" if r is not None else "RSI n/a") +
            (f" · stochastic {s:.0f}" if s is not None else "") +
            (f" · %B {pctb:.0f}" if pctb is not None else "") +
            (f" · volume {vr:.1f}x its usual for this hour"
             if vr is not None else ""),
            f"stop {stop:,.2f}, {100*risk/px:.2f}% away, placed beyond the "
            f"level and beyond the nearest swing point",
            f"no fixed target — the stop follows {SP_TRAIL}x the hour's "
            f"average range behind the best price reached",
        ]
        return {
            "date": date.today().isoformat(),
            "created_hm": datetime.now().strftime("%H:%M"),
            "symbol": sym, "side": "LONG" if up else "SHORT",
            "level_kind": lname, "level": round(lvl, 2), "candle": cname,
            "entry": round(px, 2),
            # sl MOVES — it is the trailing stop and is rewritten every hour.
            # sl0 is where it started, and is never touched again, because
            # "how far away was the stop when you took it" is a question the
            # record has to be able to answer months later.
            "sl": round(stop, 2), "sl0": round(stop, 2),
            "risk0": round(risk, 4), "atr": round(a, 4),
            "trail_mult": SP_TRAIL,
            "rsi": round(r, 1) if r is not None else None,
            "pctb": round(pctb, 1) if pctb is not None else None,
            "stoch": round(s, 1) if s is not None else None,
            "vol": round(vr, 2) if vr is not None else None,
            "hour_of_day": hm, "status": "LIVE", "r": None, "peak_r": 0.0,
            "triggered_on": date.today().isoformat(),
            "triggered_at": datetime.now().strftime("%H:%M"),
            "closed_on": "", "closed_at": "", "exit_px": None,
            "bar_ts": ts, "fo": bool(fo),
            "reasons": " · ".join(why),
        }
    return None


def sp_resolve(sym, hours):
    """Settle every open SP call on this stock from the candles themselves.

    The entry is taken AT the signal bar's close, so the trade is live from
    the next hour. The trailing stop is recomputed bar by bar from the best
    price reached since — and, as everywhere else in this desk, a bar that
    reaches both the stop and a new high is scored the bad way round.
    """
    if not hours:
        return
    idx = {str(b.get("date", ""))[:16].replace("T", " "): n
           for n, b in enumerate(hours)}
    today = date.today().isoformat()
    for c in SP:
        if c.get("symbol") != sym or c.get("status") != "LIVE":
            continue
        i = idx.get(c.get("bar_ts") or "")
        if i is None:
            continue
        long = c["side"] == "LONG"
        entry, a = c["entry"], c["atr"]
        stop = c["sl"]
        best = entry
        for j in range(i + 1, len(hours)):
            x = hours[j]
            ts = str(x.get("date", ""))[:16].replace("T", " ")
            d, hm = ts[:10], ts[11:16]
            if (long and x["low"] <= stop) or ((not long) and x["high"] >= stop):
                # WHERE YOU ARE ACTUALLY FILLED WHEN THE STOP IS HIT.
                #
                # Not always at the stop. A stop order resting in the market
                # is triggered at the open if the stock has already gapped
                # past it overnight, and you get the OPEN, which is worse -
                # sometimes far worse. 91% of these trades are held
                # overnight, so this is the ordinary case, not a rare one.
                #
                # This was found and fixed in the SMC engine in September
                # (test_gap.py, 47 tests). SP was written later and never got
                # the fix. Measured on 58,033 trades, assuming the stop price
                # was worth +0.089% a trade of pure fiction: with honest
                # fills the setup's edge over simply owning the stock falls
                # from +0.089% to -0.002%, and its t-statistic from 12.1 to
                # -0.3. The entire measured edge was this bug.
                px = stop
                gapped = ((x["open"] < stop) if long else (x["open"] > stop))
                if gapped:
                    px = x["open"]
                got = round(((px - entry) if long else (entry - px))
                            / c["risk0"], 2)
                # The stop that ends a trade is not always a loss. Once it has
                # trailed past the entry it is taking PROFIT, and calling that
                # "STOPPED" makes every winner read like a failure on the card.
                c["status"] = "TRAILED OUT" if got > 0 else "STOPPED"
                c["exit_px"] = round(px, 2)
                c["r"] = got
                c["closed_on"], c["closed_at"] = d, hm
                break
            best = max(best, x["high"]) if long else min(best, x["low"])
            c["peak_r"] = round(((best - entry) if long else (entry - best))
                                / c["risk0"], 2)
            ns = best - SP_TRAIL * a if long else best + SP_TRAIL * a
            stop = max(stop, ns) if long else min(stop, ns)
            c["sl"] = round(stop, 2)
            if j - i >= SP_HOLD_BARS:
                c["status"] = "TIME OUT"
                c["exit_px"] = round(x["close"], 2)
                c["r"] = round(((x["close"] - entry) if long
                                else (entry - x["close"])) / c["risk0"], 2)
                c["closed_on"], c["closed_at"] = d, hm
                break


def sp_save():
    try:
        with open(SP_LOG, "w", newline="", encoding="utf8") as f:
            w = csv.DictWriter(f, fieldnames=SP_FIELDS)
            w.writeheader()
            for c in SP:
                w.writerow({k: ("" if c.get(k) is None else c.get(k))
                            for k in SP_FIELDS})
    except Exception as e:
        print(f"  [SP] could not save the log: {e}")


def _sp_read(path, into, what):
    if not os.path.exists(path):
        return
    try:
        with open(path, newline="", encoding="utf8") as f:
            for row in csv.DictReader(f):
                for k in ("entry", "sl", "sl0", "risk0", "atr", "level", "r",
                          "peak_r", "rsi", "pctb", "stoch", "exit_px",
                          "trail_mult", "vol"):
                    v = row.get(k)
                    row[k] = float(v) if v not in (None, "") else None
                row["fo"] = str(row.get("fo")).lower() in ("true", "1")
                into.append(row)
        print(f"  [SP] {len(into)} {what} loaded")
    except Exception as e:
        print(f"  [SP] could not read {what}: {e}")


def sp_load():
    _sp_read(SP_LOG, SP, "live calls")
    # Read-only. Nothing in this file ever writes back to sp_history.csv.
    _sp_read(SP_HIST_LOG, SP_HIST, "past calls")


def smc_watcher(kite, scanner):
    """Intraday scan a couple of minutes after each 15-minute bar closes;
    the positional scan once, after the close."""
    done_positional = None
    while True:
        try:
            now = datetime.now()
            mins = now.hour * 60 + now.minute
            if now.weekday() < 5:
                if (9 * 60 + 32) <= mins <= (15 * 60 + 10) and now.minute % 15 in (2, 3):
                    smc_intraday(kite, scanner)
                if mins >= (15 * 60 + 40) and done_positional != date.today():
                    smc_positional(scanner, kite)
                    done_positional = date.today()
            smc_track()
            smc_expire_positional()
            # A call whose price is already through its stop but whose row
            # still says open. Once a minute, all session - see
            # close_passed_stop() for why this is needed and why it reads the
            # live price rather than the recorded low.
            if now.weekday() < 5 and (9 * 60 + 20) <= mins <= (15 * 60 + 30):
                track_since_fill()
                close_passed_stop()
            smc_save()
            sp_save()
            smc_bars_save()
            sp_bars_save()
        except Exception as e:
            print(f"  [SMC] watcher error: {e}")
        time.sleep(60)


def swing_watcher_full(scanner, T):
    """Sweep the whole market for swing setups every 10 minutes, all session."""
    while True:
        now = datetime.now()
        if now.weekday() < 5 and (9 * 60 + 20) <= (now.hour * 60 + now.minute) <= (15 * 60 + 50):
            try:
                swing_sweep(scanner, T)
            except Exception as e:
                print(f"  [Swing] sweep failed: {e}")
        time.sleep(600)


# ── the evening swing scan ─────────────────────────────────────────────────
# The five setups do not all fire the same way. Four are intraday and appear
# during the session. The fifth, BREAKOUT-20D, is judged on the DAY'S CLOSING
# candle - which does not exist until the market shuts.
#
# So the desk does that scan itself, once, after the close, while it is still
# running and still logged in. Otherwise the swing half of the record depends
# on somebody remembering to run a second program every evening, and a record
# with holes in it is worth very little.
SWING_AT = (15, 45)         # after the close, before the session is over
_swing_done = None


def evening_scan(scanner, T):
    """Run the swing setup across the engine's own daily cache."""
    global _swing_done
    cache = dict(getattr(scanner, "daily", {}) or {})
    if len(cache) < 100:
        print(f"  [Swing] only {len(cache)} stocks cached — too early, skipping today")
        return
    rows, checked = [], 0
    stamp = datetime.now().strftime("%H:%M")
    for sym, blob in cache.items():
        bars = blob.get("d") if isinstance(blob, dict) else blob
        if not bars or len(bars) < 60:
            continue
        last = bars[-1]
        if not last.get("close") or last["close"] < 20:
            continue
        v20 = bars[-20:]
        turn = sum(b["close"] * (b.get("volume") or 0) for b in v20) / 20 / 1e7
        if turn < 5:
            continue
        checked += 1
        try:
            reg = T.daily_regime(bars)
            c = T.setup_breakout20(sym, "EQ", reg, bars)
        except Exception:
            continue
        if not c:
            continue
        if adopt(scanner, T, c) is None:
            continue          # the engine's own rules turned it down
        why = c.get("reasons")
        rows.append({
            # The day the CALL was made, not the day of the candle it was read
            # from. A 20-day breakout is computed from the previous close, and
            # filing it under that date put today's live calls into yesterday's
            # record - the dashboard showed nothing for today while quietly
            # growing a day that had already finished.
            "date": date.today().isoformat(),
            "created_hm": stamp, "symbol": sym, "seg": "EQ",
            "setup": c["setup"], "side": c["side"], "tf": "SWING",
            "score": c["score"], "conviction": c["conviction"],
            "entry": c["entry"], "sl": c["sl"], "t1": c["t1"], "t2": c["t2"],
            "rr": c["rr"], "risk0": c["risk0"], "status": "WAITING", "r": None,
            "triggered_at": "", "closed_at": "",
            "horizon": c.get("horizon", "3-10 sessions"), "level": c.get("level", ""),
            "reasons": " · ".join(why) if isinstance(why, (list, tuple)) else (why or ""),
        })
    fresh = merge(rows)
    _swing_done = date.today()
    print(f"\n  [Swing] {date.today()} — checked {checked} tradable stocks, "
          f"{len(rows)} calls, {len(fresh)} new.")
    for r in sorted(rows, key=lambda x: -x["score"])[:12]:
        print(f"     {r['symbol']:<12} {r['side']:<6} entry {r['entry']:>9.2f}  "
              f"SL {r['sl']:>9.2f}  T1 {r['t1']:>9.2f}  T2 {r['t2']:>9.2f}  "
              f"score {r['score']}")
    if len(rows) > 12:
        print(f"     ... and {len(rows)-12} more, all on the dashboard.")


def swing_watcher(scanner, T):
    while True:
        now = datetime.now()
        due = (now.hour, now.minute) >= SWING_AT and now.weekday() < 5
        if due and _swing_done != date.today():
            try:
                evening_scan(scanner, T)
            except Exception as e:
                print(f"  [Swing] scan failed: {e}")
        time.sleep(60)


# ── the web page ───────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                                    # keep the console for the engine

    def _send(self, body, ctype):
        b = body.encode("utf8") if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(b)
        except Exception:
            pass

    def do_GET(self):
        if self.path.startswith("/api/indices"):
            self._send(json.dumps({"indices": _idx["rows"], "at": _idx["at"]}),
                       "application/json")
            return
        if self.path.startswith("/api/sp_bars"):
            # The bars behind one SP call. Same shape the Formations chart
            # already draws, so the pop-up code is shared rather than copied.
            from urllib.parse import urlparse, parse_qs, unquote
            q = parse_qs(urlparse(self.path).query)
            kk = unquote((q.get("k") or [""])[0])
            row = next((c for c in (SP + SP_HIST) if sp_kid(c) == kk), None)
            self._send(json.dumps({"bars": SP_BARS.get(kk, []),
                                   "call": row or {}}), "application/json")
            return
        if self.path.startswith("/api/smc_bars"):
            from urllib.parse import urlparse, parse_qs, unquote
            q = parse_qs(urlparse(self.path).query)
            k = unquote((q.get("k") or [""])[0])
            row = next((c for c in SMC if smc_kid(c) == k), None)
            self._send(json.dumps({"bars": SMC_BARS.get(k, []),
                                   "call": row or {}}), "application/json")
            return
        if self.path.startswith("/api/audit"):
            # THE ALARM, MOVED SOMEWHERE MM WILL ACTUALLY HEAR IT (09/09).
            #
            # audit_record() has caught every record bug it was built for -
            # including the 210 rows that closed before they opened. It caught
            # them the morning they appeared. But it printed the warning into
            # the console window, which MM does not read and has told me he
            # does not read. An alarm nobody hears is not a safety net.
            #
            # The same check now answers over HTTP, so the dashboard can put a
            # red banner across the top of the page the moment anything
            # impossible is in the record - next to the P&L it makes untrue.
            try:
                bad = audit_record(quiet=True)
                # AND THE ONE THAT ONLY THE LIVE PRICE CAN SEE (09/09).
                # audit_record() reads the record alone, so it cannot know that
                # GRAPHITE was sitting at 858.90 with a target of 828.17 and
                # still open. A call that is past its target or its stop RIGHT
                # NOW is not being watched by anything, and that is invisible
                # in the record itself. 0.25% of slack keeps a price resting
                # exactly on the level from flickering the warning on and off.
                px = _ltp.get("px") or {}
                for r in HISTORY:
                    if not OPEN_ST(r) or r.get("status") == "WAITING":
                        continue
                    q = px.get(r.get("symbol")) or {}
                    p = q.get("ltp")
                    try:
                        t2, sl = float(r["t2"]), float(r["sl"])
                    except (TypeError, ValueError, KeyError):
                        continue
                    if not p:
                        continue
                    long = (r.get("side") or "LONG") == "LONG"
                    if (p >= t2 * 1.0025) if long else (p <= t2 * 0.9975):
                        bad.setdefault(
                            "open, but the price is already past the target", []).append(r)
                    elif (p <= sl * 0.9975) if long else (p >= sl * 1.0025):
                        bad.setdefault(
                            "open, but the price is already past the stop", []).append(r)
                out = [{"name": k, "n": len(v),
                        "days": sorted({(x.get("date") or "") for x in v}),
                        "sample": [f"{x.get('date')} {x.get('symbol')} "
                                   f"{x.get('setup')}" for x in v[:3]]}
                       for k, v in sorted(bad.items(), key=lambda kv: -len(kv[1]))]
                self._send(json.dumps({"problems": out,
                                       "calls": len(HISTORY)}), "application/json")
            except Exception as e:
                self._send(json.dumps({"problems": [], "error": str(e)}),
                           "application/json")
            return
        if self.path.startswith("/api/smc"):
            rows = []
            for c in SMC:
                if "fo" not in c or c.get("fo") is None:
                    fo_tag(c)          # rows loaded from an older log file
                d = dict(c)
                d["kid"] = smc_kid(c)
                d["has_chart"] = smc_kid(c) in SMC_BARS
                px = _ltp["px"].get(c["symbol"]) or {}
                d["ltp"] = px.get("ltp")
                if d["status"] in ("LIVE",) and d.get("entry") and d.get("risk0"):
                    ltp = px.get("ltp")
                    if ltp:
                        mv = (ltp - d["entry"]) if d["side"] == "LONG" else (d["entry"] - ltp)
                        d["r_now"] = round(mv / d["risk0"], 2)
                        d["pct_now"] = round(100 * mv / d["entry"], 2)
                rows.append(d)
            rows.sort(key=lambda r: (r.get("date") or "", r.get("created_hm") or ""),
                      reverse=True)
            self._send(json.dumps({"smc": rows}), "application/json")
            return
        if self.path.startswith("/api/sp"):
            rows = []
            for c in SP:
                d = dict(c)
                px = _ltp["px"].get(c["symbol"]) or {}
                d["ltp"] = px.get("ltp")
                # A running trade shows what it is worth RIGHT NOW and how far
                # the trailing stop has climbed — the two things you actually
                # want to see on an open position.
                if d.get("status") == "LIVE" and d.get("entry") and d.get("risk0"):
                    ltp = px.get("ltp")
                    if ltp:
                        mv = ((ltp - d["entry"]) if d["side"] == "LONG"
                              else (d["entry"] - ltp))
                        d["r_now"] = round(mv / d["risk0"], 2)
                        d["pct_now"] = round(100 * mv / d["entry"], 2)
                        d["locked"] = round(
                            ((d["sl"] - d["entry"]) if d["side"] == "LONG"
                             else (d["entry"] - d["sl"])) / d["risk0"], 2)
                d["kid"] = sp_kid(d)
                d["has_chart"] = d["kid"] in SP_BARS
                rows.append(d)
            rows.sort(key=lambda r: ((r.get("date") or ""),
                                     (r.get("created_hm") or "")), reverse=True)
            # Past calls travel in their own list so the page can never mix
            # them into the live tally by accident.
            hist = sorted(SP_HIST, key=lambda r: ((r.get("date") or ""),
                                                  (r.get("created_hm") or "")),
                          reverse=True)
            for _h in hist:
                try:
                    _h["kid"] = sp_kid(_h)
                    _h["has_chart"] = _h["kid"] in SP_BARS
                except Exception:
                    _h["kid"] = ""
            self._send(json.dumps({"sp": rows, "past": hist}),
                       "application/json")
            return
        if self.path.startswith("/api/watching"):
            # Every idea the engine BUILT and then declined to publish, with
            # complete levels. It has always written these to disk; nothing
            # ever showed them. A near-miss you never see is a near-miss you
            # can never judge - and judging them is the whole point of Q1.
            p = os.path.join(DATA, ".tech_watching.json")
            rows = []
            try:
                store = json.load(open(p, encoding="utf8")) or {}
                for r in (store.get("rows") or {}).values():
                    d = to_row(r)
                    d["status"] = "WATCHING"
                    d["short_by"] = round(max(0.0, (r.get("fire_score") or 6.5)
                                              - (r.get("score") or 0)), 1)
                    rows.append(d)
            except Exception:
                pass
            rows.sort(key=lambda r: (r.get("date") or "", r.get("created_hm") or ""),
                      reverse=True)
            self._send(json.dumps({"watching": rows[:600]}), "application/json")
            return
        if self.path.startswith("/api/backtest"):
            p = os.path.join(DATA, "backtest.json")
            if os.path.exists(p):
                try:
                    self._send(open(p, encoding="utf8").read(), "application/json")
                    return
                except Exception:
                    pass
            self._send("{}", "application/json")
            return
        if self.path.startswith("/api/calls"):
            snap = dict(STATE)
            live = [to_row(c) for c in snap.get("calls", [])]
            merge(live + [to_row(c) for c in snap.get("resolved_today", [])])
            # Live price is attached AFTER merging, never before: the permanent
            # record stores what was decided, not what the screen happened to
            # show a second later.
            # Keyed on the full call identity, not the symbol. Two calls on one
            # stock - a morning pullback and an afternoon squeeze - would
            # otherwise overwrite each other and one would vanish from the page.
            priced = {log_key(r): with_price(dict(r)) for r in live}
            # Every OPEN call is priced, not only the ones the engine still
            # holds. A row the engine has dropped is still shown on the page,
            # and showing it without a price makes a real trade look broken.
            out = []
            for r in HISTORY:
                if OPEN_ST(r):
                    out.append(priced.get(log_key(r)) or with_price(dict(r)))
                else:
                    out.append(r)
            lean = {1: "buyers ahead", -1: "sellers ahead",
                    0: "balanced"}[_flow.get("bias", 0)]
            band = (f"<b>Live.</b><span>Engine running since "
                    f"{STARTED:%H:%M}. {snap.get('universe',0)} instruments watched · "
                    f"last scan {snap.get('ts') or '—'} · "
                    f"large-cap flow {lean} ({_flow.get('score',0):+.2f}) · "
                    f"{len(HISTORY)} calls on record. Virtual money only — "
                    f"nothing is ever executed.</span>")
            st = cap_state()
            self._send(json.dumps({"calls": out, "band": band,
                                   "priced_at": _ltp.get("at"),
                                   "gaps": (_GAPS["rows"]
                                            if _GAPS["day"] == date.today().isoformat()
                                            else []),
                                   "cap": {"limit": DAY_LOSS_CAP,
                                           "used": round(st["used"], 2),
                                           "closed": st["n"],
                                           "halted": st["halted"]}}),
                       "application/json")
            return
        if self.path in ("/", "/index.html"):
            try:
                self._send(open(SITE, encoding="utf8").read(), "text/html; charset=utf-8")
            except Exception as e:
                self._send(f"<pre>Cannot read the dashboard file:\n{SITE}\n{e}</pre>",
                           "text/html; charset=utf-8")
            return
        self.send_response(404)
        self.end_headers()


def serve():
    # 0.0.0.0, not 127.0.0.1, so a phone on the same wifi can open the page.
    # 127.0.0.1 accepts connections only from this PC and nothing else - which
    # is why the dashboard would not load on a phone.
    #
    # What this DOES mean: anyone else on your wifi who types the address can
    # read your dashboard. It is read-only - there is no login, no keys and no
    # way to place a trade through it - but it is your record, so do not run
    # this on a public or shared network.
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.serve_forever()


# ── the market-cap floor ───────────────────────────────────────────────────
MIN_MCAP_CR = 500                 # equities below this are not scanned
MCAP_FILES = ["fundamentals.json", ".fundamentals.json"]


def load_mcaps():
    """Market caps, keyed by symbol, in CRORES.

    Accepts the shape Jenil's dashboard writes:
        {"stocks": [{"name": "RELIANCE", "mcap": 1932000000000}, ...]}
    and a plain {"RELIANCE": 193200} mapping.

    Units are guessed from the numbers themselves rather than assumed: a real
    market cap in rupees is astronomically larger than the same figure in
    crores, so the two cannot be confused. Assuming the wrong one would either
    admit everything or exclude everything, silently.
    """
    for name in MCAP_FILES:
        p = os.path.join(DATA, name)
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding="utf8"))
        except Exception as e:
            print(f"  Could not read {name}: {e}")
            continue
        out = {}
        rows = d.get("stocks") if isinstance(d, dict) else None
        if rows is None and isinstance(d, dict):
            rows = [{"name": k, "mcap": v} for k, v in d.items()]
        for s in (rows or []):
            n, m = s.get("name"), s.get("mcap")
            if not n or not m:
                continue
            try:
                m = float(m)
            except Exception:
                continue
            out[str(n).upper()] = m
        if not out:
            continue
        # Decided from the LARGEST value. India's biggest company is about
        # Rs 20 lakh crore, so a crore figure never exceeds ~1e7, a lakh figure
        # ~1e9, a rupee figure ~1e14. The bands do not overlap.
        top = max(out.values())
        if top > 1e10:
            out = {k: v / 1e7 for k, v in out.items()}      # rupees -> crores
            unit = "rupees, converted to crores"
        elif top > 1e7:
            out = {k: v / 1e5 for k, v in out.items()}      # lakhs -> crores
            unit = "lakhs, converted to crores"
        else:
            unit = "crores"
        print(f"  Market caps loaded: {len(out)} symbols from {name} ({unit}).")
        return out
    return {}


SHARES = os.path.join(DATA, "shares_outstanding.json")


def live_prices(kite, symbols):
    """Last traded price for a list of NSE symbols. Chunked - the API will not
    take two thousand at once."""
    out = {}
    syms = list(symbols)
    for i in range(0, len(syms), 200):
        chunk = ["NSE:" + s for s in syms[i:i + 200]]
        try:
            for k, v in (kite.ltp(chunk) or {}).items():
                p = v.get("last_price")
                if p:
                    out[k.split(":", 1)[1]] = float(p)
        except Exception:
            pass
        time.sleep(0.35)
    return out


def shares_from_mcaps(mcaps, prices):
    """Turn a one-off market-cap list into a share count, which is the thing
    that actually stays still.

        shares = market cap in rupees / price

    Market caps go stale the moment they are published, because prices move
    every day. Share counts barely move at all. So we convert once, keep the
    share count, and recompute the market cap from live prices from then on.

    The level still inherits whatever date the source file was measured on -
    that error is carried, not removed. What this fixes is the drift AFTER
    that date, which is the part that grows.
    """
    sh = {}
    for sym, cr in mcaps.items():
        p = prices.get(sym)
        if p and p > 0 and cr > 0:
            sh[sym] = (cr * 1e7) / p
    return sh


def apply_mcap_filter(scanner):
    """Wrap build_universe rather than editing the engine.

    His file stays exactly as he wrote it - it is the thing under test, and a
    universe change is one rule, applied visibly, on top.
    """
    original = scanner.build_universe
    mcaps = load_mcaps()

    def wrapped():
        original()
        uni = scanner.universe
        if not mcaps:
            print(f"  NO MARKET-CAP FILE — the Rs{MIN_MCAP_CR} crore filter is NOT")
            print(f"  being applied. Scanning all {len(uni)} instruments.")
            print(f"  Put fundamentals.json in the DATA folder to switch it on.")
            return

        eq_syms = [s for s, m in uni.items() if m.get("seg") == "EQ"]
        print(f"  Pricing {len(eq_syms)} equities to update market caps ...")
        prices = live_prices(scanner.kite, eq_syms)

        shares = {}
        if os.path.exists(SHARES):
            try:
                shares = json.load(open(SHARES, encoding="utf8")).get("shares", {})
            except Exception:
                shares = {}
        if not shares:
            shares = shares_from_mcaps(mcaps, prices)
            try:
                json.dump({"built": date.today().isoformat(), "shares": shares},
                          open(SHARES, "w", encoding="utf8"))
                print(f"     derived share counts for {len(shares)} companies "
                      f"(one time — market caps now update themselves daily)")
            except Exception as e:
                print(f"     (could not save share counts: {e})")

        today, stale = {}, 0
        for sym in eq_syms:
            s, p = shares.get(sym), prices.get(sym)
            if s and p:
                today[sym] = s * p / 1e7          # rupees -> crores
            elif mcaps.get(sym) is not None:
                today[sym] = mcaps[sym]           # no price today, use the file
                stale += 1

        kept, small, unknown = {}, 0, 0
        for sym, meta in uni.items():
            if meta.get("seg") != "EQ":
                kept[sym] = meta                  # indices and MCX are exempt
                continue
            m = today.get(sym)
            if m is None:
                unknown += 1                      # no figure = cannot clear the bar
                continue
            if m < MIN_MCAP_CR:
                small += 1
                continue
            kept[sym] = meta
        with scanner.lock:
            scanner.universe = kept

        eq = sum(1 for v in kept.values() if v.get("seg") == "EQ")
        print(f"  Market-cap filter, priced today:")
        print(f"     {eq} equities above Rs{MIN_MCAP_CR} crore")
        print(f"     {small} below the floor, {unknown} with no figure"
              + (f", {stale} using the file's value" if stale else ""))
        if unknown > eq:
            print(f"     NOTE: more symbols have no market cap than pass the")
            print(f"     filter. The fundamentals file is probably incomplete.")

    scanner.build_universe = wrapped


_FM_PROC = {"p": None}


def _formations_child():
    """Run FORMATIONS LIVE.py beside the desk, printing into this window."""
    import subprocess
    path = os.path.join(FEED, "FORMATIONS LIVE.py")
    if not os.path.exists(path):
        print("  [FM] FORMATIONS LIVE.py not found - Formations tab will be "
              "empty.")
        return
    while True:
        try:
            print("  [FM] starting the Formations scanner in this window")
            _t0 = time.time()
            p = subprocess.Popen([sys.executable, "-u", path], cwd=FEED,
                                 env={**os.environ, "FM_CHILD": "1"})
            _FM_PROC["p"] = p
            p.wait()
            # A scanner that dies in seconds will not be fixed by trying
            # again in seconds - it is usually a missing login. Backing off
            # keeps this window readable instead of one line a minute.
            wait = 600 if (time.time() - _t0) < 30 else 60
            print(f"  [FM] the scanner stopped (code {p.returncode}) - "
                  f"trying again in {wait // 60} min. The desk is "
                  f"unaffected."
                  + ("  If this repeats, today's Zerodha login is missing."
                     if wait == 600 else ""))
        except Exception as e:
            print(f"  [FM] could not start the scanner: {e}")
            wait = 600
        time.sleep(wait)


def _formations_stop():
    p = _FM_PROC.get("p")
    try:
        if p and p.poll() is None:
            p.terminate()
    except Exception:
        pass


def main():
    print("=" * 60)
    print("  ASCENDRA CAPITAL — CALL DESK")
    print("=" * 60)
    if not os.path.exists(SITE):
        print(f"\n  Cannot find the dashboard at:\n    {SITE}\n")
        hold(1)
    ensure_deps()
    kite = load_kite()

    HISTORY.extend(load_log())
    # Rewrite the file once, immediately. The record gained a "blocked" column
    # on 08/09; the file on disk still carries the old header, and append_log()
    # writes rows against the NEW field list. Appending a 25-value row under a
    # 24-column header is how a log quietly goes crooked, so the header is
    # brought up to date before anything can be appended to it.
    dedupe_history(quiet=False)
    rewrite_log()
    # Repair BEFORE settling: the rows the old settler got wrong are already
    # closed, so settle_stale() would walk straight past them.
    repair_settle_bug(kite)
    repair_swing_fill_day(kite)
    # BEFORE the settlers: the engine's own book already knows how several of
    # today's calls ended, and taking that first means the settlers only have
    # to work on what is genuinely still unresolved.
    _closed = sync_from_engine_book()
    if _closed:
        print(f"  {_closed} call{'s' if _closed > 1 else ''} the engine had "
              f"already closed were still open in the record — taken from its "
              f"own book.")
    settle_stale(kite)
    settle_swing(kite)
    audit_record()
    smc_load()
    sp_load()
    smc_bars_load()
    sp_bars_load()
    smc_repair_fill_bug(kite)
    repair_smc_before_fill(kite)
    smc_settle_stale(kite)
    print(f"  {len(HISTORY)} calls already on record.")

    # ── the index tokens the engine needs ──────────────────────────────────
    # build_universe() reads config.TOKEN_NIFTY / TOKEN_SENSEX / TOKEN_BANKNIFTY.
    # The config.py in the package Jenil sent only defines state_dir(), so that
    # line raises, build_universe() dies, and the engine ends up with NO
    # universe at all - it starts, says nothing is wrong, and scans nothing.
    #
    # Resolved from Kite's own instrument list rather than hardcoded, so the
    # numbers are the exchange's rather than someone's memory of them. The
    # constants are only a fallback if the lookup fails.
    import config as C
    if not hasattr(C, "TOKEN_NIFTY"):
        want = {"NIFTY 50": "TOKEN_NIFTY", "NIFTY BANK": "TOKEN_BANKNIFTY"}
        found = {}
        try:
            for row in kite.instruments("NSE"):
                ts = (row.get("tradingsymbol") or "").upper()
                if ts in want:
                    found[want[ts]] = row["instrument_token"]
        except Exception as e:
            print(f"  (index lookup on NSE failed: {e})")
        try:
            for row in kite.instruments("BSE"):
                if (row.get("tradingsymbol") or "").upper() == "SENSEX":
                    found["TOKEN_SENSEX"] = row["instrument_token"]
                    break
        except Exception:
            pass
        defaults = {"TOKEN_NIFTY": 256265, "TOKEN_BANKNIFTY": 260105,
                    "TOKEN_SENSEX": 265}
        for k, v in defaults.items():
            tok = found.get(k, v)
            setattr(C, k, tok)
            print(f"  {k:<18} {tok}" + ("" if k in found else "   (fallback)"))

    # The same three tokens feed the index strip at the top of the dashboard.
    # Read back off the config object so this works whether they were just
    # resolved above or were already defined in config.py.
    INDEX_TOKENS[:] = [("NIFTY 50", getattr(C, "TOKEN_NIFTY", 256265)),
                       ("BANK NIFTY", getattr(C, "TOKEN_BANKNIFTY", 260105)),
                       ("SENSEX", getattr(C, "TOKEN_SENSEX", 265))]

    import tech_calls as T
    print("  Reading basket order flow ...")
    flow_once(kite)
    lean = {1: "buyers ahead", -1: "sellers ahead", 0: "balanced"}[_flow["bias"]]
    print(f"     {lean}  (score {_flow['score']:+.3f} · {_flow['detail']})")

    scanner = T.TechScanner(kite, get_flow_bias=lambda: _flow["bias"])
    apply_mcap_filter(scanner)
    widen_coverage(scanner, T)
    per_cycle = FAST_LANE + ROLLING
    print(f"""
  COVERAGE
     swing setup      every tradable stock, every 10 minutes
     intraday setups  {FAST_LANE} most active every cycle, plus {ROLLING} rotating
                      -> roughly {per_cycle} stocks per 150s cycle, and the whole
                      market seen about every {2330 * 150 / per_cycle / 60:.0f} minutes
                      (a 15-minute candle only changes 4 times an hour, so a
                       sweep at this pace loses no signal)""")

    threading.Thread(target=log_saver, daemon=True).start()
    threading.Thread(target=price_watcher, args=(kite,), daemon=True).start()
    threading.Thread(target=index_watcher, args=(kite,), daemon=True).start()
    threading.Thread(target=flow_watcher, args=(kite,), daemon=True).start()
    threading.Thread(target=scanner.loop, args=(STATE,), daemon=True).start()
    # THE POSITION TRACKER. Jenil's engine has two loops, not one:
    #   loop()       hunts for NEW setups. It quotes the whole market, so a
    #                cycle takes minutes, and SCAN_SECS=150 is a floor not a
    #                promise - on 07/09 it was publishing every 3 to 5 minutes.
    #   fast_loop()  watches calls that ALREADY EXIST. It quotes only the open
    #                symbols, so it is cheap, and it is written to run every
    #                FAST_SECS=5 seconds.
    # Only the first was ever started. Every open trade was therefore having
    # its stop and target checked once every few MINUTES, off a single price
    # sample. TEGA on 07/09: SHORT with a stop at 1570, price ran to 1576.80
    # and came back inside one of those gaps, and the stop never registered -
    # the trade was still showing LIVE an hour later. The engine's own comments
    # show this class of miss being chased before (DWARKESH, 24/8) without
    # anyone noticing the tracker was not running at all.
    # No rule changes here. The same test, on a price sampled every 5 seconds
    # instead of every few minutes.
    # Hand back everything still open BEFORE the tracker starts, so it begins
    # its first pass already watching them rather than a few seconds later.
    reattach_open(scanner)
    if hasattr(scanner, "fast_loop"):
        threading.Thread(target=scanner.fast_loop, args=(STATE,), daemon=True).start()
        print("     position tracker  every "
              f"{getattr(T, 'FAST_SECS', 5)}s on open calls only")
    else:
        print("     WARNING: this engine has no fast_loop - open calls are "
              "tracked only by the market scan, so stops can be missed.")
    threading.Thread(target=swing_watcher, args=(scanner, T), daemon=True).start()
    threading.Thread(target=swing_watcher_full, args=(scanner, T), daemon=True).start()
    threading.Thread(target=open_watcher, args=(kite,), daemon=True).start()
    print("     gap check at open 09:16 — anything the opening trade already decided")
    threading.Thread(target=late_watcher, args=(scanner, T, kite), daemon=True).start()
    print("     late breakout pass " + ", ".join(LATE_SWEEP_AT) +
          " — the whole market on today's bar, when a real day's volume exists")
    threading.Thread(target=smc_watcher, args=(kite, scanner), daemon=True).start()
    threading.Thread(target=audit_watcher, daemon=True).start()
    print("     record check      every 30 min while the market is open")
    threading.Thread(target=settle_watcher, args=(kite,), daemon=True).start()
    threading.Thread(target=sweep_passed, args=(kite,), daemon=True).start()
    print("     end-of-day close  15:45, from the exchange's own candles — no "
          "intraday call is ever left open")
    threading.Thread(target=record_watcher, daemon=True).start()
    print("     record keeper     every 15s — a trade closing reaches the "
          "record even with the page shut")
    threading.Thread(target=serve, daemon=True).start()

    # ── FORMATIONS, IN THIS WINDOW (21/09) ────────────────────────────────
    # MM: "why cant you make it in one black window?" No good reason - the
    # Formations scanner was written as its own program because he wanted it
    # live before the open that morning, and it stayed that way out of habit.
    # It is started here instead, as a child of the desk, so there is one
    # window to open and one to close.
    #
    # Deliberately a separate PROCESS rather than another thread. This window
    # is what watches his open stops, and a stop nobody is watching is not a
    # stop. A scanner fault can take its own process down and the desk will
    # not notice; it could not be made to bring the desk down with it.
    threading.Thread(target=_formations_child, daemon=True).start()

    url = f"http://localhost:{PORT}"
    phone = None
    try:
        import socket
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(("8.8.8.8", 80))          # never sends anything - this just
        ip = sk.getsockname()[0]             # asks the OS which address this
        sk.close()                           # PC uses on its own network
        phone = f"http://{ip}:{PORT}"
    except Exception:
        pass
    print(f"""
  ------------------------------------------------------------
   YOUR DASHBOARD IS AT   {url}
""" + (f"""
   ON YOUR PHONE          {phone}
                          (same wifi as this PC)
""" if phone else "") + f"""  ------------------------------------------------------------

   Opening it in your browser now.

   LEAVE THIS WINDOW OPEN all day. It is the engine. Close it
   and open calls stop being watched.

   The page refreshes itself every 10 seconds.

   Below, every new call is printed as it happens.
  ------------------------------------------------------------
""")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n  Stopping. Saving the record ...")
        rewrite_log()
        print(f"  Saved {len(HISTORY)} calls to DATA\\calls_log.csv\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n  Something went wrong: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        print("\n  Screenshot this window and send it to Claude.\n")
        hold(1)
