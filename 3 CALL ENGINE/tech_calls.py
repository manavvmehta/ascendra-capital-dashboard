#!/usr/bin/env python3
"""
tech_calls.py — the multi-asset technical call engine behind the TRADE tab.

Distilled from the technical-analysis canon, weighted by evidence:
  • Multi-timeframe alignment (Elder Triple Screen): the higher timeframe
    grants direction, the lower timeframe times the entry. Non-negotiable gate.
  • Volume confirmation (Murphy/Weinstein; ORB studies: volume filter adds
    ~8-12pts of win rate). Indices have no volume → range-vs-ATR expansion
    is the participation proxy.
  • ATR everything (Wilder): stops are volatility-scaled, never fixed points.
  • Setups implemented:
      PULLBACK  — buy the dip at EMA20/VWAP in a daily uptrend (highest
                  consensus setup in the literature; Elder/Raschke/Minervini)
      ORB       — 30-min opening range break WITH daily-trend alignment and
                  volume (Crabel; academic index-futures studies)
      SQUEEZE   — Bollinger BandWidth compression → expansion break
                  (Bollinger; Carter's TTM squeeze)
      BREAKOUT20— fresh 20-day Donchian break for SWING calls; bonus when
                  within 5%% of the 52-week high (George & Hwang 2004 —
                  the 52w-high effect is confirmed on Indian equities)
      VWAPFLIP  — decisive intraday VWAP reclaim/reject (institutional bias
                  axis) — indices and top names only
  • Confluence scoring: a call fires only at score ≥ 6.5; everything else
    goes to the "watching" list with what's missing. Never force a trade.

Pure math lives in analyze_instrument() — candles in, calls out — so the
whole engine is testable offline. TechScanner does the Kite I/O:
market-wide funnel (bulk quotes → shortlist → candles → setups) plus call
lifecycle tracking (WAITING → LIVE → T1/T2/SL/TIME) persisted to disk.
"""
import hashlib
import json
import os
import math
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import state as S

_SETUP_ERRS = []      # setup exceptions this cycle (reported in aggregate)
_CACHE_ERRS = []      # daily-cache fetch failures

# ── state paths ────────────────────────────────────────────────────────────
# Resolved at CALL time through config.state_dir(), never as module-level
# Desktop constants. On 29/08 exactly that pattern let the test suite write
# fixture trades into a live book — the tests passed throughout and
# the only symptom was a fictional company on screen. On his Mac
# config.state_dir() returns ~/Desktop, so this is a no-op in production and a
# redirect under TE_DATA_DIR in tests.
import config as _C


def tech_calls_file():
    return _C.state_dir() / ".tech_calls.json"


def tech_hist_file():
    """Permanent history — never trimmed."""
    return _C.state_dir() / ".tech_calls_history.json"


def watching_file():
    """Rows that scored BELOW the fire threshold and were never traded.

    Added 31/08 as instrumentation. This is the counterfactual for the score
    gate: every idea the engine built and then declined to publish, with its
    complete levels, so it can be replayed through the same grading harness.

    IT IS NOT the counterfactual for the hard VOLUME gates. ORB, SQUEEZE and
    BREAKOUT-20D `return None` before any levels exist, so a breakout rejected
    at 1.8x volume never becomes a candidate and never reaches this file. Those
    rejections are logged separately, as numbers only — see gate_rejects_file().
    """
    return _C.state_dir() / ".tech_watching.json"


def gate_rejects_file():
    """Setups killed by a hard participation gate: the numbers, not a trade.

    No levels, therefore NOT gradeable. It records what was rejected and by how
    much, so the distribution of what the gates remove is at least measurable.
    """
    return _C.state_dir() / ".tech_gate_rejects.json"


# Bumped whenever the recorded field set changes. Old rows are NEVER
# backfilled — they stay censored and this marks them.
SCHEMA_VERSION = 2

_GATE_REJECTS = []    # drained each scan into gate_rejects_file()


def _atomic_write(path, text, keep_backup=False):
    """Write via a temp file + os.replace, which is atomic on POSIX.

    `path.write_text()` truncates the target and THEN streams the bytes. Kill
    the process in that window — Ctrl+C, the Mac sleeping, a power cut — and
    the file is left truncated. For .tech_calls_history.json that is 316KB of
    the permanent record the 26/8 verdict reads, and this dashboard is
    restarted several times a day. The window is small; the number of times we
    open it is not.

    os.replace() is atomic: readers see either the whole old file or the whole
    new one, never a half-written one. keep_backup also rolls the previous
    good copy to .bak first, so even a catastrophic case is recoverable.

    Found in the 24/8 sweep — third pass, after he asked "are you sure?" a
    third time.
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    if keep_backup and path.exists():
        try:
            bak = path.with_suffix(path.suffix + ".bak")
            bak.write_bytes(path.read_bytes())
        except Exception as e:
            print(f"  [Tech] backup of {path.name} failed: {e}")
    os.replace(str(tmp), str(path))

def daily_cache_file():
    return _C.state_dir() / ".tech_daily_cache.json"

FIRE_SCORE = 6.5          # minimum confluence to publish a call
HIGH_SCORE = 8.0          # "HIGH conviction" label
MIN_PRICE = 20.0          # skip micro-priced names
MIN_TURNOVER_CR = 5.0     # 20-day avg daily turnover — the tradability floor
SHORTLIST_N = 25          # equities that get full candle analysis per cycle
SCAN_SECS = 150           # funnel cadence — hunting for NEW setups (expensive:
                          # quotes the whole equity universe)
FAST_SECS = 5             # tracker cadence — watching calls that already exist
                          # (cheap: one quote call for the open symbols). Split
                          # out on 24/8; before that a trigger could go unnoticed
                          # for a full 150s.
# Consecutive no-quote scans before an untrackable call is VOIDED.
# 60 x 5s = 5 minutes. A transient chunk failure lasts a scan or two; five
# minutes of silence on a market-hours symbol means it is not being tracked
# at all. Warnings alone let four calls reach _noq=240 (20 min) on 28/08,
# two of them displayed as LIVE with stops that could not fire.
NOQ_VOID = 60
COOLDOWN_S = 45 * 60      # per instrument+setup+direction re-fire gap
MCX_ENABLED = False       # suspended 10/8: 12 resolved MCX calls = −8.0R, every
                          # symbol negative (GOLD −4, NG −3.5, SILVER −0.5).
                          # Flip to True to re-enable if a future review earns it.
WAIT_EXPIRE_S = 90 * 60   # un-triggered call expires
SWING_MAX_DAYS = 10

# ── participation gates, named once so a row can record them ───────────────
# These were literals scattered through the setup functions. They are the
# numbers that decide whether a setup lives or dies, so they belong in the
# fingerprint — a row written before a change and a row written after came
# from different engines, and nothing else on disk would say so.
PART_THR = {
    "PULLBACK":     {"volume": 1.1,  "range": 1.2,  "hard": False},
    "ORB":          {"volume": 1.3,  "range": 1.2,  "hard": True},
    "SQUEEZE":      {"volume": 1.5,  "range": 1.35, "hard": True},
    "BREAKOUT-20D": {"volume": 2.5,  "range": 2.5,  "hard": True},
    "VWAP-FLIP":    {"volume": 1.2,  "range": 1.2,  "hard": True},
}
# ATR multiples used to place stops, per setup
STOP_ATR = {"PULLBACK": 1.2, "ORB": 1.2, "SQUEEZE": 1.2,
            "BREAKOUT-20D": 2.0, "VWAP-FLIP": 0.8}


def engine_params():
    """Everything the engine BELIEVED when it wrote a row.

    schema_version says which FIELDS a row has. It says nothing about the
    thresholds in force. Move FIRE_SCORE from 6.5 to 7.0 and every earlier row
    came from a different engine — without this, that change silently poisons
    the whole archive, because a year of history would look like one
    population when it is two.

    Stored as a short hash on every row plus one full copy per file, so the
    per-row cost is ~20 bytes and the analysis can still split on any value.
    """
    return {
        "fire_score": FIRE_SCORE, "high_score": HIGH_SCORE,
        "part_thr": PART_THR, "stop_atr": STOP_ATR,
        "swing_max_days": SWING_MAX_DAYS, "wait_expire_s": WAIT_EXPIRE_S,
        "cooldown_s": COOLDOWN_S, "noq_void": NOQ_VOID,
        "min_price": MIN_PRICE, "min_turnover_cr": MIN_TURNOVER_CR,
        "shortlist_n": SHORTLIST_N, "scan_secs": SCAN_SECS,
        "fast_secs": FAST_SECS, "mcx_enabled": MCX_ENABLED,
    }


def params_hash():
    """Stable across runs and across dict insertion order — sort_keys matters,
    or the same config would fingerprint differently after a refactor."""
    blob = json.dumps(engine_params(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


PARAMS_HASH = params_hash()


def _params_log(store):
    """A hash on a row is only useful if it can be resolved back to values.

    Each file keeps a small append-only ledger, hash -> {params, first_seen}.
    One entry per distinct configuration ever used, not one per row — so a
    year spanning two threshold changes carries three entries, and the split
    is `group by params_hash`.
    """
    log = dict((store or {}).get("params") or {})
    if PARAMS_HASH not in log:
        log[PARAMS_HASH] = {"first_seen": datetime.now().isoformat(timespec="seconds"),
                            "params": engine_params()}
    return log


def params_log_file():
    return _C.state_dir() / ".tech_params_log.json"


_params_logged = False


def ensure_params_logged():
    """The same ledger for the calls and history files.

    Those two are shaped as a list and a flat dict of calls, so an embedded
    "params" key would either collide with a call key or be read back as a
    call by anything iterating values. It goes in its own file instead —
    rows carry the hash, this resolves it.
    """
    global _params_logged
    if _params_logged:
        return False        # once per process: not even a stat() after the first
    try:
        p = params_log_file()
        log = json.loads(p.read_text()) if p.exists() else {}
        if PARAMS_HASH in log:
            _params_logged = True
            return False
        log[PARAMS_HASH] = {"first_seen": datetime.now().isoformat(timespec="seconds"),
                            "params": engine_params()}
        _atomic_write(p, json.dumps(log, indent=1))
        _params_logged = True
        return True
    except Exception:
        return False        # never let bookkeeping break a save

MCX_WANT = ("GOLD", "SILVER", "CRUDEOIL", "NATURALGAS")


def _is_etfish(sym, name=""):
    """ETFs / index funds pass the turnover screen but barely move — junk
    setups that also cluster (7 correlated calls at 09:21 on 19/8).

    The SYMBOL alone is not enough: MON100 / MONQ50 (Motilal Oswal Nasdaq
    ETFs) carry nothing in the ticker that says 'ETF'. Kite's instrument
    NAME does, so that is the primary test; the symbol patterns stay as a
    fallback for when name is unavailable.

    Deliberately narrow on names so real companies survive: MONARCH,
    MOTILALOFS, MOIL, GOLDIAM, MOTHERSON must all pass through.
    """
    n = " " + (name or "").upper().replace("-", " ") + " "
    if (" ETF " in n or n.endswith("ETF ") or "EXCHANGE TRADED" in n
            or "INDEX FUND" in n or "MUTUAL FUND" in n or " BEES " in n
            or "BHARAT BOND" in n or "LIQUIDBEES" in n
            or "NIFTY" in n or "SENSEX" in n or "NASDAQ" in n):
        return True
    u = (sym or "").upper()
    if u in _ETF_SYMS:
        return True
    return (u.endswith(("BEES", "ETF", "IETF", "CASE", "INAV"))
            or u.startswith(("LIQUID", "GILT", "SDL", "GSEC", "EBBETF",
                             "SETFN", "MOGSEC", "MOM100", "MON100",
                             "MONQ50", "MOSMALL", "MOM50")))


# Explicit escape hatch for ETFs whose ticker AND name both hide it.
_ETF_SYMS = {
    "MON100", "MONQ50", "MOM100", "MOM50", "MOSMALL", "MOGSEC", "MOVALUE",
    "MOLOWVOL", "MOQUALITY", "MOHEALTH", "MODEFENCE", "MAHKTECH",
    "MASPTOP50", "HNGSNGBEES", "TOP100CASE", "AXISCETF", "SILVERBEES",
    "GOLDBEES", "GOLDSHARE", "GOLDIETF", "SILVERIETF",
}


# ── indicators (plain lists in, floats out) ─────────────────────────────────
def _c(x, k):
    v = x.get(k, 0)
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def ema(vals, n):
    if not vals:
        return []
    k = 2.0 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return [50.0] * len(closes)
    out = [50.0] * len(closes)
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = 100 - 100 / (1 + (ag / al if al > 0 else 1e9))
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100 - 100 / (1 + (ag / al if al > 0 else 1e9))
    return out


def atr(candles, n=14):
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = _c(candles[i], "high"), _c(candles[i], "low"), _c(candles[i - 1], "close")
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0.0
    a = sum(trs[:n]) / max(min(n, len(trs)), 1)
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def adx(candles, n=14):
    if len(candles) < n * 2 + 1:
        return 0.0
    pdm, ndm, trs = [], [], []
    for i in range(1, len(candles)):
        up = _c(candles[i], "high") - _c(candles[i - 1], "high")
        dn = _c(candles[i - 1], "low") - _c(candles[i], "low")
        pdm.append(up if up > dn and up > 0 else 0.0)
        ndm.append(dn if dn > up and dn > 0 else 0.0)
        h, l, pc = _c(candles[i], "high"), _c(candles[i], "low"), _c(candles[i - 1], "close")
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    def _sm(xs):
        s = sum(xs[:n]); out = [s]
        for x in xs[n:]:
            s = s - s / n + x
            out.append(s)
        return out
    st, sp, sn_ = _sm(trs), _sm(pdm), _sm(ndm)
    dxs = []
    for t, p, m in zip(st, sp, sn_):
        if t <= 0:
            continue
        pdi, ndi = 100 * p / t, 100 * m / t
        if pdi + ndi > 0:
            dxs.append(100 * abs(pdi - ndi) / (pdi + ndi))
    if len(dxs) < n:
        return dxs[-1] if dxs else 0.0
    a = sum(dxs[:n]) / n
    for d in dxs[n:]:
        a = (a * (n - 1) + d) / n
    return a


def boll(closes, n=20, k=2.0):
    if len(closes) < n:
        m = sum(closes) / max(len(closes), 1)
        return m, m, m, 0.0
    seg = closes[-n:]
    m = sum(seg) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in seg) / n)
    up, dn = m + k * sd, m - k * sd
    return m, up, dn, (up - dn) / m if m else 0.0


def bandwidth_series(closes, n=20, k=2.0):
    out = []
    for i in range(n, len(closes) + 1):
        _, _, _, bw = boll(closes[:i], n, k)
        out.append(bw)
    return out


def vwap_today(intra, today):
    """VWAP over today's bars; None when volume absent (indices)."""
    pv = vv = 0.0
    for c in intra:
        if _bar_date(c) != today:
            continue
        v = _c(c, "volume")
        if v <= 0:
            continue
        typ = (_c(c, "high") + _c(c, "low") + _c(c, "close")) / 3
        pv += typ * v
        vv += v
    return pv / vv if vv > 0 else None


def _bar_date(c):
    d = c.get("date")
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    try:
        return datetime.fromisoformat(str(d)[:19]).date()
    except Exception:
        return None


def _bar_time(c):
    d = c.get("date")
    if isinstance(d, datetime):
        return d.time()
    try:
        return datetime.fromisoformat(str(d)[:19]).time()
    except Exception:
        return None


def has_volume(candles):
    return any(_c(c, "volume") > 0 for c in candles[-30:])


def participation(candles, i=-1, n=20):
    """Volume ratio when volume exists, else range-vs-ATR expansion (indices)."""
    if has_volume(candles):
        vols = [_c(c, "volume") for c in candles]
        seg = vols[-n - 1:-1] or vols[:-1]
        av = sum(seg) / max(len(seg), 1)
        return (vols[i] / av if av > 0 else 1.0), "volume"
    a = atr(candles[:len(candles) + i + 1 or None], 14)
    rng = _c(candles[i], "high") - _c(candles[i], "low")
    return (rng / a if a > 0 else 1.0), "range"


def swing_levels(candles, w=2):
    highs, lows = [], []
    for i in range(w, len(candles) - w):
        hi, lo = _c(candles[i], "high"), _c(candles[i], "low")
        if all(hi >= _c(candles[j], "high") for j in range(i - w, i + w + 1)):
            highs.append(hi)
        if all(lo <= _c(candles[j], "low") for j in range(i - w, i + w + 1)):
            lows.append(lo)
    return highs, lows


# ── daily regime ────────────────────────────────────────────────────────────
def daily_regime(daily):
    """UP / DOWN / FLAT from EMA20/50 + ADX + closing location."""
    closes = [_c(c, "close") for c in daily]
    if len(closes) < 30:
        return {"dir": "FLAT", "adx": 0.0, "atr_d": atr(daily), "why": "not enough daily history"}
    e20, e50 = ema(closes, 20)[-1], ema(closes, 50)[-1]
    a = adx(daily, 14)
    last = closes[-1]
    if e20 > e50 and last > e20 * 0.995:
        d = "UP"
    elif e20 < e50 and last < e20 * 1.005:
        d = "DOWN"
    else:
        d = "FLAT"
    hi52 = max(_c(c, "high") for c in daily[-250:])
    return {"dir": d, "adx": round(a, 1), "atr_d": atr(daily),
            "e20": e20, "e50": e50, "hi52": hi52, "rsi_d": rsi(closes)[-1],
            "near52": last / hi52 if hi52 > 0 else 0,
            "why": f"EMA20 {'>' if e20 > e50 else '<'} EMA50, ADX {a:.0f}"}


# ── the setups ──────────────────────────────────────────────────────────────
def _r6(x):
    """Round for storage, and turn a non-number into None rather than 0.

    A 0 where the measurement failed is indistinguishable from a real 0 and
    would silently enter a mean.
    """
    try:
        if x is None:
            return None
        f = float(x)
        return None if (f != f or f in (float("inf"), float("-inf"))) else round(f, 6)
    except Exception:
        return None


def measure(m15, reg, part, part_kind, part_threshold, ema20=None, vwap=None,
            room_r=None, n=20):
    """Everything the analysis will want, captured AT SIGNAL TIME.

    Instrumentation only — nothing here feeds a decision. Added 31/08 because
    the volume/expansion question could not be asked of the existing record:
    the ratio was written into a prose reason string and the raw inputs were
    never stored at all.

    Distances are signed and expressed in ATR units so they are comparable
    across a Rs50 stock and a Rs5,000 one. Positive means price is ABOVE the
    level.
    """
    a = atr(m15, 14) if m15 else 0.0
    vols = [_c(c, "volume") for c in m15] if m15 else []
    seg = vols[-n - 1:-1] if len(vols) > 1 else []
    avg_v = (sum(seg) / len(seg)) if seg else None
    sig_v = vols[-1] if vols else None
    rngs = [_c(c, "high") - _c(c, "low") for c in m15] if m15 else []
    rseg = rngs[-n - 1:-1] if len(rngs) > 1 else []
    avg_r = (sum(rseg) / len(rseg)) if rseg else None
    sig_r = rngs[-1] if rngs else None
    closes = [_c(c, "close") for c in m15] if m15 else []
    last = closes[-1] if closes else None
    return {
        "part": _r6(part), "part_kind": part_kind,
        "part_threshold": _r6(part_threshold),
        "sig_vol": _r6(sig_v), "avg_vol_20": _r6(avg_v),
        "sig_range": _r6(sig_r), "avg_range_20": _r6(avg_r),
        "atr14": _r6(a), "adx14": _r6(reg.get("adx") if reg else None),
        "rsi": _r6(rsi(closes)[-1] if len(closes) >= 15 else None),
        "dist_to_ema20": _r6((last - ema20) / a) if (last and ema20 and a > 0) else None,
        "dist_to_vwap": _r6((last - vwap) / a) if (last and vwap and a > 0) else None,
        "room_r": _r6(room_r),
    }


def _gate_reject(name, seg, setup, side, part, part_kind, threshold, reg):
    """A setup killed by a hard participation gate.

    NUMBERS ONLY — there are no levels, because every hard gate returns before
    they are computed. These rows are therefore NOT gradeable and must never be
    replayed as trades. What they give is the distribution of what the gates
    remove, and how far under the line it was.

    Recovering a gradeable counterfactual would mean moving level computation
    ahead of the gates in all three setups — a control-flow change to live
    trading code, deliberately NOT made here.
    """
    try:
        _GATE_REJECTS.append({
            "symbol": name, "seg": seg, "setup": setup, "side": side,
            "date": str(date.today()), "ts": datetime.now().strftime("%H:%M:%S"),
            "part": _r6(part), "part_kind": part_kind,
            "part_threshold": _r6(threshold),
            "shortfall": _r6((threshold - part) / threshold if threshold else None),
            "adx14": _r6(reg.get("adx") if reg else None),
            "regime": (reg or {}).get("dir"),
            "gradeable": False, "schema_version": SCHEMA_VERSION,
            "params_hash": PARAMS_HASH,
        })
    except Exception:
        pass          # instrumentation must never break a trading decision


def _mk_call(name, seg, setup, side, tf, entry, sl, t1, t2, score, reasons, extra=None,
             instr=None, comp=None):
    risk0 = abs(entry - sl)
    rr = round(abs(t1 - entry) / max(risk0, 1e-9), 2)
    c = {"symbol": name, "seg": seg, "setup": setup, "side": side, "tf": tf,
         "entry": round(entry, 2), "sl": round(sl, 2), "risk0": round(risk0, 4),
         "t1": round(t1, 2), "t2": round(t2, 2), "rr": rr,
         "score": round(min(score, 10.0), 1),
         "conviction": "HIGH" if score >= HIGH_SCORE else "MEDIUM",
         "reasons": reasons[:4],
         "schema_version": SCHEMA_VERSION, "params_hash": PARAMS_HASH}
    if instr:
        c["instr"] = instr
    # EACH bonus as its own field, plus the base. A parallel US study had to
    # reverse-engineer which bonuses fired by recomputing indicators and
    # matching the score residual; storing them turns that into a groupby.
    if comp is not None:
        c["score_components"] = {k: _r6(v) for k, v in comp.items()}
    if extra:
        c.update(extra)
    return c


def setup_pullback(name, seg, reg, m15, now, flow_bias=0):
    if reg["dir"] not in ("UP", "DOWN") or len(m15) < 40:
        return None
    long = reg["dir"] == "UP"
    closes = [_c(c, "close") for c in m15]
    a15 = atr(m15, 14)
    if a15 <= 0:
        return None
    e20 = ema(closes, 20)[-1]
    r = rsi(closes)
    last, prev = m15[-1], m15[-2]
    lc, lo_, lh, ll = _c(last, "close"), _c(last, "open"), _c(last, "high"), _c(last, "low")
    vw = vwap_today(m15, now.date())
    # EMA20 and VWAP are two SEPARATE institutional levels — touching either
    # one is a valid pullback zone (averaging them vetoes strong trend days)
    zdist = abs(lc - e20)
    zname = "EMA20"
    if vw is not None and abs(lc - vw) < zdist:
        zdist, zname = abs(lc - vw), "VWAP"
    near_zone = zdist <= 0.9 * a15
    if long:
        dipped = min(r[-5:-1]) <= 48 and r[-1] > r[-2]
        rev_bar = lc > lo_ and (lh - lc) <= 0.4 * max(lh - ll, 1e-9)
    else:
        dipped = max(r[-5:-1]) >= 52 and r[-1] < r[-2]
        rev_bar = lc < lo_ and (lc - ll) <= 0.4 * max(lh - ll, 1e-9)
    if not (near_zone and dipped and rev_bar):
        return None
    part, pk = participation(m15)
    sc = 5.0
    comp = {"base": 5.0}                      # instrumentation only
    _thr = 1.1 if pk == "volume" else 1.2
    why = [f"daily {reg['dir']}trend ({reg['why']}) — trading with it",
           f"pullback to {zname}, RSI reset & turning"]
    if reg["adx"] >= 22:
        sc += 1.0; comp["adx"] = 1.0; why.append(f"strong trend (ADX {reg['adx']:.0f})")
    if part >= _thr:
        sc += 1.0; comp["participation"] = 1.0
        why.append(f"{'volume' if pk == 'volume' else 'range'} expansion x{part:.1f} on the turn")
    if vw is not None and ((long and lc > vw) or (not long and lc < vw)):
        sc += 0.5; comp["vwap_side"] = 0.5; why.append("right side of VWAP")
    if (long and flow_bias > 0) or (not long and flow_bias < 0):
        sc += 1.0; comp["flow"] = 1.0; why.append("basket flow agrees")
    if long:
        entry = lh + 0.05 * a15
        sl = min(ll, entry - 1.2 * a15)
    else:
        entry = ll - 0.05 * a15
        sl = max(lh, entry + 1.2 * a15)
    risk = abs(entry - sl)
    highs, lows = swing_levels(m15[-60:])
    room = None
    if long and highs:
        nxt = [h for h in highs if h > entry]
        room = (min(nxt) - entry) / risk if nxt else None
    elif not long and lows:
        nxt = [l for l in lows if l < entry]
        room = (entry - max(nxt)) / risk if nxt else None
    if room is not None and room < 0.5:
        return None                      # heavy lid right on top — no room at all
    if room is None or room >= 1.5:
        sc += 0.5; comp["room"] = 0.5; why.append("clean air to the next level")
    t1 = entry + risk if long else entry - risk
    t2 = entry + 2 * risk if long else entry - 2 * risk
    return _mk_call(name, seg, "PULLBACK", "LONG" if long else "SHORT", "INTRADAY",
                    entry, sl, t1, t2, sc, why,
                    instr=measure(m15, reg, part, pk, _thr, ema20=e20, vwap=vw,
                                  room_r=room),
                    comp=comp)


def setup_orb(name, seg, reg, m15, now, mcx=False):
    if len(m15) < 6 or reg["dir"] == "FLAT":
        return None
    tmin = now.hour * 60 + now.minute
    win = (9 * 60 + 45 <= tmin <= 11 * 60 + 30) if not mcx else (9 * 60 + 40 <= tmin <= 11 * 60)
    if not win:
        return None
    today = now.date()
    tbars = [c for c in m15 if _bar_date(c) == today]
    if len(tbars) < 3:
        return None
    orb = tbars[:2]
    or_hi = max(_c(c, "high") for c in orb)
    or_lo = min(_c(c, "low") for c in orb)
    width = or_hi - or_lo
    ad = reg.get("atr_d") or 0
    if ad > 0 and not (0.2 * ad <= width <= 0.9 * ad):
        return None                      # degenerate or exhausted opening range
    last = tbars[-1]
    lc = _c(last, "close")
    long = lc > or_hi
    short = lc < or_lo
    if not (long or short):
        return None
    if (long and reg["dir"] != "UP") or (short and reg["dir"] != "DOWN"):
        return None                      # evidence: with-trend ORB wins ~10pts more
    part, pk = participation(m15)
    _thr = 1.3 if pk == "volume" else 1.2
    if part < _thr:
        _gate_reject(name, seg, "ORB", "LONG" if long else "SHORT", part, pk, _thr, reg)
        return None                      # breakout without participation = trap risk
    sc = 5.5
    comp = {"base": 5.5}
    why = [f"30-min opening range broken {'up' if long else 'down'} WITH the daily {reg['dir']}trend",
           f"{'volume' if pk == 'volume' else 'range'} confirmation x{part:.1f}"]
    if reg["adx"] >= 22:
        sc += 1.0; comp["adx"] = 1.0; why.append(f"trending regime (ADX {reg['adx']:.0f})")
    prev_close = None
    dbars = [c for c in m15 if _bar_date(c) and _bar_date(c) < today]
    if dbars:
        prev_close = _c(dbars[-1], "close")
    if prev_close:
        gap = (_c(orb[0], "open") - prev_close) / prev_close
        if (long and gap > 0.002) or (short and gap < -0.002):
            sc += 0.5; comp["gap"] = 0.5; why.append("gap in the breakout direction")
    entry = lc
    sl = (or_hi + or_lo) / 2 if width > 0 else entry - 1.2 * atr(m15, 14)
    if short:
        sl = (or_hi + or_lo) / 2
    t1 = entry + 0.75 * width if long else entry - 0.75 * width
    t2 = entry + 1.5 * width if long else entry - 1.5 * width
    if abs(entry - sl) <= 0:
        return None
    return _mk_call(name, seg, "ORB", "LONG" if long else "SHORT", "INTRADAY",
                    entry, sl, t1, t2, sc, why,
                    {"or_hi": round(or_hi, 2), "or_lo": round(or_lo, 2)},
                    instr=measure(m15, reg, part, pk, _thr), comp=comp)


def setup_squeeze(name, seg, reg, m15, now):
    if len(m15) < 60:
        return None
    closes = [_c(c, "close") for c in m15]
    bws = bandwidth_series(closes[-120:], 20)
    if len(bws) < 30:
        return None
    prior = sorted(bws[:-1])
    pct = prior[max(0, int(len(prior) * 0.25) - 1)]
    was_squeezed = min(bws[-4:-1]) <= pct
    if not was_squeezed:
        return None
    m, up, dn, _ = boll(closes[:-1], 20)
    lc = _c(m15[-1], "close")
    long = lc > up
    short = lc < dn
    if not (long or short):
        return None
    # 3-yr NIFTY backtest: counter-trend squeezes were the losing half
    # (−0.22R avg, 53% stopped) — with-trend only, and demand a harder break
    if reg["dir"] != "FLAT" and ((long and reg["dir"] != "UP") or (short and reg["dir"] != "DOWN")):
        return None
    part, pk = participation(m15)
    _thr = 1.5 if pk == "volume" else 1.35
    if part < _thr:
        _gate_reject(name, seg, "SQUEEZE", "LONG" if long else "SHORT", part, pk, _thr, reg)
        return None
    a15 = atr(m15, 14)
    sc = 5.5
    comp = {"base": 5.5}
    why = ["volatility squeeze (bands at 25th-pct compression) breaking " + ("up" if long else "down"),
           f"{'volume' if pk == 'volume' else 'range'} expansion x{part:.1f} on the break"]
    if (long and reg["dir"] == "UP") or (short and reg["dir"] == "DOWN"):
        sc += 1.5; comp["trend_align"] = 1.5
        why.append(f"break aligns with daily {reg['dir']}trend")
    entry = lc
    sl = m if abs(lc - m) <= 2.0 * a15 else (entry - 1.2 * a15 if long else entry + 1.2 * a15)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    t1 = entry + risk if long else entry - risk
    t2 = entry + 2 * risk if long else entry - 2 * risk
    return _mk_call(name, seg, "SQUEEZE", "LONG" if long else "SHORT", "INTRADAY",
                    entry, sl, t1, t2, sc, why,
                    instr=measure(m15, reg, part, pk, _thr), comp=comp)


def setup_breakout20(name, seg, reg, daily, can_short=False):
    """Swing: fresh 20-day Donchian break; George-Hwang 52w-high bonus."""
    if len(daily) < 30:
        return None
    closes = [_c(c, "close") for c in daily]
    last = closes[-1]
    hi20 = max(_c(c, "high") for c in daily[-21:-1])
    lo20 = min(_c(c, "low") for c in daily[-21:-1])
    ad = reg.get("atr_d") or atr(daily)
    if ad <= 0:
        return None
    long = last > hi20 and (last - hi20) <= 0.015 * last
    short = can_short and last < lo20 and (lo20 - last) <= 0.015 * last
    if not (long or short):
        return None
    if (long and reg["dir"] == "DOWN") or (short and reg["dir"] == "UP"):
        return None
    vols = [_c(c, "volume") for c in daily]
    has_v = sum(vols[-20:]) > 0
    part = 1.0
    if has_v:
        av = sum(vols[-21:-1]) / 20
        part = vols[-1] / av if av > 0 else 1.0
        if part < 2.5:
            _gate_reject(name, seg, "BREAKOUT-20D", "LONG" if long else "SHORT",
                         part, "volume", 2.5, reg)
            return None      # live record 21/7–10/8: breakouts under x2.5 vol
                             # went 0W/7SL — a breakout without volume is a trap
    rd = reg.get("rsi_d", 50)
    if long and rd > 78:
        return None                      # chasing an already-vertical chart
    sc = 5.5
    comp = {"base": 5.5}
    why = [f"fresh 20-day {'high' if long else 'low'} — momentum continuation"]
    if has_v:
        why.append(f"breakout volume x{part:.1f}")
        if part >= 1.5:
            sc += 1.0; comp["participation"] = 1.0
        elif part >= 1.2:
            sc += 0.5; comp["participation"] = 0.5
    if long and reg.get("near52", 0) >= 0.95:
        sc += 1.5; comp["near52"] = 1.5
        why.append("within 5% of 52-week high (strongest momentum zone — George & Hwang)")
    if reg["adx"] >= 22:
        sc += 1.0; comp["adx"] = 1.0; why.append(f"established trend (ADX {reg['adx']:.0f})")
    entry = last
    sl = entry - 2.0 * ad if long else entry + 2.0 * ad
    lvl = hi20 if long else lo20
    if long:
        sl = max(sl, lvl - 0.6 * ad)      # tighter of 2×ATR / just under the level
    else:
        sl = min(sl, lvl + 0.6 * ad)
    risk = abs(entry - sl)
    t1 = entry + 2 * risk if long else entry - 2 * risk
    t2 = entry + 3.5 * risk if long else entry - 3.5 * risk
    # BREAKOUT-20D measures on DAILY bars, not 15-minute ones, so its
    # participation is the daily volume ratio computed above.
    return _mk_call(name, seg, "BREAKOUT-20D", "LONG" if long else "SHORT", "SWING",
                    entry, sl, t1, t2, sc, why,
                    {"horizon": "3-10 sessions", "level": round(lvl, 2)},
                    instr=measure(daily, reg, part, "volume" if has_v else "none", 2.5),
                    comp=comp)


def setup_vwapflip(name, seg, reg, m15, now, flow_bias=0):
    if len(m15) < 10:
        return None
    today = now.date()
    tbars = [c for c in m15 if _bar_date(c) == today]
    if len(tbars) < 4:
        return None
    vw = vwap_today(m15, today)
    a15 = atr(m15, 14)
    if a15 <= 0:
        return None
    if vw is None:                        # index: proxy with day midpoint
        vw = (max(_c(c, "high") for c in tbars) + min(_c(c, "low") for c in tbars)) / 2
    prev2, last = tbars[-2], tbars[-1]
    pc, lc = _c(prev2, "close"), _c(last, "close")
    long = pc <= vw and lc > vw and (lc - _c(last, "open")) > 0.5 * a15
    short = pc >= vw and lc < vw and (_c(last, "open") - lc) > 0.5 * a15
    if not (long or short):
        return None
    if (long and reg["dir"] == "DOWN") or (short and reg["dir"] == "UP"):
        return None
    part, pk = participation(m15)
    sc = 5.0
    comp = {"base": 5.0}
    _thr = 1.2
    why = [f"decisive VWAP {'reclaim' if long else 'reject'} with a wide-range bar"]
    if part >= _thr:
        sc += 1.0; comp["participation"] = 1.0
        why.append(f"{'volume' if pk == 'volume' else 'range'} expansion x{part:.1f}")
    if (long and reg["dir"] == "UP") or (short and reg["dir"] == "DOWN"):
        sc += 1.0; comp["trend"] = 1.0; why.append(f"daily {reg['dir']}trend behind it")
    if (long and flow_bias > 0) or (not long and short and flow_bias < 0):
        sc += 1.5; comp["flow"] = 1.5; why.append("basket order-flow pushing the same way")
    entry = lc
    sl = vw - 0.8 * a15 if long else vw + 0.8 * a15
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    t1 = entry + risk if long else entry - risk
    t2 = entry + 2 * risk if long else entry - 2 * risk
    return _mk_call(name, seg, "VWAP-FLIP", "LONG" if long else "SHORT", "INTRADAY",
                    entry, sl, t1, t2, sc, why,
                    instr=measure(m15, reg, part, pk, _thr, vwap=vw), comp=comp)


def analyze_instrument(name, seg, daily, m15, now, flow_bias=0, can_short_swing=False):
    """All setups for one instrument. Returns (fired, watching)."""
    reg = daily_regime(daily)
    if seg == "EQ" and daily:
        px = _c(daily[-1], "close")
        if px > 0 and (reg.get("atr_d") or 0) / px < 0.005:
            return [], []                # dead mover — nothing tradable here
    mcx = seg == "MCX"
    cands = []
    # SQUEEZE disabled on indices: 3-yr backtest shows no edge there even
    # with-trend (−0.18R); it stays live for equities/MCX (true volume+ranges)
    fns = [(setup_pullback, (name, seg, reg, m15, now, flow_bias)),
           (setup_orb, (name, seg, reg, m15, now, mcx)),
           (setup_vwapflip, (name, seg, reg, m15, now, flow_bias))]
    if seg != "IDX":
        fns.insert(2, (setup_squeeze, (name, seg, reg, m15, now)))
    for fn, args in fns:
        try:
            c = fn(*args)
            if c:
                cands.append(c)
        except Exception as e:
            _SETUP_ERRS.append(str(e))   # a setup that throws looks exactly like a setup that never fires
    if seg != "IDX":       # swing breakouts are for equities/MCX — index is an
        try:               # options vehicle where same-day theta kills the hold
            c = setup_breakout20(name, seg, reg, daily, can_short=(mcx or can_short_swing))
            if c:
                cands.append(c)
        except Exception as e:
            _SETUP_ERRS.append(str(e))
    if seg == "IDX":
        # option-worthiness floor (3-yr premium sim: small-risk index calls
        # lose to theta even when spot hits target): risk ≥ 0.07% of price
        px = _c(daily[-1], "close") if daily else 0
        cands = [c for c in cands if px <= 0 or abs(c["entry"] - c["sl"]) >= 0.0007 * px]
    fired = [c for c in cands if (c.get("score") or 0) >= FIRE_SCORE and (c.get("rr") or 0) >= 0.9]
    watch = [c for c in cands if c not in fired]
    fired.sort(key=lambda c: -(c.get("score") or 0))
    return fired[:2], watch


# ── scanner: Kite I/O, funnel, lifecycle ────────────────────────────────────
class TechScanner:
    def __init__(self, kite, get_flow_bias=None):
        self.kite = kite
        self.get_flow_bias = get_flow_bias or (lambda: 0)
        self.lock = threading.Lock()
        # Only one lifecycle pass may touch self.calls at a time. The 150s
        # funnel and the 5s tracker both call _update_lifecycle, and two of
        # them interleaving on the same call dict would corrupt its status.
        self.life_lock = threading.Lock()
        self._state_ref = None    # set by loop(); the fast tracker republishes it
        # True while the 150s funnel is mid quote-fetch. The 5s tracker yields
        # during that window: the funnel quotes the whole universe in 3 chunks
        # 1.1s apart, and a tracker call landing inside that pushes the burst to
        # ~1.8 quote calls/sec against Kite's ~1/s limit. A rejected chunk costs
        # ~400 stocks their scan for new setups, which is a worse loss than one
        # delayed trigger. At most one tracker cycle is skipped (~2.2s).
        self._funnel_quoting = False
        self._last_watch = []
        self.universe = {}        # sym → {"token": int, "seg": "EQ"}
        self.fo_names = set()     # symbols with F&O (can short swing via futures)
        self.daily = {}           # sym → {"d": [...], "u": "date"}
        self.m15_cache = {}       # sym → (fetched_at, candles)
        self.calls = []           # live + resolved-today
        self.scoreboard = {"wins": 0, "losses": 0, "sum_r": 0.0, "recent": []}
        self.status = "starting"
        self.cache_ready = 0
        self.last_scan = None
        self._cooldown = {}
        self._sl_block = {}       # (symbol, side) → date str: no same-day re-entry after SL
        self._load()

    # ―― persistence ――
    def _warn(self, tag, msg):
        """Report a degradation once per distinct message. Each of these was an
        `except: pass`, which made a failed save, a dropped quote batch or a
        broken setup indistinguishable from a quiet market."""
        seen = getattr(self, "_warned", None)
        if seen is None:
            seen = self._warned = {}
        if seen.get(tag) == msg:
            return
        seen[tag] = msg
        self.last_warn = f"{tag}: {msg}"
        print(f"  [Tech] {tag} — {msg}")

    def _load(self):
        self.phase = {"start": str(date.today()), "n": 0, "sum_r": 0.0}
        try:
            if tech_calls_file().exists():
                d = json.loads(tech_calls_file().read_text())
                self.scoreboard = d.get("scoreboard", self.scoreboard)
                self.phase = d.get("phase", self.phase)
                today = str(date.today())
                loaded = d.get("calls", [])
                self.calls = [c for c in loaded
                              if (c.get("date") == today or
                                  (c.get("tf") == "SWING" and c.get("status") in ("WAITING", "LIVE", "T1_HIT")))
                              and not _is_etfish(c.get("symbol", ""))]
                # any OPEN call the filter just dropped must be closed out in
                # the permanent history, or it freezes there as LIVE forever
                # (EXIDEIND orphan, 14/8). Voided, not graded — no fill data.
                kept = {id(c) for c in self.calls}
                for c in loaded:
                    if (id(c) not in kept
                            and c.get("status") in ("WAITING", "LIVE", "T1_HIT")):
                        c["status"] = "VOID_RESTART"
                        c["r"] = None
                        c["closed_date"] = today
                        c["note"] = "dropped at engine restart — untracked, excluded from stats"
                        self.calls.append(c)   # one last persist → history updates
                # restart must not forget today's SL lockouts
                for c in self.calls:
                    if c.get("status") == "SL_HIT" and c.get("date") == today:
                        self._sl_block[(c["symbol"], c["side"])] = today
                # restart must not forget cooldowns either (JGCHEM ×7 bug, 10/8)
                for k, v in (d.get("cooldown") or {}).items():
                    parts = k.split("|")
                    if len(parts) == 3:
                        self._cooldown[tuple(parts)] = v
        except Exception as e:
            print(f"  [Tech] could not load the live call book: {e} — starting empty, open calls from before this restart are gone")
        try:
            if daily_cache_file().exists():
                self.daily = json.loads(daily_cache_file().read_text())
                self.cache_ready = len(self.daily)
        except Exception as e:
            print(f"  [Tech] daily cache unreadable: {e} — rebuilding from scratch, first scan will be slow")

    def _save(self):
        ensure_params_logged()   # no-op after the first save under a given config
        try:
            # NEVER truncate an OPEN call off the persisted list (EXIDEIND
            # orphan, found 14/8: a dropped open call freezes in history as
            # LIVE forever). Open calls always survive; resolved fill the rest.
            opens = [c for c in self.calls
                     if c.get("status") in ("WAITING", "LIVE", "T1_HIT")]
            done = [c for c in self.calls
                    if c.get("status") not in ("WAITING", "LIVE", "T1_HIT")]
            keep = done[-max(80 - len(opens), 20):] + opens
            _atomic_write(tech_calls_file(), json.dumps(
                {"calls": keep, "scoreboard": self.scoreboard,
                 "phase": self.phase,
                 "cooldown": {"|".join(k): v for k, v in self._cooldown.items()}}))
        except Exception as e:
            self._warn("LIVE BOOK NOT SAVED", f"{e} — a restart will lose every open call")
        try:   # permanent history: every call ever, keyed by created+symbol+setup;
            hist = {}   # re-saving updates status/R in place, nothing is ever dropped
            if tech_hist_file().exists():
                hist = json.loads(tech_hist_file().read_text())
            for c in self.calls:
                k = (f"{c.get('created') or (str(c.get('date','?')) + ' ' + str(c.get('created_hm','?')))}"
                     f"|{c.get('symbol')}|{c.get('setup')}")
                hist[k] = c
            _atomic_write(tech_hist_file(), json.dumps(hist), keep_backup=True)
        except Exception as e:
            self._warn("PERMANENT HISTORY NOT SAVED", f"{e} — resolved calls are missing from the record the 26/8 verdict reads")

    # ── instrumentation stores (added 31/08) ───────────────────────────────
    WATCH_KEEP_DAYS = 90

    @staticmethod
    def _watch_key(c, session):
        """One row per distinct IDEA, not one per scan.

        The funnel re-runs every 150s over a 25-name shortlist, so a naive
        append would write the same near-identical row dozens of times a day
        and the file would be mostly duplicates. Entry is rounded to 2dp
        because it drifts by paise between scans as the signal bar updates.
        """
        return f"{session}|{c.get('symbol')}|{c.get('setup')}|{round(float(c.get('entry') or 0), 2)}"

    def _save_watching(self, rows, now):
        """Every idea the engine BUILT and then declined to publish.

        The counterfactual for the score gate: complete levels, so each row can
        be replayed through the same grading harness as a real call.

        FIRST occurrence wins. A later scan sees a more developed bar and would
        record levels the call would never have had — the first sighting is the
        moment it would have been published.
        """
        try:
            session = str(now.date())
            path = watching_file()
            store = {}
            if path.exists():
                store = json.loads(path.read_text()) or {}
            rowmap = store.get("rows") or {}
            added = 0
            for c in rows:
                k = self._watch_key(c, session)
                if k in rowmap:
                    continue                      # keep the FIRST sighting
                rec = dict(c)
                rec.update({"session": session, "seen_at": now.strftime("%H:%M:%S"),
                            "created": now.isoformat(timespec="seconds"),
                            "date": session, "created_hm": now.strftime("%H:%M"),
                            "published": False, "status": "WATCHING",
                            "fire_score": FIRE_SCORE,
                            "schema_version": SCHEMA_VERSION,
                            "params_hash": PARAMS_HASH})
                rowmap[k] = rec
                added += 1
            if not added:
                return
            cutoff = str(now.date() - timedelta(days=self.WATCH_KEEP_DAYS))
            rowmap = {k: v for k, v in rowmap.items() if (v.get("session") or "") >= cutoff}
            _atomic_write(path, json.dumps({"rows": rowmap,
                                            "schema_version": SCHEMA_VERSION,
                                            "params": _params_log(store)}))
        except Exception as e:
            self._warn("watching list not saved",
                       f"{e} — the score-gate counterfactual is not being recorded")

    def _save_gate_rejects(self):
        """Setups killed by a hard participation gate. NUMBERS ONLY.

        Not gradeable — the gates return before any levels exist. This records
        the distribution of what they remove, which is the most that can be
        recovered without moving level computation ahead of the gates.
        """
        global _GATE_REJECTS
        if not _GATE_REJECTS:
            return
        batch, _GATE_REJECTS = _GATE_REJECTS, []
        try:
            path = gate_rejects_file()
            store = {}
            if path.exists():
                store = json.loads(path.read_text()) or {}
            rowmap = store.get("rows") or {}
            for r in batch:
                k = (f"{r['date']}|{r['symbol']}|{r['setup']}|"
                     f"{round(float(r.get('part') or 0), 2)}")
                rowmap.setdefault(k, r)           # first sighting wins here too
            cutoff = str(date.today() - timedelta(days=self.WATCH_KEEP_DAYS))
            rowmap = {k: v for k, v in rowmap.items() if (v.get("date") or "") >= cutoff}
            _atomic_write(path, json.dumps({"rows": rowmap,
                                            "schema_version": SCHEMA_VERSION,
                                            "gradeable": False,
                                            "params": _params_log(store)}))
        except Exception as e:
            self._warn("gate rejects not saved", str(e))

    def _save_daily(self):
        try:
            _atomic_write(daily_cache_file(), json.dumps(self.daily))
        except Exception as e:
            self._warn("daily cache not saved", f"{e} — it will be refetched next start")

    # ―― universe ――
    def build_universe(self):
        self.status = "loading instruments"
        uni = {}
        try:
            for row in self.kite.instruments("NSE"):
                if row.get("segment") == "NSE" and row.get("instrument_type") == "EQ":
                    sym = row["tradingsymbol"]
                    # ETFs/funds pass turnover screens but barely move — junk setups
                    if _is_etfish(sym, row.get("name", "")):
                        continue
                    if sym.isalpha() or "-" not in sym:
                        uni[sym] = {"token": row["instrument_token"], "seg": "EQ"}
        except Exception as e:
            print(f"  [Tech] NSE instruments failed: {e}")
        try:
            for row in self.kite.instruments("NFO"):
                if row.get("instrument_type") == "FUT":
                    self.fo_names.add(row.get("name", ""))
        except Exception as e:
            self._warn("F&O name list failed", f"{e} — every call this session is classified as non-F&O, which changes shorting eligibility")
        try:
            best = {}
            for row in self.kite.instruments("MCX"):
                if row.get("instrument_type") != "FUT" or row.get("name") not in MCX_WANT:
                    continue
                exp = row.get("expiry")
                if not exp:
                    continue
                exp_d = exp if isinstance(exp, date) else datetime.fromisoformat(str(exp)[:10]).date()
                if exp_d < date.today():
                    continue
                cur = best.get(row["name"])
                if cur is None or exp_d < cur[0]:
                    best[row["name"]] = (exp_d, row)
            for nm, (exp_d, row) in best.items():
                uni["MCX:" + nm] = {"token": row["instrument_token"], "seg": "MCX",
                                    "tsym": row["tradingsymbol"], "expiry": str(exp_d)}
        except Exception as e:
            print(f"  [Tech] MCX instruments failed: {e}")
        import config as C
        uni["NIFTY"] = {"token": C.TOKEN_NIFTY, "seg": "IDX"}
        uni["SENSEX"] = {"token": C.TOKEN_SENSEX, "seg": "IDX"}
        uni["BANKNIFTY"] = {"token": C.TOKEN_BANKNIFTY, "seg": "IDX"}
        with self.lock:
            self.universe = uni
        print(f"  [Tech] Universe: {len(uni)} instruments "
              f"({sum(1 for v in uni.values() if v['seg'] == 'EQ')} equities, "
              f"{sum(1 for v in uni.values() if v['seg'] == 'MCX')} MCX, 3 indices)")

    def build_daily_cache(self):
        """Background: daily candles for every universe symbol (rate-limited)."""
        self.status = "building daily cache"
        todo = [s for s in self.universe
                if s not in self.daily or self.daily[s].get("u") != str(date.today())]
        pri = {"IDX": 0, "MCX": 1, "EQ": 2}
        todo.sort(key=lambda s: pri.get(self.universe[s]["seg"], 3))
        now = datetime.now()
        done = 0
        for sym in todo:
            tok = self.universe[sym]["token"]
            try:
                cd = self.kite.historical_data(tok, now - timedelta(days=200), now, "day")
                self.daily[sym] = {"u": str(date.today()),
                                   "d": [{"date": str(c["date"])[:10], "open": c["open"],
                                          "high": c["high"], "low": c["low"],
                                          "close": c["close"], "volume": c.get("volume", 0)}
                                         for c in cd[-120:]]}
                done += 1
            except Exception as e:
                _CACHE_ERRS.append(str(e))
            self.cache_ready = len(self.daily)
            if done % 50 == 0 and done:
                self._save_daily()
            time.sleep(0.36)
        self._save_daily()
        # liquidity filter: drop illiquid equities from the working universe
        drop = []
        for sym, meta in self.universe.items():
            if meta["seg"] != "EQ":
                continue
            if self._untradable(sym):
                drop.append(sym)
        with self.lock:
            for s in drop:
                self.universe.pop(s, None)
        self.status = "ready"
        print(f"  [Tech] Daily cache ready: {self.cache_ready} symbols; "
              f"tradable universe {len(self.universe)} (≥{MIN_TURNOVER_CR}cr/day turnover)")
        if _CACHE_ERRS:
            print(f"  [Tech] ...but {len(_CACHE_ERRS)} symbols failed to fetch "
                  f"(first: {_CACHE_ERRS[0]}) — they are ABSENT from the scan, "
                  f"not merely quiet")
            _CACHE_ERRS.clear()

    def _untradable(self, sym):
        """Why this symbol cannot be traded, or None if it can.

        ONE definition, used by BOTH the universe prune and the call gate.
        They were separate before: the prune ran at the END of the daily cache
        build (09:26 on 28/08) while the funnel was already issuing calls from
        an unpruned universe at 09:22. Five calls went out on names failing
        these very tests — GVPTECH at Rs0.06cr/day turnover, a 1/83rd of the
        floor. Their quotes then stopped (a pruned symbol is dropped from
        _bulk_quotes) so the LTP froze and the call could never trigger or
        resolve. the owner spotted the frozen 8.50 on screen.
        """
        d = (self.daily.get(sym) or {}).get("d") or []
        if len(d) < 25:
            return "less than 25 daily candles"
        turn = sum(_c(c, "close") * _c(c, "volume") for c in d[-20:]) / 20
        px = _c(d[-1], "close")
        ad = atr(d, 14)
        if turn < MIN_TURNOVER_CR * 1e7:
            return f"turnover Rs{turn / 1e7:.2f}cr < Rs{MIN_TURNOVER_CR}cr"
        if px < MIN_PRICE:
            return f"price Rs{px} < Rs{MIN_PRICE}"
        if px > 0 and ad / px < 0.005:
            return f"ATR {ad / px * 100:.2f}% < 0.5% (it does not move)"
        return None

    # ―― funnel ――
    def _bulk_quotes(self, symbols, uni):
        """uni = the scan's own snapshot — the cache-builder thread may prune
        self.universe concurrently, so never index the live dict here."""
        out = {}
        toks = [uni[s]["token"] for s in symbols if s in uni]
        for i in range(0, len(toks), 450):
            chunk = toks[i:i + 450]
            try:
                q = self.kite.quote(chunk)
                out.update(q)
            except Exception as e:
                self._warn("quote chunk failed", f"{e} — {len(chunk)} symbols were NOT scanned this cycle; they are missing, not flat")
            # Pace only BETWEEN chunks. The sleep used to run after the last one
            # too, which cost 1.1s of every 5s tracker cycle for nothing — the
            # tracker almost always fits in a single chunk.
            if i + 450 < len(toks):
                time.sleep(1.1)
        by_tok = {uni[s]["token"]: s for s in symbols if s in uni}
        return {by_tok[int(t)]: v for t, v in out.items() if int(t) in by_tok}

    def _circuit_locked(self, q, side):
        """Is this stock pinned at a circuit limit in the direction we want?

        Returns "upper" / "lower" / None.

        Kite's quote carries upper_circuit_limit / lower_circuit_limit, so use
        those when present. When they are missing, fall back to the signature a
        locked stock always shows: the last price sitting exactly ON the day's
        extreme AND the change from the previous close landing on a standard
        band (2/5/10/20%). Both conditions are required — a stock merely making
        a new high is not locked.

        KNOWN FALSE POSITIVE, accepted deliberately: a stock up exactly 5.00%
        AND sitting precisely on its day high is indistinguishable from a locked
        one without the real limits, so the fallback blocks it. The window is
        narrow (4.94-5.06% and to the paisa on the high) and the asymmetry is
        stark — skipping the odd genuine breakout costs one signal, whereas
        taking a circuit-locked call is a guaranteed loss after costs: the price
        cannot move your way and intraday forces a square-off at 15:10.
        Whenever Kite supplies the limits this fallback never runs at all.
        """
        if not q:
            return None
        try:
            ltp = _c(q, "last_price")
            if ltp <= 0:
                return None
            uc, lc = _c(q, "upper_circuit_limit"), _c(q, "lower_circuit_limit")
            if uc > 0 and ltp >= uc - 0.005 and side == "LONG":
                return "upper"
            if lc > 0 and ltp <= lc + 0.005 and side == "SHORT":
                return "lower"
            if uc > 0 or lc > 0:
                return None            # limits known and not hit — trust them
            oh = q.get("ohlc") or {}
            pc, hi, lo = _c(oh, "close"), _c(oh, "high"), _c(oh, "low")
            if pc <= 0:
                return None
            chg = (ltp / pc - 1) * 100
            on_band = any(abs(abs(chg) - b) < 0.06 for b in (2, 5, 10, 20))
            if not on_band:
                return None
            if side == "LONG" and chg > 0 and hi > 0 and abs(ltp - hi) < 0.005:
                return "upper"
            if side == "SHORT" and chg < 0 and lo > 0 and abs(ltp - lo) < 0.005:
                return "lower"
        except Exception as e:
            self._warn("circuit check failed", f"{e} — circuit filter not applied")
        return None

    def _screen(self, quotes, now):
        """Cheap stage-1 score from snapshots → equity shortlist."""
        elapsed = max((now.hour * 60 + now.minute) - (9 * 60 + 15), 5) / 375.0
        rows = []
        for sym, q in quotes.items():
            if self.universe.get(sym, {}).get("seg") != "EQ":
                continue
            ltp = _c(q, "last_price")
            oh = q.get("ohlc") or {}
            o, h, l = _c(oh, "open"), _c(oh, "high"), _c(oh, "low")
            pc = _c(oh, "close")
            vol = _c(q, "volume") or _c(q, "volume_traded")
            d = (self.daily.get(sym) or {}).get("d") or []
            if not d or ltp <= 0 or pc <= 0:
                continue
            av20 = sum(_c(c, "volume") for c in d[-20:]) / max(min(20, len(d)), 1)
            rvol = vol / max(av20 * min(elapsed, 1.0), 1)
            ad = atr(d, 14)
            rngx = (h - l) / ad if ad > 0 else 0
            chg = abs(ltp / pc - 1)
            pos = (ltp - l) / max(h - l, 1e-9)
            edge = max(pos, 1 - pos)
            rows.append((sym, rvol * 2 + rngx + chg * 100 * 0.3 + edge))
        rows.sort(key=lambda r: -r[1])
        return [r[0] for r in rows[:SHORTLIST_N]]

    def _m15(self, sym, now, uni):
        ent = self.m15_cache.get(sym)
        if ent and (now - ent[0]).total_seconds() < 240:
            return ent[1]
        meta = uni.get(sym)
        if not meta:
            return ent[1] if ent else []
        try:
            cd = self.kite.historical_data(meta["token"],
                                           now - timedelta(days=6), now, "15minute")
            self.m15_cache[sym] = (now, cd)
            time.sleep(0.36)
            return cd
        except Exception:
            time.sleep(0.36)
            return ent[1] if ent else []

    def _merged_daily(self, sym, quotes):
        """Cached daily bars + today's forming bar from the live quote."""
        d = list((self.daily.get(sym) or {}).get("d") or [])
        q = quotes.get(sym)
        if q and d:
            oh = q.get("ohlc") or {}
            ltp = _c(q, "last_price")
            if _c(oh, "open") > 0 and str(date.today()) != d[-1].get("date"):
                d.append({"date": str(date.today()), "open": _c(oh, "open"),
                          "high": max(_c(oh, "high"), ltp), "low": min(_c(oh, "low"), ltp) if _c(oh, "low") > 0 else ltp,
                          "close": ltp, "volume": _c(q, "volume") or _c(q, "volume_traded")})
        return d

    # ―― lifecycle ――
    def _update_lifecycle(self, quotes, now):
        """Track every open call against the latest quotes.

        PER-CALL ISOLATION (24/8 sweep): each call is processed inside its own
        try/except. Before this, one malformed call — a KeyError on a field an
        older on-disk format didn't carry — raised out of the whole loop and
        every OTHER open call went untracked that cycle. Survivable when this
        ran once per 150s; not survivable now the tracker runs it every 5s,
        where it would mean nothing is tracked at all, silently, forever.

        A failing call is reported once and skipped; the rest are unaffected.
        """
        ts = now.strftime("%H:%M")
        today_s = str(date.today())
        for c in self.calls:
            try:
                self._track_one(c, quotes, now, ts, today_s)
            except Exception as e:
                self._warn(f"lifecycle failed for {c.get('symbol')}",
                           f"{type(e).__name__}: {e} — this call is NOT being "
                           f"tracked; its stop and targets cannot fire. Other "
                           f"calls are unaffected.")

    def _track_one(self, c, quotes, now, ts, today_s):
        # Single-iteration loop, deliberately. The body below is byte-identical
        # to the one that has run since 11/8 and uses `continue` to mean "done
        # with this call". Inside a one-pass loop `continue` still means exactly
        # that, so the logic is unchanged — whereas `if True:` would have been a
        # SyntaxError, and rewriting every `continue` to `return` by hand would
        # have risked changing behaviour in a function that grades real money.
        for _once in (0,):
            if c.get("status") not in ("WAITING", "LIVE", "T1_HIT"):
                # resolved TODAY: keep LTP breathing so the card shows where
                # price went after the exit (owner, 17/8) — record untouched
                if c.get("closed_date") == today_s:
                    q0 = quotes.get(c["symbol"])
                    l0 = _c(q0, "last_price") if q0 else 0
                    if l0 > 0:
                        c["ltp"] = round(l0, 2)
                continue
            q = quotes.get(c["symbol"])
            ltp = _c(q, "last_price") if q else 0
            if ltp <= 0:
                # No quote → this call is skipped ENTIRELY, including its
                # max-hold check, so it can hang OPEN forever (the EXIDEIND
                # orphan class). Happens when a symbol leaves the universe
                # mid-life — turnover drops under the floor, or a segment is
                # suspended. Detect it; do not silently ride.
                n = c["_noq"] = c.get("_noq", 0) + 1
                if n in (20, 100, 500):
                    self._warn(f"no quote for {c.get('symbol')}",
                               f"{n} consecutive scans with no price — it is "
                               f"not being tracked and its exit rules cannot "
                               f"fire. Last known LTP {c.get('ltp')}")
                # 28/08: WARNING WAS NOT ENOUGH. GVPTECH and three others sat at
                # _noq=240 — twenty minutes untracked — with two of them shown as
                # LIVE. A position displayed as LIVE whose stop cannot fire is
                # worse than no position: it is a safety system that has stopped
                # without saying so. After NOQ_VOID scans the call is CLOSED.
                #
                # Not graded. r stays None. There is no honest exit price for a
                # symbol we could not see, and inventing one would put a made-up
                # number in the permanent record.
                if n >= NOQ_VOID:
                    c["status"] = "VOID_NOQUOTE"
                    c["r"] = None
                    c["closed_date"] = today_s
                    c["closed_at"] = now.strftime("%H:%M")
                    c["note"] = (f"untracked for {n} scans (~{n * FAST_SECS // 60} min) — "
                                 f"no quote, so entry/stop/target could never fire. "
                                 f"Voided, not graded.")
                    self._warn(f"{c.get('symbol')} VOIDED — untracked",
                               f"no price for {n} scans. It was showing as "
                               f"{c.get('status')} with rules that could not fire.")
                continue
            if c.get("_noq"):
                c.pop("_noq", None)            # quotes are back
            c["ltp"] = round(ltp, 2)
            # Extremes seen since the call was created. These used to update
            # ONLY inside the WAITING branch, so they froze the instant a call
            # went live — exactly when they are needed to tell whether a target
            # or stop was reached between two 150s scans (found 24/8 from
            # DWARKESH: day high 57.99 vs T1 57.98, hi_seen stuck at 57.16).
            c["hi_seen"] = round(max(c.get("hi_seen", ltp), ltp), 2)
            c["lo_seen"] = round(min(c.get("lo_seen", ltp), ltp), 2)
            oh = q.get("ohlc") or {}
            if _c(oh, "high") > 0:
                c["day_h"] = round(max(_c(oh, "high"), ltp), 2)
                c["day_l"] = round(min(_c(oh, "low"), ltp) if _c(oh, "low") > 0 else ltp, 2)
            pc = _c(oh, "close")
            if pc > 0:
                c["prev_c"] = round(pc, 2)
                c["chg_pct"] = round((ltp / pc - 1) * 100, 2)
            long = c["side"] == "LONG"
            if c["status"] == "WAITING":
                age = (now - datetime.fromisoformat(c["created"])).total_seconds()
                # Extremes SINCE THE CALL WAS CREATED. day_h/day_l cannot answer
                # this: they include minutes before the call existed. Recorded
                # ONLY — the trigger below still uses the sampled LTP, unchanged.
                # Purpose: measure how many entries the 150s scan gap misses, so
                # 26/8 can decide on evidence instead of impression.
                # WHICH DIRECTION DOES THE ENTRY GET REACHED FROM?
                #
                # `ltp >= entry` for a long is BREAKOUT semantics: correct when
                # the market was BELOW the entry when the call was made and has
                # to rise into it. It is wrong when the call was issued with the
                # market ALREADY ABOVE the entry — then the only way price can
                # reach 41.52 is by falling to it, and a resting buy limit fills
                # on that downward pass.
                #
                # JINDWORLD, 01/09 09:24. LONG, entry 41.52, market 41.61 at
                # issue. Price fell straight through 41.52 to 40.93. the owner's
                # real limit order at 41.52 FILLED. The engine, testing
                # `ltp >= 41.52` against samples that peaked at 41.50, never
                # triggered and filed it NOT TRIGGERED — a fill he actually got,
                # recorded as one that was never available.
                #
                # entry_side is stamped at issue from ltp0. Rows that predate it
                # keep the old test, so nothing already graded changes meaning.
                _side_at_issue = c.get("entry_side")
                if _side_at_issue == "above":       # entry is BELOW the market
                    trig = ltp <= c["entry"] if long else ltp >= c["entry"]
                elif _side_at_issue == "below":     # entry is ABOVE the market
                    trig = ltp >= c["entry"] if long else ltp <= c["entry"]
                else:
                    trig = ltp >= c["entry"] if long else ltp <= c["entry"]
                stopped_first = ltp <= c["sl"] if long else ltp >= c["sl"]
                if trig:
                    c["status"] = "LIVE"; c["triggered_at"] = ts
                elif stopped_first or (c["tf"] == "INTRADAY" and age > WAIT_EXPIRE_S):
                    # Did the exchange print through the entry while we were
                    # not looking? day_h/day_l only tell us if the touch could
                    # have happened before the call; hi_seen/lo_seen are clean.
                    touched = ((c.get("hi_seen", 0) >= c["entry"]) if long
                               else (c.get("lo_seen", 9e9) <= c["entry"]))
                    if touched:
                        c["missed_entry"] = True
                    c["status"] = "EXPIRED"; c["closed_at"] = ts
                    c["closed_date"] = date.today().isoformat()
                continue
            risk0 = c.get("risk0") or abs(c["entry"] - c["sl"]) or 1e-9
            r1 = abs(c["t1"] - c["entry"]) / risk0
            r2 = abs(c["t2"] - c["entry"]) / risk0
            hit_sl = ltp <= c["sl"] if long else ltp >= c["sl"]
            hit_t1 = ltp >= c["t1"] if long else ltp <= c["t1"]
            hit_t2 = ltp >= c["t2"] if long else ltp <= c["t2"]
            if c["status"] == "LIVE" and hit_sl:
                self._resolve(c, "SL_HIT", -1.0, ts)
            elif c["status"] == "T1_HIT" and hit_sl:
                self._resolve(c, "T1_TRAIL_OUT", r1 / 2, ts)   # half booked at T1, half scratched
            elif hit_t2:
                self._resolve(c, "T2_HIT", (r1 + r2) / 2, ts)  # half at T1, half at T2
            elif c["status"] == "LIVE" and hit_t1:
                c["status"] = "T1_HIT"
                c["sl"] = round(c["entry"], 2)          # SL → breakeven, ride to T2
                c["note"] = "T1 hit — book half, SL to entry, riding to T2"
            # time stop
            mins = now.hour * 60 + now.minute
            if c["status"] in ("LIVE", "T1_HIT") and c["tf"] == "INTRADAY":
                cutoff = 22 * 60 + 50 if c["seg"] == "MCX" else 15 * 60 + 10
                if mins >= cutoff:
                    live_r = (ltp - c["entry"]) / risk0 * (1 if long else -1)
                    r = (r1 / 2 + live_r / 2) if c["status"] == "T1_HIT" else live_r
                    self._resolve(c, "TIME_EXIT", round(r, 2), ts)
            # SWING max-hold: SWING_MAX_DAYS was declared from day one but
            # never enforced (found 13/8 — a swing missing T2 could sit
            # forever). Exit at market on session ≥ 10, 15:10 onward.
            elif (c["status"] in ("LIVE", "T1_HIT") and c["tf"] == "SWING"
                  and mins >= 15 * 60 + 10):
                try:
                    d0 = date.fromisoformat(str(c.get("date"))[:10])
                    sess, dcur = 0, d0
                    while dcur <= date.today():
                        if dcur.weekday() < 5:
                            sess += 1
                        dcur += timedelta(days=1)
                    if sess >= SWING_MAX_DAYS:
                        live_r = (ltp - c["entry"]) / risk0 * (1 if long else -1)
                        r = (r1 / 2 + live_r / 2) if c["status"] == "T1_HIT" else live_r
                        self._resolve(c, "TIME_EXIT", round(r, 2), ts)
                except Exception as e:
                    # if this throws, the SWING max-hold never fires and the
                    # call rides forever — the UNIMECH bug, found 19/8
                    self._warn("SWING time-exit failed",
                               f"{e} on {c.get('symbol')} — its {SWING_MAX_DAYS}-day "
                               f"max hold is NOT being enforced")

    def _resolve(self, c, status, r, ts):
        c["status"] = status
        c["closed_at"] = ts
        # Freeze the price the call actually closed at. `ltp` keeps breathing
        # for the rest of the resolution day (so the card can show where price
        # went afterwards), which meant the displayed "LTP" was the day's CLOSE,
        # not the exit — CORDELIA showed 978.40 while its R was computed from
        # 978.87 (found 24/8).
        if c.get("exit_px") is None and c.get("ltp") is not None:
            c["exit_px"] = c["ltp"]
        c["closed_date"] = str(date.today())
        c["r"] = round(r, 2)
        S.notify("CALL", f"{c.get('symbol')} {status.replace('_',' ')} {('%+.2f' % r)}R",
                 "good" if r > 0.15 else ("bad" if r < -0.15 else "info"))
        if status == "SL_HIT":
            if not hasattr(self, "_sl_block"):
                self._sl_block = {}
            self._sl_block[(c["symbol"], c["side"])] = str(date.today())
        sb = self.scoreboard
        if r > 0.15:
            sb["wins"] += 1
        elif r < -0.15:
            sb["losses"] += 1
        sb["sum_r"] = round(sb.get("sum_r", 0) + r, 2)
        ph = getattr(self, "phase", None)
        if ph is not None and c.get("seg") == "IDX":
            ph["n"] = ph.get("n", 0) + 1
            ph["sum_r"] = round(ph.get("sum_r", 0.0) + r, 2)
        # .get(), not [] — a call restored from an older on-disk format without
        # "setup" used to raise KeyError here. That was survivable at the 150s
        # cadence; now the fast tracker runs this every 5s, so one malformed
        # call would stop ALL open calls being tracked on every cycle. Found in
        # the 24/8 sweep.
        sb.setdefault("recent", []).append({"sym": c.get("symbol"), "setup": c.get("setup"),
                                            "r": round(r, 2), "date": c.get("date")})
        sb["recent"] = sb["recent"][-20:]

    # ―― one scan cycle ――
    def _drain_setup_errs(self):
        if _SETUP_ERRS:
            n = len(_SETUP_ERRS)
            self._warn("setup functions raised",
                       f"{n} exception(s) this cycle (first: {_SETUP_ERRS[0]}) — "
                       f"those setups produced NO candidates, which is not the "
                       f"same as producing none")
            _SETUP_ERRS.clear()

    def scan_once(self):
        now = datetime.now()
        if now.weekday() >= 5:
            self.status = "weekend"
            return
        mins = now.hour * 60 + now.minute
        nse_open = 9 * 60 + 16 <= mins <= 15 * 60 + 40   # derivatives till 15:40 (3-Aug-26)
        mcx_open = 9 * 60 + 5 <= mins <= 23 * 60 + 25
        if not (nse_open or mcx_open):
            self.status = "market closed"
            return
        with self.lock:
            active = dict(self.universe)
        open_syms = {c["symbol"] for c in self.calls if c.get("status") in ("WAITING", "LIVE", "T1_HIT")}
        want = set(open_syms)
        idx_mcx = [s for s, m in active.items() if m["seg"] in ("IDX", "MCX")]
        eq_all = [s for s, m in active.items() if m["seg"] == "EQ" and s in self.daily]
        self.status = "scanning"
        self._funnel_quoting = True
        try:
            quotes = self._bulk_quotes(eq_all + idx_mcx, active) if nse_open else \
                self._bulk_quotes([s for s in idx_mcx if active[s]["seg"] == "MCX"] + list(open_syms & set(active)), active)
        finally:
            self._funnel_quoting = False
        with self.life_lock:
            self._update_lifecycle(quotes, now)
        shortlist = self._screen(quotes, now) if nse_open else []
        targets = list(dict.fromkeys(
            ([s for s in idx_mcx if active[s]["seg"] == "IDX"] if nse_open else []) +
            ([s for s in idx_mcx if active[s]["seg"] == "MCX"] if mcx_open else []) +
            shortlist))
        flow = 0
        try:
            flow = self.get_flow_bias()
        except Exception as e:
            self._warn("flow bias unavailable", f"{e} — mkt_flow logs as 0 for these calls, which mutes the 26/8 market-filter question")
        entry_ok_nse = 9 * 60 + 20 <= mins <= 14 * 60 + 45
        entry_ok_mcx = 9 * 60 + 20 <= mins <= 22 * 60 + 45
        fresh, watching = [], []
        for sym in targets:
            seg = active[sym]["seg"]
            m15 = self._m15(sym, now, active)
            dcand = self._merged_daily(sym, quotes)
            if len(m15) < 20 or len(dcand) < 25:
                continue
            fb = flow if seg == "IDX" else 0
            fired, watch = analyze_instrument(sym, seg, dcand, m15, now,
                                              flow_bias=fb,
                                              can_short_swing=(sym in self.fo_names))
            entry_ok = entry_ok_mcx if seg == "MCX" else entry_ok_nse
            if seg == "IDX" and mins > 14 * 60:
                entry_ok = False      # 3-yr backtest: index entries after 14:00 avg 0.0R
            for c in fired:
                if seg == "MCX" and not MCX_ENABLED:
                    continue
                if not entry_ok and c["tf"] == "INTRADAY":
                    continue
                # ── CIRCUIT LOCK (owner, 26/8) ──────────────────────────────
                # KANCHI 09:20 was called LONG at 411.90 — which was the 5%
                # UPPER CIRCUIT (prev close 392.30 x 1.05 = 411.92). LTP, day
                # high and entry were all the same number: the stock was frozen
                # at the ceiling. It legally cannot trade higher today, so T1 at
                # 414.22 is unreachable, and being INTRADAY it must be squared
                # off by 15:10 — a guaranteed scratch at best, a loss after
                # costs. The squeeze setup saw "breaking up on x7.9 volume"; it
                # was the circuit being hit, not a breakout.
                #
                # A call that cannot be filled or cannot profit is not a signal.
                _lock = self._circuit_locked(quotes.get(sym), c["side"])
                if _lock:
                    self._warn(f"{sym} at its {_lock} circuit",
                               f"{c['side']} call suppressed — price is frozen at "
                               f"the limit and cannot move that way today")
                    continue
                # post-SL lockout: once a symbol+direction stops us out, no
                # re-entry that side for the rest of the day (any setup) —
                # live record: GOLD/NATURALGAS re-shorts alone cost −3R
                if self._sl_block.get((sym, c["side"])) == str(date.today()):
                    continue
                # ── STOCK DAY-STOP (agreed 12/8 after the 7-SL day): once
                #    resolved EQ intraday calls sum to −5R today, no new EQ
                #    intraday entries. Correlated red-tape days get capped;
                #    swings and index calls have their own rules. ──
                if seg == "EQ" and c.get("tf") != "SWING":
                    _dr = sum(x.get("r", 0) for x in self.calls
                              if x.get("r") is not None and x.get("seg") == "EQ"
                              and x.get("tf") != "SWING"
                              and x.get("closed_date") == str(date.today()))
                    if _dr <= -5.0:
                        if getattr(self, "_daystop_day", None) != str(date.today()):
                            self._daystop_day = str(date.today())
                            S.notify("ENGINE", f"⛔ STOCK DAY-STOP hit ({_dr:+.1f}R) — "
                                     "no new intraday stock entries today", "warn")
                        continue
                # 20-day-breakout swings: skip the opening 30 min — gap-driven
                # pops print "fresh 20d high x2 vol" then fade (0/3 live)
                if (c["setup"] == "BREAKOUT-20D" and seg == "EQ"
                        and mins < 9 * 60 + 45):
                    continue
                key = (sym, c["setup"], c["side"])
                if sym in open_syms or time.time() - self._cooldown.get(key, 0) < COOLDOWN_S:
                    continue
                # same signal must never fire twice in one day: if ANY call today
                # (even resolved) has same sym+setup+side at ~the same entry,
                # it's the same trade — one JGCHEM breakout became 7 "wins" (10/8)
                if any(c2.get("symbol") == sym and c2.get("setup") == c["setup"]
                       and c2.get("side") == c["side"]
                       and c2.get("date") == str(date.today())
                       and abs((c2.get("entry") or 0) - c["entry"]) / max(c["entry"], 1e-9) < 0.003
                       for c2 in self.calls):
                    continue
                # ── VERDICT-WINDOW LOGGING (12/8-25/8, owner): stamp the market
                #    regime on every call so the "longs only when the index
                #    agrees" filter can be judged on data, not on one red day ──
                _npts = None
                try:
                    _npts = S.state.get("nifty_change_pts")
                except Exception as e:
                    self._warn("nifty pts unavailable", f"{e} — mkt_nifty_pts not recorded for these calls")
                c.update(status="WAITING", created=now.isoformat(timespec="seconds"),
                         created_hm=now.strftime("%H:%M"), date=str(date.today()),
                         mkt_flow=flow, mkt_nifty_pts=_npts,
                         ltp=_c(quotes.get(sym) or {}, "last_price") or c["entry"])
                # ── entry reachability (instrumentation only, 25/8) ──────────
                # The owner, 25/8, on an APTUS grading he did not believe. The
                # investigation found something else: 25 of 495 calls were born
                # ALREADY PAST their entry — the declared price was never
                # available after the call existed. Re-graded at the best price
                # actually on offer, those 25 gave back 9.19R, about 8% of the
                # whole edge. MCX 12/8 was recorded +2.75R on an entry 8% away
                # from anything that traded afterwards.
                #
                # Setups compute an entry from 15-min candles; by the time the
                # call is issued the market may already be through it. Nothing
                # recorded whether that had happened, so nobody could see it.
                #
                # ltp0 is the price AT ISSUE. It changes no rule and no grade —
                # it just makes the question answerable forever. The rule fix
                # (skip, or re-price to the market) is a 26/8 decision.
                _l0 = _c(quotes.get(sym) or {}, "last_price")
                if _l0:
                    c["ltp0"] = round(_l0, 2)
                    _past = (_l0 > c["entry"]) if c["side"] == "LONG" else (_l0 < c["entry"])
                    # Which side of the entry the market was on AT ISSUE. This
                    # decides the trigger direction in _update_lifecycle — see
                    # the JINDWORLD note there. Stamped for every call, not just
                    # the ones that are through, so the test is never guessing.
                    c["entry_side"] = "above" if _past else "below"
                    if _past:
                        c["entry_gap_pct"] = round(abs(_l0 - c["entry"]) / c["entry"] * 100, 3)
                        self._warn(f"{sym} issued past its entry",
                                   f"entry {c['entry']} but the market is already at "
                                   f"{round(_l0, 2)} ({c['entry_gap_pct']}% through it) — "
                                   f"this fill was not available when the call was made")
                # THE GATE (28/08). Universe membership is not enough: the
                # prune runs at the end of the daily-cache build, so during the
                # morning window this loop sees names that are about to be
                # dropped. Re-test the symbol itself, every time, right before
                # the call becomes real. A call we cannot fill is worse than no
                # call — it also freezes at WAITING forever, because a pruned
                # symbol stops being quoted.
                if (c.get("seg") == "EQ" or c.get("seg") is None) and sym not in ("NIFTY", "BANKNIFTY", "SENSEX"):
                    _why = self._untradable(sym)
                    if _why:
                        self._warn(f"{sym} call SUPPRESSED",
                                   f"{_why} — issued from an unpruned universe; "
                                   f"not tradable, so no call was made")
                        continue
                self._cooldown[key] = time.time()
                S.notify("CALL", f"NEW {c['side']} {sym} — {c['setup']} score {c['score']} "
                                 f"entry {c['entry']}", "info")
                self.calls.append(c)
                open_syms.add(sym)
                fresh.append(c)
            watching.extend(watch[:1])
        watching.sort(key=lambda c: -(c.get("score") or 0))
        self.last_scan = now.strftime("%H:%M:%S")
        self.status = "ready"
        self._save()
        # Persist the FULL watching list, not the six the tab shows. This is
        # instrumentation and must not inherit a display cap.
        self._save_watching(watching, now)
        self._save_gate_rejects()
        self._drain_setup_errs()
        return {"fresh": fresh, "watching": watching[:6]}

    def snapshot(self, watching=None):
        today = str(date.today())
        live = [c for c in self.calls if c.get("status") in ("WAITING", "LIVE", "T1_HIT")]
        done = [c for c in self.calls if c.get("date") == today
                and c.get("status") not in ("WAITING", "LIVE", "T1_HIT")]
        sb = dict(self.scoreboard)
        n = sb.get("wins", 0) + sb.get("losses", 0)
        sb["hit_pct"] = round(sb.get("wins", 0) / n * 100) if n else None
        ph = dict(getattr(self, "phase", {}) or {})
        ph["avg_r"] = round(ph["sum_r"] / ph["n"], 2) if ph.get("n") else None
        today_r = round(sum(c.get("r", 0) for c in done
                            if c.get("r") is not None and c.get("seg") == "IDX"), 2)
        return {"status": self.status, "ts": self.last_scan,
                "phase": ph, "today_r": today_r,
                "universe": len(self.universe), "cache_ready": self.cache_ready,
                # .get() — snapshot() is now called from the 5s tracker as well
                # as the funnel, so a single call missing "score" would raise
                # here and stop every open call being republished (24/8 sweep).
                "calls": sorted(sorted(live, key=lambda c: c.get("created") or "",
                                       reverse=True), key=lambda c: -(c.get("score") or 0)),
                "resolved_today": done[-10:], "watching": watching or [],
                "scoreboard": sb}

    # ―― fast lifecycle tracker ――
    # The owner, 24/8: "many trades i have missed because the dashboard didnt say
    # live." He was right, and the cause was structural. _update_lifecycle —
    # the code that decides a call has triggered, hit its stop or reached a
    # target — only ran inside the 150s funnel, so a call could sit at LIVE for
    # two and a half minutes before saying so. He places these by hand, so that
    # delay was the whole trade.
    #
    # The 150s cadence exists because the FUNNEL is expensive: it quotes the
    # entire equity universe to hunt for new setups. Tracking calls that already
    # exist is not expensive — it is one quote call for a handful of symbols.
    # There was never a reason for the cheap job to wait on the costly one.
    #
    # NOTE FOR 26/8: this changes no rule. Same setups, same entries, same stops,
    # same targets. What changes is SAMPLING RESOLUTION — from 150s to 5s. It
    # makes grading truer, not different, but it does put a seam in the data:
    # calls before 24/8 were graded at 150s resolution and will under-report
    # both stops and targets touched between scans.
    def lifecycle_once(self):
        now = datetime.now()
        if now.weekday() >= 5:
            return 0
        mins = now.hour * 60 + now.minute
        nse_open = 9 * 60 + 16 <= mins <= 15 * 60 + 40
        mcx_open = 9 * 60 + 5 <= mins <= 23 * 60 + 25
        if not (nse_open or mcx_open):
            return 0
        with self.lock:
            active = dict(self.universe)
            open_syms = [c["symbol"] for c in self.calls
                         if c.get("status") in ("WAITING", "LIVE", "T1_HIT")]
        syms = [s for s in dict.fromkeys(open_syms) if s in active]
        if not syms:
            return 0
        if self._funnel_quoting:
            return 0            # yield to the funnel — see _funnel_quoting
        quotes = self._bulk_quotes(syms, active)
        if not quotes:
            return 0
        with self.life_lock:
            self._update_lifecycle(quotes, now)
        # Republish, or the API keeps serving the statuses from the last funnel
        # pass and none of this work is visible.
        if self._state_ref is not None:
            # update() ONLY — never clear() first. clear()+update() is two
            # operations, and /api/tech_calls does dict(S.tech_calls) with no
            # lock, so a reader landing between them sees an EMPTY dict and the
            # TRADE tab blanks. Measured: ~2.4M partial reads/sec under
            # contention with clear(), zero without it. snapshot() always
            # returns the same key set, so clear() removes nothing update()
            # would not immediately restore. Found in the 24/8 re-check —
            # harmless at the old 150s cadence, live at 5s.
            self._state_ref.update(self.snapshot(self._last_watch))
        return len(syms)

    def fast_loop(self, state_ref):
        self._state_ref = state_ref
        while True:
            try:
                self.lifecycle_once()
            except Exception as e:
                self._warn("fast lifecycle failed", f"{e} — open calls are being "
                           f"tracked only by the {SCAN_SECS}s funnel until this clears")
            time.sleep(FAST_SECS)

    def loop(self, state_ref):
        self._state_ref = state_ref
        try:
            self.build_universe()
            threading.Thread(target=self.build_daily_cache, daemon=True).start()
        except Exception as e:
            print(f"  [Tech] init failed: {e}")
        last_watch = []
        while True:
            try:
                res = self.scan_once()
                if res:
                    last_watch = res["watching"]
                    self._last_watch = last_watch
                    for c in res["fresh"]:
                        print(f"  [TechCall] {c['side']} {c['symbol']} {c['setup']} "
                              f"score {c['score']} entry {c['entry']} SL {c['sl']}")
                state_ref.update(self.snapshot(last_watch))   # see lifecycle_once: no clear()
            except Exception as e:
                print(f"  [Tech] scan failed: {e}")
                self.status = f"error: {e}"
            time.sleep(SCAN_SECS)


# ── self-test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import random
    random.seed(11)

    def bars(n, start, drift, vol, base_v=100_000, start_dt=None, step_min=15):
        out, px = [], start
        t = start_dt or datetime(2026, 7, 21, 9, 15)
        for i in range(n):
            o = px
            px += drift + random.uniform(-vol, vol)
            out.append({"date": t + timedelta(minutes=step_min * i), "open": o,
                        "high": max(o, px) + random.uniform(0, vol * 0.5),
                        "low": min(o, px) - random.uniform(0, vol * 0.5),
                        "close": px, "volume": int(base_v * (0.7 + random.random()))})
        return out

    now = datetime(2026, 7, 21, 10, 30)

    # 1) daily regime sanity
    up_d = bars(60, 100, 0.8, 1.0, start_dt=datetime(2026, 4, 1), step_min=60 * 24)
    dn_d = bars(60, 200, -0.8, 1.0, start_dt=datetime(2026, 4, 1), step_min=60 * 24)
    assert daily_regime(up_d)["dir"] == "UP" and daily_regime(dn_d)["dir"] == "DOWN"
    print("regime: UP/DOWN detected correctly")

    # 2) engineered PULLBACK long must fire — deterministic same-day scene:
    #    steady rise (price hugs EMA20), 3-bar RSI reset, strong turn bar
    def engineer_pullback(scale, vol0):
        mm = []
        t = datetime(2026, 7, 21, 9, 15)
        px = 100.0 * scale
        for i in range(60):                       # gentle rise, ranges ≫ slope
            o = px
            px = o + 0.08 * scale
            mm.append({"date": t + timedelta(minutes=5 * i), "open": o,
                       "high": max(o, px) + 0.5 * scale, "low": min(o, px) - 0.5 * scale,
                       "close": px, "volume": vol0})
        for _ in range(3):                        # three red bars — RSI resets
            o = mm[-1]["close"]
            px = o - 0.5 * scale
            mm.append({"date": mm[-1]["date"] + timedelta(minutes=5), "open": o,
                       "high": o + 0.1 * scale, "low": px - 0.1 * scale,
                       "close": px, "volume": int(vol0 * 0.8) if vol0 else 0})
        o = mm[-1]["close"]                       # strong green turn bar, 3x volume
        cl = o + 0.85 * scale
        mm.append({"date": mm[-1]["date"] + timedelta(minutes=5), "open": o,
                   "high": cl + 0.05 * scale, "low": o - 0.1 * scale,
                   "close": cl, "volume": vol0 * 3})
        return mm

    d = bars(60, 100, 0.9, 0.8, start_dt=datetime(2026, 4, 1), step_min=60 * 24)
    m = engineer_pullback(1.0, 100_000)
    c = setup_pullback("TESTEQ", "EQ", daily_regime(d), m, now)
    assert c and c["side"] == "LONG" and c["sl"] < c["entry"] < c["t1"] <= c["t2"], c
    print(f"pullback: fires LONG, entry {c['entry']} SL {c['sl']} T1 {c['t1']} score {c['score']}")

    # 3) ORB long with trend + volume must fire inside the window only
    d = bars(60, 100, 0.9, 0.9, start_dt=datetime(2026, 4, 1), step_min=60 * 24)
    ad = atr(d, 14)
    m = bars(30, 148, 0.05, 0.35, start_dt=datetime(2026, 7, 20, 9, 15))
    t0 = datetime(2026, 7, 21, 9, 15)
    orw = max(0.45 * ad, 1.0)
    m += [{"date": t0, "open": 150, "high": 150 + orw / 2, "low": 150 - orw / 2, "close": 150.2, "volume": 90_000},
          {"date": t0 + timedelta(minutes=15), "open": 150.2, "high": 150 + orw / 2, "low": 150 - orw / 2, "close": 150.1, "volume": 85_000},
          {"date": t0 + timedelta(minutes=30), "open": 150.1, "high": 150 + orw / 2 + 0.2, "low": 150, "close": 150 + orw / 2 + 0.15, "volume": 300_000}]
    c = setup_orb("TESTEQ", "EQ", daily_regime(d), m, datetime(2026, 7, 21, 10, 0))
    assert c and c["side"] == "LONG" and c["or_hi"] > 0, c
    print(f"ORB: fires LONG on with-trend break, entry {c['entry']} SL {c['sl']}")
    assert setup_orb("TESTEQ", "EQ", daily_regime(d), m, datetime(2026, 7, 21, 13, 0)) is None
    print("ORB: correctly silent outside the 9:45–11:30 window")

    # 4) squeeze: tight coil then a volume break must fire
    d = bars(60, 100, 0.4, 0.9, start_dt=datetime(2026, 4, 1), step_min=60 * 24)
    m = bars(80, 150, 0.3, 2.2, start_dt=datetime(2026, 7, 17, 9, 15))
    px = m[-1]["close"]
    for i in range(25):    # dead-tight coil
        o = px
        px = o + random.uniform(-0.06, 0.06)
        m.append({"date": m[-1]["date"] + timedelta(minutes=15), "open": o, "high": max(o, px) + 0.04,
                  "low": min(o, px) - 0.04, "close": px, "volume": 60_000})
    o = px
    cl = o + 2.8
    m.append({"date": m[-1]["date"] + timedelta(minutes=15), "open": o, "high": cl + 0.1,
              "low": o - 0.05, "close": cl, "volume": 400_000})
    c = setup_squeeze("TESTEQ", "EQ", daily_regime(d), m, now)
    assert c and c["side"] == "LONG", c
    print(f"squeeze: fires LONG on compression break, score {c['score']}")

    # 5) 20-day breakout swing + 52w-high bonus
    d = bars(120, 100, 0.5, 1.2, start_dt=datetime(2026, 1, 15), step_min=60 * 24)
    hi20 = max(_c(x, "high") for x in d[-21:-1])
    d.append({"date": d[-1]["date"] + timedelta(days=1), "open": hi20 - 1, "high": hi20 * 1.006,
              "low": hi20 - 2, "close": hi20 * 1.005, "volume": int(sum(_c(x, "volume") for x in d[-20:]) / 20 * 3.0)})
    reg = daily_regime(d)
    c = setup_breakout20("TESTEQ", "EQ", reg, d)
    assert c and c["tf"] == "SWING" and c["side"] == "LONG" and c["rr"] >= 1.5, c
    print(f"breakout-20d: SWING LONG fires, RR {c['rr']}, score {c['score']} (near52 {reg['near52']:.2f})")

    # 6) index instrument (ZERO volume) can still fire — participation proxy + flow
    d = [dict(x, volume=0) for x in bars(60, 23000, 120, 60, start_dt=datetime(2026, 4, 1), step_min=60 * 24)]
    m = engineer_pullback(240.0, 0)
    c = setup_pullback("NIFTY", "IDX", daily_regime(d), m, now, flow_bias=1)
    assert c and c["side"] == "LONG", c
    assert any("flow" in w for w in c["reasons"]), c["reasons"]
    print(f"index (no volume): pullback still fires via range-expansion proxy + flow bonus, score {c['score']}")

    # 7) lifecycle: WAITING → LIVE → T1 → T2, and SL path, and scoreboard
    class _FakeKite:
        pass
    sc = TechScanner.__new__(TechScanner)
    sc.calls = []
    sc.scoreboard = {"wins": 0, "losses": 0, "sum_r": 0.0, "recent": []}
    base = {"symbol": "X", "seg": "EQ", "setup": "PULLBACK", "side": "LONG", "tf": "INTRADAY",
            "entry": 100.0, "sl": 98.0, "risk0": 2.0, "t1": 102.0, "t2": 104.0, "rr": 1.0,
            "score": 7.0, "conviction": "MEDIUM", "reasons": [], "status": "WAITING",
            "created": "2026-07-21T10:00:00", "date": "2026-07-21"}
    sc.calls = [dict(base), dict(base, symbol="Y")]
    q = lambda p: {"last_price": p}
    t = datetime(2026, 7, 21, 10, 30)
    sc._update_lifecycle({"X": q(100.5), "Y": q(97.5)}, t)     # X triggers; Y stopped before trigger
    assert sc.calls[0]["status"] == "LIVE" and sc.calls[1]["status"] == "EXPIRED"
    sc._update_lifecycle({"X": q(102.2)}, t)                    # T1 → breakeven
    assert sc.calls[0]["status"] == "T1_HIT" and sc.calls[0]["sl"] == 100.0
    sc._update_lifecycle({"X": q(104.5)}, t)                    # T2
    assert sc.calls[0]["status"] == "T2_HIT" and sc.scoreboard["wins"] == 1
    z = dict(base, symbol="Z", status="LIVE")
    sc.calls.append(z)
    sc._update_lifecycle({"Z": q(97.9)}, t)
    assert z["status"] == "SL_HIT" and sc.scoreboard["losses"] == 1
    # X: T2 with half at T1(1R) + half at T2(2R) = +1.5R; Z: −1R → net +0.5R
    assert abs(sc.scoreboard["sum_r"] - 0.5) < 1e-9, sc.scoreboard
    print("lifecycle: WAITING→LIVE→T1(breakeven)→T2, SL path, expiry + scoreboard all correct")

    # 8) analyze_instrument end-to-end + short data safety
    fired, watch = analyze_instrument("TESTEQ", "EQ", d, m, now)
    assert isinstance(fired, list) and isinstance(watch, list)
    analyze_instrument("EMPTY", "EQ", [], [], now)
    print("analyze_instrument: end-to-end ok, empty-data safe")

    print("\nAll tech_calls self-tests passed.")
