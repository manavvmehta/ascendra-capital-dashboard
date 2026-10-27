#!/usr/bin/env python3
"""
FORMATIONS LIVE.py  -  DOUBLE-CLICK ME, after you have connected to Zerodha.

Live buy and sell calls from the chart patterns, rated out of ten, written
onto your dashboard's Formations tab and kept in a permanent record.

  Leave the window open alongside START THE DESK. It never touches the
  engine, the calls file, or any of your other tabs - if this crashes,
  your desk keeps running exactly as before.

WHAT IT WRITES, all under DATA
  formations_live.json   what is live right now
  formations_log.json    THE RECORD: every call it has ever made, with the
                         entry, stop, target, the rating at the time, and
                         what became of it

HOW TO READ THE RATING - this is the part that matters
  9 and 10   the ones that made money in testing. Swing 9-10 averaged
             +1.46% a trade over three years; intraday 9-10 +0.95%.
  8          swing only. Small and positive (+0.39%). An INTRADAY 8 lost
             money in testing - it is shown, it is not a trade.
  7 and under  lost money. They are on the screen so you can see what the
             scanner is looking at, not so you can trade them.

Nothing here is advice. It is what the tested rules point at.
"""
import csv
import json
import math
import os
import statistics
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import date, datetime, timedelta

# ═══ indicators ═══




def _w(x, n):
    """Wilder's smoothing."""
    out, cur = [None] * len(x), None
    for i, v in enumerate(x):
        if v is None:
            continue
        cur = v if cur is None else cur - cur / n + v
        out[i] = cur
    return out


# ── TREND ─────────────────────────────────────────────────────────────────
def sma(v, n):
    out, s = [None] * len(v), 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n:
            s -= v[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(v, n):
    k, out, cur = 2.0 / (n + 1), [], None
    for x in v:
        cur = x if cur is None else (x - cur) * k + cur
        out.append(cur)
    return out


def wma(v, n):
    out = [None] * len(v)
    d = n * (n + 1) / 2
    for i in range(n - 1, len(v)):
        out[i] = sum(v[i - n + 1 + j] * (j + 1) for j in range(n)) / d
    return out


def macd(v, fast=12, slow=26, sig=9):
    f, s = ema(v, fast), ema(v, slow)
    line = [a - b for a, b in zip(f, s)]
    signal = ema(line, sig)
    hist = [a - b for a, b in zip(line, signal)]
    return line, signal, hist


def atr(bars, n=14):
    tr = [None]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    out, cur = [None] * len(bars), None
    for i in range(1, len(bars)):
        cur = tr[i] if cur is None else (cur * (n - 1) + tr[i]) / n
        out[i] = cur if i >= n else None
    return out


def adx(bars, n=14):
    """Returns (adx, +DI, -DI). ADX measures STRENGTH, the DIs give direction."""
    tr, pdm, ndm = [None], [None], [None]
    for i in range(1, len(bars)):
        h, l = bars[i]["high"], bars[i]["low"]
        ph, pl, pc = bars[i-1]["high"], bars[i-1]["low"], bars[i-1]["close"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
        up, dn = h - ph, pl - l
        pdm.append(up if (up > dn and up > 0) else 0.0)
        ndm.append(dn if (dn > up and dn > 0) else 0.0)
    a_, p_, m_ = _w(tr, n), _w(pdm, n), _w(ndm, n)
    pdi = [None] * len(bars)
    ndi = [None] * len(bars)
    dx = [None] * len(bars)
    for i in range(len(bars)):
        if not a_[i]:
            continue
        pdi[i] = 100 * (p_[i] or 0) / a_[i]
        ndi[i] = 100 * (m_[i] or 0) / a_[i]
        s = pdi[i] + ndi[i]
        dx[i] = 100 * abs(pdi[i] - ndi[i]) / s if s else None
    out, cur = [None] * len(bars), None
    for i, v in enumerate(dx):
        if v is None:
            continue
        cur = v if cur is None else (cur * (n - 1) + v) / n
        out[i] = cur
    return out, pdi, ndi


def psar(bars, step=0.02, cap=0.2):
    """Parabolic SAR. Returns the dot, and which side of price it is on."""
    if len(bars) < 3:
        return [None] * len(bars)
    out = [None] * len(bars)
    long = bars[1]["close"] > bars[0]["close"]
    sar = bars[0]["low"] if long else bars[0]["high"]
    ep = bars[0]["high"] if long else bars[0]["low"]
    af = step
    for i in range(1, len(bars)):
        sar = sar + af * (ep - sar)
        b = bars[i]
        if long:
            sar = min(sar, bars[i - 1]["low"], bars[max(0, i - 2)]["low"])
            if b["low"] < sar:
                long, sar, ep, af = False, ep, b["low"], step
            elif b["high"] > ep:
                ep, af = b["high"], min(af + step, cap)
        else:
            sar = max(sar, bars[i - 1]["high"], bars[max(0, i - 2)]["high"])
            if b["high"] > sar:
                long, sar, ep, af = True, ep, b["high"], step
            elif b["low"] < ep:
                ep, af = b["low"], min(af + step, cap)
        out[i] = sar
    return out


def supertrend(bars, n=10, mult=3.0):
    """Returns (line, direction) where direction is +1 up, -1 down."""
    a = atr(bars, n)
    line = [None] * len(bars)
    dirn = [None] * len(bars)
    up = dn = None
    d = 1
    for i, b in enumerate(bars):
        if a[i] is None:
            continue
        mid = (b["high"] + b["low"]) / 2
        bu, bl = mid - mult * a[i], mid + mult * a[i]
        up = bu if up is None else (max(bu, up) if bars[i-1]["close"] > up else bu)
        dn = bl if dn is None else (min(bl, dn) if bars[i-1]["close"] < dn else bl)
        if d == 1 and b["close"] < up:
            d = -1
        elif d == -1 and b["close"] > dn:
            d = 1
        line[i] = up if d == 1 else dn
        dirn[i] = d
    return line, dirn


def ichimoku(bars, a=9, b=26, c=52):
    """Tenkan, Kijun, Span A, Span B. The cloud is between A and B."""
    def mid(n, i):
        w = bars[max(0, i - n + 1):i + 1]
        return (max(x["high"] for x in w) + min(x["low"] for x in w)) / 2
    tn = [mid(a, i) if i >= a - 1 else None for i in range(len(bars))]
    kj = [mid(b, i) if i >= b - 1 else None for i in range(len(bars))]
    sa = [((tn[i] + kj[i]) / 2) if (tn[i] and kj[i]) else None
          for i in range(len(bars))]
    sb = [mid(c, i) if i >= c - 1 else None for i in range(len(bars))]
    return tn, kj, sa, sb


# ── MOMENTUM ──────────────────────────────────────────────────────────────
def rsi(v, n=14):
    out = [None] * len(v)
    if len(v) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = v[i] - v[i - 1]
        g += max(d, 0.0); l += max(-d, 0.0)
    ag, al = g / n, l / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(v)):
        d = v[i] - v[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def stoch(bars, n=14, smooth=3):
    raw = [None] * len(bars)
    for i in range(n - 1, len(bars)):
        w = bars[i - n + 1:i + 1]
        hh, ll = max(x["high"] for x in w), min(x["low"] for x in w)
        raw[i] = 50.0 if hh == ll else 100 * (bars[i]["close"] - ll) / (hh - ll)
    out = [None] * len(bars)
    for i in range(len(bars)):
        w = [x for x in raw[max(0, i - smooth + 1):i + 1] if x is not None]
        out[i] = sum(w) / len(w) if w else None
    return out


def cci(bars, n=20):
    out = [None] * len(bars)
    tp = [(b["high"] + b["low"] + b["close"]) / 3 for b in bars]
    for i in range(n - 1, len(bars)):
        w = tp[i - n + 1:i + 1]
        m = sum(w) / n
        md = sum(abs(x - m) for x in w) / n
        out[i] = (tp[i] - m) / (0.015 * md) if md else 0.0
    return out


def williams_r(bars, n=14):
    out = [None] * len(bars)
    for i in range(n - 1, len(bars)):
        w = bars[i - n + 1:i + 1]
        hh, ll = max(x["high"] for x in w), min(x["low"] for x in w)
        out[i] = -50.0 if hh == ll else -100 * (hh - bars[i]["close"]) / (hh - ll)
    return out


def roc(v, n=12):
    out = [None] * len(v)
    for i in range(n, len(v)):
        out[i] = 100 * (v[i] - v[i - n]) / v[i - n] if v[i - n] else None
    return out


# ── VOLUME ────────────────────────────────────────────────────────────────
def obv(bars):
    out, cur = [], 0.0
    for i, b in enumerate(bars):
        v = b.get("volume") or 0
        if i and b["close"] > bars[i - 1]["close"]:
            cur += v
        elif i and b["close"] < bars[i - 1]["close"]:
            cur -= v
        out.append(cur)
    return out


def vwap(bars):
    """Session VWAP - resets each day, as every desk computes it."""
    out, day, pv, vv = [], None, 0.0, 0.0
    for b in bars:
        d = str(b.get("date", ""))[:10]
        if d != day:
            day, pv, vv = d, 0.0, 0.0
        tp = (b["high"] + b["low"] + b["close"]) / 3
        v = b.get("volume") or 0
        pv += tp * v
        vv += v
        out.append(pv / vv if vv else None)
    return out


def ad_line(bars):
    out, cur = [], 0.0
    for b in bars:
        rng = b["high"] - b["low"]
        if rng > 0:
            mf = ((b["close"] - b["low"]) - (b["high"] - b["close"])) / rng
            cur += mf * (b.get("volume") or 0)
        out.append(cur)
    return out


def cmf(bars, n=20):
    out = [None] * len(bars)
    mfv, vol = [], []
    for b in bars:
        rng = b["high"] - b["low"]
        v = b.get("volume") or 0
        mfv.append((((b["close"] - b["low"]) - (b["high"] - b["close"])) / rng * v)
                   if rng > 0 else 0.0)
        vol.append(v)
    for i in range(n - 1, len(bars)):
        sv = sum(vol[i - n + 1:i + 1])
        out[i] = sum(mfv[i - n + 1:i + 1]) / sv if sv else None
    return out


def mfi(bars, n=14):
    out = [None] * len(bars)
    tp = [(b["high"] + b["low"] + b["close"]) / 3 for b in bars]
    for i in range(n, len(bars)):
        pos = neg = 0.0
        for j in range(i - n + 1, i + 1):
            f = tp[j] * (bars[j].get("volume") or 0)
            if tp[j] > tp[j - 1]:
                pos += f
            elif tp[j] < tp[j - 1]:
                neg += f
        out[i] = 100.0 if neg == 0 else 100 - 100 / (1 + pos / neg)
    return out


def volume_profile(bars, i, look=60, bins=24):
    """The price where most volume traded - the Point of Control."""
    w = bars[max(0, i - look + 1):i + 1]
    if len(w) < 10:
        return None
    lo = min(x["low"] for x in w)
    hi = max(x["high"] for x in w)
    if hi <= lo:
        return None
    step = (hi - lo) / bins
    buckets = [0.0] * bins
    for x in w:
        mid = (x["high"] + x["low"]) / 2
        k = min(int((mid - lo) / step), bins - 1)
        buckets[k] += x.get("volume") or 0
    k = buckets.index(max(buckets))
    return lo + (k + 0.5) * step


# ── EXTRAS ────────────────────────────────────────────────────────────────
def pivots(prev_h, prev_l, prev_c):
    p = (prev_h + prev_l + prev_c) / 3.0
    return {"P": p, "R1": 2 * p - prev_l, "S1": 2 * p - prev_h,
            "R2": p + (prev_h - prev_l), "S2": p - (prev_h - prev_l),
            "R3": prev_h + 2 * (p - prev_l), "S3": prev_l - 2 * (prev_h - p)}


def fib(hi, lo, up=True):
    """Retracement levels of the last leg. up=True for a leg that went UP."""
    d = hi - lo
    r = {}
    for f in (0.236, 0.382, 0.5, 0.618, 0.786):
        r[f"{f*100:.1f}%"] = (hi - d * f) if up else (lo + d * f)
    r["ext 1.272"] = (hi + d * 0.272) if up else (lo - d * 0.272)
    r["ext 1.618"] = (hi + d * 0.618) if up else (lo - d * 0.618)
    return r


# ═══ the chart patterns, to the book ═══



LEFT = RIGHT = 3


def pivots(bars, left=LEFT, right=RIGHT):
    hi, lo = [], []
    for i in range(left, len(bars) - right):
        h, l = bars[i]["high"], bars[i]["low"]
        if all(bars[j]["high"] < h for j in range(i - left, i)) and \
           all(bars[j]["high"] <= h for j in range(i + 1, i + right + 1)):
            hi.append(i)
        if all(bars[j]["low"] > l for j in range(i - left, i)) and \
           all(bars[j]["low"] >= l for j in range(i + 1, i + right + 1)):
            lo.append(i)
    return hi, lo


def vol_avg(bars, n=20):
    out = [None] * len(bars)
    run = 0.0
    for i, b in enumerate(bars):
        run += b.get("volume") or 0
        if i >= n:
            run -= bars[i - n].get("volume") or 0
            out[i] = run / n
    return out


def _vol_falls(bars, a, b):
    mid = (a + b) // 2
    v0 = [bars[i].get("volume") or 0 for i in range(a, max(mid, a + 1))]
    v1 = [bars[i].get("volume") or 0 for i in range(mid, b + 1)]
    return bool(v0 and v1 and statistics.fmean(v1) < statistics.fmean(v0))


def _break_up(bars, start, level, window):
    for i in range(start + 1, min(start + 1 + window, len(bars))):
        if bars[i]["close"] > level:
            return i
    return None


def _break_dn(bars, start, level, window):
    for i in range(start + 1, min(start + 1 + window, len(bars))):
        if bars[i]["close"] < level:
            return i
    return None


def _pack(name, side, t, bars, stop, target, crit, extra=None):
    met = sum(1 for v in crit.values() if v)
    d = {"pattern": name, "side": side, "tbar": t,
         "breakout_px": bars[t]["close"], "stop": stop, "target": target,
         "score": 100.0 * met / max(len(crit), 1), "crit": crit}
    if extra:
        d.update(extra)
    return d


# ── TRIANGLES ───────────────────────────────────────────────────────────────
def triangles(bars, v20, min_bars, max_bars, window=20):
    out = []
    hs, ls = pivots(bars)
    H = {i: bars[i]["high"] for i in hs}
    L = {i: bars[i]["low"] for i in ls}
    for k in range(len(hs)):
        for m in range(k + 1, min(k + 8, len(hs))):
            pk = hs[k:m + 1]
            width = pk[-1] - pk[0]
            if not (8 <= width <= max_bars):
                continue
            tro = [i for i in ls if pk[0] < i < pk[-1]]
            if len(tro) < 2:
                continue
            if not ((len(pk) >= 3 and len(tro) >= 2) or
                    (len(tro) >= 3 and len(pk) >= 2)):
                continue
            tops = [H[i] for i in pk]
            bots = [L[i] for i in tro]
            top, bot = max(tops), min(bots)
            hgt = top - bot
            if hgt <= 0 or hgt / top < 0.02:
                continue
            flat_top = (max(tops) - min(tops)) / top <= 0.0075
            flat_bot = (max(bots) - min(bots)) / bot <= 0.0075
            up_bot = all(bots[j] < bots[j + 1] for j in range(len(bots) - 1))
            dn_top = all(tops[j] > tops[j + 1] for j in range(len(tops) - 1))
            j0 = max(0, pk[0] - 40)
            base = {
                "4+ touches, not the bare minimum": len(pk) + len(tro) >= 6,
                "volume recedes while it forms": _vol_falls(bars, pk[0], pk[-1]),
                "long enough to be a real formation": width >= min_bars,
            }

            # ASCENDING: flat ceiling, rising floor
            if flat_top and up_bot and \
                    (top - bots[-1]) <= 0.70 * (top - bots[0]) and \
                    not any(bars[i]["close"] > top
                            for i in range(pk[0], pk[-1] + 1)):
                t = _break_up(bars, pk[-1], top, window)
                if t:
                    c = dict(base)
                    c["volume expands on the breakout"] = bool(
                        v20[t] and (bars[t].get("volume") or 0) >= 1.5 * v20[t])
                    c["prior trend is up (a continuation)"] = \
                        bars[pk[0]]["close"] > bars[j0]["close"]
                    c["breaks out promptly"] = (t - pk[-1]) <= 10
                    out.append(_pack("ASC-TRIANGLE", "LONG", t, bars,
                                     bots[-1],
                                     bars[t]["close"] + 0.70 * hgt, c,
                                     {"pk": pk, "tro": tro, "line": top}))
                    break

            # DESCENDING: flat floor, falling ceiling
            if flat_bot and dn_top and \
                    (tops[-1] - bot) <= 0.70 * (tops[0] - bot) and \
                    not any(bars[i]["close"] < bot
                            for i in range(pk[0], pk[-1] + 1)):
                t = _break_dn(bars, pk[-1], bot, window)
                if t:
                    c = dict(base)
                    c["volume expands on the breakout"] = bool(
                        v20[t] and (bars[t].get("volume") or 0) >= 1.5 * v20[t])
                    c["prior trend is down (a continuation)"] = \
                        bars[pk[0]]["close"] < bars[j0]["close"]
                    c["breaks out promptly"] = (t - pk[-1]) <= 10
                    out.append(_pack("DESC-TRIANGLE", "SHORT", t, bars,
                                     tops[-1],
                                     bars[t]["close"] - 0.50 * hgt, c,
                                     {"pk": pk, "tro": tro, "line": bot}))
                    break

            # SYMMETRICAL: both sides converging
            if dn_top and up_bot and not flat_top and not flat_bot and \
                    (tops[-1] - bots[-1]) <= 0.60 * (tops[0] - bots[0]):
                t = _break_up(bars, pk[-1], tops[-1], window)
                if t:
                    c = dict(base)
                    c["volume expands on the breakout"] = bool(
                        v20[t] and (bars[t].get("volume") or 0) >= 1.5 * v20[t])
                    c["breaks out before the apex"] = (t - pk[-1]) <= 12
                    c["prior trend is up (a continuation)"] = \
                        bars[pk[0]]["close"] > bars[j0]["close"]
                    out.append(_pack("SYM-TRIANGLE", "LONG", t, bars,
                                     bots[-1],
                                     bars[t]["close"] + 0.58 * hgt, c,
                                     {"pk": pk, "tro": tro,
                                      "line": tops[-1]}))
                    break
    return out


# ── DOUBLE BOTTOM / TOP ─────────────────────────────────────────────────────
def doubles(bars, v20, window=25):
    out = []
    hs, ls = pivots(bars)
    H = {i: bars[i]["high"] for i in hs}
    L = {i: bars[i]["low"] for i in ls}
    for k in range(len(ls) - 1):
        a, b = ls[k], ls[k + 1]
        if not (5 <= b - a <= 90):
            continue
        # the two valleys bottom near the same price - the book says ~1%
        if abs(L[a] - L[b]) / max(L[a], L[b]) > 0.04:
            continue
        mid = [i for i in hs if a < i < b]
        if not mid:
            continue
        peak = max(H[i] for i in mid)
        low = min(L[a], L[b])
        rise = (peak - low) / low
        if rise < 0.04:
            continue
        t = _break_up(bars, b, peak, window)
        if not t:
            continue
        c = {
            "valleys within 1% of each other":
                abs(L[a] - L[b]) / max(L[a], L[b]) <= 0.01,
            "at least a 10% rise between them": rise >= 0.10,
            "the valleys are weeks apart": (b - a) >= 10,
            "volume recedes while it forms": _vol_falls(bars, a, b),
            "volume expands on the breakout":
                bool(v20[t] and (bars[t].get("volume") or 0) >= 1.5 * v20[t]),
            "breaks out promptly": (t - b) <= 15,
        }
        out.append(_pack("DOUBLE-BOTTOM", "LONG", t, bars, low,
                         bars[t]["close"] + 0.73 * (peak - low), c,
                         {"pk": mid, "tro": [a, b], "line": peak}))
    for k in range(len(hs) - 1):
        a, b = hs[k], hs[k + 1]
        if not (5 <= b - a <= 90):
            continue
        if abs(H[a] - H[b]) / max(H[a], H[b]) > 0.04:
            continue
        mid = [i for i in ls if a < i < b]
        if not mid:
            continue
        valley = min(L[i] for i in mid)
        high = max(H[a], H[b])
        drop = (high - valley) / high
        if drop < 0.04:
            continue
        t = _break_dn(bars, b, valley, window)
        if not t:
            continue
        c = {
            "peaks within 1% of each other":
                abs(H[a] - H[b]) / max(H[a], H[b]) <= 0.01,
            "at least a 10% fall between them": drop >= 0.10,
            "the peaks are weeks apart": (b - a) >= 10,
            "volume recedes while it forms": _vol_falls(bars, a, b),
            "volume expands on the breakout":
                bool(v20[t] and (bars[t].get("volume") or 0) >= 1.5 * v20[t]),
            "breaks out promptly": (t - b) <= 15,
        }
        out.append(_pack("DOUBLE-TOP", "SHORT", t, bars, high,
                         bars[t]["close"] - 0.65 * (high - valley), c,
                         {"pk": [a, b], "tro": mid, "line": valley}))
    return out


# ── HEAD AND SHOULDERS, both ways ───────────────────────────────────────────
def head_shoulders(bars, v20, window=25):
    out = []
    hs, ls = pivots(bars)
    H = {i: bars[i]["high"] for i in hs}
    L = {i: bars[i]["low"] for i in ls}
    for k in range(len(ls) - 2):
        a, b, c3 = ls[k:k + 3]
        if c3 - a > 120:
            continue
        # the middle valley must be the deepest - that is the head
        if not (L[b] < L[a] and L[b] < L[c3]):
            continue
        # shoulders near the same price
        if abs(L[a] - L[c3]) / max(L[a], L[c3]) > 0.06:
            continue
        arm = [i for i in hs if a < i < c3]
        if len(arm) < 2:
            continue
        neck = max(H[i] for i in arm)
        t = _break_up(bars, c3, neck, window)
        if not t:
            continue
        d1, d2 = b - a, c3 - b
        vl = statistics.fmean([bars[i].get("volume") or 0
                               for i in range(a - 2, a + 3)]) if a >= 2 else 0
        vr = statistics.fmean([bars[i].get("volume") or 0
                               for i in range(c3 - 2, c3 + 3)]) \
            if c3 + 3 <= len(bars) else 0
        cr = {
            "shoulders within 3% of each other":
                abs(L[a] - L[c3]) / max(L[a], L[c3]) <= 0.03,
            "shoulders a similar distance from the head":
                abs(d1 - d2) <= 0.4 * max(d1, d2),
            "the head is clearly deeper":
                L[b] < 0.97 * min(L[a], L[c3]),
            "volume lower on the right shoulder": vr < vl,
            "volume expands on the breakout":
                bool(v20[t] and (bars[t].get("volume") or 0) >= 1.5 * v20[t]),
            "breaks out promptly": (t - c3) <= 15,
        }
        out.append(_pack("INV-HEAD-SHOULDERS", "LONG", t, bars, L[c3],
                         bars[t]["close"] + 0.71 * (neck - L[b]), cr,
                         {"pk": arm, "tro": [a, b, c3], "line": neck}))
    for k in range(len(hs) - 2):
        a, b, c3 = hs[k:k + 3]
        if c3 - a > 120:
            continue
        if not (H[b] > H[a] and H[b] > H[c3]):
            continue
        if abs(H[a] - H[c3]) / max(H[a], H[c3]) > 0.06:
            continue
        arm = [i for i in ls if a < i < c3]
        if len(arm) < 2:
            continue
        neck = min(L[i] for i in arm)
        t = _break_dn(bars, c3, neck, window)
        if not t:
            continue
        d1, d2 = b - a, c3 - b
        cr = {
            "shoulders within 3% of each other":
                abs(H[a] - H[c3]) / max(H[a], H[c3]) <= 0.03,
            "shoulders a similar distance from the head":
                abs(d1 - d2) <= 0.4 * max(d1, d2),
            "the head is clearly higher":
                H[b] > 1.03 * max(H[a], H[c3]),
            "volume recedes while it forms": _vol_falls(bars, a, c3),
            "volume expands on the breakout":
                bool(v20[t] and (bars[t].get("volume") or 0) >= 1.5 * v20[t]),
            "breaks out promptly": (t - c3) <= 15,
        }
        out.append(_pack("HEAD-SHOULDERS", "SHORT", t, bars, H[c3],
                         bars[t]["close"] - 0.55 * (H[b] - neck), cr,
                         {"pk": [a, b, c3], "tro": arm, "line": neck}))
    return out


# ── CUP AND HANDLE ──────────────────────────────────────────────────────────
def cup_handle(bars, v20, window=20):
    out = []
    hs, ls = pivots(bars)
    H = {i: bars[i]["high"] for i in hs}
    L = {i: bars[i]["low"] for i in ls}
    for k in range(len(hs) - 1):
        a, b = hs[k], hs[k + 1]
        if not (15 <= b - a <= 160):
            continue
        if abs(H[a] - H[b]) / max(H[a], H[b]) > 0.06:
            continue
        rim = max(H[a], H[b])
        inner = [i for i in ls if a < i < b]
        if not inner:
            continue
        base = min(inner, key=lambda i: L[i])
        cup_lo = L[base]
        depth = (rim - cup_lo) / rim
        if not (0.08 <= depth <= 0.55):
            continue
        hand = [i for i in ls if b < i <= b + 30]
        if not hand:
            continue
        hl = min(L[i] for i in hand)
        # the handle must sit in the UPPER HALF of the cup
        if hl < rim - 0.5 * (rim - cup_lo):
            continue
        t = _break_up(bars, max(hand), rim, window)
        if not t:
            continue
        pos = (base - a) / max(b - a, 1)
        c = {
            "U-shaped, the low near the middle": 0.30 <= pos <= 0.70,
            "rims within 2% of each other":
                abs(H[a] - H[b]) / max(H[a], H[b]) <= 0.02,
            "handle at least a week long": (max(hand) - b) >= 5,
            "handle shallow (upper third of the cup)":
                hl >= rim - 0.33 * (rim - cup_lo),
            "volume expands on the breakout":
                bool(v20[t] and (bars[t].get("volume") or 0) >= 1.5 * v20[t]),
            "cup long enough": (b - a) >= 25,
        }
        out.append(_pack("CUP-HANDLE", "LONG", t, bars, hl,
                         bars[t]["close"] + 0.61 * (rim - cup_lo), c,
                         {"pk": [a, b], "tro": [base] + hand,
                          "line": rim}))
    return out


# ── FLAGS ───────────────────────────────────────────────────────────────────
def flags(bars, v20, max_flag=15, window=3):
    """A flag is a PAUSE, so the break must come straight out of it.

    MM, 18/09, checking the gallery: 3PLAND entered at 61.81 with a stop at
    39.00 - a 37% risk on a pattern the books call tight. The fault was here:
    the breakout was allowed up to 10 bars after the flag, so price ran a long
    way from the flag's low before "breaking out", and the stop was left
    behind at a price that had nothing to do with the trade. The window is
    now 3 bars, and the risk is capped at the flag's own depth.
    """
    out = []
    n = len(bars)
    for i in range(25, n - max_flag - 2):
        pole_lo = min(b["low"] for b in bars[i - 20:i])
        pole_hi = max(b["high"] for b in bars[i - 20:i])
        pole = pole_hi - pole_lo
        run = (bars[i]["close"] - bars[i - 20]["close"]) / bars[i - 20]["close"]
        if abs(run) < 0.12:
            continue
        for j in range(5, max_flag + 1):
            if i + j >= n - 2:
                break
            seg = bars[i:i + j]
            sh = max(b["high"] for b in seg)
            sl = min(b["low"] for b in seg)
            if sh <= sl or (sh - sl) > 0.5 * pole:
                continue
            drift = (seg[-1]["close"] - seg[0]["close"]) / seg[0]["close"]
            if run > 0:
                t = _break_up(bars, i + j - 1, sh, window)
                if not t:
                    continue
                # the stop must sit INSIDE the flag, not wherever price was
                if (bars[t]["close"] - sl) / bars[t]["close"] > 0.12:
                    continue
                c = {
                    "the pole is steep": run >= 0.20,
                    "the flag tilts AGAINST the trend": drift < 0,
                    "the flag is tight": (sh - sl) <= 0.35 * pole,
                    "under three weeks": j <= 15,
                    "volume recedes in the flag": _vol_falls(bars, i, i + j - 1),
                    "volume expands on the breakout":
                        bool(v20[t] and (bars[t].get("volume") or 0)
                             >= 1.5 * v20[t]),
                }
                out.append(_pack("BULL-FLAG", "LONG", t, bars, sl,
                                 bars[t]["close"] + 0.46 * pole, c,
                                 {"pk": [i, i + j - 1], "tro": [i, i + j - 1],
                                  "line": sh}))
            else:
                t = _break_dn(bars, i + j - 1, sl, window)
                if not t:
                    continue
                if (sh - bars[t]["close"]) / bars[t]["close"] > 0.12:
                    continue
                c = {
                    "the pole is steep": run <= -0.20,
                    "the flag tilts AGAINST the trend": drift > 0,
                    "the flag is tight": (sh - sl) <= 0.35 * pole,
                    "under three weeks": j <= 15,
                    "volume recedes in the flag": _vol_falls(bars, i, i + j - 1),
                    "volume expands on the breakout":
                        bool(v20[t] and (bars[t].get("volume") or 0)
                             >= 1.5 * v20[t]),
                }
                out.append(_pack("BEAR-FLAG", "SHORT", t, bars, sh,
                                 bars[t]["close"] - 0.46 * pole, c,
                                 {"pk": [i, i + j - 1], "tro": [i, i + j - 1],
                                  "line": sl}))
            break
    return out


def find_all(bars, min_bars=20, max_bars=140, gap=10):
    """Every pattern in this series, on whatever timeframe it is.

    OVERLAP SUPPRESSION, added 18/09. The first count produced 231,239
    patterns, and some of that was real - MM was right that the full set gives
    plenty of trades - but some was the same formation counted five times.
    The flag scanner in particular walks every bar, so one pole and one flag
    emit a fresh "pattern" at each of several breakout bars, and a backtest
    would then take the same trade five times and call it five results.

    So within one stock and one pattern name, a second instance inside `gap`
    bars of the last one is dropped, keeping the better-scoring of the two.
    A trader watching that chart sees ONE flag, and so should the record.
    """
    if len(bars) < 60:
        return []
    v20 = vol_avg(bars)
    out = []
    out += triangles(bars, v20, min_bars, max_bars)
    out += doubles(bars, v20)
    out += head_shoulders(bars, v20)
    out += cup_handle(bars, v20)
    out += flags(bars, v20)
    # one per (pattern, breakout bar); keep the best-scoring
    best = {}
    for s in out:
        k = (s["pattern"], s["tbar"])
        if k not in best or s["score"] > best[k]["score"]:
            best[k] = s
    kept = []
    for s in sorted(best.values(), key=lambda s: s["tbar"]):
        prev = None
        for q in reversed(kept):
            if q["pattern"] == s["pattern"]:
                prev = q
                break
        if prev is not None and s["tbar"] - prev["tbar"] < gap:
            if s["score"] > prev["score"]:
                kept[kept.index(prev)] = s
            continue
        kept.append(s)
    return kept


# ═══ the candlesticks, from book four ═══



def _b(x):
    return abs(x["close"] - x["open"])


def _rng(x):
    return max(x["high"] - x["low"], 1e-9)


def _up(x):
    return x["close"] > x["open"]


def _dn(x):
    return x["close"] < x["open"]


def _upper(x):
    return x["high"] - max(x["close"], x["open"])


def _lower(x):
    return min(x["close"], x["open"]) - x["low"]


def _mid(x):
    return (x["open"] + x["close"]) / 2.0


def _doji(x, tol=0.10):
    return _b(x) <= tol * _rng(x)


# ── SINGLE CANDLES ──────────────────────────────────────────────────────────
def marubozu(x):
    """#5/#6/#65. Long body, almost no wicks - one side in full control."""
    if _b(x) < 0.80 * _rng(x):
        return None
    if _upper(x) > 0.12 * _rng(x) or _lower(x) > 0.12 * _rng(x):
        return None
    return "LONG" if _up(x) else "SHORT"


def belt_hold(x, prev):
    """#12/#71/#72/#100. Opens at the extreme, closes at the other end."""
    if _b(x) < 0.60 * _rng(x):
        return None
    if _up(x) and _lower(x) <= 0.05 * _rng(x) and x["open"] < prev["low"]:
        return "LONG"
    if _dn(x) and _upper(x) <= 0.05 * _rng(x) and x["open"] > prev["high"]:
        return "SHORT"
    return None


def hammer(x):
    """#1. Lower shadow at least 2x the body, small upper shadow."""
    if _b(x) <= 0:
        return None
    if _lower(x) >= 2 * _b(x) and _upper(x) <= 0.5 * _b(x) \
            and _b(x) <= 0.4 * _rng(x):
        return "LONG"
    return None


def takuri(x):
    """#82/#99. A hammer with a shadow three to five times the body -
    the book calls it the deepest rejection there is."""
    if _b(x) <= 0:
        return None
    if _lower(x) >= 3 * _b(x) and _upper(x) <= 0.25 * _rng(x):
        return "LONG"
    return None


def inverted_hammer(x):
    """#3. Long upper shadow after a decline."""
    if _b(x) <= 0:
        return None
    if _upper(x) >= 2 * _b(x) and _lower(x) <= 0.5 * _b(x) \
            and _b(x) <= 0.4 * _rng(x):
        return "LONG"
    return None


def shooting_star(x):
    """#4. The same shape after a rally - bearish."""
    if _b(x) <= 0:
        return None
    if _upper(x) >= 2 * _b(x) and _lower(x) <= 0.5 * _b(x) \
            and _b(x) <= 0.4 * _rng(x):
        return "SHORT"
    return None


def dragonfly(x):
    """#8/#84. Open = high = close, long lower shadow."""
    return "LONG" if (_doji(x) and _lower(x) >= 0.6 * _rng(x)
                      and _upper(x) <= 0.1 * _rng(x)) else None


def gravestone(x):
    """#9/#83. The mirror."""
    return "SHORT" if (_doji(x) and _upper(x) >= 0.6 * _rng(x)
                       and _lower(x) <= 0.1 * _rng(x)) else None


def indecision(x):
    """#7/#10/#11/#13/#64/#85. Doji, spinning top, long-legged doji,
    high-wave, four price doji - nobody is in control."""
    if _rng(x) <= 0:
        return True                       # four price doji
    if _doji(x):
        return True
    if _b(x) <= 0.30 * _rng(x) and _upper(x) >= 0.25 * _rng(x) \
            and _lower(x) >= 0.25 * _rng(x):
        return True                       # spinning top / high wave
    return False


# ── TWO CANDLES ─────────────────────────────────────────────────────────────
def engulfing(a, x):
    """#15/#16. The body completely covers the previous body."""
    if _up(x) and _dn(a) and x["close"] >= a["open"] and x["open"] <= a["close"]:
        return "LONG"
    if _dn(x) and _up(a) and x["close"] <= a["open"] and x["open"] >= a["close"]:
        return "SHORT"
    return None


def harami(a, x):
    """#19/#20/#21/#22. The small candle sits inside the previous body."""
    if _b(a) <= 0:
        return None
    inside = max(x["open"], x["close"]) <= max(a["open"], a["close"]) and \
        min(x["open"], x["close"]) >= min(a["open"], a["close"])
    if not inside or _b(x) > 0.6 * _b(a):
        return None
    return "LONG" if _dn(a) else "SHORT"


def piercing(a, x):
    """#17. Opens below the prior low, closes above the prior midpoint."""
    if _dn(a) and _up(x) and x["open"] < a["low"] \
            and a["close"] < x["close"] < a["open"] \
            and x["close"] > _mid(a):
        return "LONG"
    return None


def dark_cloud(a, x):
    """#18. Opens above the prior high, closes below the prior midpoint."""
    if _up(a) and _dn(x) and x["open"] > a["high"] \
            and a["open"] < x["close"] < a["close"] \
            and x["close"] < _mid(a):
        return "SHORT"
    return None


def kicker(a, x):
    """#42/#43/#75/#76. Two marubozus with a clean gap and no overlap -
    the book calls it the most violent sentiment flip there is."""
    if _dn(a) and _up(x) and x["open"] > a["open"]:
        return "LONG"
    if _up(a) and _dn(x) and x["open"] < a["open"]:
        return "SHORT"
    return None


def tweezer(a, x):
    """#23/#24/#63. Two candles rejecting the same level twice."""
    tol = 0.002
    if _dn(a) and _up(x) and abs(a["low"] - x["low"]) <= tol * x["low"]:
        return "LONG"
    if _up(a) and _dn(x) and abs(a["high"] - x["high"]) <= tol * x["high"]:
        return "SHORT"
    return None


def counterattack(a, x):
    """#78/#79. Closes at the SAME level as the prior close - the prior
    move is neutralised. The book treats this as a reversal WARNING."""
    tol = 0.002
    if abs(a["close"] - x["close"]) > tol * x["close"]:
        return None
    if _dn(a) and _up(x):
        return "LONG"
    if _up(a) and _dn(x):
        return "SHORT"
    return None


def hook_reversal(a, x):
    """#67. Lower low but closes above the prior high (or the mirror).
    The book: "one of the strongest clues of an upcoming trend reversal"
    and explicitly a FALSE BREAKOUT TRAP marker."""
    if x["low"] < a["low"] and x["close"] > a["high"]:
        return "LONG"
    if x["high"] > a["high"] and x["close"] < a["low"]:
        return "SHORT"
    return None


def separating(a, x):
    """#73/#74. Same open as the prior candle, opposite colour - the
    pullback failed and the trend resumes."""
    tol = 0.002
    if abs(a["open"] - x["open"]) > tol * x["open"]:
        return None
    if _dn(a) and _up(x):
        return "LONG"
    if _up(a) and _dn(x):
        return "SHORT"
    return None


def window(a, x):
    """#47/#48. A true gap with no overlap at all."""
    if x["low"] > a["high"]:
        return "LONG"
    if x["high"] < a["low"]:
        return "SHORT"
    return None


# ── THREE CANDLES ───────────────────────────────────────────────────────────
def star(a, b, x):
    """#30/#31/#32/#33. Long body, small body, long body back into the
    first - the book's most reliable reversal."""
    if _b(b) > 0.4 * _b(a) or _b(a) <= 0:
        return None
    if _dn(a) and _up(x) and x["close"] > _mid(a) and b["close"] < a["close"]:
        return "LONG"
    if _up(a) and _dn(x) and x["close"] < _mid(a) and b["close"] > a["close"]:
        return "SHORT"
    return None


def abandoned_baby(a, b, x):
    """#29/#87. A star where the middle candle gaps on BOTH sides."""
    if not _doji(b):
        return None
    if _dn(a) and _up(x) and b["high"] < a["low"] and b["high"] < x["low"]:
        return "LONG"
    if _up(a) and _dn(x) and b["low"] > a["high"] and b["low"] > x["high"]:
        return "SHORT"
    return None


def three_soldiers(a, b, x):
    """#34/#35. Three long candles marching the same way, each opening
    inside the last body and closing near its high."""
    g = [a, b, x]
    if all(_up(c) for c in g) and \
            all(_b(c) >= 0.5 * _rng(c) for c in g) and \
            x["close"] > b["close"] > a["close"] and \
            a["close"] < b["open"] < a["high"] and \
            b["close"] < x["open"] < b["high"]:
        return "LONG"
    if all(_dn(c) for c in g) and \
            all(_b(c) >= 0.5 * _rng(c) for c in g) and \
            x["close"] < b["close"] < a["close"] and \
            a["low"] < b["open"] < a["close"] and \
            b["low"] < x["open"] < b["close"]:
        return "SHORT"
    return None


def three_inside(a, b, x):
    """#36/#37. Harami, then the third candle confirms it."""
    h = harami(a, b)
    if h == "LONG" and x["close"] > a["high"]:
        return "LONG"
    if h == "SHORT" and x["close"] < a["low"]:
        return "SHORT"
    return None


def three_outside(a, b, x):
    """#38/#39. Engulfing, then the third candle confirms it."""
    e = engulfing(a, b)
    if e == "LONG" and x["close"] > b["close"]:
        return "LONG"
    if e == "SHORT" and x["close"] < b["close"]:
        return "SHORT"
    return None


def advance_block(a, b, x):
    """#54. Three green candles with SHRINKING bodies and growing upper
    wicks - the book's early warning that the rally is tiring."""
    g = [a, b, x]
    if not all(_up(c) for c in g):
        return None
    if _b(a) > _b(b) > _b(x) and _upper(x) > _b(x):
        return "SHORT"
    return None


def deliberation(a, b, x):
    """#55. Two strong green candles then a small one right at the top."""
    if _up(a) and _up(b) and _b(x) <= 0.3 * _b(b) and x["close"] > b["close"] * 0.99:
        return "SHORT"
    return None


def three_clouds(a, b, x):
    """#89. Three green candles making LOWER highs - distribution."""
    if all(_up(c) for c in (a, b, x)) and a["high"] > b["high"] > x["high"]:
        return "SHORT"
    return None


# ── THE THREE VERDICTS ──────────────────────────────────────────────────────
GO_1 = (marubozu, hammer, takuri, inverted_hammer, shooting_star,
        dragonfly, gravestone)
GO_2 = (engulfing, piercing, dark_cloud, kicker, tweezer, separating,
        window, harami)
GO_3 = (star, abandoned_baby, three_soldiers, three_inside, three_outside)
TRAP_2 = (counterattack, hook_reversal)
TRAP_3 = (advance_block, deliberation, three_clouds)


def read(bars, i, side):
    """What do the candles at bar i say about a breakout in `side`?

    Returns (verdict, names) where verdict is +1 agree, 0 neutral,
    -1 the book says this is a trap. Looks at bars i-2 .. i only.
    """
    if i < 3:
        return 0, []
    x, b, a = bars[i], bars[i - 1], bars[i - 2]
    agree, against = [], []

    def add(fn, v):
        if v is None:
            return
        (agree if v == side else against).append(fn.__name__)

    for fn in GO_1:
        add(fn, fn(x))
    add(belt_hold, belt_hold(x, b))
    for fn in GO_2:
        add(fn, fn(b, x))
    for fn in GO_3:
        add(fn, fn(a, b, x))

    traps = []
    for fn in TRAP_2:
        v = fn(b, x)
        if v is not None and v != side:
            traps.append(fn.__name__)
    for fn in TRAP_3:
        v = fn(a, b, x)
        if v is not None and v != side:
            traps.append(fn.__name__)

    if traps:
        return -1, traps
    if indecision(x) and not agree:
        return 0, ["indecision"]
    if agree and len(agree) > len(against):
        return 1, agree
    if against and len(against) > len(agree):
        return -1, against
    return 0, agree + against


# ═══════════════════════════════════════════════════════════════════════════
#  THE LIVE PART
# ═══════════════════════════════════════════════════════════════════════════
SWING_FRESH, INTRA_FRESH = 12, 26
MIN_TURNOVER = 5e7
# The F&O names are what the intraday side trades, and there are only about
# 216 of them - at Zerodha's pacing that is a sweep every ninety seconds
# instead of every five minutes. The wider list only matters for the swing
# patterns, which are built from DAILY bars and cannot change more than once
# or twice an hour, so it runs on its own slower clock.
FAST_EVERY = 120                 # seconds - the F&O names
FULL_EVERY = 900                 # seconds - everything else
FPORT = 5056
COST_DEL, COST_FUT = 0.222, 0.07


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
SITE = os.path.join(HERE, "WEBSITE", "ascendra_desk.html")
LIVEF = os.path.join(DATA, "formations_live.json")
LOGF = os.path.join(DATA, "formations_log.json")
DAILYF = os.path.join(DATA, "formations_daily.json")


def hold(code=0):
    # Started by the desk (one window, 21/09) there is nobody at this prompt,
    # and waiting here would swallow the keys MM types at the desk's own menu.
    if os.environ.get("FM_CHILD"):
        sys.exit(code)
    try:
        input("\n  Press Enter to close this window. ")
    except Exception:
        pass
    sys.exit(code)


def ensure_kiteconnect():
    """The window that runs this may be a different Python from the one that
    runs the desk, and the Zerodha library is installed per-Python. So rather
    than fail, install it into THIS interpreter and carry on - which is what
    START THE DESK already does for itself."""
    try:
        import kiteconnect  # noqa
        return
    except ImportError:
        pass
    print("  The Zerodha library is not installed for this Python.")
    print("  Installing it now - this takes about twenty seconds...\n")
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
    print(f"""
  COULD NOT INSTALL IT AUTOMATICALLY.

  Open a Command Prompt and run this one line:

      "{sys.executable}" -m pip install kiteconnect

  Then double-click FORMATIONS LIVE again.
""")
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
        print(f"\n  TODAY'S LOGIN IS MISSING (saved token is from "
              f"{tok.get('date')}).\n  Run connect.py in '5 LIVE FEED', then "
              f"start this again.\n")
        hold(1)
    from kiteconnect import KiteConnect
    k = KiteConnect(api_key=api_key)
    k.set_access_token(tok["access_token"])
    k.quote(["NSE:RELIANCE"])
    return k


# ── the pieces lifted from the backtest, unchanged ─────────────────────────
FRAC = {"ASC-TRIANGLE": 0.70, "DESC-TRIANGLE": 0.50, "SYM-TRIANGLE": 0.58,
        "DOUBLE-BOTTOM": 0.73, "DOUBLE-TOP": 0.65,
        "INV-HEAD-SHOULDERS": 0.71, "HEAD-SHOULDERS": 0.55,
        "CUP-HANDLE": 0.61, "BULL-FLAG": 0.46, "BEAR-FLAG": 0.46}


def book_target(s):
    f = FRAC.get(s["pattern"])
    if not f:
        return s["target"]
    bx = s["breakout_px"]
    h = abs(s["target"] - bx) / f
    return bx + h if s["side"] == "LONG" else bx - h


def by_day(bars, n):
    out, cur, day = [], [], None
    for i, b in enumerate(bars):
        d = str(b["date"])[:10]
        if day is not None and d != day:
            cur = []
        day = d
        cur.append((i, b))
        if len(cur) == n:
            g = [x for _, x in cur]
            out.append({"date": g[0]["date"], "open": g[0]["open"],
                        "high": max(x["high"] for x in g),
                        "low": min(x["low"] for x in g),
                        "close": g[-1]["close"],
                        "volume": sum(x.get("volume") or 0 for x in g),
                        "i0": cur[0][0], "i1": cur[-1][0]})
            cur = []
    return out


def sessions(bars):
    out, cur, day = [], [], None
    for i, b in enumerate(bars):
        d = str(b["date"])[:10]
        if day is not None and d != day and cur:
            g = [x for _, x in cur]
            out.append({"date": g[0]["date"], "open": g[0]["open"],
                        "high": max(x["high"] for x in g),
                        "low": min(x["low"] for x in g),
                        "close": g[-1]["close"],
                        "volume": sum(x.get("volume") or 0 for x in g),
                        "i0": cur[0][0], "i1": cur[-1][0]})
            cur = []
        day = d
        cur.append((i, b))
    if cur:
        g = [x for _, x in cur]
        out.append({"date": g[0]["date"], "open": g[0]["open"],
                    "high": max(x["high"] for x in g),
                    "low": min(x["low"] for x in g),
                    "close": g[-1]["close"],
                    "volume": sum(x.get("volume") or 0 for x in g),
                    "i0": cur[0][0], "i1": cur[-1][0]})
    return out


def turnover(bars):
    out, vals = [0.0] * len(bars), []
    for i, b in enumerate(bars):
        if i >= 20:
            out[i] = statistics.median(vals[i - 20:i])
        vals.append(b["close"] * (b.get("volume") or 0))
    return out


def retest(low, start, long, line, window):
    touched = None
    for j in range(start, min(start + window, len(low))):
        b = low[j]
        back = (b["low"] <= line) if long else (b["high"] >= line)
        if back and touched is None:
            touched = j
        if touched is not None and j > touched:
            if (b["close"] > line) if long else (b["close"] < line):
                return j, b["close"]
    return None, None


# THE SHAPE HAS TO BE THE SHAPE (30/09, from MM asking whether the patterns
# being picked are the right ones).
#
# rate() scores the formation out of 10 from five things, and the shape itself
# is worth at most 2 of them - a "loose formation" earns 0.5. Beating the
# industry is worth 3, candles 2, volume 1, the 50 EMA 1. So a badly-shaped
# formation could still reach the rating floor of 8 on everything except its
# shape, and be traded. Of the seven calls rated 8+ on the book on 30/09, five
# had failed one of their own criteria, and four of those were "valleys within
# 1% of each other" - which IS the definition of a double bottom. A W whose two
# bottoms are at different prices is not a double bottom with a low score; it
# is a different shape wearing the name.
#
# These are the criteria that define each shape rather than describe its
# quality. If one of them failed, the pattern was not found, and no amount of
# industry strength makes it found. Volume, promptness and width stay as
# scoring inputs - they are quality, not identity. Triangles and flags are not
# listed: their geometry is enforced when the pattern is built, so there is no
# defining criterion left to fail here.
SHAPE_GATE = {
    "DOUBLE-BOTTOM":       ["valleys within 1% of each other"],
    "DOUBLE-TOP":          ["peaks within 1% of each other"],
    "INV-HEAD-SHOULDERS":  ["shoulders within 3% of each other",
                            "the head is clearly deeper"],
    "HEAD-SHOULDERS":      ["shoulders within 3% of each other",
                            "the head is clearly higher"],
    "CUP-HANDLE":          ["rims within 2% of each other"],
}


def rate(r):
    """The rating, exactly as it was backtested. 0-10."""
    pts, why = 0, []
    q = r.get("score") or 0
    if q >= 85:
        pts += 2; why.append("textbook formation")
    elif q >= 70:
        pts += 1.5; why.append("good formation")
    elif q >= 55:
        pts += 0.5; why.append("loose formation")
    else:
        why.append("weak formation")
    # The shape veto, before anything else can compensate for it. Same shape as
    # the candle veto below: a hard 0, with the reason named.
    _miss = [k for k in SHAPE_GATE.get(r.get("pattern") or "", [])
             if (r.get("crit") or {}).get(k) is False]
    if _miss:
        return 0, why + [f"NOT THIS SHAPE - {_miss[0]}"]
    v = r.get("vs_sec")
    if v is None:
        why.append("no industry data")
    elif 10 <= v < 35:
        pts += 3; why.append(f"beating its industry by {v:.0f} points")
    elif 0 <= v < 10:
        pts += 1; why.append(f"just ahead of its industry ({v:.0f})")
    elif v >= 35:
        why.append(f"STRETCHED {v:.0f} points above its industry")
    else:
        why.append(f"lagging its industry by {abs(v):.0f} points")
    c = r.get("cand")
    if c is not None and c > 0:
        pts += 2; why.append("candles confirm")
    elif c is not None and c < 0:
        return 0, why + ["CANDLES SAY TRAP - stand aside"]
    else:
        why.append("candles undecided")
    if r.get("vol"):
        pts += 1; why.append("volume expanded on the breakout")
    else:
        why.append("volume did not expand")
    if r.get("trend50"):
        pts += 1; why.append("with the 50 EMA")
    else:
        why.append("against the 50 EMA")
    b = r.get("breadth")
    if b is None:
        pass
    elif b >= 55:
        pts += 1; why.append("market is strong")
    elif b >= 40:
        pts += 0.5; why.append("market is mixed")
    else:
        why.append("MARKET IS WEAK")
    return round(min(10, pts)), why


# ── the scan, same rules as the backtest ───────────────────────────────────
def scan_one(sym, m15, sector_of, sec_med, breadth, fo, daily=None):
    """THE SWING SIDE HAD NEVER FIRED ONCE (21/09).

    Not a tuning problem - it could not run at all. The sweep downloads 70
    days of 15-minute candles, sessions() folds those into about 48 daily
    bars, and the line below demands 80 before it will look at a stock. So
    every name was skipped, silently, on every sweep since the day this was
    written. Every call MM has ever had from this scanner was intraday, and
    swing is the better half: rated 9-10 it tested at Rs 2,916 a trade
    against Rs 1,896 for intraday.

    The fix is to stop manufacturing daily bars out of a two-month intraday
    window and just ask the exchange for the daily chart - two years of it,
    once a day, since a daily candle cannot change until the close. `daily`
    is that chart. Each of its bars is told which hourly bar it ends on, so
    the retest entry still happens on the hourly chart exactly as before.
    Bars older than the intraday window get no such index and are skipped by
    the freshness test below, which is correct: a breakout from three months
    ago is not a call."""
    res = []
    h1 = by_day(m15, 4)
    if daily:
        last = {}
        for q, b in enumerate(h1):
            last[str(b["date"])[:10]] = q
        day_hi = []
        for b in daily:
            c = dict(b)
            c["i1"] = last.get(str(b["date"])[:10])
            day_hi.append(c)
    else:
        day_hi = sessions(h1)        # the old behaviour, if the daily
                                     # download failed for this name
    for section in ("SWING", "INTRADAY"):
        if section == "SWING":
            hi, low, mn, mx, fresh, widen = day_hi, h1, 12, 90, SWING_FRESH, True
        else:
            if sym not in fo:
                continue
            hi, low, mn, mx, fresh, widen = h1, m15, 20, 140, INTRA_FRESH, False
        if len(hi) < 80 or len(low) < 200:
            continue
        cl = [b["close"] for b in hi]
        e20, e50 = ema(cl, 20), ema(cl, 50)
        rs = rsi(cl)
        _, _, mhist = macd(cl)
        v20 = vol_avg(hi)
        atrh = atr(hi)
        tn = turnover(hi)
        try:
            found = find_all(hi, min_bars=mn, max_bars=mx)
        except Exception:
            continue
        for s in found:
            k = s["tbar"]
            if tn[k] < MIN_TURNOVER:
                continue
            piv = list(s.get("pk") or []) + list(s.get("tro") or [])
            if piv and k < max(piv) + RIGHT:
                continue
            j0 = hi[k].get("i1")
            if j0 is None or len(low) - 1 - j0 > fresh:
                continue
            long = s["side"] == "LONG"
            if (not long) and sym not in fo:
                continue
            A = atrh[k]
            if not A or A <= 0:
                continue
            struct, bx = s["stop"], s["breakout_px"]
            t2 = book_target(s)
            line = s.get("line")
            if line is None:
                continue
            if not ((struct < bx < t2) if long else (t2 < bx < struct)):
                continue
            j, e = retest(low, j0 + 1, long, line, fresh + 10)
            if j is not None and e:
                state, entry = "TRIGGERED", e
            else:
                state, entry = "WAITING", line
            if not entry or entry <= 0:
                continue
            st = struct
            if widen:
                wide = entry - 3 * A if long else entry + 3 * A
                st = min(struct, wide) if long else max(struct, wide)
            if not ((st < entry < t2) if long else (t2 < entry < st)):
                continue
            rk = abs(entry - st) / entry
            if not (0.002 < rk < 0.40):
                continue
            cv, cn = read(hi, k, s["side"])
            sec = sector_of.get(sym)
            r60 = (cl[k] / cl[k - 60] - 1) if k >= 60 and cl[k - 60] else None
            med = sec_med.get(sec)
            vs = (100 * (r60 - med)) if (r60 is not None and med is not None) else None
            row = {"sym": sym, "section": section, "state": state,
                   "at": str(hi[k]["date"])[:16], "pattern": s["pattern"],
                   "side": s["side"], "score": round(s["score"], 1),
                   "entry": round(entry, 2), "stop": round(st, 2),
                   "target": round(t2, 2), "line": round(line, 2),
                   "last": round(low[-1]["close"], 2),
                   "risk_pct": round(100 * rk, 2),
                   "reward_pct": round(100 * abs(t2 - entry) / entry, 2),
                   "rr": round(abs(t2 - entry) / abs(entry - st), 2),
                   "sec": sec, "vs_sec": round(vs, 1) if vs is not None else None,
                   "breadth": round(breadth, 0) if breadth is not None else None,
                   "cand": cv, "cand_names": cn,
                   # THE CHART. MM asked for tap-to-see-the-formation on day
                   # one and the live cards never carried their bars, so they
                   # had nothing to draw. The pattern window on the higher
                   # timeframe with its own pivot points, and the lower
                   # timeframe around the entry - the same two charts the
                   # historical cards show.
                   "D": [[round(x["open"], 2), round(x["high"], 2),
                          round(x["low"], 2), round(x["close"], 2)]
                         for x in hi[max(0, k - 70):min(len(hi), k + 8)]],
                   "tb": k - max(0, k - 70),
                   "ph": [q - max(0, k - 70) for q in (s.get("pk") or [])
                          if max(0, k - 70) <= q < min(len(hi), k + 8)],
                   "pl": [q - max(0, k - 70) for q in (s.get("tro") or [])
                          if max(0, k - 70) <= q < min(len(hi), k + 8)],
                   "H": [[round(x["open"], 2), round(x["high"], 2),
                          round(x["low"], 2), round(x["close"], 2)]
                         for x in low[max(0, j0 - 25):]],
                   "eb": max(0, (j if j is not None else j0 + 1)
                             - max(0, j0 - 25)),
                   "vol": bool(v20[k] and (hi[k].get("volume") or 0) >= 1.5 * v20[k]),
                   "trend50": bool(e50[k] is not None and
                                   ((cl[k] > e50[k]) if long else (cl[k] < e50[k]))),
                   "ind": {"ema20": round(e20[k], 2) if e20[k] else None,
                           "ema50": round(e50[k], 2) if e50[k] else None,
                           "rsi": round(rs[k], 1) if rs[k] else None,
                           "macd_hist": round(mhist[k], 3) if mhist[k] is not None else None,
                           "vol_x": round((hi[k].get("volume") or 0) / v20[k], 2) if v20[k] else None,
                           "atr_pct": round(100 * A / cl[k], 2)},
                   "crit": s.get("crit") or {}}
            row["vs_sec_v"] = vs
            row["rating"], row["why"] = rate(
                {"score": s["score"], "vs_sec": vs, "cand": cv,
                 "vol": row["vol"], "trend50": row["trend50"], "breadth": breadth,
                 "pattern": s["pattern"], "crit": s.get("crit") or {}})
            row["key"] = f"{sym}|{section}|{s['pattern']}|{row['at']}"
            res.append(row)
    return res


def fetch(kite, tokens, syms, days=70):
    """15-minute bars for each name. Paced, and a failure never stops the sweep."""
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
            # DROP THE CANDLE THAT IS STILL FORMING. Zerodha hands back the
            # in-progress 15-minute bar, and a breakout "close" on a bar with
            # ten minutes left to run is not a close at all - it can be gone
            # by the time the bar ends. Only finished bars decide anything.
            bars = [{"date": str(x["date"]), "open": float(x["open"]),
                     "high": float(x["high"]), "low": float(x["low"]),
                     "close": float(x["close"]),
                     "volume": float(x.get("volume") or 0)} for x in raw]
            if bars:
                try:
                    lastb = datetime.fromisoformat(bars[-1]["date"][:19])
                    if (datetime.now() - lastb).total_seconds() < 15 * 60:
                        bars.pop()
                except Exception:
                    bars.pop()
            if len(bars) > 200:
                out[s] = bars
        except Exception:
            pass
        time.sleep(0.36)
    return out


def fetch_daily(kite, tokens, syms, years=2):
    """The daily chart, straight from the exchange, once a day.

    A daily candle cannot change until the session closes, so re-downloading
    two years of them every fifteen minutes would be a thousand pointless
    requests an hour. This keeps them in DATA\\formations_daily.json and only
    goes back to Zerodha when the file is from an earlier day. On a fresh
    morning it costs one request per stock; for the rest of the day, none.
    """
    today = date.today().isoformat()
    cache = {}
    try:
        old = json.load(open(DAILYF, encoding="utf8"))
        if old.get("date") == today:
            cache = old.get("bars") or {}
    except Exception:
        pass
    todo = [s for s in syms if s not in cache and s in tokens]
    if todo:
        print(f"  daily chart: {len(todo)} to download "
              f"(once a day, then it is cached)", flush=True)
        frm = (date.today() - timedelta(days=int(years * 366))).isoformat()
        to = date.today().isoformat()
        for n, s in enumerate(todo, 1):
            try:
                raw = kite.historical_data(tokens[s], frm, to, "day") or []
                rows = [[str(x["date"])[:10], float(x["open"]),
                         float(x["high"]), float(x["low"]), float(x["close"]),
                         float(x.get("volume") or 0)] for x in raw]
                if len(rows) > 120:
                    cache[s] = rows
            except Exception:
                pass
            time.sleep(0.36)
            if n % 200 == 0:
                print(f"    daily {n}/{len(todo)}", flush=True)
        try:
            json.dump({"date": today, "bars": cache},
                      open(DAILYF, "w", encoding="utf8"))
        except Exception:
            pass
    out = {}
    for s, rows in cache.items():
        out[s] = [{"date": r[0], "open": r[1], "high": r[2], "low": r[3],
                   "close": r[4], "volume": r[5]} for r in rows]
    return out


def market_context(BARS, sector_of, DAILY=None):
    """Breadth, and each industry's median 60-day move.

    THIS WAS SILENTLY RETURNING NOTHING (22/09). It asked for sixty daily
    bars per stock and built them out of the seventy-day intraday download,
    which yields about forty-eight - so every single stock hit the `continue`
    below and the function returned (None, {}) on every sweep it has ever run.

    The cost was not small. The rating gives three points for beating your own
    industry and one for a strong market; with both missing the most any call
    could score was SIX, and MM has been waiting for a 9 or a 10 that could
    not arithmetically exist. Same root cause as the swing side: two months of
    intraday candles cannot stand in for a daily chart.

    It now uses the real daily download when there is one, and only falls back
    to the old way if there is not.
    """
    above = tot = 0
    bysec = {}
    src = DAILY if DAILY else None
    for s, b in BARS.items():
        d = (src or {}).get(s)
        if not d:
            d = sessions(by_day(b, 4))
        if len(d) < 60:
            continue
        cl = [x["close"] for x in d]
        m = sma(cl, 50)
        if m[-1]:
            tot += 1
            above += 1 if cl[-1] > m[-1] else 0
        if cl[-61]:
            bysec.setdefault(sector_of.get(s), []).append(cl[-1] / cl[-61] - 1)
    med = {k: statistics.median(v) for k, v in bysec.items()
           if k and len(v) >= 5}
    return (100.0 * above / tot if tot else None), med


# ── the record: every call this ever makes, and what became of it ──────────
def load_log():
    try:
        return json.load(open(LOGF, encoding="utf8"))
    except Exception:
        return {}


def save_log(L):
    tmp = LOGF + ".tmp"
    json.dump(L, open(tmp, "w", encoding="utf8"))
    os.replace(tmp, LOGF)


def update_record(LOG, live, BARS):
    """Add anything new, then settle anything already open.

    THE RECORD WAS BOOKING TRADES THAT HAPPENED BEFORE THE CALL EXISTED
    (23/09). MM: "how are we making such mistakes. it is very risky for me to
    place trades."

    He is right and this was the worst bug yet. A pattern can complete on an
    older candle and only be found on a later sweep - the scanner downloads
    seventy days of history every time it wakes. The settle loop then walked
    that history from the PATTERN'S OWN CANDLE, so it would find a fill and an
    exit that had both already happened, days before the call was ever shown.
    JIOFIN appeared on MM's screen this morning; the record had it bought at
    09:30 and stopped at 13:45 the PREVIOUS day. 165 of 191 rows were like
    that. A record built that way is not a record of anything takeable, and
    any average computed from it is fiction.

    Two rules now, and they are not negotiable:

      1. Nothing is settled using a candle older than the moment the call was
         first written down. That is what `first_seen` is for, and it was
         being ignored.

      2. A call whose entry had ALREADY filled before it was first seen is
         marked MISSED and never produces a result. It was not takeable: by
         the time anyone could have acted, the entry was behind them.

    The same guard that makes the backtests honest - a trade may only use
    information that existed when it was made - now applies to the live record
    as well. It should have from the start.
    """
    added = 0
    now = datetime.now().isoformat(timespec="seconds")
    for r in live:
        k = r["key"]
        if k not in LOG:
            LOG[k] = {**r, "first_seen": now,
                      "status": "WAITING" if r["state"] == "WAITING" else "OPEN",
                      "filled_at": None, "closed_at": None,
                      "exit_px": None, "result_pct": None}
            # Already filled by the time we saw it? Then it was never ours.
            if r["state"] != "WAITING":
                LOG[k]["status"] = "MISSED"
                LOG[k]["why_missed"] = ("the entry had already filled before "
                                        "this call was made")
            added += 1
        else:
            LOG[k]["last"] = r["last"]
            LOG[k]["rating"] = r["rating"]

    closed = 0
    for k, c in LOG.items():
        if c.get("status") in ("TARGET", "STOP", "MISSED"):
            continue
        b = BARS.get(c["sym"])
        if not b:
            continue
        long = c["side"] == "LONG"
        e, st, tg = c["entry"], c["stop"], c["target"]

        # RULE 1: never look at a candle from before the call was made.
        seen = (c.get("first_seen") or "")[:16].replace("T", " ")
        floor = max(str(c.get("at") or "")[:16], seen)
        start = None
        for i, x in enumerate(b):
            if str(x["date"])[:16] >= floor:
                start = i
                break
        if start is None:
            continue

        if c["status"] == "WAITING":
            for i in range(start, len(b)):
                x = b[i]
                if (x["close"] > c["line"]) if long else (x["close"] < c["line"]):
                    if (x["low"] <= c["line"]) if long else (x["high"] >= c["line"]):
                        c["status"] = "OPEN"
                        c["filled_at"] = str(x["date"])[:16]
                        start = i
                        break
        if c["status"] != "OPEN":
            continue
        for i in range(start, len(b)):
            x = b[i]
            if (x["low"] <= st) if long else (x["high"] >= st):
                px = x["open"] if ((x["open"] < st) if long else (x["open"] > st)) else st
                c.update(status="STOP", closed_at=str(x["date"])[:16],
                         exit_px=round(px, 2))
                break
            if (x["high"] >= tg) if long else (x["low"] <= tg):
                px = x["open"] if ((x["open"] > tg) if long else (x["open"] < tg)) else tg
                c.update(status="TARGET", closed_at=str(x["date"])[:16],
                         exit_px=round(px, 2))
                break
        if c.get("exit_px") is not None and c.get("result_pct") is None:
            cost = COST_DEL if c["section"] == "SWING" else COST_FUT
            g = 100 * ((c["exit_px"] - e) if long else (e - c["exit_px"])) / e
            c["result_pct"] = round(g - cost, 2)
            closed += 1

    # THE GUARD. If this ever prints, the rules above have been broken and the
    # numbers on the dashboard cannot be trusted until it is understood.
    bad = 0
    for c in LOG.values():
        fs = c.get("first_seen") or ""
        for f in ("filled_at", "closed_at"):
            t = c.get(f) or ""
            if t and t < fs[:16].replace("T", " "):
                bad += 1
                break
    if bad:
        print(f"  !! {bad} rows in the record are settled from candles older "
              f"than the call itself. That should now be impossible - tell "
              f"Claude.")
    return added, closed


# THE RECORD WAS BEING CUT OFF AT 400 ROWS (07/10)
#
# MM: "YESTERDAY I WAS 30K PLUS AND NOW MINUS, HOW IS IT POSSIBLE." He was
# right that something was wrong, and it was this. Both publish points sorted
# the log newest-first and then kept the first 400. The log itself is complete
# on disk - nothing was ever destroyed - but the dashboard only ever saw the
# newest 400 calls, and the engine prints about 37 a day. So every few days the
# oldest rows fell off the front of the page.
#
# A call falling off the page is not harmless, because an OPEN POSITION can
# fall off with it. On 07/10 four rated-8 positions that are still open were
# invisible on his book - USHAMART, GABRIEL, SOUTHBANK and AETHER - and AETHER
# alone was carrying Rs 19,336. His money appeared to vanish overnight; it had
# simply aged out of the window.
#
# The whole log is published now. Nothing is trimmed, so a position cannot be
# dropped for being old, and the record is the record.
def write_site(live, LOG):
    """Put the live calls and the record onto the dashboard. Only the two
    blocks this file owns are touched - nothing else on the page is read or
    rewritten, so the rest of MM's desk is untouched."""
    try:
        h = open(SITE, encoding="utf8").read()
    except Exception as e:
        print(f"  could not read the dashboard: {e}")
        return
    payload = json.dumps({"as_of": datetime.now().isoformat(timespec="seconds"),
                          "live": live,
                          "record": sorted(LOG.values(),
                                           key=lambda c: c.get("first_seen", ""),
                                           reverse=True)},
                         separators=(",", ":"))
    start = "/*FMLIVE_START*/"
    end = "/*FMLIVE_END*/"
    block = f"{start}const FMLIVE = {payload};{end}"
    if start in h and end in h:
        a = h.index(start); b = h.index(end) + len(end)
        h = h[:a] + block + h[b:]
    else:
        k = h.rindex("</script>")
        h = h[:k] + "\n" + block + "\n" + h[k:]
    tmp = SITE + ".tmp"
    open(tmp, "w", encoding="utf8").write(h)
    os.replace(tmp, SITE)


# ── a tiny server of its own, so the dashboard can just ASK ────────────────
# MM, 21/09: "WHY WILL I HAVE TO REFRESH? WHY CANT YOU DIRECTLY GIVE DATA."
# He is right. The page was being rewritten on disk, which a browser has no
# way of noticing. So this serves the calls on its own port and the tab pulls
# them every twenty seconds. Nothing about his desk changes - that is still
# on 5055, untouched, and if this server dies the desk does not care.
_SERVE = {"payload": '{"as_of":null,"live":[],"record":[]}'}


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
        ThreadingHTTPServer(("127.0.0.1", FPORT), _H).serve_forever()
    except Exception as e:
        print(f"  (could not open port {FPORT}: {e} - the page will still "
              f"update, but you will have to refresh it)")


def sweep(kite, tokens, syms, sector_of, fo, LOG, ctx):
    t0 = time.time()
    BARS = fetch(kite, tokens, syms)
    if ctx.get("bars"):
        merged = dict(ctx["bars"]); merged.update(BARS); BARS = merged
    ctx["bars"] = BARS
    # the daily chart for the swing side - downloaded once, then cached
    try:
        DAILY = fetch_daily(kite, tokens, list(BARS.keys()))
    except Exception as e:
        print(f"  daily chart unavailable ({e}) - swing will be quiet")
        DAILY = {}
    breadth, sec_med = market_context(BARS, sector_of, DAILY)
    ctx["breadth"], ctx["sec_med"] = breadth, sec_med
    live = []
    for s, b in BARS.items():
        try:
            live += scan_one(s, b, sector_of, sec_med, breadth, fo,
                             DAILY.get(s))
        except Exception:
            pass
    live.sort(key=lambda r: (-r["rating"], r["sym"]))
    added, closed = update_record(LOG, live, BARS)
    # WHEN THE CALL APPEARED, AND WHEN IT FILLED (22/09).
    # MM: "can you add time at which the buy call/sell call was
    # generated?" The record already knew both; the cards never showed
    # them, so every call looked like it had just happened.
    for r in live:
        c = LOG.get(r["key"]) or {}
        # THE DATE MATTERS AS MUCH AS THE TIME (23/09). MM: "the time that
        # is showing on the card is wrong". It was not wrong, it was
        # half-told - only the clock time went to the card, so a call
        # first seen last Thursday read as though it had just happened.
        # And a filled time was being sent for calls that had not filled:
        # the record remembers a fill from an earlier day while today's
        # scan has the price back below the level and calls it WAITING.
        fs = c.get("first_seen") or ""
        r["seen_on"], r["seen_at"] = fs[:10], fs[11:16]
        fa = c.get("filled_at") or ""
        if r.get("state") == "TRIGGERED" and fa:
            r["filled_on"], r["filled_at"] = fa[:10], fa[11:16]
        else:
            r["filled_on"] = r["filled_at"] = ""
    save_log(LOG)
    json.dump({"as_of": datetime.now().isoformat(timespec="seconds"),
               "live": live}, open(LIVEF, "w", encoding="utf8"))
    _SERVE["payload"] = json.dumps(
        {"as_of": datetime.now().isoformat(timespec="seconds"),
         "live": live,
         "record": sorted(LOG.values(), key=lambda c: c.get("first_seen", ""),
                          reverse=True)}, separators=(",", ":"))
    write_site(live, LOG)          # belt and braces: the file is written too
    top = [r for r in live if r["rating"] >= 9]
    print(f"  {datetime.now():%H:%M:%S}  {len(BARS)} stocks · {len(live)} live "
          f"· {len(top)} rated 9+ · {added} new · {closed} closed "
          f"({time.time()-t0:.0f}s)")
    for r in top[:12]:
        arrow = "BUY " if r["side"] == "LONG" else "SELL"
        print(f"      {'*' if r['rating']>=10 else ' '} {r['sym']:<12}"
              f"{r['rating']:>3}/10  {r['section'][:5]:<6}{r['state']:<10}"
              f"{arrow} {r['entry']:>9.2f}   SL {r['stop']:>9.2f}   "
              f"TGT {r['target']:>9.2f}   now {r['last']:>9.2f}")


def main():
    print("""
  ASCENDRA - FORMATIONS, LIVE

  Scans for the chart patterns that passed testing, rates each one out of
  ten, and writes every call it makes into a permanent record.

  Started by the desk, in the desk's own window. Nothing to open separately.
""")
    kite = load_kite()
    print("  connected to Zerodha")
    try:
        fo = json.load(open(os.path.join(DATA, "fo_universe.json"), encoding="utf8"))
        fo = set(fo["map"].keys()) if isinstance(fo, dict) and "map" in fo \
            else set(fo if isinstance(fo, list) else fo.keys())
    except Exception:
        fo = set()
    sector_of = {}
    p = os.path.join(DATA, "ind_niftytotalmarket_list.csv")
    if os.path.exists(p):
        import csv as _csv
        for row in _csv.DictReader(open(p, encoding="utf-8-sig")):
            if row.get("Symbol") and row.get("Industry"):
                sector_of[row["Symbol"].strip()] = row["Industry"].strip()
    print(f"  {len(fo)} F&O names · {len(sector_of)} with an industry")
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
    syms = sorted(set(fo) | set(sector_of)) or sorted(tokens)
    gone = [s for s in syms if s not in tokens]
    syms = [s for s in syms if s in tokens]
    if gone:
        print(f"  ({len(gone)} on the list have no NSE instrument "
              f"today: {', '.join(gone[:8])}"
              f"{' ...' if len(gone) > 8 else ''})")
    print(f"  watching {len(syms)} stocks: the {len(fo)} F&O names every "
          f"{FAST_EVERY//60} minutes, all of them every {FULL_EVERY//60}\n")
    LOG = load_log()
    print(f"  record holds {len(LOG)} calls so far\n")
    threading.Thread(target=serve, daemon=True).start()
    print(f"  serving the calls on http://127.0.0.1:{FPORT}  "
          f"(your desk stays on 5055, untouched)\n")
    fast = [s for s in syms if s in fo] or syms
    ctx, last_full = {}, 0.0
    while True:
        now = datetime.now()
        if now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) <= (15, 35):
            full = (time.time() - last_full) > FULL_EVERY
            use = syms if full else fast
            try:
                sweep(kite, tokens, use, sector_of, fo, LOG, ctx)
                if full:
                    last_full = time.time()
            except Exception as e:
                print(f"  sweep failed, carrying on: {e}")
        else:
            print(f"  {now:%H:%M:%S}  market closed - waiting")
        time.sleep(FAST_EVERY)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  stopped.")
    except Exception as e:
        import traceback
        traceback.print_exc()
        hold(1)
