#!/usr/bin/env python3
"""state.py — shared in-memory state and notifications."""
tech_calls = {}
state = {}
notes = []


def publish(ref, snap):
    """ONE operation, not clear() then update().

    Readers take these dicts without a lock. clear()+update() is two steps and
    a reader landing between them sees an EMPTY dict, so the view blanks.
    """
    ref.update(snap)


def notify(tag, msg, level="info"):
    notes.append((tag, msg, level))
    print(f"  [{tag}] {msg}")
