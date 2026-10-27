#!/usr/bin/env python3
"""
ASCENDRA.py — the only file you need to open.

DOUBLE-CLICK THIS. It shows you where things stand and asks what you want to
do. Everything happens in this one window.

It does not duplicate any of the other programs - it runs them, so there is
only ever one copy of each rule in one place.
"""

import csv
import json
import os
import subprocess
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def project_root():
    d = HERE
    for _ in range(4):
        if os.path.isdir(os.path.join(d, "WEBSITE")) and \
           os.path.isdir(os.path.join(d, "5 LIVE FEED")):
            return d
        p = os.path.dirname(d)
        if p == d:
            break
        d = p
    return HERE


ROOT = project_root()
FEED = os.path.join(ROOT, "5 LIVE FEED")
DATA = os.path.join(ROOT, "DATA")

G, R, Y, DIM, OFF = "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[0m"


def colour_on():
    """Windows terminals need ANSI turned on explicitly, and older ones cannot
    do it at all - in which case the codes are stripped rather than printed."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        h = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(mode))
        ctypes.windll.kernel32.SetConsoleMode(h, mode.value | 0x0004)
        return True
    except Exception:
        return False


if not colour_on():
    G = R = Y = DIM = OFF = ""


def connected_today():
    p = os.path.join(FEED, "kite_token.json")
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding="utf8"))
        return d if d.get("date") == date.today().isoformat() else False
    except Exception:
        return None


def call_count():
    p = os.path.join(DATA, "calls_log.csv")
    if not os.path.exists(p):
        return 0, None
    try:
        rows = list(csv.DictReader(open(p, encoding="utf8")))
        last = max((r.get("date") or "") for r in rows) if rows else None
        return len(rows), last
    except Exception:
        return 0, None


def backtest_summary():
    p = os.path.join(DATA, "backtest.json")
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding="utf8"))
        return d.get("overall") and d
    except Exception:
        return None


def status():
    print()
    print("  " + "-" * 58)
    tok = connected_today()
    if tok:
        print(f"  Zerodha      {G}connected{OFF}  ({tok.get('user_name','')})")
    elif tok is False:
        print(f"  Zerodha      {Y}today's login needed{OFF}")
    else:
        print(f"  Zerodha      {R}not set up yet{OFF}")

    n, last = call_count()
    if n:
        print(f"  Calls        {G}{n} on record{OFF}   last {last}")
    else:
        print(f"  Calls        {DIM}none yet{OFF}")

    bt = backtest_summary()
    if bt:
        o = bt["overall"]
        # An interval clear of zero means RELIABLE, not GOOD. The sign of the
        # mean decides which. Printing "holds up" over a negative average
        # endorses a losing system in the one line read most often.
        if o["lo"] < 0 < o["hi"]:
            verdict = f"{Y}not proven{OFF}"
        elif o["mean"] > 0:
            verdict = f"{G}makes money{OFF}"
        else:
            verdict = f"{R}LOSES money{OFF}"
        print(f"  Backtest     {o['n']:,} trades · {o['win']}% win · "
              f"{o['mean']:+.3f}R avg · {verdict}")
    else:
        print(f"  Backtest     {DIM}not run{OFF}")
    print("  " + "-" * 58)


def run(script, label):
    """Run one of the programs. A menu entry may name a folder before the
    file - the F&O study lives in its own folder, not with the desk."""
    path = os.path.join(ROOT, script) if (os.sep in script or "/" in script) \
        else os.path.join(FEED, script)
    path = os.path.normpath(path)
    if not os.path.exists(path):
        print(f"\n  {R}Cannot find {script}.{OFF}\n")
        return
    print(f"\n{DIM}  ---- {label} " + "-" * max(0, 46 - len(label)) + OFF + "\n")
    try:
        subprocess.run([sys.executable, path])
    except KeyboardInterrupt:
        pass
    print(f"\n{DIM}  ---- back to the menu " + "-" * 34 + OFF)


MENU = [
    ("1", "Log in to Zerodha", "connect.py", "every morning, first"),
    ("2", "Start the desk", "desk.py", "live engine + your website"),
    ("3", "Scan now", "scan.py", "after the close — tonight's calls"),
    ("4", "Backtest", "backtest.py", "three years, one-off"),
    ("5", "Get intraday data", "NSE FO BACKTEST/intraday_data.py",
     "for the F&O study — after the close, one-off"),
]


def main():
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print("=" * 62)
        print("   A S C E N D R A   C A P I T A L")
        print(f"{DIM}   {datetime.now():%A %d %B %Y · %H:%M}{OFF}")
        print("=" * 62)
        status()

        tok = connected_today()
        print()
        for key, name, _s, note in MENU:
            mark = " "
            if key == "1" and not tok:
                mark = f"{Y}>{OFF}"
            elif key == "2" and tok:
                mark = f"{G}>{OFF}"
            print(f"   {mark} {key}   {name:<22}{DIM}{note}{OFF}")
        print(f"     Q   Quit")

        if not tok:
            print(f"\n{Y}   Start with 1 — nothing else works without today's login.{OFF}")

        try:
            c = input("\n   Choose: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if c in ("q", "quit", "exit"):
            print("\n   Closing. The desk keeps running only if its own window "
                  "is still open.\n")
            return
        for key, name, script, _n in MENU:
            if c == key:
                run(script, name)
                try:
                    input("\n   Press Enter for the menu. ")
                except Exception:
                    return
                break
        else:
            if c:
                print(f"\n   {R}No option '{c}'.{OFF}")
                try:
                    input("   Press Enter. ")
                except Exception:
                    return


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n  Something went wrong: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        try:
            input("\n  Screenshot this and send it to Claude. Press Enter. ")
        except Exception:
            pass
