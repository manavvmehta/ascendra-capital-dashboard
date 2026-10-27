#!/usr/bin/env python3
"""
SET UP DAILY 6PM RUN.py

DOUBLE-CLICK THIS ONCE. From then on Windows fetches the day's prices at
6:00 pm every day on its own. You never have to remember.

It does not run the download now - it books it with Windows Task Scheduler,
the same thing Windows itself uses for updates. Nothing keeps running in the
background, and nothing starts with your PC. At 6pm Windows wakes it, it
works for a minute or two, and it stops.

TO STOP IT LATER
    Double-click this file again and choose R to remove.

IF YOUR PC IS OFF AT 6PM
    The task is set to catch up as soon as the machine is next on, so a night
    away does not leave a hole in your data.
"""

import os
import subprocess
import sys
import tempfile

TASK = "Ascendra Capital - daily NSE prices"
HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "DOWNLOAD DATA - double click me.py")


def hold(code=0):
    try:
        input("\n  Press Enter to close this window. ")
    except Exception:
        pass
    sys.exit(code)


def pythonw():
    """Prefer pythonw.exe so the nightly run does not flash a black window."""
    exe = sys.executable or ""
    cand = exe.replace("python.exe", "pythonw.exe")
    return cand if cand.endswith("pythonw.exe") and os.path.exists(cand) else exe


def exists():
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK],
                       capture_output=True, text=True, shell=False)
    return r.returncode == 0


def remove():
    r = subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print("\n  Removed. Windows will not run it any more.")
        print("  Your data and everything already downloaded is untouched.")
    else:
        print("\n  Could not remove it:")
        print("  " + (r.stderr or r.stdout).strip())


XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Fetches the day's NSE closing prices for the Ascendra Capital call engine.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T18:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{cmd}</Command>
      <Arguments>"{script}" --auto</Arguments>
      <WorkingDirectory>{cwd}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def install():
    xml = XML.format(cmd=pythonw(), script=TARGET, cwd=HERE)
    fd, path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    # Task Scheduler XML must be UTF-16, as declared in the header above.
    with open(path, "w", encoding="utf-16") as f:
        f.write(xml)
    r = subprocess.run(["schtasks", "/Create", "/TN", TASK, "/XML", path, "/F"],
                       capture_output=True, text=True)
    try:
        os.remove(path)
    except Exception:
        pass

    if r.returncode == 0:
        print("""
  ============================================================
    DONE. Windows will now fetch prices at 6:00 pm every day.
  ============================================================

    You do not need to do anything each evening any more.

    To check it worked, open this file tomorrow:
        nse_data\\daily_run_log.txt
    It records every run, with the date and what it fetched.

    To stop it: double-click this file again and press R.
""")
    else:
        print("\n  Windows would not accept the schedule:")
        print("  " + (r.stderr or r.stdout).strip())
        print("\n  Screenshot this window and send it to Claude.")


def main():
    print("=" * 60)
    print("  DAILY 6 PM PRICE FETCH")
    print("=" * 60)

    if os.name != "nt":
        print("\n  This only works on Windows.")
        hold(1)

    if not os.path.exists(TARGET):
        print(f"""
  PROBLEM: cannot find the downloader.

  Expected it here:
      {TARGET}

  This file must sit in the SAME folder as
  "DOWNLOAD DATA - double click me.py".
""")
        hold(1)

    if exists():
        print(f"""
  The 6pm job is ALREADY set up.

    K  keep it as it is
    R  remove it
""")
        try:
            ans = input("  Type K or R and press Enter: ").strip().upper()
        except Exception:
            ans = "K"
        if ans == "R":
            remove()
        else:
            print("\n  Left as it is. Nothing changed.")
        hold()

    print(f"""
  This will ask Windows to run the price download every day at
  6:00 pm.

    what runs   DOWNLOAD DATA - double click me.py
    when        6:00 pm, daily
    how long    a minute or two
    if PC off   catches up next time the machine is on

  Nothing runs in the background. Nothing starts with Windows.
""")
    try:
        ok = input("  Set this up? (y/n): ").strip().lower()
    except Exception:
        ok = "y"
    if ok != "y":
        print("\n  Cancelled. Nothing was changed.")
        hold()
    install()
    hold()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n  Something went wrong: {type(e).__name__}: {e}")
        print("  Screenshot this window and send it to Claude.")
        hold(1)
