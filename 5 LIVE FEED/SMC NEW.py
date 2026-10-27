"""ASCENDRA - SMC, THE NEW BOOK.

One setup, two books. Built 22/10/2026 after three weeks of getting it wrong,
and every one of those mistakes is named in the comments so none of them comes
back. The connection, the price feed, the record and the dashboard writing are
taken unchanged from SMC TWO BOOKS.py because those parts were already right;
only the rules are new.
"""

import json
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = 5057
SMC_PIVOT = 3
KEEP_LEVELS = 8
SLOT = 200000.0                     # money per position, for the record only
COST_CASH = 0.059                   # intraday round trip, per cent
COST_FUT = 0.062                    # stock future round trip, per cent
FAST_EVERY = 15 * 60                # the 15-minute book
SLOW_EVERY = 60 * 60                # the daily book
PRICE_EVERY = 60                    # the live price on the calls already written


# ── where everything lives ────────────────────────────────────────────────
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
LOGF = os.path.join(DATA, "smc2_log.json")
SITE = os.path.join(HERE, "WEBSITE", "ascendra_desk.html")
DAILYF = os.path.join(DATA, "smc2_daily.json")


def hold(code=0):
    try:
        input("\n  Press Enter to close this window. ")
    except Exception:
        pass
    sys.exit(code)


def ensure_kiteconnect():
    try:
        import kiteconnect  # noqa
        return
    except ImportError:
        pass
    print("  The Zerodha library is not installed for this Python.")
    print("  Installing it now - about twenty seconds...\n")
    import subprocess
    for args in (["-m", "pip", "install", "--quiet", "kiteconnect"],
                 ["-m", "pip", "install", "--quiet", "--user", "kiteconnect"]):
        try:
            subprocess.run([sys.executable] + args, check=True)
        except Exception:
            continue
        try:
            import importlib
            importlib.invalidate_caches()
            import kiteconnect  # noqa
            print("  Installed.\n")
            return
        except ImportError:
            continue
    print("\n  COULD NOT INSTALL IT. Open a Command Prompt and run:\n"
          f"      {sys.executable} -m pip install kiteconnect\n")
    hold(1)


def load_kite():
    ensure_kiteconnect()
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
        print(f"\n  TODAY'S LOGIN IS MISSING (the saved token is from "
              f"{tok.get('date')}).\n  Run connect.py in '5 LIVE FEED', then "
              f"start this again.\n")
        hold(1)
    from kiteconnect import KiteConnect
    k = KiteConnect(api_key=api_key)
    k.set_access_token(tok["access_token"])
    k.quote(["NSE:RELIANCE"])
    return k


# ── the sweep, exactly as the desk defines it ─────────────────────────────

# ── the rules, as MM wrote them and as they were tested ───────────────────
#
# This is the whole strategy. It is deliberately short and deliberately dull,
# because every version of it that was clever turned out to be wrong.
#
# The long road here, so nobody repeats it:
#
#   * a swing used to mean one candle with one lower candle either side. On a
#     chart with two candles a day that makes almost every candle a swing, so
#     "structure broke" fired on noise. MM: "are you taking major high's and
#     low's? if not then take that." Three candles either side now.
#   * structure used to be read on a 4-hour chart that was built by counting
#     sixteen 15-minute candles from the start of the file, so one "4-hour"
#     candle could run from Monday afternoon to Wednesday morning. Tested on
#     honest candles that version made Rs 12 a trade. Structure is read on the
#     DAILY chart now, where a candle is a day and cannot be grouped wrongly.
#   * the level being swept used to be only last week's low. His note also
#     lists liquidity pools - equal lows - and those are the commonest thing
#     to get swept. Adding them tripled the number of trades.
#   * the order block used to be whatever sat behind the sweep candle. His
#     note says price sweeps the low AND THEN enters a demand block, which is
#     two events. Two events now.
#
# Confirmed over three years and 1,281 stocks, both halves separately.

MAJOR_SIDE = 10         # major swing: ten confirmed candles on each side
EMA_N = 50
RSI_N = 14
MFI_N = 14
CONF_WITHIN = 10
POOL_TOL = 0.0025
POOL_BACK = 120
HOLD_DAYS = 10         # how far back to look for pools


def swings_major(b, side=MAJOR_SIDE):
    """A high needs `side` lower candles on BOTH sides. Same for a low."""
    out = []
    for i in range(side, len(b) - side):
        hi, lo = b[i]["high"], b[i]["low"]
        if all(b[j]["high"] < hi for j in range(i - side, i)) and \
           all(b[j]["high"] < hi for j in range(i + 1, i + 1 + side)):
            out.append((i, "H", hi))
        if all(b[j]["low"] > lo for j in range(i - side, i)) and \
           all(b[j]["low"] > lo for j in range(i + 1, i + 1 + side)):
            out.append((i, "L", lo))
    out.sort()
    return out


def structure(b, side=MAJOR_SIDE):
    """Trend, and whether a break continues it (BOS) or reverses it (CHoCH).

    A swing is only published `side` candles after it prints, because that is
    when its right-hand shoulder exists. Nothing here can see the future.
    """
    by_confirm = {}
    for idx, kind, px in swings_major(b, side):
        by_confirm.setdefault(idx + side, []).append((idx, kind, px))
    H, L, state = [], [], []
    trend = 0
    broken_h, broken_l = set(), set()
    for i in range(len(b)):
        for idx, kind, px in by_confirm.get(i, []):
            (H if kind == "H" else L).append((idx, px))
        ev = lvl = None
        c = b[i]["close"]
        if H and H[-1][0] not in broken_h and c > H[-1][1]:
            ev = "CHoCH" if trend < 0 else "BOS"
            lvl = H[-1][1]; broken_h.add(H[-1][0]); trend = 1
        elif L and L[-1][0] not in broken_l and c < L[-1][1]:
            ev = "CHoCH" if trend > 0 else "BOS"
            lvl = L[-1][1]; broken_l.add(L[-1][0]); trend = -1
        state.append({"trend": trend, "event": ev, "level": lvl})
    return state


def bias(b, side=MAJOR_SIDE):
    """+1 when the chart is making higher highs AND higher lows."""
    by_confirm = {}
    for idx, kind, px in swings_major(b, side):
        by_confirm.setdefault(idx + side, []).append((kind, px))
    H, L, out = [], [], []
    for i in range(len(b)):
        for kind, px in by_confirm.get(i, []):
            (H if kind == "H" else L).append(px)
        v = 0
        if len(H) >= 2 and len(L) >= 2:
            if H[-1] > H[-2] and L[-1] > L[-2]:
                v = 1
            elif H[-1] < H[-2] and L[-1] < L[-2]:
                v = -1
        out.append(v)
    return out


def to_weekly(daily):
    out, cur, wk = [], [], None
    for b in daily:
        y, w, _ = date.fromisoformat(str(b["date"])[:10]).isocalendar()
        if (y, w) != wk:
            if cur:
                out.append({"date": cur[0]["date"],
                            "open": cur[0]["open"],
                            "high": max(x["high"] for x in cur),
                            "low": min(x["low"] for x in cur),
                            "close": cur[-1]["close"]})
            cur, wk = [], (y, w)
        cur.append(b)
    if cur:
        out.append({"date": cur[0]["date"], "open": cur[0]["open"],
                    "high": max(x["high"] for x in cur),
                    "low": min(x["low"] for x in cur),
                    "close": cur[-1]["close"]})
    return out


def pools(d, i, wk_lo):
    """Every level a sweep could be reaching for: last week's low, and any
    cluster of equal lows. These are where the stops are."""
    out = [wk_lo]
    sw = [s for s in swings_major(d[:i]) if s[1] == "L" and s[0] >= i - POOL_BACK]
    vals = [p for _, _, p in sw]
    for a in range(len(vals)):
        for b in range(a + 1, len(vals)):
            if abs(vals[a] - vals[b]) / max(vals[b], 1e-9) <= POOL_TOL:
                out.append(min(vals[a], vals[b]))
                break
    return sorted(set(round(x, 2) for x in out))


def swept(bar, level):
    """Took the stops and came back: the low went under, the close did not."""
    return bar["low"] < level <= bar["close"]


def demand_blocks(d, upto, back=60):
    """Blocks that already existed at `upto` - a down candle immediately
    followed by an up candle that closes above its high."""
    out = []
    for j in range(max(1, upto - back), upto):
        if d[j]["close"] < d[j]["open"] and d[j + 1]["close"] > d[j + 1]["open"] \
           and d[j + 1]["close"] > d[j]["high"]:
            out.append((j, d[j]["low"], d[j]["high"]))
    return out


def ema_series(d, n=EMA_N):
    out, e, k = [], d[0]["close"], 2.0 / (n + 1)
    for b in d:
        out.append(b["close"] > e)
        e = (b["close"] - e) * k + e
    return out


# ── the two books ─────────────────────────────────────────────────────────
#
# One setup, two ways out. MM asked for exactly this: "one with all the rules
# and regulations i told you about, it has a good success ratio. one is with
# smaller targets but with full conviction."
BOOKS = {
    "LONG TERM": {"chart": "daily structure, hourly entry",
                   "target": "next-liquidity", "hold_days": HOLD_DAYS,
                   "cost": COST_FUT,
                   "note": "fresh SMC swing book: major swings, sweep, CHoCH, FVG midpoint, SMA + RSI + MFI"},
}


def sma_values(values, n):
    out=[]
    for i in range(len(values)):
        w=values[max(0,i-n+1):i+1]
        out.append(sum(w)/len(w))
    return out


def rsi_values(values, n=RSI_N):
    out=[50.0]*len(values); gain=loss=0.0
    for i in range(1,len(values)):
        g=max(0.0, values[i]-values[i-1]); l=max(0.0, values[i-1]-values[i])
        gain=(gain*(n-1)+g)/n; loss=(loss*(n-1)+l)/n
        out[i]=100.0 if loss==0 else 100.0-100.0/(1.0+gain/loss)
    return out


def mfi_values(d, n=MFI_N):
    tp=[(x['high']+x['low']+x['close'])/3.0 for x in d]
    pos=[]; neg=[]
    for i,t in enumerate(tp):
        flow=t*float(d[i].get('volume') or 0)
        if i==0 or t>=tp[i-1]: pos.append(flow); neg.append(0.0)
        else: pos.append(0.0); neg.append(flow)
    out=[]
    for i in range(len(d)):
        p=sum(pos[max(0,i-n+1):i+1]); q=sum(neg[max(0,i-n+1):i+1])
        out.append(100.0 if q==0 else 100.0-100.0/(1.0+p/q))
    return out


def week_context(d):
    wk=to_weekly(d); mp={}
    for i,b in enumerate(wk):
        y,w,_=date.fromisoformat(str(b['date'])[:10]).isocalendar(); mp[(y,w)]=i
    prev_hi=[]; prev_lo=[]; wb=bias(wk, MAJOR_SIDE); wi=[]
    for b in d:
        y,w,_=date.fromisoformat(str(b['date'])[:10]).isocalendar(); k=mp.get((y,w),0); wi.append(k)
        if k>0:
            prev_hi.append(wk[k-1]['high']); prev_lo.append(wk[k-1]['low'])
        else: prev_hi.append(None); prev_lo.append(None)
    return wk,wi,wb,prev_hi,prev_lo


def fvg_zone(h, start_date, end_date, direction):
    z=None
    for i in range(2,len(h)):
        d=str(h[i]['date'])[:10]
        if d<start_date or d>end_date: continue
        a=h[i-2]; b=h[i]
        if direction==1 and a['high'] < b['low']: z=(a['high'],b['low'])
        if direction==-1 and a['low'] > b['high']: z=(b['high'],a['low'])
    return z


def _daily_candidate(d, fo):
    if len(d) < 180: return None
    sw,db=bias(d,MAJOR_SIDE)[0] if False else (None,None)
    sw=swings_major(d,MAJOR_SIDE); _,db=bias_with_swings(d,MAJOR_SIDE)
    wk,wi,wb,pwhs,pwls=week_context(d)
    closes=[x['close'] for x in d]; sma=sma_values(closes,EMA_N); rsi=rsi_values(closes); mfi=mfi_values(d)
    for conf in range(len(d)-2, max(EMA_N+20,len(d)-35), -1):
        for direction in (1,-1):
            if direction==-1 and not fo: continue
            if db[conf]!=direction: continue
            k=wi[conf]
            if k<1 or wb[k-1]!=direction: continue
            if direction==1 and not (closes[conf]>sma[conf] and rsi[conf]>50 and mfi[conf]>50): continue
            if direction==-1 and not (closes[conf]<sma[conf] and rsi[conf]<50 and mfi[conf]<50): continue
            for s in range(conf-1, max(EMA_N+10,conf-CONF_WITHIN-1), -1):
                if db[s]!=direction or wi[s]<1 or wb[wi[s]-1]!=direction: continue
                levels=[]
                if direction==1 and pwls[s] is not None: levels.append(pwls[s])
                if direction==-1 and pwhs[s] is not None: levels.append(pwhs[s])
                piv=[p for i,kp,p in sw if i+MAJOR_SIDE<=s and i>=s-POOL_BACK and kp==('L' if direction==1 else 'H')]
                for a in piv:
                    if sum(abs(a-b)/max(abs(b),1e-9)<=POOL_TOL for b in piv)>=2: levels.append(a)
                if not levels: continue
                sweep=any((d[s]['low']<lev<=d[s]['close']) if direction==1 else (d[s]['high']>lev>=d[s]['close']) for lev in levels)
                if not sweep: continue
                opp='H' if direction==1 else 'L'
                prior=[(i,p) for i,kp,p in sw if kp==opp and i+MAJOR_SIDE<=s]
                if not prior: continue
                choch=prior[-1][1]
                if direction==1:
                    if d[conf]['close']<=choch or any(d[q]['close']>choch for q in range(s+1,conf)): continue
                else:
                    if d[conf]['close']>=choch or any(d[q]['close']<choch for q in range(s+1,conf)): continue
                return {'d':d,'sw':sw,'s':s,'conf':conf,'direction':direction,
                        'level':min(levels) if direction==1 else max(levels),
                        'pwh':pwhs[conf],'pwl':pwls[conf],
                        'signal_date':str(d[conf]['date'])[:10]}
    return None


def bias_with_swings(d, side=MAJOR_SIDE):
    sw=swings_major(d,side); by={}
    for i,k,p in sw: by.setdefault(i+side,[]).append((i,k,p))
    H=[];L=[];out=[0]*len(d)
    for t in range(len(d)):
        for i,k,p in by.get(t,[]): (H if k=='H' else L).append((i,p))
        out[t]=1 if len(H)>=2 and len(L)>=2 and H[-1][1]>H[-2][1] and L[-1][1]>L[-2][1] else (-1 if len(H)>=2 and len(L)>=2 and H[-1][1]<H[-2][1] and L[-1][1]<L[-2][1] else 0)
    return sw,out


def find_setup(d, h1, fo):
    c=_daily_candidate(d,fo)
    if not c or not h1: return None
    z=fvg_zone(h1,c['d'][c['s']]['date'][:10],c['signal_date'],c['direction'])
    if not z: return None
    lo,hi=z; direction=c['direction']; entry=(lo+hi)/2.0
    if direction==1:
        stop=min(c['d'][c['s']]['low'],lo)*0.9995
        lv=sorted([p for i,k,p in c['sw'] if k=='H' and i+MAJOR_SIDE<=c['conf'] and p>entry])
        target=lv[0] if lv else (c['pwh'] if c['pwh'] and c['pwh']>entry else None)
    else:
        stop=max(c['d'][c['s']]['high'],hi)*1.0005
        lv=sorted([p for i,k,p in c['sw'] if k=='L' and i+MAJOR_SIDE<=c['conf'] and p<entry], reverse=True)
        target=lv[0] if lv else (c['pwl'] if c['pwl'] and c['pwl']<entry else None)
    if target is None or (direction==1 and not target>entry) or (direction==-1 and not target<entry): return None
    return {'at':c['signal_date'],'swept':str(c['d'][c['s']]['date'])[:10],
            'level':round(c['level'],2),'entry':round(entry,2),'stop':round(stop,2),
            'target':round(target,2),'risk':abs(entry-stop),'direction':direction,
            'shape':'major sweep, CHoCH, FVG midpoint, SMA + RSI + MFI'}


def make_call(book, sym, s, fo):
    return {'book':book,'sym':sym,'market':'FO' if fo else 'EQUITY',
            'side':'LONG' if s['direction']==1 else 'SHORT','at':s['at'],
            'shape':s['shape'],'level':s['level'],'entry':s['entry'],
            'trigger':s['entry'],'stop':s['stop'],'target':s['target'],
            'risk_pct':round(s['risk']/s['entry']*100,2),'state':'WAITING',
            'first_seen':datetime.now().isoformat(timespec='seconds'),
            'broke_at':s['at'],'filled_at':None,'closed_at':None,
            'exit_px':None,'result_pct':None,'status':'WAITING','last':None,
            'broke_seen':datetime.now().isoformat(timespec='seconds')}


def live_already(L, book, sym):
    return any(c['book']==book and c['sym']==sym and c['state'] in ('WAITING','BROKE','IN') for c in L.values())


def key_of(c): return f"{c['book']}|{c['sym']}|{c['at']}|{c.get('side','LONG')}"


def step(c, h1):
    if c['state'] in ('DONE','GONE') or not h1: return
    direction=1 if c.get('side','LONG')=='LONG' else -1
    after=[b for b in h1 if str(b['date'])[:10] > str(c['at'])[:10]]
    if not after: return
    c['last']=round(after[-1]['close'],2); seen=set()
    if c['state'] in ('WAITING','BROKE'):
        for b in after:
            seen.add(str(b['date'])[:10])
            if len(seen)>HOLD_DAYS:
                c['state']='GONE'; c['status']='NEVER CAME BACK'; c['closed_at']=str(b['date']); return
            if b['low']<=c['entry']<=b['high']:
                c['state']='IN'; c['status']='OPEN'; c['filled_at']=str(b['date'])
                c['entry']=round(min(c['entry'],b['open']) if direction==1 else max(c['entry'],b['open']),2); break
        if c['state']!='IN': return
    run=[b for b in after if str(b['date'])>=str(c['filled_at'])]; seen=set()
    for n,b in enumerate(run):
        seen.add(str(b['date'])[:10])
        if len(seen)>HOLD_DAYS:
            c['exit_px']=round(run[n-1]['close'],2); c['status']='TIME'; c['closed_at']=str(run[n-1]['date']); break
        stophit=(b['low']<=c['stop']) if direction==1 else (b['high']>=c['stop'])
        targethit=(b['high']>=c['target']) if direction==1 else (b['low']<=c['target'])
        if stophit: c['exit_px']=c['stop']; c['status']='STOP'; c['closed_at']=str(b['date']); break
        if n>0 and targethit: c['exit_px']=c['target']; c['status']='TARGET'; c['closed_at']=str(b['date']); break
    if c['exit_px'] is not None and c['result_pct'] is None:
        c['result_pct']=round(100*(direction*(c['exit_px']-c['entry'])/c['entry']-COST_FUT/100),2); c['state']='DONE'


def live_already(L, book, sym):
    return any(c["book"] == book and c["sym"] == sym
               and c["state"] in ("WAITING", "BROKE", "IN")
               for c in L.values())


def key_of(c):
    return f"{c['book']}|{c['sym']}|{c['at']}"


def step(c, h1):
    """Advance a waiting/open swing call using completed hourly candles.

    The first touch fills at the entry (or the next candle open if it gaps
    through it). Stops are checked before targets when both occur in one bar.
    A call expires after the configured number of trading days.
    """
    cfg = BOOKS[c["book"]]
    if c.get("state") in ("DONE", "GONE") or not h1:
        return
    direction = 1 if c.get("side", "LONG") == "LONG" else -1
    after = [b for b in h1 if str(b["date"])[:10] > str(c["at"])[:10]]
    if not after:
        return
    c["last"] = round(after[-1]["close"], 2)

    if c.get("state") in ("WAITING", "BROKE"):
        seen = set()
        for b in after:
            day = str(b["date"])[:10]
            seen.add(day)
            if len(seen) > cfg["hold_days"]:
                c["state"] = "GONE"; c["status"] = "NEVER CAME BACK"; c["closed_at"] = str(b["date"])
                return
            touched = b["low"] <= c["entry"] if direction == 1 else b["high"] >= c["entry"]
            if touched:
                c["state"] = "IN"; c["status"] = "OPEN"; c["filled_at"] = str(b["date"])
                if direction == 1:
                    c["entry"] = round(max(c["entry"], b["open"]), 2)
                else:
                    c["entry"] = round(min(c["entry"], b["open"]), 2)
                break
        if c.get("state") != "IN":
            return

    run = [b for b in after if str(b["date"]) >= str(c.get("filled_at") or c["at"])]
    seen = set()
    for n, b in enumerate(run):
        seen.add(str(b["date"])[:10])
        if len(seen) > cfg["hold_days"]:
            c["exit_px"] = round(run[n - 1]["close"], 2); c["status"] = "TIME"; c["closed_at"] = str(run[n - 1]["date"])
            break
        stop_hit = b["low"] <= c["stop"] if direction == 1 else b["high"] >= c["stop"]
        target_hit = b["high"] >= c["target"] if direction == 1 else b["low"] <= c["target"]
        if stop_hit:
            c["exit_px"] = c["stop"]; c["status"] = "STOP"; c["closed_at"] = str(b["date"]); break
        if n > 0 and target_hit:
            c["exit_px"] = c["target"]; c["status"] = "TARGET"; c["closed_at"] = str(b["date"]); break
    if c.get("exit_px") is not None and c.get("result_pct") is None:
        gross = 100 * direction * (c["exit_px"] - c["entry"]) / c["entry"]
        c["result_pct"] = round(gross - cfg["cost"], 2); c["state"] = "DONE"

def fetch_15m(kite, tokens, syms, days=40):
    out = {}
    frm = (date.today() - timedelta(days=days)).isoformat()
    to = date.today().isoformat()
    for s in syms:
        t = tokens.get(s)
        if not t:
            continue
        try:
            raw = kite.historical_data(t, f"{frm} 09:00:00", f"{to} 15:40:00",
                                       "15minute")
            bars = [{"date": str(x["date"])[:19], "open": float(x["open"]),
                     "high": float(x["high"]), "low": float(x["low"]),
                     "close": float(x["close"]),
                     "volume": float(x.get("volume") or 0)} for x in raw]
            if bars:
                try:
                    lastb = datetime.fromisoformat(bars[-1]["date"])
                    if (datetime.now() - lastb).total_seconds() < 15 * 60:
                        bars.pop()
                except Exception:
                    bars.pop()
            if len(bars) > 60:
                out[s] = bars
        except Exception:
            pass
        time.sleep(0.05)
    return out


def fetch_daily(kite, tokens, syms, years=2):
    """Daily candles change once a day, so they are cached."""
    today = date.today().isoformat()
    try:
        old = json.load(open(DAILYF, encoding="utf8"))
        # AND DO NOT TRUST AN EMPTY ONE EITHER (28/09). Refusing to WRITE an
        # empty cache was only half the fix - a poisoned cache from earlier in
        # the day was still being read back, so the book stayed dead until
        # midnight. An empty cache is now ignored and the download retried.
        if old.get("date") == today and old.get("bars"):
            # A CACHE BUILT BEFORE THE OPEN HAS NO CANDLE FOR TODAY (06/10).
            #
            # The cache is stamped with the date it was written, so one written
            # at 00:50 counts as "today's" and is handed back all session - and
            # the newest candle in it is YESTERDAY'S. The long-term book then
            # spends the whole day unable to see today at all: it cannot fill,
            # cannot stop out, and cannot retire a call whose target has gone.
            # That is why PNBHOUSING, BHEL, HDFCLIFE, PRESTIGE and ICICIPRULI
            # were still asking to be bought at prices the market had left
            # behind hours earlier.
            #
            # While the market is open the cache is ignored and the candles are
            # pulled again, so the book is reading the day it is trading in.
            # Outside market hours the cache is correct and is used as before.
            now = datetime.now()
            trading = (now.weekday() < 5
                       and (9, 15) <= (now.hour, now.minute) <= (15, 35))
            newest = max((b[-1]["date"] for b in old["bars"].values() if b),
                         default="")
            if not trading or newest[:10] == today:
                print(f"  daily candles: using today's cache "
                      f"({len(old['bars'])} names, newest {newest})")
                return old["bars"]
            print(f"  daily candles: the cache stops at {newest} and the market "
                  f"is open - pulling today's candles")
    except Exception:
        pass
    out = {}
    frm = (date.today() - timedelta(days=365 * years)).isoformat()
    for n, s in enumerate(syms, 1):
        t = tokens.get(s)
        if not t:
            continue
        try:
            raw = kite.historical_data(t, frm, today, "day")
            bars = [{"date": str(x["date"])[:10], "open": float(x["open"]),
                     "high": float(x["high"]), "low": float(x["low"]),
                     "close": float(x["close"]),
                     "volume": float(x.get("volume") or 0)} for x in raw]
            if len(bars) > 120:
                out[s] = bars
        except Exception:
            pass
        if n % 50 == 0:
            print(f"    daily {n}/{len(syms)}", flush=True)
        time.sleep(0.05)
    # NEVER CACHE AN EMPTY DOWNLOAD (28/09). If every request failed - a
    # stale token, a rate limit, a dropped connection - `out` is empty, and
    # writing it as today's cache meant the long-term book had no candles for
    # the whole day and quietly did nothing. Found on 28/09 with a 34-byte
    # cache file and twelve calls frozen since Friday.
    if not out:
        print("  daily candles: NOTHING came back from Zerodha - not caching "
              "that. Check the login and leave this running; it will try again.")
        return {}
    try:
        json.dump({"date": today, "bars": out}, open(DAILYF, "w"))
    except Exception:
        pass
    return out


_PX = {"map": {}, "at": "", "err": ""}


def quote_all(kite, tokens, syms):
    """ONE LIVE PRICE FOR THE WHOLE DESK (28/09).

    MM: "havells price is showing wrong. why is it happening again and again."
    Because every engine on the desk carries its OWN price, taken whenever that
    engine's scan last came round. HAVELLS sat at 1082.70 because the old SMC
    scan had not been back to it. Fixing one tab at a time never ends - there
    is always another engine with another stale number.

    So this reads a real quote for every stock the desk watches, once a minute,
    and publishes it as one table the dashboard can use for any tab. Around
    1,900 names is ten requests, well inside Zerodha's limit.
    """
    out, err = {}, ""
    # A HUNDRED AT A TIME (28/09). Two hundred came back empty every single
    # pass - kite.ltp puts every symbol in the URL, and the request is refused
    # once that gets long. The failure is also carried to the desk now, so a
    # dead price table says why instead of just being blank.
    for i in range(0, len(syms), 100):
        chunk = [x for x in syms[i:i + 100] if x in tokens]
        if not chunk:
            continue
        try:
            q = kite.ltp(["NSE:" + x for x in chunk])
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            continue
        for k, v in q.items():
            pr = v.get("last_price")
            if pr:
                out[k.split(":", 1)[-1]] = round(float(pr), 2)
        time.sleep(0.05)
    _PX["err"] = err
    if out:
        _PX["map"] = out
        _PX["at"] = datetime.now().isoformat(timespec="seconds")
        _PX["err"] = ""
    else:
        print(f"  (live prices: nothing came back{' - ' + err if err else ''})",
              flush=True)
    return _PX["map"]


def mark(kite, tokens, L):
    """A PRICE LABELLED "PRICE NOW" HAS TO BE NOW (28/09).

    MM: "it is showing price now 700.45. cmp is 714 ... and it is showing
    wrong." He was right and the fault was here, not on the dashboard.

    `last` was only ever set inside step(), from the last candle that had
    already closed. Two things followed from that, both bad:

      - the daily book had NO price at all. A long-term call is written on
        today's daily candle, so there is no candle after it, so step() never
        reached the line that sets `last`. All twelve long-term calls showed
        a blank price.
      - the 15-minute book carried whatever the last completed sweep saw. A
        sweep over 1,900 stocks takes most of a quarter of an hour, so the
        price on the desk could be half an hour behind the market. That is
        the 700.45 against a real 714 on RKFORGE.

    A quote is one request to Zerodha for every name on the books - twenty-six
    names today - so it is read every minute and it is what the desk shows.
    Only calls that are still alive are marked; a closed trade keeps the exit
    price it actually got.
    """
    alive = ("WAITING", "BROKE", "IN")
    want = sorted({c["sym"] for c in L.values()
                   if c.get("state") in alive and c["sym"] in tokens})
    if not want:
        return
    px = _PX["map"]
    if not px:
        return                      # no quote yet - leave the prices alone
    for c in L.values():
        if c.get("state") in alive and px.get(c["sym"]):
            c["last"] = px[c["sym"]]

    # SEVENTEEN MINUTES IS TOO LATE TO BE TOLD (06/10). MM: "why did i get
    # notification of voltas so late? why?"
    #
    # He was told at 10:02:36 about a breakout that happened somewhere inside
    # the 09:45 candle - up to seventeen minutes after the fact. The cause is
    # structural: a breakout is only noticed when its 15-minute candle has
    # CLOSED, which is 10:00, and the 400-name sweep then takes until 10:02.
    # Every call today was 17-18 minutes late by the same arithmetic.
    #
    # This engine already asks Zerodha for a live price on every name once a
    # minute. That price knows about the breakout immediately. So a WAITING
    # call whose live price has gone through its trigger is flagged here, and
    # the desk says so straight away.
    #
    # NOTHING ABOUT THE RECORD MOVES. `state` is untouched; the breakout is
    # still confirmed, timed and recorded from the closed candle exactly as
    # before, because a tick is not a candle and the book has to stay provable.
    # This is a heads-up, not a decision - it only changes when he hears.
    now_s = datetime.now().isoformat(timespec="seconds")
    for c in L.values():
        if c.get("state") != "WAITING" or not c.get("trigger"):
            continue
        p = px.get(c["sym"])
        if p and p >= c["trigger"] and not c.get("live_through"):
            c["live_through"] = now_s


# ── the permanent record ──────────────────────────────────────────────────
def load_log():
    try:
        return json.load(open(LOGF, encoding="utf8"))
    except Exception:
        return {}


def save_log(L):
    tmp = LOGF + ".tmp"
    json.dump(L, open(tmp, "w"))
    os.replace(tmp, LOGF)


_SERVE = {"payload": json.dumps({"as_of": None, "books": {}})}


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        b = _SERVE["payload"].encode("utf8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        try:
            self.wfile.write(b)
        except Exception:
            pass


def serve():
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), _H).serve_forever()
    except Exception as e:
        print(f"  (could not open port {PORT}: {e})")


def write_site(payload):
    """WRITE THE CALLS INTO THE PAGE ITSELF (25/09).

    Serving them on a port was not enough: a page loaded from 127.0.0.1:5055
    is refused when it tries to read 127.0.0.1:5057, so the tab sat there
    saying "not running" while this program was running perfectly. The
    formations scanner solved the same problem long ago by writing its calls
    straight into the HTML between two markers, and that is why the Patterns
    tab has always had data. Same thing here. The port stays, for the browsers
    where it does work - this is the belt.
    """
    start, end = "/*SMC2_START*/", "/*SMC2_END*/"
    block = f"{start}const SMC2LIVE = {payload};{end}"
    try:
        h = open(SITE, encoding="utf8").read()
    except Exception as e:
        print(f"  (could not read the dashboard: {e})")
        return
    if start in h and end in h:
        a = h.index(start); b = h.index(end) + len(end)
        h = h[:a] + block + h[b:]
    else:
        try:
            k = h.rindex("</script>")
        except ValueError:
            return
        h = h[:k] + "\n" + block + "\n" + h[k:]
    try:
        tmp = SITE + ".smc2.tmp"
        open(tmp, "w", encoding="utf8").write(h)
        os.replace(tmp, SITE)
    except Exception as e:
        print(f"  (could not write the dashboard: {e})")


def publish(L):
    books = {}
    for b in BOOKS:
        rows = [c for c in L.values() if c["book"] == b]
        done = [c for c in rows if c["result_pct"] is not None]
        booked = sum(c["result_pct"] / 100.0 * SLOT for c in done)
        openr = [c for c in rows if c["state"] == "IN"]
        marked = sum(((1 if c.get("side", "LONG") == "LONG" else -1)
                      * (c["last"] - c["entry"]) / c["entry"] * SLOT)
                     for c in openr if c.get("last") and c.get("entry"))
        books[b] = {
            "chart": BOOKS[b]["chart"], "note": BOOKS[b]["note"],
            # A CALL THAT HAS BROKEN OUT MUST STILL BE ON THE DESK (06/10).
            #
            # This line read `state == "WAITING"`, so the moment a call broke
            # out it left the page completely - it was not waiting, not held,
            # not closed. It only reappeared if it filled. That is exactly the
            # state where MM needs to be watching it: the level has gone, the
            # pullback is what he has to put the order into.
            #
            # It is also why the desk kept looking empty, and why the twelve
            # calls put back this morning were nowhere to be seen even though
            # the record had them. BROKE belongs here.
            "waiting": [c for c in rows if c["state"] in ("WAITING", "BROKE")],
            "open": openr,
            "closed": sorted(done, key=lambda c: c["closed_at"] or "",
                             reverse=True)[:400],
            "booked": round(booked), "closed_n": len(done),
            "marked": round(marked), "open_n": len(openr),
            "missed_n": sum(1 for c in rows if c["status"] == "MISSED"),
            "wins": sum(1 for c in done if c["result_pct"] > 0),
        }
    _SERVE["payload"] = json.dumps(
        {"as_of": datetime.now().isoformat(timespec="seconds"),
         "slot": SLOT, "books": books,
         # the whole desk's live prices - see quote_all()
         "px": _PX["map"], "px_at": _PX["at"], "px_err": _PX["err"]})
    write_site(_SERVE["payload"])



def main():
    print("""
  ASCENDRA - FRESH SMC SWING BOOK

  Fresh start: the old SMC record is not loaded.
  Daily/weekly bias, ten-candle major swings, liquidity sweep, daily CHoCH,
  FVG midpoint entry on an hourly chart, SMA + RSI + MFI confirmation.
  F&O names may be long or short. Equity names are long-only.
""")
    kite = load_kite(); print("  connected to Zerodha")
    try:
        raw = json.load(open(os.path.join(DATA, "fo_universe.json"), encoding="utf8"))
        fo = set(raw["map"].keys()) if isinstance(raw, dict) and "map" in raw else set(raw)
    except Exception:
        fo = set()
    syms_all = set(fo)
    mc = os.path.join(DATA, "mcap_1000cr.json")
    if os.path.exists(mc):
        try:
            syms_all |= set(json.load(open(mc, encoding="utf8"))["caps"].keys())
        except Exception:
            pass
    p = os.path.join(DATA, "ind_niftytotalmarket_list.csv")
    if os.path.exists(p):
        import csv as _csv
        for row in _csv.DictReader(open(p, encoding="utf-8-sig")):
            if row.get("Symbol"):
                syms_all.add(row["Symbol"].strip())
    inst = kite.instruments("NSE")
    tokens = {i["tradingsymbol"]: i["instrument_token"] for i in inst
              if i.get("segment") == "NSE" and i.get("instrument_type") in ("EQ", "BE")}
    syms = sorted(s for s in syms_all if s in tokens)
    print(f"  watching {len(syms)} stocks ({len(fo & set(syms))} F&O; equity long-only)")

    L = {}
    print("  fresh start: previous SMC calls are not loaded")
    publish(L); threading.Thread(target=serve, daemon=True).start()
    print(f"  serving on http://127.0.0.1:{PORT}   (your desk stays on 5055)\n")
    last_scan = last_px = 0.0
    while True:
        now = datetime.now()
        trading = now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) <= (16, 0)
        try:
            if trading and time.time() - last_px > PRICE_EVERY:
                last_px = time.time(); quote_all(kite, tokens, syms); mark(kite, tokens, L); publish(L)
            if time.time() - last_scan > SLOW_EVERY:
                last_scan = time.time()
                print(f"  {now:%H:%M:%S}  scanning {len(syms)} stocks ...", flush=True)
                D = fetch_daily(kite, tokens, syms); found = 0
                for sym, dbars in D.items():
                    if len(dbars) < 180:
                        continue
                    is_fo = sym in fo
                    live = [c for c in L.values() if c["sym"] == sym and c["state"] in ("WAITING", "BROKE", "IN")]
                    candidate = _daily_candidate(dbars, is_fo)
                    if not live and not candidate:
                        continue
                    h = fetch_15m(kite, tokens, [sym], days=40).get(sym)
                    h1 = by_hour(h) if h else []
                    for c in live:
                        step(c, h1)
                    if not candidate or not h1 or live_already(L, "LONG TERM", sym):
                        continue
                    s = find_setup(dbars, h1, is_fo)
                    if not s:
                        continue
                    c = make_call("LONG TERM", sym, s, is_fo)
                    if key_of(c) in L:
                        continue
                    L[key_of(c)] = c; found += 1
                    print(f"  LONG TERM  {sym:12s} {c['side']:5s} entry {c['entry']} stop {c['stop']} target {c['target']}", flush=True)
                save_log(L); publish(L)
                print(f"  {datetime.now():%H:%M:%S}  done - {found} new call{'s' if found != 1 else ''}, {sum(1 for c in L.values() if c['state'] == 'IN')} open", flush=True)
        except Exception as e:
            print(f"  (scan failed, carrying on: {e})", flush=True)
        time.sleep(30)

def by_hour(bars15):
    """15-minute candles into hourly ones, anchored to the clock.

    Anchored, not counted. Counting sixteen candles from the start of a file
    is what produced "4-hour" candles that ran across two days and a backtest
    result that fell apart the moment it was checked.
    """
    OPEN = 9 * 60 + 15
    buckets = {}
    order = []
    for b in bars15:
        t = str(b["date"])
        day = t[:10]
        try:
            mins = int(t[11:13]) * 60 + int(t[14:16])
        except (ValueError, IndexError):
            continue
        slot = max(0, mins - OPEN) // 60
        k = (day, slot)
        if k not in buckets:
            buckets[k] = []
            order.append(k)
        buckets[k].append(b)
    out = []
    for k in order:
        c = buckets[k]
        out.append({"date": c[0]["date"], "open": c[0]["open"],
                    "high": max(x["high"] for x in c),
                    "low": min(x["low"] for x in c),
                    "close": c[-1]["close"],
                    "volume": sum(x.get("volume") or 0 for x in c)})
    return out


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  stopped.")
    except Exception:
        import traceback
        traceback.print_exc()
        hold(1)





