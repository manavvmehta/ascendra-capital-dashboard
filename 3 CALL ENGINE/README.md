# The equity call engine

A live intraday + swing scanner on NSE cash equities. It has been running
since 21 July 2026 and issues calls someone actually trades from.

This package is the engine, its tests, and its complete call history.
Nothing here executes anything.

---

## Run it

```bash
python3 tech_calls.py                    # the engine's own self-test
python3 tests/instrumentation_tests.py   # 110 assertions
python3 tests/tradable_tests.py          #  22
python3 tests/tracking_loop_tests.py     #  21
python3 tests/differential_tests.py      #  10
python3 tests/mutate_instrumentation.py  # 34 mutants, all must die
```

State goes wherever `config.state_dir()` points — your home Desktop by
default, or set `TE_DATA_DIR`. The tests always set it to a temp directory.

To run the engine live you need a Kite session. You almost certainly do not
need to: `calls.csv` already holds everything it has ever produced.

---

## The files

```
tech_calls.py       the whole engine: funnel, 5 setups, score, lifecycle
config.py           where state lives
state.py            shared state + notifications
tests/              5 suites + a mutation runner
tests/baseline/     a pre-instrumentation snapshot, for the differential test

calls.csv           624 calls, 21 Jul - 1 Sep 2026, 479 with a final R
HOW_IT_WORKS.txt    the basis for every call, in plain language
OPEN_QUESTIONS.txt  where judgement is needed
analysis_score_check.py   the script that found the score problem
```

---

## What it does, briefly

Every 150s during market hours: quote the whole equity universe, drop
anything failing a tradability gate (min Rs20 price,
Rs5cr 20-day turnover, must actually move), rank the rest
on a cheap snapshot score and keep the top 25, pull 15-minute
candles for those only, and run five setups against them.

    PULLBACK        with-trend pullback to VWAP/EMA20, RSI reset and turn
    ORB             opening-range break, 09:45-11:30 only
    SQUEEZE         Bollinger bandwidth in its bottom quartile, then a break
    BREAKOUT-20D    fresh 20-day Donchian high, swing, 3-10 sessions
    VWAP-FLIP       price crosses VWAP and holds

A confluence score >= 6.5 fires; below that the row is recorded as
"watching" and is not actionable. Open calls are re-checked every
5s, separately from the 150s hunt — a stop checked only
every 150s is not a stop.

Participation (signal-bar volume vs its 20-bar average) is a HARD GATE on
three setups and only scores on two:

    PULLBACK      1.1x   soft
    ORB           1.3x   HARD
    SQUEEZE       1.5x   HARD
    BREAKOUT-20D  2.5x   HARD
    VWAP-FLIP     1.2x   HARD

`HOW_IT_WORKS.txt` has the entries, stops, targets and scoring in full.

---

## The record, and the problem

479 resolved calls. 52% win rate, +0.233R average.

    SQUEEZE        147   58%   +0.314R   [+0.14, +0.49]
    BREAKOUT-20D   131   41%   +0.303R   [+0.03, +0.58]
    PULLBACK       125   53%   +0.068R   [-0.10, +0.24]
    VWAP-FLIP       71   61%   +0.187R   [-0.02, +0.40]
    ORB              5   60%   +0.724R   [-0.49, +1.93]

Read the intervals. Two cross zero.

**The confluence score does not predict the outcome.** Rank correlation with R
is near zero and not distinguishable from none. Worse, the buckets are not
comparable: SQUEEZE only ever publishes at one score, and the top scores are
almost entirely BREAKOUT-20D — so comparing buckets compares SETUPS.

The score decides what fires. There is no evidence it should. That is the
single biggest problem here and it is question one.

---

## Two things about the data

**Only 22 of 624 rows carry `comp_*` and `instr_*` columns.**
Those record the score's components and the measurements taken at signal time.
The instrumentation went in on 01/09/2026, and earlier rows were deliberately
left censored rather than backfilled — a reconstructed value would be
look-ahead wearing a helpful face. Filter on `schema_version = 2`.

**R is measured against the ORIGINAL stop.** The engine moves the stop to
breakeven after the first target, so the stored `sl` on a row that reached T1
is not the stop it was taken with. Use `entry` and `risk0`, never `sl`, when
reconstructing.
