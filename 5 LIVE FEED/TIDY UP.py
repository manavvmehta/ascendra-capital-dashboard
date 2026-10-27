"""TIDY THE 5 LIVE FEED FOLDER.

MM: "first delete the files which are not important or else make a different
folder for them. i want this folder to be organised."

Nothing is deleted. Everything that is moved goes into OLD VERSIONS inside
the same folder, so if any of it turns out to matter it is one drag away.

WHAT MOVES - only dated backups and files nothing starts:

    *.BEFORE-*.py            every dated backup, six of them
    SMC TWO BOOKS.py/.bat    the old SMC engine, replaced by SMC NEW
    backtest.py              a one-off from September
    scan.py, intraday_data.py  one-off helpers, nothing calls them
    __pycache__              Python's own cache, rebuilds itself

WHAT STAYS - everything the desk actually runs:

    ASCENDRA.py              the desk itself
    connect.py               the Zerodha login
    desk.py                  what ASCENDRA.py serves
    FORMATIONS LIVE.py/.bat  Patterns
    SMC NEW.py/.bat          the new SMC book
    COMMODITIES LIVE.py/.bat Commodities
    BUILD UNIVERSE.py/.bat   the universe builder
    SETTLE THE RECORD.py     the record repair tool
    kite_keys.txt            your API key - never moved, never copied
    kite_token.json          today's login
    MORNING - what to do.txt your own instructions

It prints exactly what it moved and what it left. Run it twice and the second
run will say there is nothing to do.
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


FEED = os.path.join(_root(), "5 LIVE FEED")
OLD = os.path.join(FEED, "OLD VERSIONS")

KEEP = {
    "ascendra.py", "connect.py", "desk.py",
    "formations live.py", "formations live.bat",
    "smc new.py", "smc new.bat",
    "commodities live.py", "commodities live.bat",
    "build universe.py", "build universe.bat",
    "settle the record.py",
    "kite_keys.txt", "kite_token.json",
    "morning - what to do.txt",
    "tidy up.py", "tidy up.bat",
}

MOVE_EXACT = {
    "smc two books.py", "smc two books.bat",
    "backtest.py", "scan.py", "intraday_data.py",
}


def main():
    if not os.path.isdir(FEED):
        print(f"  cannot find {FEED}")
        return
    os.makedirs(OLD, exist_ok=True)
    moved, kept, skipped = [], [], []
    for name in sorted(os.listdir(FEED)):
        path = os.path.join(FEED, name)
        low = name.lower()
        if name == "OLD VERSIONS":
            continue
        if os.path.isdir(path):
            if low == "__pycache__":
                try:
                    shutil.rmtree(path)
                    moved.append(name + "  (cache, deleted - it rebuilds)")
                except Exception as e:
                    skipped.append(f"{name}  ({e})")
            continue
        if low in KEEP:
            kept.append(name); continue
        if ".before-" in low or low in MOVE_EXACT:
            try:
                dest = os.path.join(OLD, name)
                if os.path.exists(dest):
                    os.remove(dest)
                shutil.move(path, dest)
                moved.append(name)
            except Exception as e:
                skipped.append(f"{name}  ({e})")
        else:
            kept.append(name + "   <- not recognised, left alone")

    print("\n  MOVED TO 'OLD VERSIONS'")
    for m in moved or ["  (nothing - already tidy)"]:
        print("   ", m)
    print("\n  STILL IN 5 LIVE FEED")
    for k in kept:
        print("   ", k)
    if skipped:
        print("\n  COULD NOT MOVE (probably open in another window)")
        for s in skipped:
            print("   ", s)
    print(f"\n  Nothing was deleted except Python's own cache.")
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
