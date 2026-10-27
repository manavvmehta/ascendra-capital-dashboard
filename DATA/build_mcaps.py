#!/usr/bin/env python3
"""
build_mcaps.py — turn any market-cap list into the file the engine reads.

DOUBLE-CLICK THIS after you have put a market-cap file in the DATA folder.

WHAT IT ACCEPTS
    Anything with a company and a market cap in it:
      - AMFI's official "Average Market Capitalisation" workbook  (.xlsx)
      - a Screener export                                          (.xlsx/.csv)
      - Jenil's .fundamentals.json
      - a two-column spreadsheet you typed yourself
    It works out which columns are which rather than demanding a fixed layout.

WHAT IT PRODUCES
    DATA/fundamentals.json   — {"stocks":[{"name":"RELIANCE","mcap":193200}, ...]}
    market caps in CRORES, keyed by NSE trading symbol.

THE HARD PART, AND WHY THE REPORT MATTERS
    Some sources list COMPANY NAMES ("Reliance Industries Ltd") and the engine
    needs TRADING SYMBOLS ("RELIANCE"). This matches them using NSE's own
    company list. Matching is never perfect, so it prints exactly how many
    names it could not place — a silent 60% match rate would quietly shrink
    your universe and look like the filter working.
"""

import csv
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def project_root():
    d = HERE
    for _ in range(4):
        if os.path.isdir(os.path.join(d, "WEBSITE")):
            return d
        p = os.path.dirname(d)
        if p == d:
            break
        d = p
    return HERE


ROOT = project_root()
DATA = os.path.join(ROOT, "DATA")
OUT = os.path.join(DATA, "fundamentals.json")
EQUITY_L = os.path.join(ROOT, "1 DOWNLOAD DATA", "nse_data", "master", "EQUITY_L.csv")


def hold(code=0):
    try:
        input("\n  Press Enter to close this window. ")
    except Exception:
        pass
    sys.exit(code)


def ensure(pkg, imp=None):
    try:
        __import__(imp or pkg)
        return True
    except ImportError:
        pass
    print(f"  Installing {pkg} (one time) ...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                    "--disable-pip-version-check", "--no-warn-script-location", pkg],
                   check=False)
    try:
        import importlib, site
        try:
            site.main()
        except Exception:
            pass
        importlib.invalidate_caches()
        __import__(imp or pkg)
        return True
    except ImportError:
        return False


# ── name matching ──────────────────────────────────────────────────────────
NOISE = re.compile(r"\b(LIMITED|LTD|PVT|PRIVATE|COMPANY|CORPORATION|CORP|INC|"
                   r"THE|AND)\b")


def norm(s):
    """Squash a company name to something two sources can agree on."""
    s = str(s or "").upper()
    s = s.replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = NOISE.sub(" ", s)
    return re.sub(r"\s+", "", s)


def load_nse_names():
    """symbol -> normalised company name, and the reverse, from NSE's own list."""
    if not os.path.exists(EQUITY_L):
        return {}, {}
    by_norm, symbols = {}, set()
    try:
        for r in csv.DictReader(open(EQUITY_L, encoding="utf8")):
            r = {k.strip(): (v or "").strip() for k, v in r.items()}
            sym = r.get("SYMBOL")
            nm = r.get("NAME OF COMPANY")
            if not sym:
                continue
            symbols.add(sym.upper())
            if nm:
                by_norm.setdefault(norm(nm), sym.upper())
    except Exception as e:
        print(f"  (could not read NSE company list: {e})")
    return by_norm, symbols


# ── reading whatever they gave us ──────────────────────────────────────────
NAMEISH = ("symbol", "nse code", "nsecode", "nse", "ticker", "scrip",
           "company name", "company", "name", "security")
CAPISH = ("average market cap", "avg market cap", "market capitalization",
          "market capitalisation", "market cap", "mcap", "cap")


def pick(headers, wanted):
    """Choose a column by header, most specific match first."""
    low = [(h or "").strip().lower() for h in headers]
    for w in wanted:
        for i, h in enumerate(low):
            if h == w:
                return i
    for w in wanted:
        for i, h in enumerate(low):
            if w in h:
                return i
    return None


def rows_from_csv(path):
    with open(path, encoding="utf8", errors="replace", newline="") as f:
        return [r for r in csv.reader(f) if any(x.strip() for x in r)]


def rows_from_xlsx(path):
    if not ensure("openpyxl"):
        print("  Could not install the spreadsheet reader.")
        return []
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        for r in ws.iter_rows(values_only=True):
            if r and any(c is not None and str(c).strip() for c in r):
                out.append(["" if c is None else c for c in r])
        if out:
            break
    return out


def rows_from_json(path):
    d = json.load(open(path, encoding="utf8"))
    out = [["name", "mcap"]]
    if isinstance(d, dict) and isinstance(d.get("stocks"), list):
        for s in d["stocks"]:
            out.append([s.get("name"), s.get("mcap")])
    elif isinstance(d, dict):
        for k, v in d.items():
            out.append([k, v])
    return out


def find_header(rows):
    """AMFI-style sheets carry title rows before the real header. Find the row
    that actually names a company column AND a market-cap column."""
    for i, r in enumerate(rows[:25]):
        hs = [str(x) for x in r]
        if pick(hs, NAMEISH) is not None and pick(hs, CAPISH) is not None:
            return i
    return None


def to_number(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except Exception:
        return None


def main():
    print("=" * 62)
    print("  BUILD MARKET CAPS")
    print("=" * 62)
    os.makedirs(DATA, exist_ok=True)

    cands = []
    for folder in (DATA, ROOT):
        if not os.path.isdir(folder):
            continue
        for fn in sorted(os.listdir(folder)):
            if fn == "fundamentals.json":
                continue
            if fn.lower().endswith((".xlsx", ".xls", ".csv", ".json")):
                cands.append(os.path.join(folder, fn))
    if not cands:
        print(f"""
  NO SOURCE FILE FOUND.

  Put a market-cap file in this folder:
      {DATA}

  Anything with company names and market caps works:
    - AMFI's "Average Market Capitalisation of listed companies"
      (search that phrase - it is a free public spreadsheet, and it is
      the official list SEBI's large/mid/small classification uses)
    - a Screener export
    - Jenil's .fundamentals.json
    - a spreadsheet you made yourself, two columns

  Then run this again.
""")
        hold(1)

    by_norm, valid_syms = load_nse_names()
    if not valid_syms:
        print("  NOTE: NSE company list not found, so names cannot be matched")
        print("        to symbols. Only files that already have NSE symbols")
        print("        will work. Run the data download first to fix this.\n")

    best = None
    for path in cands:
        fn = os.path.basename(path)
        try:
            if fn.lower().endswith(".json"):
                rows = rows_from_json(path)
            elif fn.lower().endswith(".csv"):
                rows = rows_from_csv(path)
            else:
                rows = rows_from_xlsx(path)
        except Exception as e:
            print(f"  {fn}: could not read ({e})")
            continue
        if not rows:
            continue
        hi = find_header(rows)
        if hi is None:
            print(f"  {fn}: no company + market-cap columns found, skipping")
            continue

        hdr = [str(x) for x in rows[hi]]
        ci = pick(hdr, NAMEISH)
        vi = pick(hdr, CAPISH)
        print(f"\n  Reading {fn}")
        print(f"     company column : {hdr[ci]!r}")
        print(f"     market cap     : {hdr[vi]!r}")

        got, unmatched, skipped = {}, [], 0
        for r in rows[hi + 1:]:
            if ci >= len(r) or vi >= len(r):
                continue
            raw_name = str(r[ci]).strip()
            val = to_number(r[vi])
            if not raw_name or val is None or val <= 0:
                skipped += 1
                continue
            up = raw_name.upper()
            if up in valid_syms:                 # already a trading symbol
                got[up] = val
                continue
            sym = by_norm.get(norm(raw_name))    # a company name to translate
            if sym:
                got[sym] = val
            elif not valid_syms:
                got[up] = val                    # nothing to check against
            else:
                unmatched.append(raw_name)

        if not got:
            print("     nothing usable in it")
            continue

        # Units, decided from the LARGEST value, not the median.
        # India's biggest company is about Rs 20 lakh crore. So:
        #   in crores that figure is ~2e6  - it can never exceed ~1e7
        #   in lakhs                ~2e8
        #   in rupees               ~2e13
        # The bands do not overlap, so the maximum settles it. A median-based
        # guess got this wrong on a large-cap-only list and shrank every
        # company by a factor of 100,000 without anything looking broken.
        top = max(got.values())
        if top > 1e10:
            got = {k: v / 1e7 for k, v in got.items()}
            unit = "rupees -> crores"
        elif top > 1e7:
            got = {k: v / 1e5 for k, v in got.items()}
            unit = "lakhs -> crores"
        else:
            unit = "already crores"

        print(f"     matched {len(got)} symbols   ({unit})")
        if unmatched:
            print(f"     could NOT place {len(unmatched)} names, e.g. "
                  f"{', '.join(unmatched[:3])}")
        if skipped:
            print(f"     ignored {skipped} rows with no usable figure")

        if best is None or len(got) > len(best[1]):
            best = (fn, got, unmatched)

    if not best:
        print("\n  None of those files had usable market caps.")
        hold(1)

    fn, caps, unmatched = best
    with open(OUT, "w", encoding="utf8") as f:
        json.dump({"source": fn,
                   "stocks": [{"name": k, "mcap": round(v, 2)}
                              for k, v in sorted(caps.items())]}, f, indent=1)

    above = sum(1 for v in caps.values() if v >= 500)
    print(f"""
  ------------------------------------------------------------
   DONE.  {len(caps)} companies written to DATA\\fundamentals.json
  ------------------------------------------------------------

   {above} of them are above Rs 500 crore.
   {len(caps)-above} fall below and will not be scanned.
""")
    if valid_syms:
        cover = 100 * len(caps) / max(1, len(valid_syms))
        print(f"   That covers {cover:.0f}% of NSE's {len(valid_syms)} listed symbols.")
        if cover < 60:
            print(f"""
   HEADS UP: under 60% coverage. Every symbol WITHOUT a market cap
   is excluded from scanning, so a thin file quietly shrinks your
   universe. If that number looks low, a better source is worth
   finding before trusting the filter.
""")
    print("   Next: restart the desk and the filter switches on.\n")
    hold()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n  Something went wrong: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        print("\n  Screenshot this window and send it to Claude.\n")
        hold(1)
