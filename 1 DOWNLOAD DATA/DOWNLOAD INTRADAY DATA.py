#!/usr/bin/env python3
"""
DOWNLOAD INTRADAY DATA.py

Downloads 15-minute candles for the 400 most liquid NSE stocks, one year back,
and saves them to  DATA\\intraday_bars.json

WHAT IT IS FOR
    Every backtest so far has used DAILY bars. That is enough to judge a swing
    setup, and it is useless for judging an intraday one: PULLBACK, SQUEEZE,
    VWAP-FLIP and ORB are 96% of the desk's trades and none of them has ever
    been tested, because VWAP and an opening range cannot be reconstructed
    from a daily candle.

    It is also the only way to settle a question from 10/09. A candlestick
    pattern at a level tested well when the entry happened INSIDE the signal
    bar and badly when it waited for the next one - a Rs 4,860 per trade
    difference that is purely the gap between a close and the next open. On a
    daily chart that gap is a whole night. On a 15-minute chart it is fifteen
    minutes. That is a different question and it needs this data.

HOW TO RUN IT
    1. Run  connect.py  first, so today's Zerodha login is fresh.
    2. Double-click this file.
    3. Leave it alone for about ten minutes. It prints its progress.

    If it stops early - laptop asleep, connection dropped - just run it again.
    It keeps what it already has and carries on from there.

WHAT IT COSTS
    Nothing. It uses the historical-data access the desk already uses. It makes
    about 1,200 requests, paced under Zerodha's limit.
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "DATA")
FEED = os.path.join(ROOT, "5 LIVE FEED")
OUT = os.path.join(DATA, "intraday_bars.json")

MONTHS_BACK = 12
INTERVAL = "15minute"
CHUNK = 100          # days per request - Kite's limit for 15minute is 200
PACE = 0.36          # seconds between requests


def hold(code=0):
    try:
        input("\n  Press Enter to close. ")
    except Exception:
        pass
    sys.exit(code)


SYMBOLS = [ "HDFCBANK", "ICICIBANK", "RELIANCE", "BSE", "BHARTIARTL", "INFY", "SBIN", "ETERNAL", "M&M", "BAJFINANCE", "AXISBANK", "TCS", "KALYANKJIL", "MCX", "NETWEB", "HSCL", "LT", "DIXON", "CUPID", "KOTAKBANK", "GROWW", "HINDALCO", "MARUTI", "ADANIPOWER", "HFCL", "TATASTEEL", "PAYTM", "ADANIENT", "SHRIRAMFIN", "COFORGE", "ATHERENERG", "BEL", "HCLTECH", "BHEL", "GVT&D", "POWERINDIA", "VEDL", "DATAPATTNS", "ITC", "INDIGO", "ADANIENSOL", "TECHM", "KAYNES", "EICHERMOT", "SUNPHARMA", "TVSMOTOR", "NTPC", "ADANIPORTS", "SWIGGY", "ADANIGREEN", "TMCV", "HAL", "TITAN", "LAURUSLABS", "HEROMOTOCO", "CGPOWER", "ONGC", "OFSS", "LENSKART", "POLYCAB", "TMPV", "PERSISTENT", "DIVISLAB", "SUZLON", "NATIONALUM", "HINDUNILVR", "ZEEL", "MUTHOOTFIN", "ASHOKLEY", "NESTLEIND", "TRENT", "ULTRACEMCO", "JIOFIN", "SAIL", "APARINDS", "PFC", "CANBK", "APOLLOHOSP", "VBL", "BANKBARODA", "COALINDIA", "APOLLO", "IFCI", "CHOLAFIN", "ASIANPAINT", "DLF", "POWERGRID", "WIPRO", "HINDZINC", "MOTHERSON", "GRASIM", "HINDPETRO", "UNIONBANK", "DRREDDY", "PARAS", "OLAELEC", "NYKAA", "TORNTPHARM", "HINDCOPPER", "AMBER", "HDFCAMC", "CUMMINSIND", "AEGISLOG", "BAJAJFINSV", "LODHA", "POLICYBZR", "BPCL", "INDUSTOWER", "JSWSTEEL", "SOLARINDS", "MAXHEALTH", "FEDERALBNK", "BHARATFORG", "ABB", "LUPIN", "LICI", "RECLTD", "KEI", "SBILIFE", "MAZDOCK", "DMART", "WAAREEENER", "NAUKRI", "GMRAIRPORT", "CDSL", "TATAPOWER", "CHENNPETRO", "LTM", "ANGELONE", "NMDC", "RBLBANK", "HDFCLIFE", "DELHIVERY", "INDUSINDBK", "TATACONSUM", "SONACOMS", "AUBANK", "BANDHANBNK", "MARICO", "IOC", "INDHOTEL", "YESBANK", "LTF", "BOSCHLTD", "PNB", "SAMMAANCAP", "FORCEMOT", "SYRMA", "MANAPPURAM", "BRITANNIA", "WELCORP", "ENRIN", "HYUNDAI", "INDIANB", "BIOCON", "AUROPHARMA", "CIPLA", "PIDILITIND", "BDL", "GAIL", "COCHINSHIP", "WABAG", "ABCAPITAL", "GLAND", "OIL", "WOCKPHARMA", "EXIDEIND", "ZYDUSLIFE", "RADICO", "JSWENERGY", "IDFCFIRSTB", "TDPOWERSYS", "VMM", "GODREJPROP", "MPHASIS", "FORTIS", "GLENMARK", "SIEMENS", "GRSE", "PRESTIGE", "AZAD", "RRKABEL", "HAVELLS", "BANKINDIA", "ACUTAAS", "CARTRADE", "JINDALSTEL", "360ONE", "PGEL", "PHOENIXLTD", "MAHABANK", "JSWINFRA", "PATANJALI", "ICICIGI", "PREMIERENE", "TEJASNET", "PAGEIND", "TFCILTD", "IIFL", "NAVINFLUOR", "TATAELXSI", "APLAPOLLO", "MRPL", "UPL", "KPITTECH", "RVNL", "AMBUJACEM", "REDINGTON", "UNITDSPR", "ANANTRAJ", "VOLTAS", "KIRLOSENG", "SRF", "MFSL", "ZENTEC", "RAIN", "PPLPHARMA", "BELRISE", "NHPC", "UNOMINDA", "CAMS", "ATGL", "GODREJCP", "M&MFIN", "INOXWIND", "KFINTECH", "MOREPENLAB", "UJJIVANSFB", "NEULANDLAB", "JUBLFOOD", "THANGAMAYL", "MANKIND", "PNBHOUSING", "LLOYDSME", "SBICARD", "LLOYDSENGG", "JYOTICNC", "LICHSGFIN", "TATATECH", "CPPLUS", "GESHIP", "CROMPTON", "THERMAX", "ASTRAMICRO", "CGCL", "ASTRAL", "CONCOR", "DABUR", "SAGILITY", "SANSERA", "MOTILALOFS", "SCI", "HEG", "TIINDIA", "PAISALO", "IDBI", "GRANULES", "COLPAL", "ICICIPRULI", "TIMETECHNO", "IRFC", "AVALON", "NUVAMA", "OBEROIRLTY", "NBCC", "CUB", "LGEINDIA", "ACMESOLAR", "AEROFLEX", "HBLENGINE", "NLCINDIA", "CEMPRO", "KTKBANK", "MRF", "ALKEM", "HCC", "SUPREMEIND", "KARURVYSYA", "NAZARA", "SHILPAMED", "SKYGOLD", "PIRAMALFIN", "AWL", "VIYASH", "J&KBANK", "PIIND", "GMDCLTD", "FLUOROCHEM", "SOUTHBANK", "POONAWALLA", "TARIL", "RPOWER", "SHAILY", "ASTERDM", "GENUSPOWER", "OLECTRA", "RATNAVEER", "TATACOMM", "ENGINERSIN", "PETRONET", "NEWGEN", "BAJAJHLDNG", "CHOICEIN", "IREDA", "TATACAP", "GRAPHITE", "MARKSANS", "SAILIFE", "ANANDRATHI", "NH", "GODFRYPHLP", "PARADEEP", "SHREECEM", "BLUESTARCO", "ARE&M", "INOXINDIA", "KERNEX", "SCHNEIDER", "JAINREC", "SHREEJISPG", "FCL", "RKFORGE", "CRAFTSMAN", "KIMS", "IOLCP", "TORNTPOWER", "CREDITACC", "GABRIEL", "SHYAMMETL", "RUBICON", "AGIIL", "LALPATHLAB", "BEML", "HONASA", "ANTHEM", "FINCABLES", "IEX", "HUDCO", "BALAMINES", "RBA", "BLUESTONE", "ZENSARTECH", "IRCTC", "DALBHARAT", "URBANCO", "MOSCHIP", "AARTIIND", "KEC", "CEATLTD", "WELSPUNLIV", "VIKRAMSOLR", "BSOFT", "JINDALSAW", "ECLERX", "HAPPSTMNDS", "BAJAJCON", "RATEGAIN", "MSUMI", "SENCO", "IPCALAB", "JSL", "RPTECH", "AETHER", "DEEPAKFERT", "GRAVITA", "JKTYRE", "ENDURANCE", "ITCHOTELS", "AVANTIFEED", "VINCOFE", "SBC", "ARVIND", "VOLTAMP", "TITAGARH", "EDELWEISS", "BALKRISIND", "NATCOPHARM", "BECTORFOOD", "EBGNG", "GRWRHITECH", "ACE", "BALRAMCHIN", "SENORES", "TRITURBINE", "COROMANDEL", "TBZ", "GPIL", "JAYNECOIND", "SYNGENE", "DEEPAKNTR", "PCBL", "LEMONTREE", "HDBFS", "BLUEJET", "SKIPPER", "SPARC", "MEDANTA", "LTTS", "PRICOLLTD", "CYIENTDLM" ]

print(f"""
  ------------------------------------------------------------------
   DOWNLOAD INTRADAY DATA
  ------------------------------------------------------------------
   {len(SYMBOLS)} stocks · {INTERVAL} candles · {MONTHS_BACK} months
   Saving to  DATA\\intraday_bars.json
  ------------------------------------------------------------------
""")

# ---- connect, the same way the desk does -----------------------------------
keys = os.path.join(FEED, "kite_keys.txt")
tokf = os.path.join(FEED, "kite_token.json")
if not os.path.exists(keys) or not os.path.exists(tokf):
    print("  NOT CONNECTED TO ZERODHA.\n"
          "  Open the '5 LIVE FEED' folder and run connect.py first.")
    hold(1)

api_key = None
for line in open(keys, encoding="utf8"):
    if line.strip().startswith("api_key"):
        api_key = line.split("=", 1)[-1].strip()
tok = json.load(open(tokf, encoding="utf8"))
if tok.get("date") != date.today().isoformat():
    print(f"  TODAY'S LOGIN IS MISSING (the saved one is from {tok.get('date')}).\n"
          "  Run connect.py in '5 LIVE FEED', then start this again.")
    hold(1)

# INSTALL IT HERE IF IT IS MISSING, rather than sending you back to
# connect.py. Windows often has more than one Python, and the one that opens
# a .bat is not always the one connect.py installed into - so "run connect.py
# first" is not a fix, it is a loop. This installs into whichever Python is
# actually running this file.
try:
    from kiteconnect import KiteConnect
except ImportError:
    import subprocess
    print("  Installing Zerodha's connection library for this Python ...")
    print("  (Yellow WARNING lines below are harmless.)\n", flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                    "--disable-pip-version-check", "--no-warn-script-location",
                    "kiteconnect"], check=False)
    try:
        import importlib, site
        try:
            site.main()
        except Exception:
            pass
        importlib.invalidate_caches()
        from kiteconnect import KiteConnect
    except ImportError:
        print("\n  " + "=" * 56)
        print("  Installed, but this window cannot see it yet.")
        print("  That is normal. Close this window and double-click the")
        print("  .bat again - it works the second time.")
        print("  " + "=" * 56)
        hold(1)
    print("  Installed.\n", flush=True)

kite = KiteConnect(api_key=api_key)
kite.set_access_token(tok["access_token"])
try:
    kite.quote(["NSE:RELIANCE"])
except Exception as e:
    print(f"  Zerodha refused the connection: {e}\n  Run connect.py again.")
    hold(1)
print("  Connected.\n")

# ---- what we already have --------------------------------------------------
have = {}
if os.path.exists(OUT):
    try:
        have = json.load(open(OUT, encoding="utf8")).get("bars", {})
        print(f"  Found {len(have)} stocks already downloaded - keeping them.\n")
    except Exception:
        have = {}

# ---- instrument tokens -----------------------------------------------------
print("  Reading the NSE instrument list ...", flush=True)
try:
    instr = kite.instruments("NSE")
except Exception as e:
    print(f"  Could not read the instrument list: {e}")
    hold(1)
TOK = {r["tradingsymbol"]: r["instrument_token"] for r in instr
       if r.get("instrument_type") == "EQ"}
print(f"  {len(TOK)} equity symbols.\n", flush=True)

# ---- download --------------------------------------------------------------
end = datetime.now()
start = end - timedelta(days=int(MONTHS_BACK * 30.5))
todo = [s for s in SYMBOLS if s not in have]
print(f"  {len(todo)} to download.\n", flush=True)

done = failed = 0
t0 = time.time()
for n, sym in enumerate(todo, 1):
    tk = TOK.get(sym)
    if not tk:
        failed += 1
        continue
    rows = []
    cur = start
    ok = True
    while cur < end:
        nxt = min(cur + timedelta(days=CHUNK), end)
        try:
            raw = kite.historical_data(tk, cur, nxt, INTERVAL) or []
        except Exception as e:
            if "many requests" in str(e).lower() or "too many" in str(e).lower():
                time.sleep(2.0)
                continue
            ok = False
            break
        for c in raw:
            rows.append([str(c["date"])[:16].replace("T", " "),
                         round(float(c["open"]), 2), round(float(c["high"]), 2),
                         round(float(c["low"]), 2), round(float(c["close"]), 2),
                         int(c.get("volume") or 0)])
        cur = nxt
        time.sleep(PACE)
    if ok and rows:
        have[sym] = rows
        done += 1
    else:
        failed += 1
    if n % 10 == 0 or n == len(todo):
        el = time.time() - t0
        left = (el / n) * (len(todo) - n)
        print(f"    {n}/{len(todo)}  {sym:<12} {done} ok, {failed} skipped"
              f"   about {left/60:.0f} min left", flush=True)
        # save as we go, so a crash never loses the work
        json.dump({"interval": INTERVAL, "saved": datetime.now().isoformat(),
                   "bars": have}, open(OUT, "w"))

json.dump({"interval": INTERVAL, "saved": datetime.now().isoformat(),
           "bars": have}, open(OUT, "w"))
size = os.path.getsize(OUT) / 1e6
tot = sum(len(v) for v in have.values())
print(f"""
  ------------------------------------------------------------------
   DONE
  ------------------------------------------------------------------
   {len(have)} stocks · {tot:,} candles · {size:.0f} MB
   Saved to  DATA\\intraday_bars.json

   Tell Claude it is ready.
  ------------------------------------------------------------------
""")
hold(0)
