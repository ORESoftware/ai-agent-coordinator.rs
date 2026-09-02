#!/usr/bin/env python3
"""Supervise one to three continuous artifact-recovery workers."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

MAX_WORKERS = 3


class PoolError(RuntimeError):
    pass


def worker_count(raw: str | None) -> int:
    value = 3 if raw is None else int(raw)
    if not 1 <= value <= MAX_WORKERS:
        raise PoolError(f"ARTIFACT_RECOVERY_WORKER_COUNT must be between 1 and {MAX_WORKERS}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-config", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        count = worker_count(os.getenv("ARTIFACT_RECOVERY_WORKER_COUNT"))
        prefix = os.getenv("ARTIFACT_RECOVERY_WORKER_ID_PREFIX", "continuous-recovery").strip()
        if not prefix or len(prefix) > 96:
            raise PoolError("ARTIFACT_RECOVERY_WORKER_ID_PREFIX has an invalid length")
        worker_script = Path(__file__).with_name("run_continuous_artifact_recovery_worker.py")
        if args.check_config:
            print(json.dumps({"status": "valid", "worker_count": count, "maximum": MAX_WORKERS}, sort_keys=True))
            return 0

        stopping = False
        children: dict[int, subprocess.Popen[bytes]] = {}

        def stop(_signum, _frame):
            nonlocal stopping
            stopping = True
            for child in children.values():
                child.terminate()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        def spawn(index: int) -> subprocess.Popen[bytes]:
            environment = dict(os.environ)
            environment["ARTIFACT_RECOVERY_WORKER_ID"] = f"{prefix}-{index + 1}"
            return subprocess.Popen(
                [sys.executable, str(worker_script)],
                env=environment,
                stdin=subprocess.DEVNULL,
            )

        for index in range(count):
            children[index] = spawn(index)

        while children:
            for index, child in list(children.items()):
                status = child.poll()
                if status is None:
                    continue
                if stopping:
                    del children[index]
                    continue
                time.sleep(1)
                children[index] = spawn(index)
            time.sleep(0.5)
        return 0
    except (OSError, ValueError, PoolError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
