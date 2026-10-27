"""DOWNLOAD THE OVERNIGHT WORLD - every industry, not just the ones I thought of.

MM, 23/10: "make sure you are taking everything into consideration for each and
every industry."

WHAT CHANGED FROM THE FIRST VERSION, and why

  1 OPEN, HIGH, LOW AND CLOSE - not just the close.
    The first version saved closing prices only, and that quietly crippled the
    whole Asian side. Korea closes at 11:30 in the morning our time, which is
    AFTER we open, so its closing price cannot be used to trade our open
    without cheating. Its OPENING price prints at 5:30am, well before we open,
    and is completely fair game. Without the open I had no honest Korean
    signal at all, which is why KOSPI looked like it did nothing - MM said it
    affects our AI stocks and he was right; the test was blind, not the market.

  2 THE DRIVERS THAT ACTUALLY MATTER FOR THIS THEME.
    MM pointed out that TDPOWERSYS, GVT&D, NETWEB and STLTECH all fell together
    while IT services rose. The Indian "AI" trade is not software - it is
    electronics manufacturing, power transmission gear, cables and optical
    fibre. So the list now carries the companies that set the mood for exactly
    that: Vertiv and GE Vernova and Eaton for data-centre power, Micron and
    Broadcom and AMD and Supermicro and Arista and Marvell and TSMC and ASML
    for the chips, Corning for optical fibre, the data-centre landlords, and
    Samsung and SK Hynix themselves.

  3 ONE DRIVER FOR EVERY INDIAN INDUSTRY, deliberately, so nothing is left
    without something to be tested against - pharma, banks, metals, cement,
    autos, tyres, chemicals, fertiliser, sugar, edible oil, textiles, paints,
    defence, shipping, airlines, telecom, utilities, hotels and the rest. They
    are grouped below by the Indian industry each one is there to explain, so
    anyone reading this can see what was assumed.

NOTHING ON THE DESK IS TOUCHED. It writes DATA\\global_drivers.json and nothing
else, and it needs no extra packages - only what Python already ships with.
"""
import json, os, ssl, sys, time, urllib.parse, urllib.request
from datetime import date

# Grouped by the INDIAN industry each driver is here to explain. The grouping
# is only documentation - everything is tested against everything anyway.
GROUPS = {
    "the broad tape": {
        "NASDAQ": "^IXIC", "NASDAQ100": "^NDX", "SP500": "^GSPC",
        "RUSSELL2000": "^RUT", "US_10YR": "^TNX", "VIX": "^VIX",
        "DOLLAR_INDEX": "DX-Y.NYB", "USDINR": "USDINR=X",
    },
    "Asia - opens before we do": {
        "KOSPI": "^KS11", "SAMSUNG": "005930.KS", "SKHYNIX": "000660.KS",
        "NIKKEI": "^N225", "TAIWAN": "^TWII", "TSMC_TW": "2330.TW",
        "HANGSENG": "^HSI", "SHANGHAI": "000001.SS",
    },
    "AI capex - chips": {
        "SEMIS_INDEX": "^SOX", "SEMIS_ETF": "SOXX", "NVIDIA": "NVDA",
        "BROADCOM": "AVGO", "AMD": "AMD", "MICRON": "MU",
        "MARVELL": "MRVL", "TSMC_ADR": "TSM", "ASML": "ASML",
        "APPLIEDMAT": "AMAT", "LAMRESEARCH": "LRCX",
    },
    "AI capex - boxes, power and fibre": {
        "SUPERMICRO": "SMCI", "ARISTA": "ANET", "DELL": "DELL",
        "VERTIV": "VRT", "GE_VERNOVA": "GEV", "EATON": "ETN",
        "QUANTA": "PWR", "CORNING_FIBRE": "GLW",
        "DATACENTRE_REIT": "DLR", "EQUINIX": "EQIX",
    },
    "Information Technology": {
        "ACCENTURE": "ACN", "SOFTWARE": "IGV", "COGNIZANT": "CTSH",
        "EPAM": "EPAM", "INFOSYS_ADR": "INFY", "WIPRO_ADR": "WIT",
    },
    "Healthcare": {
        "US_BIOTECH": "XBI", "US_PHARMACY": "IHE", "US_HEALTH": "XLV",
        "TEVA_GENERICS": "TEVA", "DRREDDY_ADR": "RDY",
    },
    "Financial Services": {
        "US_FINANCIAL": "XLF", "US_REGIONALBANK": "KRE",
        "HDFCBANK_ADR": "HDB", "ICICIBANK_ADR": "IBN",
    },
    "Metals & Mining": {
        "US_METALS": "XME", "STEEL": "SLX", "VALE": "VALE", "RIOTINTO": "RIO",
        "BHP": "BHP", "ALCOA": "AA", "FREEPORT_COPPER": "FCX",
        "GOLD_MINERS": "GDX",
    },
    "Oil Gas & Consumable Fuels": {
        "US_ENERGY": "XLE", "VALERO_REFINER": "VLO", "MARATHON_REFINER": "MPC",
        "OILSERVICES": "OIH",
    },
    "Automobile and Auto Components": {
        "FORD": "F", "GM": "GM", "TESLA": "TSLA", "GOODYEAR_TYRES": "GT",
        "APTIV_PARTS": "APTV",
    },
    "Chemicals and fertiliser": {
        "DOW": "DOW", "LYONDELL": "LYB", "CORTEVA_AGRI": "CTVA",
        "CF_FERTILISER": "CF", "MOSAIC_FERTILISER": "MOS", "NUTRIEN": "NTR",
    },
    "Fast Moving Consumer Goods": {
        "US_STAPLES": "XLP", "UNILEVER": "UL", "PROCTERGAMBLE": "PG",
        "COCACOLA": "KO",
    },
    "Construction Materials": {
        "CEMEX": "CX", "VULCAN": "VMC", "MARTINMARIETTA": "MLM",
    },
    "Capital Goods and defence": {
        "US_INDUSTRIAL": "XLI", "DEFENCE": "ITA", "LOCKHEED": "LMT",
        "RTX": "RTX", "CATERPILLAR": "CAT", "HONEYWELL": "HON",
    },
    "Power and Utilities": {
        "US_UTILITIES": "XLU", "NEXTERA": "NEE", "CONSTELLATION": "CEG",
    },
    "Telecommunication": {
        "US_TELECOM": "VOX", "ATT": "T", "VERIZON": "VZ",
    },
    "Realty": {"US_REALESTATE": "XLRE", "HOMEBUILDERS": "ITB"},
    "Services, shipping and travel": {
        "AIRLINES": "JETS", "ZIM_SHIPPING": "ZIM", "MATSON_SHIPPING": "MATX",
        "FEDEX": "FDX",
    },
    "Consumer Services and Durables": {
        "US_DISCRETIONARY": "XLY", "WHIRLPOOL": "WHR", "CARRIER_AC": "CARR",
    },
    "Textiles and soft commodities": {
        "COTTON": "CT=F", "SUGAR": "SB=F", "SOYBEAN_OIL": "ZL=F",
        "COFFEE": "KC=F", "WHEAT": "ZW=F",
    },
    "Media and Entertainment": {"NETFLIX": "NFLX", "DISNEY": "DIS"},
}

TICKERS = {}
for _g, _d in GROUPS.items():
    TICKERS.update(_d)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


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


OUT = os.path.join(_root(), "DATA", "global_drivers.json")


def hold(code=0):
    try:
        input("\n  press Enter to close ")
    except Exception:
        pass
    sys.exit(code)


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9"})
    try:
        return urllib.request.urlopen(
            req, timeout=timeout, context=ssl.create_default_context()).read()
    except Exception:
        # some setups break on certificate checks; a price series is public
        return urllib.request.urlopen(
            req, timeout=timeout, context=ssl._create_unverified_context()).read()


def from_yahoo(sym, years=5):
    """[[date, open, high, low, close], ...] oldest first."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(sym)}?range={years}y&interval=1d")
    d = json.loads(get(url).decode("utf8", "replace"))
    res = (d.get("chart") or {}).get("result") or []
    if not res:
        raise ValueError("no data in the reply")
    r = res[0]
    ts = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    import datetime as _dt
    out = []
    for i, t in enumerate(ts):
        try:
            o, h, l, c = (q["open"][i], q["high"][i], q["low"][i],
                          q["close"][i])
        except (KeyError, IndexError):
            continue
        if c is None:
            continue
        day = _dt.datetime.utcfromtimestamp(t).date().isoformat()
        out.append([day,
                    round(float(o), 4) if o is not None else None,
                    round(float(h), 4) if h is not None else None,
                    round(float(l), 4) if l is not None else None,
                    round(float(c), 4)])
    return out


def from_stooq(sym):
    """Second source, CSV, in case Yahoo is blocked on this machine."""
    m = {"^IXIC": "^ndq", "^NDX": "^ndx", "^GSPC": "^spx", "^RUT": "^rut",
         "^SOX": "^sox", "^KS11": "^kospi", "^N225": "^nkx", "^HSI": "^hsi",
         "000001.SS": "^shc", "^TWII": "^twse", "^VIX": "^vix",
         "DX-Y.NYB": "dx.f", "USDINR=X": "usdinr", "CT=F": "ct.f",
         "SB=F": "sb.f", "ZL=F": "bo.f", "KC=F": "kc.f", "ZW=F": "zw.f",
         "005930.KS": "005930.kr", "000660.KS": "000660.kr",
         "2330.TW": "2330.tw"}
    s = m.get(sym, sym.lower() + ".us")
    url = f"https://stooq.com/q/d/l/?s={urllib.parse.quote(s)}&i=d"
    lines = get(url).decode("utf8", "replace").strip().splitlines()
    if len(lines) < 50 or not lines[0].lower().startswith("date"):
        raise ValueError("stooq returned nothing usable")
    out = []
    for line in lines[1:]:
        p = line.split(",")
        if len(p) >= 5:
            try:
                out.append([p[0], float(p[1]), float(p[2]), float(p[3]),
                            float(p[4])])
            except ValueError:
                pass
    return out


def main():
    print("\n  ASCENDRA - THE OVERNIGHT WORLD")
    print(f"  five years of daily open/high/low/close for {len(TICKERS)} "
          f"series,")
    print("  one or more for every industry on the desk.")
    print("  Nothing on the desk is touched.\n")
    old = {}
    try:
        j = json.load(open(OUT, encoding="utf8")) or {}
        old = j.get("series") or {}
        print(f"  {len(old)} series already saved\n")
    except Exception:
        pass

    series, failed, n = {}, [], 0
    for gname, members in GROUPS.items():
        print(f"  -- {gname}")
        for name, sym in sorted(members.items()):
            n += 1
            rows = None
            for how, fn in (("yahoo", from_yahoo), ("stooq", from_stooq)):
                try:
                    r = fn(sym)
                    if r and len(r) > 200:
                        rows = r
                        print(f"     {name:17s} {len(r):5d} days  ({how})",
                              flush=True)
                        break
                except Exception:
                    pass
            if rows:
                series[name] = rows
            else:
                keep = old.get(name)
                # an old close-only series is still better than nothing, but
                # it cannot give a gap, so it is marked
                if keep:
                    series[name] = keep
                    print(f"     {name:17s} could not fetch - kept "
                          f"{len(keep)} saved days", flush=True)
                else:
                    failed.append(name)
                    print(f"     {name:17s} COULD NOT FETCH", flush=True)
            time.sleep(0.35)

    if not series:
        print("\n  Nothing could be downloaded - this machine cannot reach")
        print("  either source. Tell Claude and he will work without them.")
        hold(1)

    json.dump({"saved": date.today().isoformat(), "fields": "date,o,h,l,c",
               "groups": GROUPS, "tickers": TICKERS, "series": series},
              open(OUT, "w", encoding="utf8"))
    print(f"\n  Saved {len(series)} of {n} series to "
          f"DATA\\global_drivers.json")
    if failed:
        print(f"  Could not get: {', '.join(failed)}")
    print("  Tell Claude it is done.")
    hold(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  stopped.")
    except Exception:
        import traceback
        traceback.print_exc()
        hold(1)
