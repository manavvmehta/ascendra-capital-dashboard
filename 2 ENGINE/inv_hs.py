#!/usr/bin/env python3
"""
inv_hs.py — inverted head-and-shoulders detection on daily candles.

THIS IS A VERBATIM COPY OF THE PRODUCTION DETECTOR from PART 2 of the handoff.
Do not "improve" it. It is the thing under test. Any variant belongs in
variants.py as an explicit, single-rule change so its effect can be measured
on its own.

DETECTION ONLY. It finds the shape and reports it. No stop, no target, no
position, no book.
"""
from datetime import date

# ── the only knobs, all in one place ───────────────────────────────────────
PIVOT_K = 5          # a swing low is the lowest close/low within +/- K bars
LOOKBACK = 200       # daily bars examined
MIN_HEAD_EDGE = 0.03  # the head must be >=3% below the shallower shoulder
SHOULDER_TOL = 0.60  # shoulders' depths may differ by up to 60% of the deeper
NECK_TOL = 0.05      # the two peaks either side of the head may differ by <=5%
MIN_DEPTH_PCT = 0.04  # head must sit >=4% below the neckline
MAX_BARS = 160       # the whole pattern must fit in this many bars
MIN_BARS = 20        # ...and take at least this many
# NO VOLUME GATE (31/08).
FRESH_BARS = 3       # the breakout close must be within this many bars of now


def pivots(bars, k=PIVOT_K):
    """(lows, highs) as lists of (index, price)."""
    lo, hi = [], []
    for i in range(k, len(bars) - k):
        w = bars[i - k:i + k + 1]
        if bars[i]["low"] < min(b["low"] for b in w if b is not bars[i]):
            lo.append((i, bars[i]["low"]))
        if bars[i]["high"] > max(b["high"] for b in w if b is not bars[i]):
            hi.append((i, bars[i]["high"]))
    return lo, hi


def find(bars, symbol=""):
    """Return the best inverted H&S in `bars`, or None."""
    if len(bars) < MIN_BARS + 2 * PIVOT_K:
        return None
    lows, highs = pivots(bars)
    if len(lows) < 3 or len(highs) < 2:
        return None

    best = None
    for a in range(len(lows) - 2):
        for b in range(a + 1, len(lows) - 1):
            for c in range(b + 1, len(lows)):
                iL, pL = lows[a]          # left shoulder
                iH, pH = lows[b]          # head
                iR, pR = lows[c]          # right shoulder
                span = iR - iL
                if not (MIN_BARS <= span <= MAX_BARS):
                    continue
                if pH > min(pL, pR) * (1 - MIN_HEAD_EDGE):
                    continue
                h1 = [p for (i, p) in highs if iL < i < iH]
                h2 = [p for (i, p) in highs if iH < i < iR]
                if not h1 or not h2:
                    continue
                p1, p2 = max(h1), max(h2)
                if p1 <= 0 or p2 <= 0:
                    continue
                neck_gap = abs(p1 - p2) / max(p1, p2)
                if neck_gap > NECK_TOL:
                    continue
                neck = max(p1, p2)
                if neck <= 0:
                    continue
                dL, dR, dH = neck - pL, neck - pR, neck - pH
                if dH / neck < MIN_DEPTH_PCT:
                    continue
                if dL <= 0 or dR <= 0:
                    continue
                if abs(dL - dR) / max(dL, dR) > SHOULDER_TOL:
                    continue
                sym = 1 - abs(dL - dR) / max(dL, dR)
                score = (dH / neck) * 100 * sym + iR * 0.01
                if best is None or score > best["score"]:
                    best = {"score": score, "symbol": symbol,
                            "i_left": iL, "i_head": iH, "i_right": iR,
                            "left": pL, "head": pH, "right": pR, "neck": neck,
                            "d_left": dL, "d_right": dR, "d_head": dH,
                            "neck_left": round(p1, 2), "neck_right": round(p2, 2),
                            "neck_gap_pct": round(neck_gap * 100, 2),
                            "sym_pct": round(sym * 100, 1),
                            "bars": span}
    if best is None:
        return None

    # ── has it broken out? ──
    tail = bars[best["i_right"]:]
    neck = best["neck"]
    brk = None
    for j, bar in enumerate(tail):
        if bar["close"] > neck:
            idx = best["i_right"] + j
            v20 = [x["volume"] for x in bars[max(0, idx - 20):idx] if x.get("volume")]
            avg = (sum(v20) / len(v20)) if v20 else 0
            brk = {"i": idx, "date": str(bar["date"])[:10], "close": bar["close"],
                   "volume": bar.get("volume") or 0, "vol_avg20": round(avg),
                   "vol_x": round((bar.get("volume") or 0) / avg, 2) if avg else None,
                   "bars_ago": len(bars) - 1 - idx}
            break

    best.update({
        "date_left": str(bars[best["i_left"]]["date"])[:10],
        "date_head": str(bars[best["i_head"]]["date"])[:10],
        "date_right": str(bars[best["i_right"]]["date"])[:10],
        "last_close": bars[-1]["close"],
        "breakout": brk,
        "t1_right": round(neck + best["d_right"], 2),
        "t1_left": round(neck + best["d_left"], 2),
        "t2_head": round(neck + best["d_head"], 2),
    })
    return best


def qualifies(p, fresh=FRESH_BARS):
    """A fresh close above the neckline. That is the whole test."""
    b = p.get("breakout")
    if not b:
        return False, "no close above the neckline yet"
    if b["bars_ago"] > fresh:
        return False, f"broke out {b['bars_ago']} bars ago — not fresh"
    v = f" on {b['vol_x']}x volume" if b.get("vol_x") else ""
    return True, f"closed above {p['neck']:.2f}{v}"
