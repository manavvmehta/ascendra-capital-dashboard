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
SHORT_LAST_SWEEP = "15:00"           # the short book writes nothing from this
                                    # candle on - see the note in the sweep loop


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


def live_already(L, book, sym):
    """ONE LIVE CALL PER STOCK, PER BOOK (05/10).

    MM found ASIANPAINT and OBEROIRLTY each sitting in the long-term book
    twice. A call is named book|stock|sweep-date, so the only thing stopping a
    duplicate was the same sweep candle being written twice. ASIANPAINT swept
    last week's low on 30/09 and again on 01/10 - two different dates, two
    different names, two live calls on one stock. If both filled that is Rs 4L
    in a single name on a book that sizes Rs 2L, which is not the book he
    agreed to run.

    The newer sweep is simply not written while the older one is still alive.
    """
    return any(c["book"] == book and c["sym"] == sym
               and c["state"] in ("WAITING", "BROKE", "IN")
               for c in L.values())


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
                # AND AN ORDER MAY NOT APPEAR AFTER THREE EITHER (07/10).
                #
                # The same thought one step later. A call written at 09:15 that
                # only breaks its trigger on the 15:00 candle would reach the
                # desk as "place this order" with minutes to go, on a book that
                # must be flat by the close. JMFINANCIL broke out at 14:15 and
                # was still being shown at 15:20. Past the cutoff the breakout
                # is noted and the call is retired instead of flashed.
                if (cfg["hold_days"] == 0
                        and str(b["date"])[11:16] >= SHORT_LAST_SWEEP):
                    c["broke_at"] = str(b["date"])
                    c["state"] = "GONE"
                    c["status"] = "GONE - broke out too late in the day to place"
                    return
                c["state"] = "BROKE"
                c["status"] = "BROKE OUT"
                c["broke_at"] = str(b["date"])
                # WHEN WE FOUND OUT (05/10). A call that has been sitting
                # unwatched cannot be settled backwards against history nobody
                # was reading at the time - see the note on broke_seen below.
                c["broke_seen"] = datetime.now().isoformat(timespec="seconds")
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
        # AND THE PULLBACK CANNOT ARRIVE THREE DAYS LATE (01/10)
        #
        # MM: "check why so many losses in trades done." All 29 of today's
        # closes were calls from the 28th and 29th. MEDPLUS swept its level on
        # 28/09 15:15, broke out on 30/09, and filled today at 12:30 - on a
        # book whose whole rule is yesterday's level, bought today, closed
        # today. The give-up test existed but sat in the `else` of this loop,
        # so it only ran when NO pullback was ever found; if one turned up
        # forty days later the call filled anyway.
        #
        # Releasing the frozen BROKE calls this afternoon is what exposed it -
        # each was walked against the whole stored history at once and filled
        # on a candle from days after its breakout. Those are not trades he
        # could have taken, and they are the losses he is looking at.
        #
        # The window is now explicit: the short-term book must fill the same
        # day it broke out, the daily book within its hold window.
        # A PULLBACK THAT HAPPENED BEFORE WE KNEW ABOUT THE BREAKOUT IS NOT A
        # TRADE HE COULD HAVE TAKEN (05/10).
        #
        # MM: "there are so many calls like paytm that didnt flash and i
        # couldnt see it. even adani power."
        #
        # Unfreezing the BROKE calls this afternoon made the engine look at
        # eight calls it had ignored since 29/09. A week of price history was
        # waiting for them, so seven filled and three closed instantly - all
        # stamped 01/10, four days in the past. The bell never rang because the
        # fills were not today's, and he was never told to buy any of them. They
        # are correct by the rule and completely useless as trades.
        #
        # So the pullback is now only looked for in candles that printed at or
        # after the moment the engine SAW the breakout. A call that was not
        # being watched simply has no history to be settled against.
        seen = c.get("broke_seen")
        back = [b for b in after if str(b["date"]) > c["broke_at"]
                and (not seen or str(b["date"]) >= str(seen)[:10])]
        if cfg["hold_days"] == 0:
            back = [b for b in back
                    if str(b["date"])[:10] == str(c["broke_at"])[:10]]
        else:
            days = sorted({str(b["date"])[:10] for b in back})[:cfg["hold_days"]]
            back = [b for b in back if str(b["date"])[:10] in days]
        if not back:
            # A BREAKOUT ON THE NEWEST CANDLE IS NOT A FAILED PULLBACK (05/10,
            # from MM: "sudeepphrm made a high but didnt get any buy call").
            #
            # THIS IS THE ONE THAT HAS BEEN EATING THE BOOK.
            #
            # SUDEEPPHRM swept at 14:45, broke its trigger of 1178.90 on the
            # 15:00 candle, and was killed as NEVER CAME BACK in the same pass.
            # It then traded down to 1172.30, under its entry of 1172.80 - the
            # pullback DID come. The engine had already thrown the call away.
            #
            # Why: `back` is the candles AFTER the breakout, inside the window.
            # The sweep runs once a quarter hour, so when it reads a breakout it
            # is reading the newest closed candle - and there is nothing after
            # it yet, because the next candle is still forming. `back` is empty,
            # and empty was being read as "the pullback never came".
            #
            # It never came because it had not had time to. Whenever the engine
            # is up to date, EVERY breakout was being killed one candle after it
            # happened; fills only ever happened on passes where the engine had
            # fallen two or more candles behind. That is why this book shows 80
            # calls thrown away against 40 taken.
            #
            # Empty `back` means the question has not been asked yet, so the
            # call is left alone and asked again next pass. The call is only
            # given up once its day (or its hold window) is actually behind us,
            # which is the check below.
            # AND THE WINDOW IS THE CALL'S OWN, NOT TODAY'S DATE (06/10).
            #
            # The check above said "give up unless it broke out today". That is
            # right for the intraday book, whose window IS one day. On the daily
            # book the window is ten days, so a call that broke out yesterday
            # still has nine days to pull back - and this killed it at the first
            # pass of the morning, before today's candle had even formed.
            #
            # The scheduled desk check caught it: all twelve long-term calls
            # marked NEVER CAME BACK had been given ZERO of their ten candles.
            # Six of them died at 00:49 this morning - Rs 12 lakh of book thrown
            # away before the open - and LAURUSLABS and MFSL had actually traded
            # back to their entries, so they were real trades.
            #
            # An empty `back` on the daily book means no candle has arrived yet,
            # which is not an answer. The call waits. The `else` branch below,
            # which only fires once candles exist, is what retires it when the
            # window is genuinely spent.
            if cfg["hold_days"] == 0:
                if str(c["broke_at"])[:10] >= date.today().isoformat():
                    return
            else:
                return
            c["state"] = "GONE"
            c["status"] = "NEVER CAME BACK"
            return
        for b in back:
            if b["low"] <= c["entry"]:
                c["state"] = "IN"
                c["status"] = "OPEN"
                c["filled_at"] = str(b["date"])
                break
            # THE MOVE CAN FINISH WITHOUT US (06/10, from MM on KRN).
            #
            # KRN swept at 15:15 yesterday, broke out on today's open and ran to
            # 1,462. The desk was still saying "place this order" at 1,393 for a
            # target of 1,433 - a target price had already gone straight through
            # before the limit could ever fill. Seven of the fourteen orders on
            # the desk this morning were in that state.
            #
            # An order that price has run past is not an order, it is a
            # reminder of a trade that did not happen. Once a candle inside the
            # window trades at or beyond the target without the pullback having
            # come, the call is over.
            #
            # The fill is checked FIRST on each candle: a resting limit at the
            # entry is hit on the way down, so if one candle reaches both, the
            # trade is given rather than taken away.
            if b["high"] >= c["target"]:
                c["state"] = "GONE"
                c["status"] = "GONE - it hit the target before it filled"
                return
        else:
            limit = 1 if cfg["hold_days"] == 0 else 6
            seen_days = len({str(b["date"])[:10] for b in back})
            # A WINDOW THAT HAS CLOSED CANNOT STILL BE WAITING (05/10, from MM
            # asking why there were no live calls and finding eight rows from
            # 28/09 and 01/10 still sitting in BROKE OUT.)
            #
            # `back` is already capped at the book's own window - one single day
            # on the short-term book - so for an intraday call `seen_days` can
            # never exceed its limit of 1. The pullback never came, and nothing
            # in here was ever allowed to say so, so the row stayed BROKE for
            # ever. It was harmless money-wise (hidden from the desk, counted
            # nowhere, and checked against the bars it never filled) but it is
            # still a lie in the record and it hides how the book really did.
            #
            # The day the breakout happened IS the whole window for an intraday
            # call. Once that day is behind us the answer is no. The daily book
            # is the same thought with its own hold window.
            shut = (str(c["broke_at"])[:10] < date.today().isoformat()
                    if cfg["hold_days"] == 0
                    else seen_days >= cfg["hold_days"])
            if seen_days > limit or shut:
                c["state"] = "GONE"
                c["status"] = "NEVER CAME BACK"
            return
    if c["state"] != "IN":
        return
    run = [b for b in after if str(b["date"]) >= c["filled_at"]]
    day0 = c["filled_at"][:10]
    _prev = None
    for b in run:
        # Same reasoning on the way out: on the candle that filled us we cannot
        # know whether the high or the low came first, so the target is not
        # claimable there. The stop still is - that is the conservative side,
        # and it is the convention the calls engine already uses.
        _fill_bar = str(b["date"]) == c["filled_at"]
        d = str(b["date"])[:10]
        if cfg["hold_days"] == 0 and d != day0:
            # AN INTRADAY CALL THAT SURVIVED THE DAY COULD NEVER CLOSE (30/09,
            # from MM: MANAPPURAM past its target of 320.40 at 321.35 and still
            # showing OPEN, a day after it was filled).
            #
            # This used to just break. The square-off below lives in the loop's
            # `else`, which a break skips - so once a position carried into a
            # new day, every later candle belonged to a new day, hit this line,
            # broke, and skipped the close again. It was not late; it could
            # never close at all, and would have sat in the book for ever
            # holding a slot.
            #
            # The position is intraday, so it ended when its own day ended.
            # It is closed here at the last candle of that day, which is the
            # honest price - not at today's, which was never available to it.
            if _prev is not None:
                c["exit_px"] = round(_prev["close"], 2)
                c["status"] = "SQUARED OFF"
                c["closed_at"] = str(_prev["date"])
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
        _prev = b
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

    # TAKE BACK THE TRADES THAT WERE NEVER TAKEABLE (01/10)
    #
    # Before the window above existed, a call could fill days after its
    # breakout - on the short-term book that is not the rule, it is a
    # different strategy. Those rows are voided here, once: they keep their
    # place in the record with the reason, and they stop counting as money.
    voided = 0
    for c in L.values():
        if not c.get("broke_at") or not c.get("filled_at"):
            continue
        if str(c.get("status", "")).startswith("VOID"):
            continue
        days = BOOKS[c["book"]]["hold_days"]
        bd, fd = str(c["broke_at"])[:10], str(c["filled_at"])[:10]
        late = (fd != bd) if days == 0 else (
            (datetime.fromisoformat(fd) - datetime.fromisoformat(bd)).days > days)
        if late:
            c["status"] = "VOID - filled outside its window"
            c["state"] = "GONE"
            c["result_pct"] = None
            c["exit_px"] = None
            voided += 1
    # AND A TARGET CANNOT BE TAKEN ON THE CANDLE THAT FILLED YOU (01/10)
    #
    # Inside one candle there is no way to know whether the high or the low
    # came first, so this engine gives the trade its stop there and refuses it
    # its target - the conservative side. One row predates that guard:
    # FINCABLES, 29/09 09:30, filled and target-hit on the same candle for
    # +0.83%. It flatters the record, which is the direction that matters most
    # to get right, so it goes the same way as the rest.
    for c in L.values():
        if str(c.get("status", "")).startswith("VOID"):
            continue
        if (c.get("status") == "TARGET" and c.get("filled_at")
                and str(c.get("closed_at")) == str(c.get("filled_at"))):
            c["status"] = "VOID - target taken on its own fill candle"
            c["state"] = "GONE"
            c["result_pct"] = None
            c["exit_px"] = None
            voided += 1

    # AND A FILL CANNOT HAPPEN ON THE CANDLE THAT BROKE OUT (05/10)
    #
    # The same reasoning as the guard above, but on the way IN. The
    # break-then-pullback fix of 29/09 made the pullback come from a strictly
    # later candle than the break, because inside one candle there is no way to
    # know whether the high or the low came first. Eleven rows written on the
    # 28th and 29th predate that fix and still carry filled_at equal to
    # broke_at - one candle doing two jobs. They are not trades that could have
    # been taken, so they keep their place in the record with the reason and
    # stop counting as money. They were +Rs 1,200 between them, so this makes
    # the book look slightly worse, which is the direction worth being right
    # about.
    for c in L.values():
        if str(c.get("status", "")).startswith("VOID"):
            continue
        if (c.get("broke_at") and c.get("filled_at")
                and str(c["filled_at"]) <= str(c["broke_at"])):
            c["status"] = "VOID - filled on its own breakout candle"
            c["state"] = "GONE"
            c["result_pct"] = None
            c["exit_px"] = None
            voided += 1

    # THE SEVEN TRADES THE UNFREEZE INVENTED (05/10, one time only)
    #
    # These are the calls that filled on 01/10 but were only discovered when the
    # BROKE freeze was lifted on the afternoon of 05/10. No bell rang for any of
    # them, so MM was never told to place them. Three had already closed for
    # -Rs 15,340 and four were showing under YOU ARE HOLDING. He asked for them
    # out. The broke_seen guard above is what stops this ever happening again.
    for key in ("LONG TERM|ADANIPOWER|2026-09-29",
                "LONG TERM|ASIANPAINT|2026-09-29",
                "LONG TERM|AUBANK|2026-09-29",
                "LONG TERM|PAYTM|2026-09-29",
                "LONG TERM|ITC|2026-09-29",
                "LONG TERM|TVSMOTOR|2026-09-29",
                "LONG TERM|VEDL|2026-09-29"):
        c = L.get(key)
        if c and not str(c.get("status", "")).startswith("VOID"):
            c["status"] = "VOID - settled from history, never alerted"
            c["state"] = "GONE"
            c["result_pct"] = None
            c["exit_px"] = None
            voided += 1

    # AND ONE LIVE CALL PER STOCK, APPLIED TO THE ONES ALREADY THERE (05/10)
    #
    # live_already() stops a NEW duplicate being written but could not remove
    # the duplicates that predate it: ASIANPAINT was sitting in the long-term
    # book three times at once, which is Rs 6L in one name on a book that sizes
    # Rs 2L. The oldest live call on a stock is kept; the later ones go.
    keep = {}
    for key, c in sorted(L.items(), key=lambda kv: str(kv[1].get("at"))):
        if c["state"] not in ("WAITING", "BROKE", "IN"):
            continue
        tag = (c["book"], c["sym"])
        if tag in keep:
            c["status"] = "VOID - the stock already had a live call"
            c["state"] = "GONE"
            c["result_pct"] = None
            c["exit_px"] = None
            voided += 1
        else:
            keep[tag] = key

    # AND NOTHING ALREADY BROKEN OUT MAY BE SETTLED FROM BEFORE NOW (05/10)
    for c in L.values():
        if c["state"] == "BROKE" and not c.get("broke_seen"):
            c["broke_seen"] = datetime.now().isoformat(timespec="seconds")

    # PUT BACK THE CALLS THAT WERE NEVER GIVEN A CHANCE (06/10, one time)
    #
    # Every long-term call sitting in NEVER CAME BACK was retired without a
    # single candle of its window being looked at - the bug above. They go back
    # to BROKE so the fixed code can judge them properly. broke_seen is reset to
    # NOW, not to when they broke out, so none of them can be settled against
    # price action MM was never shown: they can only fill from today forward,
    # and the bell will ring if they do.
    revived = 0
    for c in L.values():
        if c["book"] == "LONG TERM" and c.get("status") == "NEVER CAME BACK" \
           and c.get("broke_at") and not c.get("filled_at"):
            c["state"] = "BROKE"
            c["status"] = "BROKE OUT"
            c["broke_seen"] = datetime.now().isoformat(timespec="seconds")
            revived += 1
    if revived:
        print(f"  put back {revived} long-term call{'' if revived == 1 else 's'} "
              f"that were retired without a single candle being looked at")
        save_log(L)

    if voided:
        save_log(L)
        print(f"  voided {voided} trade{'' if voided == 1 else 's'} that filled "
              f"outside the book's own window - they were never takeable\n")
    publish(L)
    threading.Thread(target=serve, daemon=True).start()
    print(f"  serving on http://127.0.0.1:{PORT}   (your desk stays on 5055)\n")

    last_slow = last_fast = last_say = 0.0
    last_slot = -1
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
                    # A CALL THAT BREAKS OUT MUST KEEP BEING WATCHED (05/10)
                    #
                    # MM: "when a call breaks out, the engine stops watching
                    # it." This list decides which calls get moved forward, and
                    # BROKE was not in it - so the moment a call broke out it
                    # left the list and step() was never called on it again.
                    # The pullback that was the whole point of the rule could
                    # never be noticed, the order could never fill, and the row
                    # sat in BROKE OUT for ever. This is the same freeze as last
                    # week; the fix was made inside step() but never here, so
                    # nothing reached the code that was fixed.
                    #
                    # Safe to unfreeze now in a way it was not last week: the
                    # fill window inside step() caps the pullback search at the
                    # breakout day (short term) or the hold window (long term),
                    # so an old frozen call is resolved honestly instead of
                    # filling forty days late.
                    for c in [c for c in L.values()
                              if c["book"] == "LONG TERM" and c["sym"] == sym
                              and c["state"] in ("WAITING", "BROKE", "IN")]:
                        step(c, bars)
                    s = find_sweep(bars, True)
                    if s:
                        c = make_call("LONG TERM", sym, s, fo)
                        if c and key_of(c) not in L \
                           and not live_already(L, "LONG TERM", sym):
                            L[key_of(c)] = c
                            print(f"  LONG TERM  {sym:12s} {s['shape']:18s} "
                                  f"buy {c['entry']}  stop {c['stop']}  "
                                  f"target {c['target']}")
                save_log(L); publish(L)

            # SHORT TERM - the 15-minute book, only while the market is open
            # SWEEP ON THE CANDLE CLOCK, NOT A FREE-RUNNING TIMER (30/09,
            # MM: "i need you to flash calls on the spot").
            #
            # This used to fire every FAST_EVERY seconds from whenever the
            # program happened to start. fetch_15m will not hand back a candle
            # until it is a full 15 minutes old, so a sweep at 09:29 still only
            # sees up to 09:00 - and the next one is not until 09:44. The 09:15
            # candle therefore sat unread for nearly half an hour, and the call
            # in it reached the desk that late. FINCABLES on 29/09 was written
            # at 09:33 for a 09:15 setup.
            #
            # The sweep now runs on the first tick after each quarter hour, so
            # a candle is read about a minute after it becomes readable instead
            # of up to fifteen. The loop already ticks every PRICE_EVERY (60s),
            # and this fires at most once per quarter hour, so the number of
            # Zerodha calls is unchanged - it is the same four sweeps an hour,
            # just moved onto the candle boundaries where they are useful.
            slot = (now.hour * 60 + now.minute) // 15
            if trading and slot != last_slot and (now.minute % 15) >= 1:
                last_slot = slot
                last_fast = time.time()
                B = fetch_15m(kite, tokens, syms)
                for sym, bars in B.items():
                    # A CALL THAT BREAKS OUT MUST KEEP BEING WATCHED (05/10)
                    #
                    # MM: "when a call breaks out, the engine stops watching
                    # it." This list decides which calls get moved forward, and
                    # BROKE was not in it - so the moment a call broke out it
                    # left the list and step() was never called on it again.
                    # The pullback that was the whole point of the rule could
                    # never be noticed, the order could never fill, and the row
                    # sat in BROKE OUT for ever. This is the same freeze as last
                    # week; the fix was made inside step() but never here, so
                    # nothing reached the code that was fixed.
                    #
                    # Safe to unfreeze now in a way it was not last week: the
                    # fill window inside step() caps the pullback search at the
                    # breakout day (short term) or the hold window (long term),
                    # so an old frozen call is resolved honestly instead of
                    # filling forty days late.
                    for c in [c for c in L.values()
                              if c["book"] == "SHORT TERM" and c["sym"] == sym
                              and c["state"] in ("WAITING", "BROKE", "IN")]:
                        step(c, bars)
                    s = find_sweep(bars, False)
                    # NOTHING FLASHES ON THE SHORT BOOK AFTER THREE (07/10).
                    #
                    # MM: "dont flash trades of smc short term after 3".
                    #
                    # A short-term call is squared off the same day, so one born
                    # on the 15:00 candle has to break its trigger, pull back
                    # into the body and fill, all before the close. V2RETAIL was
                    # written at 15:17 against a 15:00 sweep with thirteen
                    # minutes of market left - that is not a trade anybody can
                    # place. The call is simply never written.
                    if s and str(s["at"])[11:16] >= SHORT_LAST_SWEEP:
                        s = None
                    if s:
                        c = make_call("SHORT TERM", sym, s, fo)
                        if c and key_of(c) not in L \
                           and not live_already(L, "SHORT TERM", sym):
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
