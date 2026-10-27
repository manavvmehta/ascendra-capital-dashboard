"""Split the 3-year intraday file into pieces small enough to send to Claude.

The download produced intraday_bars_3y.json at about 1.3 GB - nine times the
old file, which is exactly what MM asked for. But the bridge between this
computer and Claude's workspace carries at most 400 MB per file, so the file
has to be cut into pieces before any of it can be analysed.

Nothing is lost and nothing is changed: each piece is a valid file of the
same shape holding a slice of the stocks, and Claude joins them back together
at the other end. The original is left exactly where it is.

Double-click DOWNLOAD 3Y - SPLIT FOR CLAUDE.bat to run this.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "DATA")
SRC = os.path.join(DATA, "intraday_bars_3y.json")
OUTDIR = os.path.join(DATA, "3y_parts")
TARGET_MB = 280            # comfortably under the 400 MB ceiling


def hold(code=0):
    try:
        input("\n  Press Enter to close. ")
    except Exception:
        pass
    sys.exit(code)


print("""
  ------------------------------------------------------------------
   SPLITTING THE 3-YEAR FILE FOR CLAUDE
  ------------------------------------------------------------------
""")

if not os.path.exists(SRC):
    print(f"  Cannot find {SRC}")
    print("  Run DOWNLOAD 3 YEARS INTRADAY first.")
    hold(1)

size = os.path.getsize(SRC) / 1e6
print(f"  Reading {size:,.0f} MB ... this takes a minute.", flush=True)
try:
    doc = json.load(open(SRC, encoding="utf8"))
except Exception as e:
    print(f"  Could not read the file: {e}")
    hold(1)
bars = doc.get("bars", {})
print(f"  {len(bars):,} stocks, "
      f"{sum(len(v) for v in bars.values()):,} candles.\n", flush=True)

os.makedirs(OUTDIR, exist_ok=True)
for old in os.listdir(OUTDIR):
    if old.startswith("part") and old.endswith(".json"):
        try:
            os.remove(os.path.join(OUTDIR, old))
        except Exception:
            pass

# how many pieces, so each lands near TARGET_MB
n_parts = max(1, int(size / TARGET_MB) + 1)
syms = sorted(bars)
per = (len(syms) + n_parts - 1) // n_parts
print(f"  Writing {n_parts} pieces of about {len(syms)//n_parts} stocks "
      f"each ...\n", flush=True)

made = []
for i in range(n_parts):
    chunk = syms[i * per:(i + 1) * per]
    if not chunk:
        continue
    path = os.path.join(OUTDIR, f"part{i+1:02d}.json")
    json.dump({"interval": doc.get("interval", "15minute"),
               "part": i + 1, "of": n_parts,
               "bars": {s: bars[s] for s in chunk}},
              open(path, "w"))
    mb = os.path.getsize(path) / 1e6
    made.append((os.path.basename(path), len(chunk), mb))
    print(f"    part{i+1:02d}.json   {len(chunk):>4} stocks   {mb:>6,.0f} MB",
          flush=True)

print(f"""
  ------------------------------------------------------------------
   DONE - {len(made)} pieces in  DATA\\3y_parts\\

   Tell Claude the split is finished.
  ------------------------------------------------------------------
""")
hold(0)
