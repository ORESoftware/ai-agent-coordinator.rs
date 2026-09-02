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
from collections import deque
from pathlib import Path
from typing import Sequence

MAX_WORKERS = 3
DEFAULT_MAX_RESTARTS = 5
DEFAULT_RESTART_WINDOW_SECONDS = 300
DEFAULT_RESTART_BACKOFF_MAX_SECONDS = 60
DEFAULT_STABLE_RUNTIME_SECONDS = 120
DEFAULT_SHUTDOWN_GRACE_SECONDS = 30
DEFAULT_KILL_GRACE_SECONDS = 5


class PoolError(RuntimeError):
    pass


def _bounded_int(
    raw: str | None,
    *,
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise PoolError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise PoolError(f"{name} must be between {minimum} and {maximum}")
    return value


def worker_count(raw: str | None) -> int:
    return _bounded_int(
        raw,
        name="ARTIFACT_RECOVERY_WORKER_COUNT",
        default=3,
        minimum=1,
        maximum=MAX_WORKERS,
    )


class RestartBudget:
    """Bound per-worker restart frequency and compute exponential backoff."""

    def __init__(
        self,
        *,
        max_restarts: int,
        window_seconds: int,
        max_backoff_seconds: int,
    ) -> None:
        self.max_restarts = max_restarts
        self.window_seconds = window_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._events: dict[int, deque[float]] = {}

    def reset(self, index: int) -> None:
        self._events.pop(index, None)

    def record(self, index: int, observed_at: float) -> tuple[bool, float, int]:
        history = self._events.setdefault(index, deque())
        cutoff = observed_at - self.window_seconds
        while history and history[0] < cutoff:
            history.popleft()
        history.append(observed_at)
        attempts = len(history)
        allowed = attempts <= self.max_restarts
        backoff = min(
            float(self.max_backoff_seconds),
            float(2 ** max(0, attempts - 1)),
        )
        return allowed, backoff, attempts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-config", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        count = worker_count(os.getenv("ARTIFACT_RECOVERY_WORKER_COUNT"))
        prefix = os.getenv(
            "ARTIFACT_RECOVERY_WORKER_ID_PREFIX", "continuous-recovery"
        ).strip()
        if not prefix or len(prefix) > 96:
            raise PoolError(
                "ARTIFACT_RECOVERY_WORKER_ID_PREFIX has an invalid length"
            )

        max_restarts = _bounded_int(
            os.getenv("ARTIFACT_RECOVERY_MAX_RESTARTS"),
            name="ARTIFACT_RECOVERY_MAX_RESTARTS",
            default=DEFAULT_MAX_RESTARTS,
            minimum=0,
            maximum=100,
        )
        restart_window_seconds = _bounded_int(
            os.getenv("ARTIFACT_RECOVERY_RESTART_WINDOW_SECONDS"),
            name="ARTIFACT_RECOVERY_RESTART_WINDOW_SECONDS",
            default=DEFAULT_RESTART_WINDOW_SECONDS,
            minimum=10,
            maximum=86_400,
        )
        restart_backoff_max_seconds = _bounded_int(
            os.getenv("ARTIFACT_RECOVERY_RESTART_BACKOFF_MAX_SECONDS"),
            name="ARTIFACT_RECOVERY_RESTART_BACKOFF_MAX_SECONDS",
            default=DEFAULT_RESTART_BACKOFF_MAX_SECONDS,
            minimum=1,
            maximum=3_600,
        )
        stable_runtime_seconds = _bounded_int(
            os.getenv("ARTIFACT_RECOVERY_STABLE_RUNTIME_SECONDS"),
            name="ARTIFACT_RECOVERY_STABLE_RUNTIME_SECONDS",
            default=DEFAULT_STABLE_RUNTIME_SECONDS,
            minimum=1,
            maximum=86_400,
        )
        shutdown_grace_seconds = _bounded_int(
            os.getenv("ARTIFACT_RECOVERY_SHUTDOWN_GRACE_SECONDS"),
            name="ARTIFACT_RECOVERY_SHUTDOWN_GRACE_SECONDS",
            default=DEFAULT_SHUTDOWN_GRACE_SECONDS,
            minimum=1,
            maximum=3_600,
        )
        kill_grace_seconds = _bounded_int(
            os.getenv("ARTIFACT_RECOVERY_KILL_GRACE_SECONDS"),
            name="ARTIFACT_RECOVERY_KILL_GRACE_SECONDS",
            default=DEFAULT_KILL_GRACE_SECONDS,
            minimum=1,
            maximum=60,
        )

        worker_script = Path(__file__).with_name(
            "run_continuous_artifact_recovery_worker.py"
        )
        public_config = {
            "status": "valid",
            "worker_count": count,
            "maximum": MAX_WORKERS,
            "max_restarts": max_restarts,
            "restart_window_seconds": restart_window_seconds,
            "restart_backoff_max_seconds": restart_backoff_max_seconds,
            "stable_runtime_seconds": stable_runtime_seconds,
            "shutdown_grace_seconds": shutdown_grace_seconds,
            "kill_grace_seconds": kill_grace_seconds,
        }
        if args.check_config:
            print(json.dumps(public_config, sort_keys=True))
            return 0

        stopping = False
        exit_code = 0
        shutdown_deadline: float | None = None
        kill_deadline: float | None = None
        kill_sent = False
        children: dict[int, subprocess.Popen[bytes]] = {}
        started_at: dict[int, float] = {}
        pending_respawns: dict[int, float] = {}
        budget = RestartBudget(
            max_restarts=max_restarts,
            window_seconds=restart_window_seconds,
            max_backoff_seconds=restart_backoff_max_seconds,
        )

        def terminate_children() -> None:
            for child in children.values():
                if child.poll() is not None:
                    continue
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass

        def request_stop(_signum: int, _frame: object) -> None:
            nonlocal stopping, shutdown_deadline
            if stopping:
                return
            stopping = True
            pending_respawns.clear()
            shutdown_deadline = time.monotonic() + shutdown_grace_seconds
            terminate_children()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)

        def spawn(index: int) -> subprocess.Popen[bytes]:
            environment = dict(os.environ)
            environment["ARTIFACT_RECOVERY_WORKER_ID"] = f"{prefix}-{index + 1}"
            child = subprocess.Popen(
                [sys.executable, str(worker_script)],
                env=environment,
                stdin=subprocess.DEVNULL,
            )
            started_at[index] = time.monotonic()
            return child

        for index in range(count):
            children[index] = spawn(index)

        while children or pending_respawns:
            now = time.monotonic()

            for index, child in list(children.items()):
                status = child.poll()
                if status is None:
                    continue
                del children[index]
                runtime = max(0.0, now - started_at.pop(index, now))
                if stopping:
                    continue

                if runtime >= stable_runtime_seconds:
                    budget.reset(index)
                allowed, delay, attempts = budget.record(index, now)
                if not allowed:
                    print(
                        "error: worker restart budget exhausted "
                        f"for slot {index + 1} after {attempts} exits "
                        f"within {restart_window_seconds} seconds",
                        file=sys.stderr,
                    )
                    exit_code = 2
                    stopping = True
                    pending_respawns.clear()
                    shutdown_deadline = now + shutdown_grace_seconds
                    terminate_children()
                    break

                pending_respawns[index] = now + delay
                print(
                    json.dumps(
                        {
                            "schema_version": (
                                "continuous_artifact_recovery_supervisor.v1"
                            ),
                            "event": "worker_restart_scheduled",
                            "slot": index + 1,
                            "exit_status": status,
                            "runtime_seconds": round(runtime, 3),
                            "restart_attempt": attempts,
                            "backoff_seconds": delay,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

            if stopping:
                for index, child in list(children.items()):
                    if child.poll() is not None:
                        del children[index]
                        started_at.pop(index, None)

                now = time.monotonic()
                if children and shutdown_deadline is not None and now >= shutdown_deadline:
                    if not kill_sent:
                        for child in children.values():
                            if child.poll() is None:
                                try:
                                    child.kill()
                                except ProcessLookupError:
                                    pass
                        kill_sent = True
                        kill_deadline = now + kill_grace_seconds
                    elif kill_deadline is not None and now >= kill_deadline:
                        print(
                            "error: worker processes did not exit after SIGKILL",
                            file=sys.stderr,
                        )
                        return 2
                if children:
                    time.sleep(0.1)
                    continue
                break

            now = time.monotonic()
            for index, due_at in list(pending_respawns.items()):
                if now < due_at:
                    continue
                children[index] = spawn(index)
                del pending_respawns[index]

            time.sleep(0.1)

        return exit_code
    except (OSError, PoolError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
