"""MOVE THE DEAD WEIGHT OUT OF THE DATA FOLDER.

MM: "can you clear all the unimportant data from ascendra capital folder? it
is not organised at all."

Nothing is deleted. Everything moves into an ARCHIVE folder beside DATA, so
it is one drag back if it turns out to matter.

HOW "UNIMPORTANT" WAS DECIDED - not by me guessing. Every program the desk
runs was read, every filename it mentions was collected, and only files that
appear in NONE of them are moved:

    ASCENDRA.py  desk.py  FORMATIONS LIVE.py  COMMODITIES LIVE.py
    SMC NEW.py   SETTLE THE RECORD.py  BUILD UNIVERSE.py  connect.py

WHAT MOVES (about 1.7 GB)

    intraday_bars_3y.json        1.3 GB, superseded by DATA\\3y_parts
    backtest_bars.json           152 MB, from a backtest in September
    intraday_bars.json           147 MB, the older version of the same thing
    announcement_history.json    147 MB, the announcements experiment
    announcements.json           2.6 MB, same
    oi_daily.json                14 MB, the open-interest tab that came off
    backtest_trades.json         the same September backtest
    formations_log-1.json        an old copy of the Patterns record
    the three .BEFORE-14OCT backups of the cleared records
    Recording 2026-09-10.mp4     84 MB screen recording in the root folder

WHAT DOES NOT MOVE, deliberately

    anything beginning .tech_    desk.py reads two of these and something I
                                 have not read writes the rest. Not mine to
                                 touch.
    3y_parts, 5m_parts           your backtest data
    everything else              because a live program names it

It prints what it moved, what it left, and how much space came back.
"""
import os, shutil, sys


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
DATA = os.path.join(HERE, "DATA")
ARCH = os.path.join(HERE, "ARCHIVE")

MOVE_FROM_DATA = [
    "intraday_bars_3y.json",
    "backtest_bars.json",
    "intraday_bars.json",
    "announcement_history.json",
    "announcements.json",
    "oi_daily.json",
    "backtest_trades.json",
    "formations_log-1.json",
    "formations_log.BEFORE-14OCT.json",
    "smc2_log.BEFORE-14OCT.json",
    "cmd_log.BEFORE-14OCT.json",
    "CHECK THE RECORD.BEFORE-28SEP.py",
    "_mcap.xlsx",
]

MOVE_FROM_ROOT = [
    "Recording 2026-09-10 102649.mp4",
]


def move(src_dir, names, label):
    out, freed = [], 0
    for n in names:
        p = os.path.join(src_dir, n)
        if not os.path.exists(p):
            continue
        try:
            sz = os.path.getsize(p)
            dest = os.path.join(ARCH, label)
            os.makedirs(dest, exist_ok=True)
            d = os.path.join(dest, n)
            if os.path.exists(d):
                os.remove(d)
            shutil.move(p, d)
            out.append((n, sz)); freed += sz
        except Exception as e:
            out.append((f"{n}  COULD NOT MOVE ({e})", 0))
    return out, freed


def main():
    print("\n  ASCENDRA - ARCHIVE THE OLD DATA")
    print("  nothing is deleted, everything moves to the ARCHIVE folder\n")
    os.makedirs(ARCH, exist_ok=True)
    a, f1 = move(DATA, MOVE_FROM_DATA, "DATA")
    b, f2 = move(HERE, MOVE_FROM_ROOT, "root")
    for n, sz in a + b:
        print(f"   moved  {sz/1e6:9.1f} MB  {n}" if sz else f"   {n}")
    if not (a or b):
        print("   nothing to move - already tidy")
    print(f"\n  {(f1 + f2)/1e9:.2f} GB moved out of the working folders.")
    print("  If anything goes wrong, drag it back from ARCHIVE.")
    print("\n  LEFT ALONE ON PURPOSE")
    print("   .tech_* files    something I have not read writes these")
    print("   3y_parts, 5m_parts   your backtest data")
    print("   every file a live program names")
    try:
        input("\n  press Enter to close ")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            input("\n  press Enter to close ")
        except Exception:
            pass
