#!/usr/bin/env python3
"""config.py — where state lives. The only thing the engine needs from it."""
import os
from pathlib import Path


def state_dir():
    """Resolved at CALL time, never frozen at import.

    A module-level `Path.home() / "Desktop"` is how a test suite once wrote
    fixture rows into a live book with every test still passing. Set
    TE_DATA_DIR to point elsewhere; the tests set it to a temp directory.
    """
    return Path(os.environ.get("TE_DATA_DIR") or (Path.home() / "Desktop"))


STATE_DIR = state_dir()
