#!/usr/bin/env python3
"""
audit.py — check EVERY call in the record for EVERY kind of impossible state.

MM has found three record bugs in two days: SMC calls filled and stopped in the
same instant, swing calls nothing was watching, and now intraday calls frozen
open overnight. Each was found by him noticing one stock. That is the wrong way
round. This checks all of them, on every row, every time.

Fifteen checks. Each one is a statement that must be true of an honest record.

28/09 — two corrections after a full pass over calls_log.csv:
  · ADDED "filled before the call was written". The fill-bug repairs of 07/09 and
    22/09 kept finding rows again because nothing here compared the fill time to
    created_hm. 76 rows were sitting in the record unflagged.
  · FIXED "stopped out but not graded -1R". It predated the trailing stop and so
    reported 357 rows, 347 of which were correct: once the stop is trailed to
    breakeven or better, a small loss IS the right grade. It now only flags a
    loss WORSE than a full stop, or a non -1R loss on a stop that never moved,
    with a 0.05R tolerance so rounding does not raise a flag.
"""
import csv, sys, collections
from datetime import date

PATH = sys.argv[1] if len(sys.argv) > 1 else "calls_log.csv"
TODAY = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
OPEN = {"WAITING", "LIVE", "T1_HIT"}

rows = list(csv.DictReader(open(PATH, encoding="utf8")))
def f(r, k):
    try: return float(r[k]) if r.get(k) not in ("", None) else None
    except Exception: return None

issues = collections.OrderedDict()
def flag(name, r, why):
    issues.setdefault(name, []).append((r, why))

for r in rows:
    st = r.get("status") or ""
    tf = (r.get("tf") or "").upper()
    d  = r.get("date") or ""
    e, sl, t1, t2 = f(r,"entry"), f(r,"sl"), f(r,"t1"), f(r,"t2")
    rk, rr = f(r,"risk0"), f(r,"r")
    ta, ca = r.get("triggered_at") or "", r.get("closed_at") or ""
    td, cd = r.get("triggered_on") or d, r.get("closed_on") or d
    long = (r.get("side") or "LONG") == "LONG"

    if st in OPEN and tf != "SWING" and d < TODAY:
        flag("intraday call left open from an earlier day", r,
             f"{d} {st} — must be out by 15:10 that day")
    if st in OPEN and rr is not None:
        flag("still open but already graded", r, f"{st} carries r={rr}")
    if st not in OPEN and st not in ("EXPIRED",) and not st.startswith("VOID") and rr is None:
        flag("closed but never graded", r, f"{st} has no r")
    if st not in OPEN and not ca:
        flag("closed with no closing time", r, st)
    if ta and ca and (cd + ca) < (td + ta):
        flag("closed BEFORE it was filled", r, f"in {td} {ta} · out {cd} {ca}")
    wh = r.get("created_hm") or ""
    if ta and wh and (td + ta) < (d + wh):
        flag("filled BEFORE the call was written", r,
             f"written {d} {wh} · filled {td} {ta} — could not have been placed")
    if st in OPEN and st != "WAITING" and not ta:
        flag("live with no fill time", r, st)
    if st == "WAITING" and ta:
        flag("waiting but carries a fill time", r, ta)
    if rk is not None and rk <= 0:
        flag("zero or negative risk", r, f"risk0={rk}")
    if e and sl and t1 and t2:
        if long and not (t2 >= t1 > e):
            flag("targets not above the entry on a long", r, f"{e}/{t1}/{t2}")
        if (not long) and not (t2 <= t1 < e):
            flag("targets not below the entry on a short", r, f"{e}/{t1}/{t2}")
        if long and sl > e + 1e-9 and st not in ("T1_HIT","T1_TRAIL_OUT"):
            flag("long with its stop above the entry", r, f"entry {e} sl {sl}")
        if (not long) and sl < e - 1e-9 and st not in ("T1_HIT","T1_TRAIL_OUT"):
            flag("short with its stop below the entry", r, f"entry {e} sl {sl}")
    if st == "SL_HIT" and rr is not None:
        # A trailed stop legitimately produces a small loss, or even a gain.
        # "Trailed" = the stop has moved to breakeven or beyond, in the
        # direction of the trade. Only an untrailed stop must grade -1R.
        trailed = (sl is not None and e is not None and
                   ((long and sl >= e - 1e-9) or ((not long) and sl <= e + 1e-9)))
        if rr < -1.05:
            flag("a loss worse than a full stop", r, f"r={rr}")
        elif not trailed and abs(rr + 1) > 0.05:
            flag("stopped out on an untrailed stop but not graded -1R", r,
                 f"r={rr} · entry {e} sl {sl}")
    if st == "T2_HIT" and rr is not None and rr <= 0:
        flag("target hit but graded a loss", r, f"r={rr}")
    if rr is not None and abs(rr) > 10:
        flag("an implausibly large result", r, f"r={rr}")
    if tf == "SWING" and st in OPEN and d < TODAY:
        pass   # legitimate — swings are held

seen = collections.Counter((r.get("date"), r.get("symbol"), r.get("setup"),
                            r.get("side"), r.get("entry")) for r in rows)
for k, n in seen.items():
    if n > 1:
        flag("the same call recorded more than once", {"date":k[0],"symbol":k[1],
             "setup":k[2],"side":k[3],"entry":k[4],"status":""}, f"{n} copies")

print("="*88)
print(f"  RECORD AUDIT  ·  {len(rows):,} calls  ·  today is {TODAY}")
print("="*88)
if not issues:
    print("\n  Every check passed. Nothing impossible in the record.\n")
else:
    tot = sum(len(v) for v in issues.values())
    print(f"\n  {tot} problems across {len(issues)} kinds:\n")
    for name, v in sorted(issues.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(v):>5}  {name}")
    print()
    for name, v in sorted(issues.items(), key=lambda kv: -len(kv[1])):
        print(f"\n  {name}  ({len(v)})")
        by_day = collections.Counter(x[0].get("date") for x in v)
        print(f"    by day: {dict(sorted(by_day.items()))}")
        for r, why in v[:4]:
            print(f"    · {r.get('date')} {r.get('symbol'):<12} {r.get('setup','') :<12} {why}")
        if len(v) > 4: print(f"    · ... and {len(v)-4} more")
print("\n" + "="*88)
