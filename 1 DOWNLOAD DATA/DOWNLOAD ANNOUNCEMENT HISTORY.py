"""ASCENDRA - ANNOUNCEMENT HISTORY, SO THE NEWS TAB CAN BE TESTED

The daily downloader gave us this week: 3,357 filings across 79 types. That is
enough to SHOW announcements on the desk, and nowhere near enough to know
which of the 79 types actually move a stock - our price history runs from
September 2023, and the announcements we hold start on the 18th of this month.
There is no overlap to measure.

This walks backwards a month at a time and collects the same filings for the
last two years, so each type has hundreds of examples instead of three. Then
the question "which announcement is worth flashing" becomes arithmetic rather
than opinion: for every type, how far did the stock move the next day compared
with its own ordinary day.

It is slow and deliberately polite to NSE - one month every few seconds, about
four minutes for two years. Leave it running.

Writes DATA\\announcement_history.json. Nothing else is touched.
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "DATA")
OUT = os.path.join(DATA, "announcement_history.json")
MONTHS_BACK = 24

try:
    import requests
except ImportError:
    print("\n  The 'requests' library is missing. In a command window run:")
    print("      pip install requests\n")
    sys.exit(1)

HEAD = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
}
KEEP = ("symbol", "desc", "an_dt", "sort_date", "sm_name", "smIndustry",
        "attchmntText")


def session():
    s = requests.Session()
    s.headers.update(HEAD)
    for url in ("https://www.nseindia.com",
                "https://www.nseindia.com/companies-listing/corporate-filings-announcements"):
        try:
            s.get(url, timeout=15)
            time.sleep(1.0)
        except Exception as e:
            print(f"  could not open {url}: {e}")
    return s


def month_edges(n):
    """(from, to) for each of the last n months, newest first."""
    out = []
    end = date.today()
    for _ in range(n):
        start = (end.replace(day=1) - timedelta(days=1)).replace(day=1) \
            if end.day == 1 else end.replace(day=1)
        out.append((start, end))
        end = start - timedelta(days=1)
    return out


def main():
    print(f"""
  ASCENDRA - ANNOUNCEMENT HISTORY

  Collecting {MONTHS_BACK} months of NSE filings, one month at a time.
  This takes a few minutes. Leave the window open.
""")
    if not os.path.isdir(DATA):
        print(f"  Cannot find the DATA folder at:\n    {DATA}\n")
        return
    s = session()

    have = {}
    if os.path.exists(OUT):
        try:
            have = json.load(open(OUT, encoding="utf8")).get("rows") or {}
        except Exception:
            have = {}

    added = 0
    failed = 0
    for frm, to in month_edges(MONTHS_BACK):
        url = ("https://www.nseindia.com/api/corporate-announcements"
               f"?index=equities&from_date={frm.strftime('%d-%m-%Y')}"
               f"&to_date={to.strftime('%d-%m-%Y')}")
        try:
            r = s.get(url, timeout=40)
            rows = r.json() if r.status_code == 200 else None
            rows = rows if isinstance(rows, list) else (rows or {}).get("data")
        except Exception as e:
            print(f"  {frm:%b %Y}: {e}")
            rows = None
        if not rows:
            failed += 1
            print(f"  {frm:%b %Y}: nothing came back")
            if failed >= 4:
                print("\n  NSE has stopped answering. Close this, wait ten "
                      "minutes and run it again - what is already collected "
                      "is saved and it will carry on from there.\n")
                break
            time.sleep(6)
            continue
        failed = 0
        n0 = len(have)
        for a in rows:
            key = f"{a.get('symbol')}|{a.get('an_dt')}|{a.get('desc')}"
            if key not in have:
                have[key] = {k: a.get(k) for k in KEEP}
        added += len(have) - n0
        print(f"  {frm:%b %Y}: {len(rows)} filings, {len(have)-n0} new "
              f"({len(have)} held)")
        json.dump({"saved": datetime.now().isoformat(timespec="seconds"),
                   "rows": have}, open(OUT, "w", encoding="utf8"))
        time.sleep(2.5)

    print(f"\n  {len(have)} filings held in DATA\\announcement_history.json "
          f"({added} new this run).\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n  stopped: {e}\n")
    input("  press Enter to close ")
