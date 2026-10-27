#!/usr/bin/env python3
"""
nse_data.py - downloads 10 years of NSE daily price history.

JUST DOUBLE-CLICK THIS FILE. It does everything on its own and tells you
what it is doing. You can close the window at any time; running it again
carries on from exactly where it stopped.

It creates a folder called "nse_data" next to this file containing:
    master/EQUITY_L.csv           every listed NSE company
    master/issued_size.csv        share counts (needed for market cap)
    bhav/YYYY/sec_YYYYMMDD.csv    one file per trading day, 10 years
    corpactions/corpactions.csv   splits and bonus issues

Nothing is scraped from a web page - these are NSE's own published files.
"""

import csv, io, json, os, random, re, subprocess, sys, time, zipfile
from datetime import date, datetime, timedelta

requests = None  # filled in by ensure_deps()

# ── configuration ──────────────────────────────────────────────────────────
# 20 months. That is all the swing caller needs: a 52-week high, a 20-day high,
# and enough bars for ATR and RSI, with margin. Ten years was for the backtest,
# which is parked - and it cost an hour instead of ten minutes.
START = date(2025, 1, 1)
END = date.today()
SLEEP = 0.45          # seconds between requests - do not lower, NSE will block
MAX_RETRY = 4
TIMEOUT = 30

BASE = "https://www.nseindia.com"
ARCH = "https://nsearchives.nseindia.com"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def ensure_deps():
    """Install the one helper package we need, if it is missing."""
    global requests
    try:
        import requests as _r
    except ImportError:
        print("  First run: installing one small helper package. Takes ~30 seconds.")
        print("  (Any yellow WARNING lines below are harmless - ignore them.)\n")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                        "--disable-pip-version-check", "--no-warn-script-location",
                        "requests"], check=False)
        # A package installed into a folder that did not exist when this process
        # started is invisible until the import machinery is told to look again.
        try:
            import importlib, site
            for p in (site.getusersitepackages(),) if hasattr(site, "getusersitepackages") else ():
                if p and p not in sys.path:
                    sys.path.insert(0, p)
            try:
                site.main()
            except Exception:
                pass
            importlib.invalidate_caches()
        except Exception:
            pass
        try:
            import requests as _r
        except ImportError:
            print("\n  " + "=" * 56)
            print("  The package installed, but this window cannot see it yet.")
            print("  This is normal and harmless.")
            print()
            print("  FIX: close this window and double-click the file again.")
            print("  It will work the second time.")
            print("  " + "=" * 56)
            hold()
            sys.exit(1)
    requests = _r


AUTO = "--auto" in sys.argv     # set by the 6pm scheduled task


def hold():
    """Keep the window open so the person can read it.

    NEVER when running unattended. A scheduled task that blocks on input()
    hangs forever, holds the lock on the data folder, and every following
    night's run fails silently behind it.
    """
    if AUTO:
        return
    try:
        input("\n  Press Enter to close this window. ")
    except Exception:
        pass


def start_log(out):
    """Under the scheduler there is no window, so keep a log worth reading."""
    if not AUTO:
        return
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "daily_run_log.txt")
    f = open(path, "a", encoding="utf8", buffering=1)

    class Tee:
        def write(self, s):
            f.write(s)
            try:
                sys.__stdout__.write(s)
            except Exception:
                pass

        def flush(self):
            f.flush()

    f.write("\n\n" + "=" * 60 + f"\n  RUN {datetime.now():%Y-%m-%d %H:%M}\n" + "=" * 60 + "\n")
    sys.stdout = sys.stderr = Tee()


# ── session handling ───────────────────────────────────────────────────────
def new_session():
    """NSE refuses archive requests without cookies from a prior page visit."""
    s = requests.Session()
    s.headers.update(HEADERS)
    for url in (BASE, f"{BASE}/all-reports"):
        try:
            s.get(url, timeout=TIMEOUT)
            time.sleep(0.3)
        except Exception:
            pass
    return s


def fetch(s, url, referer=None, expect_json=False, retries=None):
    """Returns bytes, or None on a genuine 404. Refreshes cookies on 401/403.

    `retries` is separate from MAX_RETRY because the www.nseindia.com JSON API
    is often blocked outright. Retrying a blocked endpoint four times costs ~26
    seconds per call and never succeeds - so callers that hit the API pass a
    low number and give up quickly.
    """
    hdr = {"Referer": referer} if referer else {}
    if expect_json:
        hdr["Accept"] = "application/json, text/plain, */*"
    for attempt in range(retries or MAX_RETRY):
        try:
            r = s.get(url, headers=hdr, timeout=TIMEOUT)
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                return r.content
            if r.status_code in (401, 403, 429):
                time.sleep(2 + attempt * 3)
                s.cookies.clear()
                for u in (BASE, f"{BASE}/all-reports"):
                    try:
                        s.get(u, timeout=TIMEOUT)
                    except Exception:
                        pass
                continue
            time.sleep(1 + attempt * 2)
        except Exception:
            time.sleep(1.5 + attempt * 2)
    return None


def state_path(out):
    return os.path.join(out, "_state.json")


def load_state(out):
    p = state_path(out)
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return {"holidays": []}


def save_state(out, st):
    with open(state_path(out), "w") as f:
        json.dump(st, f, indent=1)


# ── stage 1: master list and share counts ──────────────────────────────────
def stage_master(out, s):
    d = os.path.join(out, "master")
    os.makedirs(d, exist_ok=True)

    eq_path = os.path.join(d, "EQUITY_L.csv")
    if not os.path.exists(eq_path):
        b = fetch(s, f"{ARCH}/content/equities/EQUITY_L.csv", referer=BASE)
        if not b:
            print("\n  Could not reach NSE. Check your internet and run this again.")
            return False
        open(eq_path, "wb").write(b)

    rows = list(csv.DictReader(io.StringIO(open(eq_path, encoding="utf8").read())))
    rows = [{k.strip(): (v.strip() if v else "") for k, v in r.items()} for r in rows]
    symbols = [r["SYMBOL"] for r in rows if r.get("SERIES") in ("EQ", "BE")]
    print(f"  Found {len(rows)} listed companies, {len(symbols)} we care about.")

    return symbols


def stage_issued_size(out, s, symbols):
    """Share counts, for market cap. OPTIONAL - NSE often blocks this endpoint.

    Probes a few symbols first. If the endpoint is blocked, this returns
    immediately instead of spending 26 seconds per symbol failing 2,000 times.
    """
    d = os.path.join(out, "master")
    iss_path = os.path.join(d, "issued_size.csv")

    print("  Checking whether NSE will answer share-count requests ...")
    live = False
    for probe in ("RELIANCE", "TCS", "INFY"):
        b = fetch(s, f"{BASE}/api/quote-equity?symbol={probe}",
                  referer=f"{BASE}/get-quotes/equity?symbol={probe}",
                  expect_json=True, retries=1)
        if b:
            try:
                if (json.loads(b).get("securityInfo") or {}).get("issuedSize"):
                    live = True
                    break
            except Exception:
                pass
    if not live:
        print("""
  SKIPPING THIS STEP - and that is fine.

  NSE is refusing these requests from this machine. It is not
  something you did and not something a retry will fix.

  Share counts were only ever needed to work out company size.
  Claude will get that another way, from the price data itself.
  Nothing else is affected.
""")
        # leave a marker so Claude knows this was skipped deliberately
        with open(os.path.join(d, "issued_size_SKIPPED.txt"), "w") as f:
            f.write("NSE quote-equity API refused all requests. "
                    "Company size must be derived from price/turnover data.\n")
        return True

    have = set()
    if os.path.exists(iss_path):
        for r in csv.DictReader(open(iss_path, encoding="utf8")):
            if r.get("issued_size"):
                have.add(r["symbol"])
    todo = [x for x in symbols if x not in have]
    if not todo:
        print("  Share counts already collected.")
        return True
    print(f"  Getting share counts: {len(have)} done, {len(todo)} to go "
          f"(about {len(todo) * (SLEEP + 0.25) / 60:.0f} minutes)\n")

    f = open(iss_path, "a", newline="", encoding="utf8")
    w = csv.writer(f)
    if not have:
        w.writerow(["symbol", "issued_size", "face_value", "listing_date", "industry"])
    for i, sym in enumerate(todo, 1):
        url = f"{BASE}/api/quote-equity?symbol={requests.utils.quote(sym)}"
        b = fetch(s, url, referer=f"{BASE}/get-quotes/equity?symbol={sym}",
                  expect_json=True, retries=2)
        iss = fv = ld = ind = ""
        if b:
            try:
                j = json.loads(b)
                si = j.get("securityInfo") or {}
                md = j.get("metadata") or {}
                iss = si.get("issuedSize") or ""
                fv = si.get("faceValue") or ""
                ld = md.get("listingDate") or ""
                ind = md.get("industry") or ""
            except Exception:
                pass
        w.writerow([sym, iss, fv, ld, ind])
        if i % 25 == 0:
            f.flush()
            pct = 100 * i / len(todo)
            print(f"     {pct:5.1f}%   {i} of {len(todo)}", flush=True)
        time.sleep(SLEEP + random.random() * 0.25)
    f.close()
    return True


# ── stage 2: daily prices ──────────────────────────────────────────────────
def bhav_urls(d):
    """Three archive formats over ten years. Tried in this order."""
    ddmmyyyy = d.strftime("%d%m%Y")
    yyyymmdd = d.strftime("%Y%m%d")
    yr, mon, dd = d.year, MONTHS[d.month - 1], d.strftime("%d")
    return [
        ("full", f"{ARCH}/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"),
        ("udiff", f"{ARCH}/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"),
        ("legacy", f"{ARCH}/content/historical/EQUITIES/{yr}/{mon}/cm{dd}{mon}{yr}bhav.csv.zip"),
    ]


def stage_bhav(out, s):
    st = load_state(out)
    holidays = set(st.get("holidays", []))
    days, d = [], START
    while d <= END:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)

    todo = []
    for d in days:
        if d.isoformat() in holidays:
            continue
        p = os.path.join(out, "bhav", str(d.year), f"sec_{d.strftime('%Y%m%d')}.csv")
        if not os.path.exists(p):
            todo.append(d)

    done = len(days) - len(todo) - len(holidays)
    if not todo:
        print("  All trading days already downloaded.")
        return True
    print(f"  {done} days already saved. {len(todo)} to go "
          f"(about {len(todo) * (SLEEP + 0.4) / 60:.0f} minutes)\n")

    got = miss = 0
    for n, d in enumerate(todo, 1):
        outdir = os.path.join(out, "bhav", str(d.year))
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, f"sec_{d.strftime('%Y%m%d')}.csv")
        body = None
        for kind, url in bhav_urls(d):
            b = fetch(s, url, referer=f"{BASE}/all-reports")
            if not b:
                continue
            if url.endswith(".zip"):
                try:
                    z = zipfile.ZipFile(io.BytesIO(b))
                    b = z.read(z.namelist()[0])
                except Exception:
                    continue
            if len(b) < 2000:      # an error page, not real data
                continue
            body = (f"# source={kind}\n".encode() + b)
            break

        if body:
            open(path, "wb").write(body)
            got += 1
        else:
            # A date with no file is USUALLY a market holiday, and caching it
            # stops us re-probing it forever. But "no file yet" looks exactly
            # like "no file ever" - so a recent date is left uncached and gets
            # retried next run. Without this, running before ~6pm would mark
            # TODAY as a permanent holiday and today's prices would never
            # arrive, silently, on every future run.
            if (date.today() - d).days > 4:
                holidays.add(d.isoformat())
            miss += 1
        if n % 20 == 0:
            st["holidays"] = sorted(holidays)
            save_state(out, st)
            pct = 100 * n / len(todo)
            print(f"     {pct:5.1f}%   {d.strftime('%b %Y')}   "
                  f"{got} days saved", flush=True)
        time.sleep(SLEEP + random.random() * 0.3)

    st["holidays"] = sorted(holidays)
    save_state(out, st)
    print(f"\n  Saved {got} trading days. {miss} dates had no file "
          f"(market holidays - this is normal).")
    return True


# ── stage 3: splits and bonuses ────────────────────────────────────────────
def stage_corpactions(out, s):
    d = os.path.join(out, "corpactions")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "corpactions.csv")
    seen, rows = set(), []
    if os.path.exists(path):
        for r in csv.DictReader(open(path, encoding="utf8")):
            rows.append(r)
            seen.add((r.get("symbol"), r.get("exDate"), r.get("subject")))

    y = START.year
    while y <= END.year:
        a = max(START, date(y, 1, 1)).strftime("%d-%m-%Y")
        b_ = min(END, date(y, 12, 31)).strftime("%d-%m-%Y")
        url = (f"{BASE}/api/corporates-corporateActions?index=equities"
               f"&from_date={a}&to_date={b_}")
        raw = fetch(s, url, referer=f"{BASE}/companies-listing/corporate-filings-actions",
                    expect_json=True, retries=2)
        n_before = len(rows)
        if raw:
            try:
                j = json.loads(raw)
                for it in (j if isinstance(j, list) else j.get("data", [])):
                    rec = {"symbol": it.get("symbol", ""),
                           "series": it.get("series", ""),
                           "exDate": it.get("exDate", ""),
                           "recDate": it.get("recDate", ""),
                           "subject": (it.get("subject") or "").strip()}
                    k = (rec["symbol"], rec["exDate"], rec["subject"])
                    if k not in seen:
                        seen.add(k)
                        rows.append(rec)
            except Exception:
                pass
        print(f"     {y}:  {len(rows) - n_before} events", flush=True)
        y += 1
        time.sleep(SLEEP + 0.5)

    with open(path, "w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "series", "exDate", "recDate", "subject"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})

    splits = [r for r in rows if re.search(r"SPLIT|SUB-?DIVISION", r.get("subject") or "", re.I)]
    bonus = [r for r in rows if re.search(r"BONUS", r.get("subject") or "", re.I)]
    print(f"\n  {len(rows)} events total: {len(splits)} splits, {len(bonus)} bonus issues.")
    return True


# ── stage 4: check it worked ───────────────────────────────────────────────
def stage_verify(out):
    problems = []
    bh = os.path.join(out, "bhav")
    n, years = 0, {}
    if os.path.isdir(bh):
        for y in sorted(os.listdir(bh)):
            p = os.path.join(bh, y)
            if os.path.isdir(p):
                c = len([x for x in os.listdir(p) if x.endswith(".csv")])
                years[y] = c
                n += c
    print(f"  Daily price files: {n}")
    for y, c in sorted(years.items()):
        edge = y in (str(START.year), str(END.year))
        ok = edge or (200 <= c <= 260)
        print(f"     {y}: {c} days" + ("" if ok else "    <-- looks short"))
        if not ok:
            problems.append(f"{y} has only {c} trading days")

    # The two below are OPTIONAL. NSE blocks them from some connections, and
    # neither is needed for the backtest itself - so a miss is reported as
    # information, never as a problem worth re-running for.
    skipped = os.path.join(out, "master", "issued_size_SKIPPED.txt")
    iss = os.path.join(out, "master", "issued_size.csv")
    if os.path.exists(skipped):
        print("  Share counts: not available (NSE refused) - fine, not needed")
    elif os.path.exists(iss):
        rs = list(csv.DictReader(open(iss, encoding="utf8")))
        filled = sum(1 for r in rs if r.get("issued_size"))
        print(f"  Share counts: {filled} of {len(rs)} companies")
    else:
        print("  Share counts: none - fine, not needed")

    ca = os.path.join(out, "corpactions", "corpactions.csv")
    if os.path.exists(ca):
        cnt = sum(1 for _ in open(ca, encoding="utf8")) - 1
        print(f"  Splits and bonuses: {cnt} events"
              + ("   (none found - Claude will detect them from prices)"
                 if cnt <= 0 else ""))
    else:
        print("  Splits and bonuses: none (Claude will detect them from prices)")

    # the reference stock must be present
    p = os.path.join(bh, "2026", "sec_20260827.csv")
    hit = None
    if os.path.exists(p):
        for line in open(p, encoding="utf8", errors="replace"):
            if line.startswith("STLTECH,"):
                hit = line.strip()
                break
    if hit:
        print(f"  STLTECH on 27-Aug-2026 found:\n     {hit[:150]}")
    else:
        problems.append("STLTECH 27-Aug-2026 not found")

    print()
    if not problems:
        print("  " + "=" * 56)
        print("  EVERYTHING LOOKS GOOD.")
        print("  " + "=" * 56)
    else:
        print("  " + "=" * 56)
        print("  SOME THINGS NEED A SECOND RUN:")
        for x in problems:
            print("    - " + x)
        print()
        print("  Just double-click this file again - it will fill the gaps.")
        print("  If it says the same thing twice, screenshot this for Claude.")
        print("  " + "=" * 56)
    return len(problems)


# ── main ───────────────────────────────────────────────────────────────────
def banner(txt):
    print("\n" + "-" * 60)
    print("  " + txt)
    print("-" * 60)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "nse_data")
    os.makedirs(out, exist_ok=True)
    start_log(out)

    print("=" * 60)
    print("  NSE DATA DOWNLOAD")
    print("=" * 60)
    print("""
  This downloads the daily prices for every stock on the NSE,
  which is what the call engine reads each evening.

  About 10 minutes the first time. After that it only fetches
  the days it does not already have, which takes seconds.

  You can close this window whenever you like. Running it again
  picks up exactly where it stopped - nothing is lost and
  nothing is downloaded twice.
""")
    print("=" * 60)

    ensure_deps()
    t0 = time.time()
    print("\n  Connecting to NSE ...")
    s = new_session()

    # ORDER MATTERS. The ten years of prices is the only thing that cannot be
    # obtained any other way, and it is the only part every later step depends
    # on. It runs SECOND, before the optional extras, so that an evening spent
    # downloading is never wasted on the parts NSE may refuse.
    banner("STEP 1 of 4   Company list")
    symbols = stage_master(out, s)
    if not symbols:
        hold()
        return
    banner("STEP 2 of 4   Ten years of daily prices  (the important one)")
    stage_bhav(out, s)
    banner("STEP 3 of 4   Extras  (skipped automatically if NSE refuses)")
    stage_issued_size(out, s, symbols)
    stage_corpactions(out, s)
    banner("STEP 4 of 4   Checking everything arrived")
    bad = stage_verify(out)

    print(f"\n  Finished in {(time.time() - t0) / 60:.0f} minutes.")
    print(f"  Everything is in:  {out}")
    if not bad:
        print("""
  NEXT STEP
     In the Claude desktop app, click "Add folder" and choose
     the folder this file is sitting in. Then tell Claude
     it is connected.
""")
    hold()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Stopped. Nothing lost - double-click this file again "
              "to carry on.\n")
        hold()
    except Exception as e:
        print(f"\n\n  Something went wrong: {type(e).__name__}: {e}")
        print("  Screenshot this window and send it to Claude.\n")
        hold()
