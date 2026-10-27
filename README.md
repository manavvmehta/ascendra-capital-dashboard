# Ascendra Capital dashboard

This repository is a snapshot of the full `ASCENDRA CAPITAL` Desktop project, prepared on 27 October 2026. It includes the dashboard code, scanner code, data files, research files, old versions, and archives. The live Desktop folder was not changed.

**Keep this repository private.** The historical data and trade records are included. The Zerodha key and daily token files are deliberately excluded. The active dashboard page's embedded live records were replaced with empty blocks; the records remain in the separate data files. Python cache files were also omitted.

Git Large File Storage (Git LFS) is used for JSON data and video files. You need Git LFS when cloning the complete project. GitHub may count this storage and downloads against your account allowance.

## Main files

- `5 LIVE FEED/ASCENDRA.py`: morning launcher and login menu
- `5 LIVE FEED/desk.py`: local dashboard server on port 5055
- `WEBSITE/ascendra_desk.html`: dashboard page
- `5 LIVE FEED/SMC NEW.py`: current SMC scanner
- `5 LIVE FEED/FORMATIONS LIVE.py`: pattern scanner
- `5 LIVE FEED/COMMODITIES LIVE.py`: commodities scanner
- `3 CALL ENGINE/tech_calls.py`: older call engine
- `DATA/`: saved histories and trade records
- `ARCHIVE/`: previous copies and historical data

## Run locally

Install Python 3 and `requirements.txt`. In `5 LIVE FEED`, run `python connect.py` and complete your own Zerodha login. That creates private key and token files locally. Then run `python ASCENDRA.py` and use its menu to start the desk. Open `http://127.0.0.1:5055`.

The project is a research dashboard. It does not place live orders. The historical data and old versions are preserved for review; they do not prove that any strategy will be profitable.

