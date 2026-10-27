"""BUILD THE PROPER TEST UNIVERSE — every listed company over Rs 1,000 crore.

MM: "choose all the nse and bse stocks with market cap over 1000 cr".

Everything tested so far used the 1,281 names already in DATA\\3y_parts, which
is the Nifty Total Market list plus the F&O names. That is not the same thing
as "everything over a thousand crore", so this program goes and gets the real
list, works out what is missing, and downloads the missing history.

It does three things and prints what it found at each step:

  1 downloads NSE's own half-yearly average market capitalisation file - the
    official one, every listed company, not a website's copy - and keeps the
    names at or above Rs 1,000 crore
  2 compares that against the symbols already in DATA\\3y_parts and lists the
    ones with no price history yet
  3 downloads three years of 15-minute candles for those, in the same shape
    as the existing part files, and saves them as part06.json, part07.json
    and so on - nothing already there is touched or overwritten

Zerodha serves 15-minute candles 100 days at a time and allows three requests
a second, so this is paced at a third of a second and will take a while. It
prints its progress and can be stopped and started again: anything already
saved is skipped on the next run.

NOTHING IS CHANGED ON THE DESK. This writes new files into DATA and nothing
else. No engine, no dashboard, no log.
"""
import json, os, re, sys, time
from datetime import date, datetime, timedelta


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
PARTS = os.path.join(DATA, "3y_parts")
MCAPF = os.path.join(DATA, "mcap_1000cr.json")
MIN_CR = 1000.0
YEARS = 3
CHUNK = 100                      # days of 15-minute candles per request
PACE = 0.34                      # seconds between requests


def hold(code=0):
    try:
        input("\n  press Enter to close ")
    except Exception:
        pass
    sys.exit(code)


def need(mod, pip_name=None):
    """Import it, or install it - and if that fails, say so in one line.

    22/10: pip could not reach the internet on MM's machine and printed forty
    lines of its own traceback, which looks like the program crashed. It did
    not need the package at all - the list it wanted was already on his disk.
    A missing package now returns None and the caller decides what to do.
    """
    try:
        return __import__(mod)
    except ImportError:
        print(f"  ({mod} is not installed - trying to fetch it)", flush=True)
        try:
            os.system(f'"{sys.executable}" -m pip install -q {pip_name or mod}')
            return __import__(mod)
        except Exception:
            print(f"  could not install {mod}.")
            return None


# ── 1  the official market capitalisation list ────────────────────────────
def saved_list():
    """The list already on disk. Any day's copy will do - a company does not
    cross a thousand crore overnight, and needing today's copy is what sent
    this program to pip for a package it did not need."""
    try:
        d = json.load(open(MCAPF, encoding="utf8"))
        caps = d.get("caps") or {}
        if len(caps) >= 100 and min(caps.values()) >= MIN_CR * 0.99:
            return caps, d.get("as_of")
    except Exception:
        pass
    return None, None


def fetch_mcap():
    """NSE publishes average market cap for every listed company, twice a year.

    The page that links it is
    nseindia.com/regulations/listing-compliance/nse-market-capitalisation-all-companies
    and the file itself sits on nsearchives.nseindia.com. NSE refuses plain
    requests, so a browser-like header and a visit to the main site first are
    needed to get a cookie.
    """
    if need("requests") is None or need("openpyxl") is None:
        print("  cannot download without those packages.")
        return None
    import requests, openpyxl
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"})
    print("  asking NSE for the market capitalisation list ...", flush=True)
    try:
        s.get("https://www.nseindia.com", timeout=20)
        page = s.get("https://www.nseindia.com/regulations/"
                     "listing-compliance/nse-market-capitalisation-"
                     "all-companies", timeout=30).text
    except Exception as e:
        print(f"  could not reach NSE: {e}")
        return None
    links = re.findall(r'https?://nsearchives\.nseindia\.com[^"\']+?\.xlsx', page)
    links = [L for L in links if "MCAP" in L.upper() or "MARKET" in L.upper()]
    if not links:
        print("  NSE's page did not list a market cap file today.")
        return None
    url = links[0]
    print(f"  downloading {url.rsplit('/', 1)[-1]}", flush=True)
    raw = s.get(url, timeout=120).content
    tmp = os.path.join(DATA, "_mcap.xlsx")
    open(tmp, "wb").write(raw)
    wb = openpyxl.load_workbook(tmp, read_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    # find the header row and the two columns that matter
    sym_c = cap_c = head = None
    for i, r in enumerate(rows[:15]):
        cells = [str(x).strip().lower() if x is not None else "" for x in r]
        for j, c in enumerate(cells):
            if c in ("symbol", "nse symbol"):
                sym_c = j
            if "market" in c and "cap" in c:
                cap_c = j
        if sym_c is not None and cap_c is not None:
            head = i
            break
    if head is None:
        print("  could not read that file's columns - send it to Claude.")
        return None
    out = {}
    for r in rows[head + 1:]:
        try:
            sym = str(r[sym_c]).strip().upper()
            cap = float(str(r[cap_c]).replace(",", ""))
        except Exception:
            continue
        # NSE'S FILE IS IN LAKHS, NOT CRORES (22/10)
        #
        # Caught on the first run: Reliance came out as 197,078,769, which is
        # right as lakhs (Rs 19.7 lakh crore) and absurd as crores. Filtering
        # at 1,000 therefore kept everything above Rs 10 crore - 2,753 names
        # instead of the 1,405 that are actually over a thousand crore.
        cap_cr = cap / 100.0
        if sym and sym != "NONE" and cap_cr >= MIN_CR:
            out[sym] = round(cap_cr, 1)
    print(f"  {len(out)} companies at or above Rs {MIN_CR:,.0f} crore")
    json.dump({"as_of": date.today().isoformat(), "min_cr": MIN_CR,
               "source": url, "caps": out},
              open(MCAPF, "w", encoding="utf8"))
    try:
        os.remove(tmp)
    except Exception:
        pass
    return out


# ── 2  what is already here ───────────────────────────────────────────────
def have_already():
    got = set()
    if not os.path.isdir(PARTS):
        return got, []
    files = sorted(f for f in os.listdir(PARTS) if f.endswith(".json"))
    for f in files:
        try:
            got |= set(json.load(open(os.path.join(PARTS, f)))["bars"].keys())
        except Exception as e:
            print(f"  (could not read {f}: {e})")
    return got, files


# ── 3  the missing history ────────────────────────────────────────────────
def load_kite():
    if need("kiteconnect") is None:
        print("\n  kiteconnect is missing and could not be installed.\n"
              "  Your other engines use it, so try starting one of them first.\n")
        hold(1)
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
              f"{tok.get('date')}).\n  Run connect.py first.\n")
        hold(1)
    from kiteconnect import KiteConnect
    k = KiteConnect(api_key=api_key)
    k.set_access_token(tok["access_token"])
    k.quote(["NSE:RELIANCE"])
    return k


def history(kite, token, frm, to):
    """Three years of 15-minute candles, a hundred days at a time."""
    rows = []
    a = frm
    while a < to:
        b = min(a + timedelta(days=CHUNK), to)
        try:
            raw = kite.historical_data(token, a.isoformat(), b.isoformat(),
                                       "15minute") or []
            for x in raw:
                rows.append([str(x["date"])[:16], float(x["open"]),
                             float(x["high"]), float(x["low"]),
                             float(x["close"]), float(x.get("volume") or 0)])
        except Exception as e:
            print(f"    ({e})", flush=True)
        time.sleep(PACE)
        a = b
    seen, clean = set(), []
    for r in rows:
        if r[0] not in seen:
            seen.add(r[0]); clean.append(r)
    clean.sort(key=lambda r: r[0])
    return clean


def main():
    print("\n  ASCENDRA - BUILD THE TEST UNIVERSE")
    print("  every listed company over Rs 1,000 crore\n")
    caps, when = saved_list()
    if caps:
        print(f"  using the saved list from {when} "
              f"({len(caps)} companies over Rs {MIN_CR:,.0f} crore)")
    else:
        caps = fetch_mcap()
    if not caps:
        print("\n  Could not get the list. Nothing was changed.")
        hold(1)

    got, files = have_already()
    print(f"  already have price history for {len(got)} symbols "
          f"in {len(files)} part files")
    missing = sorted(s for s in caps if s not in got)
    print(f"  {len(missing)} companies over Rs {MIN_CR:,.0f} crore have no "
          f"history yet\n")
    if not missing:
        print("  Nothing to download - your universe is already complete.")
        hold(0)

    mins = len(missing) * (YEARS * 365 / CHUNK) * PACE / 60
    print(f"  downloading three years of 15-minute candles for those.")
    print(f"  roughly {mins:.0f} minutes. Leave this window open.\n")

    kite = load_kite()
    inst = kite.instruments("NSE")
    # BE-series names count too - see the note in FORMATIONS LIVE.py (23/10)
    tok = {i["tradingsymbol"]: i["instrument_token"] for i in inst
           if i.get("segment") == "NSE"
           and i.get("instrument_type") in ("EQ", "BE")}
    missing = [s for s in missing if s in tok]
    print(f"  {len(missing)} of them are tradeable on NSE today\n")

    frm = date.today() - timedelta(days=YEARS * 365)
    to = date.today()
    n_part = max([int(re.sub(r"\D", "", f) or 0) for f in files] or [0]) + 1
    bars, done = {}, 0
    for n, sym in enumerate(missing, 1):
        rows = history(kite, tok[sym], frm, to)
        if len(rows) > 2000:
            bars[sym] = rows
        done += 1
        if n % 10 == 0 or n == len(missing):
            print(f"    {n}/{len(missing)}  kept {len(bars)}", flush=True)
        # save every 250 stocks so a stop does not lose the work
        if len(bars) >= 250 or n == len(missing):
            if bars:
                p = os.path.join(PARTS, f"part{n_part:02d}.json")
                json.dump({"interval": "15minute", "part": n_part,
                           "of": n_part, "bars": bars},
                          open(p, "w", encoding="utf8"))
                print(f"  wrote {os.path.basename(p)} with {len(bars)} stocks",
                      flush=True)
                n_part += 1
                bars = {}

    print("\n  Done. Nothing on the desk was changed.")
    hold(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  stopped - anything already saved is kept.")
    except Exception:
        import traceback
        traceback.print_exc()
        hold(1)
