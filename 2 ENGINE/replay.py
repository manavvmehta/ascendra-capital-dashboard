#!/usr/bin/env python3
"""
replay.py — turns a detected shape into a paper trade, exactly per PART 3.

Reimplemented from the handoff's prose, deliberately literally. Every rule that
could be read two ways is marked RULE: with the wording it came from.

TRAP 6.3 (look-ahead through a mutated field) is handled structurally: the
original stop is stored once, never overwritten, and `risk` is always derived
from it. The working stop that moves to breakeven is a separate variable.

TRAP 6.7 (the price you could not have had) is handled by construction: entry
is bars[breakout_index + 1]["open"] and management starts at the bar AFTER
entry. Nothing reads a price from the breakout bar itself.
"""

CAPITAL_PER_TRADE = 200000


def make_trade(bars, pattern, capital=CAPITAL_PER_TRADE):
    """Replay one pattern. Returns a dict, or None if there was no trade.

    `bars` is the same oldest-first list the detector was given.
    `pattern` is the dict `inv_hs.find` returned; it must have broken out.
    """
    brk = pattern.get("breakout")
    if not brk:
        return None

    bi = brk["i"]
    if bi + 1 >= len(bars):
        return {"outcome": "no_entry_bar", "symbol": pattern.get("symbol")}

    neck = pattern["neck"]
    # RULE: "stop = the RIGHT shoulder low"
    stop_original = pattern["right"]
    # RULE: "T1 = neck + (neck - right shoulder low)"
    t1 = neck + (neck - pattern["right"])
    # RULE: "T2 = neck + (neck - head low)"
    t2 = neck + (neck - pattern["head"])
    # recorded but NOT traded
    t1_left_alt = neck + (neck - pattern["left"])

    entry_bar = bars[bi + 1]
    entry = entry_bar["open"]

    # RULE: "If that open is at or below the stop, the trade is SKIPPED
    #        entirely." Note: at OR below, hence <=.
    if entry <= stop_original:
        return {"outcome": "skipped_gap_below_stop", "symbol": pattern.get("symbol"),
                "entry_date": str(entry_bar["date"])[:10], "entry": entry,
                "stop_original": stop_original, "neck": neck}

    risk = entry - stop_original          # never recomputed. Trap 6.3.
    if risk <= 0:
        return {"outcome": "skipped_nonpositive_risk", "symbol": pattern.get("symbol")}

    qty = int(capital // entry)           # RULE: floor(capital / entry) whole shares
    if qty <= 0:
        return {"outcome": "skipped_price_above_capital", "symbol": pattern.get("symbol"),
                "entry": entry}

    stop_working = stop_original
    half_booked = False
    R = None
    outcome = None
    exit_date = None
    exit_price = None
    bars_held = 0

    # Management begins on the bar AFTER entry. The entry bar's own high/low are
    # not used: we bought at its open, and treating that same bar's extremes as
    # tradeable would assume intrabar information we did not have at the open.
    for j in range(bi + 2, len(bars)):
        b = bars[j]
        bars_held = j - (bi + 1)

        # RULE, CRITICAL ORDERING: "Within a single daily bar, the ADVERSE side
        # is assumed to happen first." So the stop test comes first, always.
        if b["low"] <= stop_working:
            if half_booked:
                # remainder exits at the stop, which is now the entry price
                R = (t1 - entry) / risk / 2
                outcome = "breakeven_after_t1"
                exit_price = stop_working
            else:
                R = -1.0
                outcome = "stopped"
                exit_price = stop_original
            exit_date = str(b["date"])[:10]
            break

        if b["high"] >= t2:
            R = ((t1 - entry) / risk + (t2 - entry) / risk) / 2
            outcome = "t2"
            exit_price = t2
            exit_date = str(b["date"])[:10]
            break

        if b["high"] >= t1 and not half_booked:
            half_booked = True
            stop_working = entry          # move stop UP to entry, keep running

    if outcome is None:
        # still open at the end of the data
        last = bars[-1]
        exit_price = last["close"]
        exit_date = str(last["date"])[:10]
        if half_booked:
            R = ((t1 - entry) / risk + (last["close"] - entry) / risk) / 2
        else:
            R = (last["close"] - entry) / risk
        outcome = "open_at_data_end"

    return {
        "symbol": pattern.get("symbol"),
        "outcome": outcome,
        "R": round(R, 4),
        "half_booked": half_booked,
        "entry_date": str(entry_bar["date"])[:10],
        "entry": round(entry, 2),
        "qty": qty,
        "stop_original": round(stop_original, 2),
        "risk_per_share": round(risk, 2),
        "stop_pct": round(100 * risk / entry, 2),
        "neck": round(neck, 2),
        "t1": round(t1, 2),
        "t2": round(t2, 2),
        "t1_left_alt": round(t1_left_alt, 2),
        "exit_date": exit_date,
        "exit_price": round(exit_price, 2) if exit_price is not None else None,
        "bars_held": bars_held,
        "breakout_date": brk["date"],
        "breakout_close": brk["close"],
        "vol_x": brk.get("vol_x"),
        "neck_gap_pct": pattern.get("neck_gap_pct"),
        "sym_pct": pattern.get("sym_pct"),
        "pattern_bars": pattern.get("bars"),
    }
