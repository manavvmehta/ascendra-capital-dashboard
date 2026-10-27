# Ascendra Capital dashboard

This is a **source-code copy** of the Ascendra Capital dashboard, prepared on 27 October 2026. It is for version history and further development. The live Desktop folder was not changed.

The dashboard is a local web page served by Python. Its main entry point is `5 LIVE FEED/ASCENDRA.py`; the page is `WEBSITE/ascendra_desk.html`. The SMC, Patterns, and Commodities scanners are separate Python programs in `5 LIVE FEED`. Supporting call-engine code is in `3 CALL ENGINE`. Download and research utilities are in `1 DOWNLOAD DATA` and `2 ENGINE`.

## Private files are deliberately absent

Broker keys, daily login tokens, downloaded market data, live trade records, archives, and previous large backtests are **not in this repository**. The dashboard page's embedded live records were replaced with empty data blocks. Do not remove the rules in `.gitignore` or upload the Desktop folder directly.

This copy cannot reproduce past performance without the separate historical data and a reviewed backtest. It is not a verified trading system and does not place real orders.

## Running a separate local copy

1. Install Python 3 and the packages in `requirements.txt`.
2. Obtain your own Zerodha Kite Connect credentials. From `5 LIVE FEED`, run `python connect.py` and complete the Zerodha login. The program creates local key and token files; keep them private.
3. Start `python ASCENDRA.py` in `5 LIVE FEED`. Use its menu to start the desk and open `http://127.0.0.1:5055`.
4. Start `SMC NEW.bat` and `COMMODITIES LIVE.bat` in separate windows if needed. Check whether `FORMATIONS LIVE.py` was started by the desk before opening its batch file separately.

The fresh SMC swing rules are in `5 LIVE FEED/SMC NEW.py`. Intraday research is ongoing; no new intraday rules are implied by this copy.

