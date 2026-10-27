"""ASCENDRA - COMPANY ANNOUNCEMENTS FROM NSE

MM asked for a News tab that flashes the announcements that actually move a
stock. This is step one: getting the announcements onto this machine.

It cannot be written from my side and trusted - NSE is not reachable from
where I work, so the exact shape of what comes back has to be seen once on
your machine before the tab is built against it. Run this, and it will tell
you plainly what it managed to get.

WHAT IT DOES
  Asks NSE for today's corporate announcements and the board-meeting and
  results calendar, and writes them to DATA\\announcements.json. Run it any
  time; it keeps the last 30 days and adds to them rather than replacing.

WHAT IT DOES NOT DO
  It does not decide which announcements matter. That comes later, by
  measuring how far each TYPE of announcement actually moved its stock the
  next day, against that stock's ordinary day. Guessing which ones matter is
  how a news feed becomes noise.

Double-click DOWNLOAD ANNOUNCEMENTS.bat. Nothing on the desk changes.
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "DATA")
OUT = os.path.join(DATA, "announcements.json")
KEEP_DAYS = 30

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


def session():
    """NSE hands out a cookie on the home page and refuses the API without it."""
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


def grab(s, url, label):
    try:
        r = s.get(url, timeout=25)
        if r.status_code != 200:
            print(f"  {label}: NSE answered {r.status_code}")
            return None
        return r.json()
    except Exception as e:
        print(f"  {label}: {e}")
        return None


def main():
    print("""
  ASCENDRA - COMPANY ANNOUNCEMENTS

  Asking NSE for today's filings. This takes a few seconds.
""")
    if not os.path.isdir(DATA):
        print(f"  Cannot find the DATA folder at:\n    {DATA}\n")
        return
    s = session()

    frm = (date.today() - timedelta(days=6)).strftime("%d-%m-%Y")
    to = date.today().strftime("%d-%m-%Y")

    jobs = [
        ("announcements",
         f"https://www.nseindia.com/api/corporate-announcements?index=equities&from_date={frm}&to_date={to}"),
        ("board meetings",
         f"https://www.nseindia.com/api/corporate-board-meetings?index=equities&from_date={frm}&to_date={to}"),
        ("results calendar",
         "https://www.nseindia.com/api/event-calendar?index=equities"),
    ]

    got = {}
    for label, url in jobs:
        j = grab(s, url, label)
        rows = j if isinstance(j, list) else (j or {}).get("data") or []
        got[label] = rows
        print(f"  {label}: {len(rows)} rows")
        time.sleep(1.2)

    if not any(got.values()):
        print("""
  Nothing came back. That is usually one of three things:
    - no internet on this machine right now
    - NSE is blocking the request (it does this sometimes; try again in a
      few minutes)
    - your office or ISP blocks nseindia.com

  Tell me what this window printed and I will work from that rather than
  guess.
""")
        return

    # what each row looks like - printed once so the tab can be built against
    # the real shape rather than an assumed one
    for label, rows in got.items():
        if rows:
            print(f"\n  a sample {label} row:")
            for k, v in list(rows[0].items())[:12]:
                v = str(v)
                print(f"      {k}: {v[:70]}")

    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf8"))
        except Exception:
            old = {}
    days = old.get("days") or {}
    days[date.today().isoformat()] = got
    cut = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    days = {d: v for d, v in days.items() if d >= cut}

    json.dump({"saved": datetime.now().isoformat(timespec="seconds"),
               "days": days}, open(OUT, "w", encoding="utf8"))
    total = sum(len(v) for v in got.values())
    print(f"\n  Saved {total} rows to DATA\\announcements.json "
          f"({len(days)} days kept).\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n  stopped: {e}\n")
    input("  press Enter to close ")
