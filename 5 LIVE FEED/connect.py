#!/usr/bin/env python3
"""
CONNECT TO ZERODHA - double click me.py

Run this ONCE to save your keys, then ONCE EVERY MORNING before the market
opens. It takes about twenty seconds.

WHY EVERY MORNING
    Zerodha's access token expires at the end of each day. That is their rule,
    not a limitation of this program, and there is no way around it. Every
    system built on Kite - including your friend's - does this same login.

WHAT IT DOES
    1. asks for your API key and secret, the first time only
    2. opens the Zerodha login page in your browser
    3. you log in as normal
    4. you paste back the address it lands on
    5. it saves today's access token and shows you a live price to prove it works

YOUR SECRET NEVER LEAVES THIS COMPUTER
    Both keys are saved to kite_keys.txt in this folder and read from there.
    They are never sent anywhere except to Zerodha itself. Do not paste them
    into a chat, an email, or a website - anyone holding your secret can act
    on your trading account.
"""

import json
import os
import subprocess
import sys
import webbrowser
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = os.path.join(HERE, "kite_keys.txt")
TOKEN = os.path.join(HERE, "kite_token.json")

KiteConnect = None


def hold(code=0):
    try:
        input("\n  Press Enter to close this window. ")
    except Exception:
        pass
    sys.exit(code)


def ensure_deps():
    global KiteConnect
    try:
        from kiteconnect import KiteConnect as K
    except ImportError:
        print("  First run: installing Zerodha's connection library.")
        print("  (Yellow WARNING lines below are harmless.)\n")
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
            from kiteconnect import KiteConnect as K
        except ImportError:
            print("\n  " + "=" * 56)
            print("  Installed, but this window cannot see it yet.")
            print("  This is normal. Close this window and double-click the")
            print("  file again - it will work the second time.")
            print("  " + "=" * 56)
            hold(1)
    KiteConnect = K


def read_keys():
    if not os.path.exists(KEYS):
        return None, None
    api_key = api_secret = None
    for line in open(KEYS, encoding="utf8"):
        line = line.strip()
        if line.startswith("api_key"):
            api_key = line.split("=", 1)[-1].strip()
        elif line.startswith("api_secret"):
            api_secret = line.split("=", 1)[-1].strip()
    return api_key or None, api_secret or None


def ask_keys():
    print("""
  ------------------------------------------------------------
   FIRST TIME SETUP
  ------------------------------------------------------------

   On the Kite Connect site, open "My apps" and click your app.
   It shows an API key, and a secret you have to click to reveal.

   Paste them below. They are saved on this computer only.
""")
    api_key = input("   API key    : ").strip()
    api_secret = input("   API secret : ").strip()
    if not api_key or not api_secret:
        print("\n   Both are needed. Nothing saved.")
        hold(1)
    with open(KEYS, "w", encoding="utf8") as f:
        f.write("# Zerodha Kite Connect keys. KEEP THIS FILE PRIVATE.\n")
        f.write("# Anyone with the secret can act on your trading account.\n")
        f.write(f"api_key = {api_key}\napi_secret = {api_secret}\n")
    print(f"\n   Saved to {os.path.basename(KEYS)}")
    return api_key, api_secret


def token_is_fresh():
    """Kite tokens die at end of day, so anything not dated today is stale."""
    if not os.path.exists(TOKEN):
        return None
    try:
        d = json.load(open(TOKEN))
        if d.get("date") == date.today().isoformat():
            return d
    except Exception:
        pass
    return None


def extract_request_token(pasted):
    """Accept the whole redirected URL, or just the token on its own."""
    s = pasted.strip().strip('"').strip("'")
    if "request_token=" in s:
        s = s.split("request_token=", 1)[1]
        for stop in ("&", "#", " "):
            if stop in s:
                s = s.split(stop, 1)[0]
    return s.strip()


def main():
    print("=" * 60)
    print("  CONNECT TO ZERODHA")
    print("=" * 60)
    ensure_deps()

    api_key, api_secret = read_keys()
    if not api_key or not api_secret:
        api_key, api_secret = ask_keys()

    have = token_is_fresh()
    if have:
        print(f"""
  Already connected for today ({have['date']}).
  Logged in as {have.get('user_name','')}.

  You do not need to do this again until tomorrow.
""")
        hold()

    kite = KiteConnect(api_key=api_key)
    url = kite.login_url()

    print(f"""
  ------------------------------------------------------------
   STEP 1   Log in
  ------------------------------------------------------------

   Your browser is opening the Zerodha login page now.
   Log in as you normally would.

   If it does not open, copy this address into your browser:

   {url}

  ------------------------------------------------------------
   STEP 2   Copy the address it lands on
  ------------------------------------------------------------

   After logging in, the browser jumps to a page that FAILS TO
   LOAD. That is expected and correct - nothing has gone wrong.

   Look at the address bar. It will read something like:

      https://127.0.0.1/?request_token=AbCdEf123456&action=login

   Copy that WHOLE address and paste it below.
""")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        pasted = input("   Paste the address here: ").strip()
    except Exception:
        pasted = ""
    req = extract_request_token(pasted)
    if not req:
        print("\n   Could not find a token in that. Make sure you copied the")
        print("   whole address from the bar, including request_token=...")
        hold(1)

    print("\n   Connecting ...")
    try:
        data = kite.generate_session(req, api_secret=api_secret)
    except Exception as e:
        msg = str(e)
        print(f"\n   Zerodha refused: {msg}\n")
        if "checksum" in msg.lower() or "api_secret" in msg.lower():
            print("   That usually means the API SECRET is wrong.")
            print(f"   Delete {os.path.basename(KEYS)} and run this again to re-enter it.")
        elif "token" in msg.lower():
            print("   That token was already used or has expired. They are")
            print("   single-use and short-lived - just run this again.")
        else:
            print("   Screenshot this window and send it to Claude.")
        hold(1)

    kite.set_access_token(data["access_token"])
    rec = {"date": date.today().isoformat(),
           "saved_at": datetime.now().strftime("%H:%M:%S"),
           "access_token": data["access_token"],
           "user_name": data.get("user_name", ""),
           "user_id": data.get("user_id", "")}
    with open(TOKEN, "w", encoding="utf8") as f:
        json.dump(rec, f, indent=1)

    print(f"""
  ------------------------------------------------------------
   CONNECTED.  Logged in as {rec['user_name']}
  ------------------------------------------------------------
""")

    # Prove the feed actually works, rather than just claiming it does.
    try:
        q = kite.quote(["NSE:RELIANCE", "NSE:INFY", "NSE:TCS"])
        print("   Live prices, fetched just now:\n")
        for k, v in q.items():
            name = k.split(":")[1]
            ltp = v.get("last_price")
            ch = v.get("net_change")
            prev = v.get("ohlc", {}).get("close")
            pct = (100 * ch / prev) if (ch is not None and prev) else None
            print(f"      {name:<10} {ltp:>10,.2f}"
                  + (f"   {pct:+.2f}%" if pct is not None else ""))
        print("""
   That is your feed working. Prices from Zerodha, on this
   machine, seconds old.

   Outside market hours these will be the last traded prices,
   which is normal.
""")
    except Exception as e:
        print(f"   Connected, but the test price fetch failed: {e}")
        print("   Send this to Claude - the login worked, so it is something small.")

    print("""   NEXT
      Tell Claude you are connected. Nothing else to do.
      Run this file again tomorrow morning before 9:15.
""")
    hold()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Stopped. Nothing saved.\n")
        hold(1)
    except Exception as e:
        print(f"\n  Something went wrong: {type(e).__name__}: {e}")
        print("  Screenshot this window and send it to Claude.\n")
        hold(1)
