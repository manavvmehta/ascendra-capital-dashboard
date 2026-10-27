"""MAKE A SMALL ZIP OF JUST THE DASHBOARD - on your Desktop.

MM, 26/10: "i want to make a zip file of the entire project but the file is too
large, i want important things which are only related to the dashboard."

The whole folder is several gigabytes, and almost all of it is downloaded price
history that can be downloaded again. This takes only the parts that would be
painful to lose or slow to rebuild, and writes

    Desktop\\ASCENDRA DASHBOARD <date>.zip

typically a few megabytes rather than gigabytes.

WHAT GOES IN

  the programs        every .py and .bat the desk runs
  the dashboard       WEBSITE\\ascendra_desk.html and the two small pages
                      beside it
  the records         the Patterns, SMC and Commodities call logs - your actual
                      trading history, which cannot be downloaded again
  the lists           the F&O list, the Nifty Total Market list with its
                      industries, and the market-cap list

WHAT IS LEFT OUT, and why

  DATA\\3y_parts, 5m_parts        gigabytes of 15-minute candles - Zerodha will
                                 serve them again
  formations_daily, smc2_daily   caches the engines rebuild every morning
  mcx_daily, mcx_intraday        same, rebuilt on demand
  the .tech_ files               another program writes these; not mine to pack
  ARCHIVE, OLD VERSIONS          already copies of things

  kite_keys.txt AND kite_token.json ARE DELIBERATELY EXCLUDED.

  Your API key, your API secret and today's login token live in those two
  files. A zip gets emailed, copied to a pen drive, uploaded for support, left
  in a Downloads folder. Anyone who reads that file can trade your account. If
  you ever restore this zip on a new machine, run connect.py once and it will
  write them again from your own Zerodha login. The zip will say plainly that
  they were skipped.
"""
import os, sys, zipfile
from datetime import date


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
WEB = os.path.join(HERE, "WEBSITE")

NEVER = {"kite_keys.txt", "kite_token.json"}

WEB_FILES = ["ascendra_desk.html", "ASCENDRA ENGINES.html",
             "ASCENDRA RECORD.html"]

DATA_FILES = [
    "formations_log.json", "formations_live.json",      # Patterns record
    "smc2_log.json", "smc2_settled.json",               # SMC record
    "cmd_log.json",                                     # Commodities record
    "fo_universe.json",                                 # the F&O list
    "ind_niftytotalmarket_list.csv",                    # names + industries
    "mcap_1000cr.json",                                 # every name over 1000cr
    "calls_log.csv",                                    # the desk's own log
    "CHECK THE RECORD.py",
]

ROOT_FILES = ["ASCENDRA - overnight combinations.csv"]


def hold(code=0):
    try:
        input("\n  press Enter to close ")
    except Exception:
        pass
    sys.exit(code)


def desktop():
    for p in (os.path.join(os.path.expanduser("~"), "Desktop"),
              os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")):
        if os.path.isdir(p):
            return p
    return os.path.expanduser("~")


def main():
    print("\n  ASCENDRA - A SMALL ZIP OF JUST THE DASHBOARD\n")
    out = os.path.join(desktop(),
                       f"ASCENDRA DASHBOARD {date.today().isoformat()}.zip")
    added, skipped, secret = [], [], []

    def put(z, path, arc):
        if os.path.basename(path) in NEVER:
            secret.append(arc)
            return
        if not os.path.exists(path):
            skipped.append(arc)
            return
        z.write(path, arc)
        added.append((arc, os.path.getsize(path)))

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        # every program the desk runs
        if os.path.isdir(FEED):
            for n in sorted(os.listdir(FEED)):
                p = os.path.join(FEED, n)
                if not os.path.isfile(p):
                    continue
                if n.lower().endswith((".py", ".bat", ".txt")):
                    put(z, p, f"5 LIVE FEED/{n}")
        for n in WEB_FILES:
            put(z, os.path.join(WEB, n), f"WEBSITE/{n}")
        for n in DATA_FILES:
            put(z, os.path.join(DATA, n), f"DATA/{n}")
        for n in ROOT_FILES:
            put(z, os.path.join(HERE, n), n)
        note = ("ASCENDRA CAPITAL - dashboard only.\n\n"
                "NOT IN THIS ZIP, ON PURPOSE:\n"
                "  kite_keys.txt and kite_token.json - your API key, API\n"
                "  secret and login token. Never put them in a zip. On a new\n"
                "  machine, run connect.py once and it writes them again.\n\n"
                "  DATA/3y_parts and 5m_parts, formations_daily.json,\n"
                "  smc2_daily.json, mcx_daily.json, mcx_intraday.json - price\n"
                "  history and caches, several gigabytes. The engines\n"
                "  download these again on their own.\n\n"
                "TO RESTORE: unzip over a copy of the ASCENDRA CAPITAL\n"
                "folder, run connect.py to log in, then start the desk.\n")
        z.writestr("READ ME FIRST.txt", note)

    added.sort(key=lambda t: -t[1])
    for a, s in added:
        print(f"   {s/1e6:8.2f} MB  {a}")
    print(f"\n  {len(added)} files, {os.path.getsize(out)/1e6:.2f} MB zipped")
    print(f"  saved to {out}")
    if secret:
        print(f"\n  LEFT OUT ON PURPOSE (your keys): {', '.join(secret)}")
        print("  Run connect.py on the new machine instead.")
    if skipped:
        print(f"\n  not found, so not included: {', '.join(skipped)}")
    hold(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        hold(1)
