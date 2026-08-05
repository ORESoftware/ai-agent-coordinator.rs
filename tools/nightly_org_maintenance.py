#!/usr/bin/env python3
"""Compatibility entrypoint for the nightly organization maintenance package."""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from nightly_org_maintenance_impl import *  # noqa: E402,F401,F403

if __name__ == "__main__":
    raise SystemExit(main())
