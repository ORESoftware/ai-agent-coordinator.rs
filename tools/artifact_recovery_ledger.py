#!/usr/bin/env python3
"""Build and maintain the durable ChatGPT/Claude artifact-recovery ledger."""

from __future__ import annotations

from artifact_recovery import *  # noqa: F401,F403
from artifact_recovery.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
