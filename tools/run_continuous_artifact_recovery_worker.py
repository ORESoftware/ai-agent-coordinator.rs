#!/usr/bin/env python3
"""Run the lease-bound artifact worker with a 50-day window and shared-state fencing."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Iterator, Sequence

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from artifact_recovery_worker_runtime.common import *
from artifact_recovery_worker_runtime.config import *
from artifact_recovery_worker_runtime.transport import *
from artifact_recovery_worker_runtime.sources import *
from artifact_recovery_worker_runtime.delivery import *
import artifact_recovery_worker_runtime.engine as engine_runtime

MAX_WINDOW_HOURS = 50 * 24
MAX_WORKERS = 3


def configured_window() -> tuple[int, int]:
    window = parse_int_env(
        "ARTIFACT_RECOVERY_WINDOW_HOURS",
        MAX_WINDOW_HOURS,
        24,
        MAX_WINDOW_HOURS,
    )
    overlap = parse_int_env(
        "ARTIFACT_RECOVERY_OVERLAP_HOURS",
        6,
        0,
        48,
    )
    if overlap >= window:
        raise WorkerError(
            "ARTIFACT_RECOVERY_OVERLAP_HOURS must be smaller than the window"
        )
    return window, overlap


@contextlib.contextmanager
def state_fence(state_dir: Path, timeout_seconds: int) -> Iterator[None]:
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / ".continuous-artifact-recovery.lock"
    deadline = time.monotonic() + max(1, timeout_seconds)
    with lock_path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise WorkerError(
                        "timed out waiting for the shared artifact-recovery state fence",
                        retryable=True,
                        error_class="state_contention",
                    )
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class FencedRecoveryEngine(engine_runtime.RecoveryEngine):
    @contextlib.contextmanager
    def state_transaction(self) -> Iterator[None]:
        # The base engine invokes this only for shared cursor/ledger
        # read-modify-write transactions. Source collection and delivery remain
        # concurrent across the bounded worker pool.
        with state_fence(self.config.state_dir, self.config.lease_seconds * 2):
            yield


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check-config", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        window, overlap = configured_window()
        engine_runtime.DEFAULT_WINDOW_HOURS = window
        engine_runtime.DEFAULT_OVERLAP_HOURS = overlap
        config = WorkerConfig.from_env(once=args.once)
        manifest = SourceManifest.from_value(
            load_json_file(config.source_manifest, "source manifest")
        )
        if args.check_config:
            output = {
                "status": "valid",
                "config": config.public_summary(),
                "window_hours": window,
                "overlap_hours": overlap,
                "maximum_pool_workers": MAX_WORKERS,
                "required_sources": list(manifest.required_sources),
                "optional_sources": list(manifest.optional_sources),
            }
            ensure_public_safe(output, "configuration summary")
            print(json.dumps(output, indent=2, sort_keys=True))
            return 0

        stop_requested = False

        def request_stop(_signum: int, _frame: object) -> None:
            nonlocal stop_requested
            stop_requested = True

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)

        http = JsonHttpClient(
            timeout_seconds=config.request_timeout_seconds,
            max_response_bytes=config.max_response_bytes,
        )
        coordinator = CoordinatorClient(config, http)
        engine = FencedRecoveryEngine(config, http=http)

        while not stop_requested:
            try:
                processed = engine_runtime.process_one(config, coordinator, engine)
            except WorkerError as exc:
                print(f"error: {exc}", file=sys.stderr)
                if config.once or not exc.retryable:
                    return 2
                if stop_requested:
                    return 0
                time.sleep(config.poll_seconds)
                continue

            if config.once:
                return 0
            if not processed and not stop_requested:
                time.sleep(config.poll_seconds)
        return 0
    except (OSError, WorkerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
