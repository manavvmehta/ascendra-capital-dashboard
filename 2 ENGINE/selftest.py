#!/usr/bin/env python3
"""
selftest.py — proves the engine reproduces the STLTECH reference levels.

This is trap 6.4 rehearsed OFFLINE, on a synthetic series built to contain
exactly the reference pattern's four prices. It cannot prove the detector finds
STLTECH in real data - only real bars can do that - but it does prove that
GIVEN that shape, the arithmetic downstream is right. If this fails, there is
no point running anything against the real universe.

Reference, from PART 1 and PART 3 of the handoff:
    left shoulder low   553.25
    head low            492.25
    right shoulder low  601.00
    neckline            683.50
    breakout close      695.85
    entry (next open)   700.00
    stop                601.00
    T1                  766.00
    T2                  874.75
    left-alt T1         813.75
"""
import sys
from datetime import date, timedelta

import inv_hs as H
import replay as RP

REF = {"left": 553.25, "head": 492.25, "right": 601.00, "neck": 683.50,
       "breakout_close": 695.85, "entry": 700.00, "stop": 601.00,
       "t1": 766.00, "t2": 874.75, "t1_left_alt": 813.75}

# (index, low, high) — piecewise linear between anchors, so no accidental pivots
ANCHORS = [
    (0,  700.00, 715.00),
    (20, 553.25, 568.00),   # LEFT SHOULDER
    (35, 668.50, 683.50),   # peak 1 -> neckline candidate, the higher one
    (50, 492.25, 507.00),   # HEAD
    (65, 665.00, 680.00),   # peak 2 -> 0.51% below peak 1, inside NECK_TOL
    (80, 601.00, 616.00),   # RIGHT SHOULDER
    (88, 686.00, 700.00),   # breakout bar
    (95, 702.00, 717.00),
]


def interp(i, key):
    for n in range(len(ANCHORS) - 1):
        i0, lo0, hi0 = ANCHORS[n]
        i1, lo1, hi1 = ANCHORS[n + 1]
        if i0 <= i <= i1:
            f = (i - i0) / (i1 - i0)
            lo = lo0 + (lo1 - lo0) * f
            hi = hi0 + (hi1 - hi0) * f
            return round(lo, 2) if key == "low" else round(hi, 2)
    lo, hi = ANCHORS[-1][1], ANCHORS[-1][2]
    return round(lo, 2) if key == "low" else round(hi, 2)


def build(tail):
    """Synthetic daily bars. `tail` is a list of (low, high, close) after entry."""
    d0 = date(2026, 4, 1)
    bars = []
    for i in range(96):
        lo, hi = interp(i, "low"), interp(i, "high")
        bars.append({"date": (d0 + timedelta(days=i)).isoformat(),
                     "open": round((lo + hi) / 2, 2), "high": hi, "low": lo,
                     "close": round((lo + hi) / 2, 2), "volume": 1_861_817})

    # bar 88 is the breakout: it must CLOSE above the neckline
    bars[88].update({"close": REF["breakout_close"], "volume": 2_028_680})
    # bar 89 is the entry bar: its OPEN is the price actually paid
    bars[89].update({"open": REF["entry"]})

    for k, (lo, hi, cl) in enumerate(tail):
        j = 90 + k
        while j >= len(bars):
            bars.append(dict(bars[-1]))
        bars[j] = {"date": (d0 + timedelta(days=j)).isoformat(),
                   "open": cl, "high": hi, "low": lo, "close": cl,
                   "volume": 1_500_000}
    return bars


def check(label, got, want, tol=0.01):
    ok = got is not None and abs(got - want) <= tol
    print(f"   {'PASS' if ok else 'FAIL'}  {label:<24} got {got}   want {want}")
    return ok


def main():
    fails = 0
    print("\n1. DETECTION — does the shape come back with the reference prices?")
    bars = build([(700, 720, 715)] * 4)
    p = H.find(bars, "STLTECH-SYNTH")
    if not p:
        print("   FAIL  no pattern found at all")
        return 1
    for k, want in (("left", REF["left"]), ("head", REF["head"]),
                    ("right", REF["right"]), ("neck", REF["neck"])):
        fails += not check(k, p[k], want)
    fails += not check("t1_right", p["t1_right"], REF["t1"])
    fails += not check("t2_head", p["t2_head"], REF["t2"])
    fails += not check("t1_left", p["t1_left"], REF["t1_left_alt"])
    print(f"   neckline peaks {p['neck_left']} / {p['neck_right']} "
          f"= {p['neck_gap_pct']}% apart (NECK_TOL allows {H.NECK_TOL*100:.0f}%)")
    print(f"   breakout {p['breakout']['date']} close {p['breakout']['close']} "
          f"on {p['breakout']['vol_x']}x volume")

    print("\n2. LEVELS — does the trade builder agree?")
    t = RP.make_trade(bars, p)
    fails += not check("entry", t["entry"], REF["entry"])
    fails += not check("stop_original", t["stop_original"], REF["stop"])
    fails += not check("t1", t["t1"], REF["t1"])
    fails += not check("t2", t["t2"], REF["t2"])
    fails += not check("t1_left_alt", t["t1_left_alt"], REF["t1_left_alt"])
    print(f"   stop is {t['stop_pct']}% below entry "
          f"(handoff says 14.1%), size {t['qty']} shares")
    fails += not check("stop_pct", t["stop_pct"], 14.1, tol=0.15)

    print("\n3. OUTCOMES — do the four paths give the R the rules imply?")
    risk = REF["entry"] - REF["stop"]

    # The pattern is detected ONCE, on the clean series, and then reused. If the
    # tail bars are fed back through find() they can create a new pivot low that
    # outscores the real shape and silently changes the neckline and stop - which
    # is a genuine property of this detector, but it is not what section 3 is
    # testing. Isolate the replay logic from the detection logic.
    def scenario(tail):
        b = build([(700, 720, 715)] * 4)      # clean, for detection
        pat = H.find(b, "STLTECH-SYNTH")
        b2 = build([(700, 720, 715)] * 4)     # same series, tail overwritten
        for k, (lo, hi, cl) in enumerate(tail):
            j = 90 + k
            if j < len(b2):
                b2[j] = {"date": b2[j]["date"], "open": cl, "high": hi,
                         "low": lo, "close": cl, "volume": 1_500_000}
        return RP.make_trade(b2, pat)

    # (a) straight to the stop, never touching T1
    t = scenario([(590, 705, 600)] * 3)
    fails += not check("(a) clean stop R", t["R"], -1.0)

    # (b) T1 then back to breakeven
    t = scenario([(700, 770, 760), (690, 765, 700), (695, 705, 700), (650, 702, 660)])
    fails += not check("(b) breakeven R", t["R"], (REF["t1"] - REF["entry"]) / risk / 2)
    print(f"        outcome={t['outcome']} half_booked={t['half_booked']}")

    # (c) all the way to T2
    t = scenario([(700, 770, 760), (760, 880, 875), (860, 890, 880)])
    want_c = ((REF["t1"] - REF["entry"]) / risk + (REF["t2"] - REF["entry"]) / risk) / 2
    fails += not check("(c) T2 R", t["R"], want_c)

    # (d) THE ORDERING RULE: one bar touches both the stop and T2.
    #     PART 3 says the adverse side is assumed first, so this must be -1.0.
    t = scenario([(595, 880, 870), (860, 890, 880)])
    fails += not check("(d) stop+T2 same bar", t["R"], -1.0)
    print(f"        outcome={t['outcome']}  (must be 'stopped', never 't2')")

    print("\n4. SKIP RULE — gap open at or below the stop must not trade")
    b5 = build([(700, 720, 715)] * 3)
    pat = H.find(b5, "STLTECH-SYNTH")
    b5[89]["open"] = 600.00           # below the 601.00 stop
    t = RP.make_trade(b5, pat)
    ok = t["outcome"] == "skipped_gap_below_stop"
    print(f"   {'PASS' if ok else 'FAIL'}  outcome = {t['outcome']}")
    fails += not ok

    print("\n5. THE MASKING PROBLEM — not a bug in the engine, a property of")
    print("   the detector, recorded here so it is measurable later.")
    b6 = build([(595, 880, 870)])     # one deep bar AFTER the real right shoulder
    p6 = H.find(b6, "STLTECH-SYNTH")
    moved = abs(p6["right"] - REF["right"]) > 0.01
    print(f"   one late deep bar -> right shoulder becomes {p6['right']} "
          f"(was {REF['right']}), stop moves with it: {'YES' if moved else 'no'}")
    print("   find() returns the BEST-SCORING shape, not the one that broke out.")

    print("\n" + "=" * 60)
    print("ALL CHECKS PASSED" if not fails else f"{fails} CHECK(S) FAILED")
    print("=" * 60)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
