#!/usr/bin/env python3
"""Bounded exhaustive model and production refinement for the recovery supervisor."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import deque
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PATH = ROOT / "tools" / "run_continuous_artifact_recovery_pool.py"
SCHEMA_VERSION = "ores.formal.continuous-recovery-supervisor.v1"
MAX_MODEL_WORKERS = 3
MODEL_MAX_RESTARTS = 2
MODEL_MAX_BACKOFF_SECONDS = 4
MODEL_DEPTH = 10


class ModelCheckError(RuntimeError):
    """Raised when the bounded model or production refinement diverges."""


class Phase(str, Enum):
    RUNNING = "running"
    BACKOFF = "backoff"
    STOPPING = "stopping"
    KILLING = "killing"
    STOPPED = "stopped"
    STUCK = "stuck"


class EventKind(str, Enum):
    CRASH = "crash"
    STABLE_EXIT = "stable_exit"
    BACKOFF_ELAPSED = "backoff_elapsed"
    WINDOW_ELAPSED = "window_elapsed"
    STOP_REQUESTED = "stop_requested"
    CHILD_EXITED = "child_exited"
    TERMINATE_GRACE_EXPIRED = "terminate_grace_expired"
    KILL_GRACE_EXPIRED = "kill_grace_expired"


@dataclass(frozen=True, order=True)
class Event:
    kind: EventKind
    slot: int | None = None

    def label(self) -> str:
        if self.slot is None:
            return self.kind.value
        return f"{self.kind.value}[{self.slot + 1}]"


@dataclass(frozen=True)
class Slot:
    phase: Phase
    attempts: int = 0
    backoff_seconds: int = 0
    generation: int = 0


@dataclass(frozen=True)
class SupervisorState:
    stopping: bool
    operator_stop: bool
    budget_exhausted: bool
    terminate_grace_elapsed: bool
    kill_grace_elapsed: bool
    kill_timeout: bool
    slots: tuple[Slot, ...]


@dataclass(frozen=True)
class ExplorationReceipt:
    workers: int
    states: int
    transitions: int
    terminal_states: int
    maximum_depth: int
    clean_shutdown_witness: tuple[str, ...]
    budget_exhaustion_witness: tuple[str, ...]
    kill_timeout_witness: tuple[str, ...]


def _load_production_module():
    spec = importlib.util.spec_from_file_location(
        "continuous_recovery_pool_production",
        PRODUCTION_PATH,
    )
    if spec is None or spec.loader is None:
        raise ModelCheckError("production supervisor module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def expected_backoff(attempts: int, maximum: int = MODEL_MAX_BACKOFF_SECONDS) -> int:
    if attempts < 1:
        raise ModelCheckError("backoff attempts must be positive")
    return min(maximum, 1 << (attempts - 1))


def initial_state(worker_count: int) -> SupervisorState:
    if not 1 <= worker_count <= MAX_MODEL_WORKERS:
        raise ModelCheckError("model worker count is outside 1..3")
    return SupervisorState(
        stopping=False,
        operator_stop=False,
        budget_exhausted=False,
        terminate_grace_elapsed=False,
        kill_grace_elapsed=False,
        kill_timeout=False,
        slots=tuple(Slot(Phase.RUNNING) for _ in range(worker_count)),
    )


def _event_key(event: Event) -> tuple[str, int]:
    return event.kind.value, -1 if event.slot is None else event.slot


def enabled_events(state: SupervisorState) -> tuple[Event, ...]:
    if state.kill_timeout:
        return ()

    events: list[Event] = []
    if not state.stopping:
        events.append(Event(EventKind.STOP_REQUESTED))
        for index, slot in enumerate(state.slots):
            if slot.phase is Phase.RUNNING:
                events.extend(
                    (
                        Event(EventKind.CRASH, index),
                        Event(EventKind.STABLE_EXIT, index),
                    )
                )
                if slot.attempts:
                    events.append(Event(EventKind.WINDOW_ELAPSED, index))
            elif slot.phase is Phase.BACKOFF:
                events.append(Event(EventKind.BACKOFF_ELAPSED, index))
    else:
        for index, slot in enumerate(state.slots):
            if slot.phase in (Phase.STOPPING, Phase.KILLING):
                events.append(Event(EventKind.CHILD_EXITED, index))
        if (
            not state.terminate_grace_elapsed
            and any(slot.phase is Phase.STOPPING for slot in state.slots)
        ):
            events.append(Event(EventKind.TERMINATE_GRACE_EXPIRED))
        if (
            state.terminate_grace_elapsed
            and not state.kill_grace_elapsed
            and any(slot.phase is Phase.KILLING for slot in state.slots)
        ):
            events.append(Event(EventKind.KILL_GRACE_EXPIRED))
    return tuple(sorted(events, key=_event_key))


def _stop_slots(
    slots: tuple[Slot, ...],
    *,
    exited_slot: int | None = None,
    exited_attempts: int | None = None,
) -> tuple[Slot, ...]:
    stopped: list[Slot] = []
    for index, slot in enumerate(slots):
        if index == exited_slot:
            stopped.append(
                replace(
                    slot,
                    phase=Phase.STOPPED,
                    attempts=exited_attempts if exited_attempts is not None else slot.attempts,
                    backoff_seconds=0,
                )
            )
        elif slot.phase is Phase.RUNNING:
            stopped.append(replace(slot, phase=Phase.STOPPING, backoff_seconds=0))
        elif slot.phase is Phase.BACKOFF:
            stopped.append(replace(slot, phase=Phase.STOPPED, backoff_seconds=0))
        else:
            stopped.append(slot)
    return tuple(stopped)


def transition(state: SupervisorState, event: Event) -> SupervisorState:
    if event not in enabled_events(state):
        raise ModelCheckError(f"disabled transition: {event.label()}")

    slots = list(state.slots)
    if event.kind is EventKind.STOP_REQUESTED:
        return replace(
            state,
            stopping=True,
            operator_stop=True,
            slots=_stop_slots(state.slots),
        )

    if event.slot is not None:
        slot = slots[event.slot]
    else:
        slot = None

    if event.kind is EventKind.CRASH:
        assert event.slot is not None and slot is not None
        attempts = slot.attempts + 1
        if attempts > MODEL_MAX_RESTARTS:
            return replace(
                state,
                stopping=True,
                budget_exhausted=True,
                slots=_stop_slots(
                    state.slots,
                    exited_slot=event.slot,
                    exited_attempts=attempts,
                ),
            )
        slots[event.slot] = replace(
            slot,
            phase=Phase.BACKOFF,
            attempts=attempts,
            backoff_seconds=expected_backoff(attempts),
        )
        return replace(state, slots=tuple(slots))

    if event.kind is EventKind.STABLE_EXIT:
        assert event.slot is not None and slot is not None
        slots[event.slot] = replace(
            slot,
            phase=Phase.BACKOFF,
            attempts=1,
            backoff_seconds=expected_backoff(1),
        )
        return replace(state, slots=tuple(slots))

    if event.kind is EventKind.WINDOW_ELAPSED:
        assert event.slot is not None and slot is not None
        slots[event.slot] = replace(slot, attempts=0)
        return replace(state, slots=tuple(slots))

    if event.kind is EventKind.BACKOFF_ELAPSED:
        assert event.slot is not None and slot is not None
        slots[event.slot] = replace(
            slot,
            phase=Phase.RUNNING,
            backoff_seconds=0,
            generation=slot.generation + 1,
        )
        return replace(state, slots=tuple(slots))

    if event.kind is EventKind.CHILD_EXITED:
        assert event.slot is not None and slot is not None
        slots[event.slot] = replace(
            slot,
            phase=Phase.STOPPED,
            backoff_seconds=0,
        )
        return replace(state, slots=tuple(slots))

    if event.kind is EventKind.TERMINATE_GRACE_EXPIRED:
        slots = [
            replace(slot, phase=Phase.KILLING)
            if slot.phase is Phase.STOPPING
            else slot
            for slot in slots
        ]
        return replace(
            state,
            terminate_grace_elapsed=True,
            slots=tuple(slots),
        )

    if event.kind is EventKind.KILL_GRACE_EXPIRED:
        slots = [
            replace(slot, phase=Phase.STUCK)
            if slot.phase is Phase.KILLING
            else slot
            for slot in slots
        ]
        return replace(
            state,
            kill_grace_elapsed=True,
            kill_timeout=True,
            slots=tuple(slots),
        )

    raise ModelCheckError(f"unimplemented transition: {event.label()}")


def invariant_errors(state: SupervisorState) -> tuple[str, ...]:
    errors: list[str] = []
    if not 1 <= len(state.slots) <= MAX_MODEL_WORKERS:
        errors.append("worker_count_is_bounded")

    if (
        state.operator_stop
        or state.budget_exhausted
        or state.terminate_grace_elapsed
        or state.kill_grace_elapsed
        or state.kill_timeout
    ) and not state.stopping:
        errors.append("stop_flags_imply_stopping")

    if state.kill_grace_elapsed and not state.terminate_grace_elapsed:
        errors.append("kill_grace_follows_terminate_grace")
    if state.kill_timeout and not state.kill_grace_elapsed:
        errors.append("kill_timeout_follows_kill_grace")
    if state.kill_timeout and not any(
        slot.phase is Phase.STUCK for slot in state.slots
    ):
        errors.append("kill_timeout_has_stuck_child")

    if state.stopping and any(
        slot.phase is Phase.BACKOFF for slot in state.slots
    ):
        errors.append("no_pending_respawn_after_stop")
    if not state.stopping and any(
        slot.phase
        in (Phase.STOPPING, Phase.KILLING, Phase.STOPPED, Phase.STUCK)
        for slot in state.slots
    ):
        errors.append("shutdown_phase_requires_stop")

    live_children = sum(
        slot.phase
        in (Phase.RUNNING, Phase.STOPPING, Phase.KILLING, Phase.STUCK)
        for slot in state.slots
    )
    if live_children > MAX_MODEL_WORKERS:
        errors.append("at_most_three_live_children")

    for index, slot in enumerate(state.slots):
        if not 0 <= slot.attempts <= MODEL_MAX_RESTARTS + 1:
            errors.append(f"attempt_bound[{index + 1}]")
        if slot.generation < 0:
            errors.append(f"generation_nonnegative[{index + 1}]")
        if slot.phase is Phase.BACKOFF:
            if not 1 <= slot.attempts <= MODEL_MAX_RESTARTS:
                errors.append(f"backoff_has_admissible_attempt[{index + 1}]")
            elif slot.backoff_seconds != expected_backoff(slot.attempts):
                errors.append(f"backoff_is_exact_and_capped[{index + 1}]")
        elif slot.backoff_seconds != 0:
            errors.append(f"delay_only_in_backoff[{index + 1}]")

    return tuple(errors)


def is_terminal(state: SupervisorState) -> bool:
    return state.stopping and all(
        slot.phase in (Phase.STOPPED, Phase.STUCK) for slot in state.slots
    )


def explore(worker_count: int, depth: int = MODEL_DEPTH) -> ExplorationReceipt:
    initial = initial_state(worker_count)
    queue = deque([(initial, 0, ())])
    seen: dict[SupervisorState, int] = {initial: 0}
    transitions = 0
    terminal_states: set[SupervisorState] = set()
    clean_shutdown_witness: tuple[str, ...] = ()
    budget_exhaustion_witness: tuple[str, ...] = ()
    kill_timeout_witness: tuple[str, ...] = ()

    while queue:
        state, current_depth, trace = queue.popleft()
        errors = invariant_errors(state)
        if errors:
            raise ModelCheckError(
                f"invariant failure after {trace}: {', '.join(errors)}"
            )

        if is_terminal(state):
            terminal_states.add(state)
            labels = tuple(event.label() for event in trace)
            if (
                state.operator_stop
                and not state.kill_timeout
                and not clean_shutdown_witness
            ):
                clean_shutdown_witness = labels
            if state.budget_exhausted and not budget_exhaustion_witness:
                budget_exhaustion_witness = labels
            if state.kill_timeout and not kill_timeout_witness:
                kill_timeout_witness = labels

        if current_depth >= depth:
            continue

        for event in enabled_events(state):
            next_state = transition(state, event)
            transitions += 1

            if state.stopping:
                for before, after in zip(state.slots, next_state.slots):
                    if after.generation > before.generation or after.phase in (
                        Phase.RUNNING,
                        Phase.BACKOFF,
                    ):
                        raise ModelCheckError(
                            f"respawn after stop via {event.label()}"
                        )

            next_depth = current_depth + 1
            if next_state not in seen or next_depth < seen[next_state]:
                seen[next_state] = next_depth
                queue.append((next_state, next_depth, trace + (event,)))

    if not clean_shutdown_witness:
        raise ModelCheckError("no bounded clean-shutdown witness was reachable")
    if not budget_exhaustion_witness:
        raise ModelCheckError("no bounded restart-exhaustion witness was reachable")
    if not kill_timeout_witness:
        raise ModelCheckError("no bounded kill-timeout witness was reachable")

    return ExplorationReceipt(
        workers=worker_count,
        states=len(seen),
        transitions=transitions,
        terminal_states=len(terminal_states),
        maximum_depth=depth,
        clean_shutdown_witness=clean_shutdown_witness,
        budget_exhaustion_witness=budget_exhaustion_witness,
        kill_timeout_witness=kill_timeout_witness,
    )


def _expect_pool_error(callable_, expected_fragment: str) -> None:
    try:
        callable_()
    except Exception as error:
        if expected_fragment not in str(error):
            raise ModelCheckError(
                f"unexpected production error: {error}"
            ) from error
    else:
        raise ModelCheckError("production parser accepted an invalid value")


def refine_production() -> dict[str, int]:
    production = _load_production_module()
    vectors = 0

    for valid, expected in ((None, 3), ("1", 1), ("2", 2), ("3", 3)):
        actual = production.worker_count(valid)
        if actual != expected:
            raise ModelCheckError(
                f"worker_count({valid!r}) returned {actual}, expected {expected}"
            )
        vectors += 1

    for invalid in ("", "0", "4", "+1", " 1", "1 ", "1_0", "3.0", "three"):
        _expect_pool_error(
            lambda value=invalid: production.worker_count(value),
            "must be",
        )
        vectors += 1

    for valid in (None, "continuous-recovery", "worker_1", "a.b-c"):
        resolved = production.worker_id_prefix(valid)
        if not resolved:
            raise ModelCheckError("valid worker prefix resolved empty")
        vectors += 1

    for invalid in (
        "",
        " leading",
        "trailing ",
        ".hidden",
        "slash/value",
        "line\nbreak",
        "snowman-\u2603",
        "a" * 97,
    ):
        _expect_pool_error(
            lambda value=invalid: production.worker_id_prefix(value),
            "ARTIFACT_RECOVERY_WORKER_ID_PREFIX",
        )
        vectors += 1

    restart_vectors = 0
    for max_restarts in (0, 1, 2, 5):
        for max_backoff in (1, 2, 4, 60):
            budget = production.RestartBudget(
                max_restarts=max_restarts,
                window_seconds=60,
                max_backoff_seconds=max_backoff,
            )
            for offset in range(max_restarts + 2):
                allowed, delay, attempts = budget.record(0, 10.0 + offset)
                expected_attempts = offset + 1
                expected_allowed = expected_attempts <= max_restarts
                expected_delay = float(
                    min(max_backoff, 1 << max(0, expected_attempts - 1))
                )
                if (allowed, delay, attempts) != (
                    expected_allowed,
                    expected_delay,
                    expected_attempts,
                ):
                    raise ModelCheckError(
                        "production restart budget diverged from the model"
                    )
                restart_vectors += 1

    budget = production.RestartBudget(
        max_restarts=2,
        window_seconds=60,
        max_backoff_seconds=4,
    )
    budget.record(0, 1.0)
    allowed, delay, attempts = budget.record(0, 62.0)
    if (allowed, delay, attempts) != (True, 1.0, 1):
        raise ModelCheckError("sliding-window expiry did not reset attempts")
    restart_vectors += 1

    budget.record(0, 63.0)
    budget.reset(0)
    allowed, delay, attempts = budget.record(0, 64.0)
    if (allowed, delay, attempts) != (True, 1.0, 1):
        raise ModelCheckError("stable-runtime reset did not reset attempts")
    restart_vectors += 1

    return {
        "configuration_vectors": vectors,
        "restart_budget_vectors": restart_vectors,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_verification(depth: int = MODEL_DEPTH) -> dict[str, object]:
    explorations = [explore(workers, depth) for workers in range(1, 4)]
    refinement = refine_production()
    return {
        "schema_version": SCHEMA_VERSION,
        "claim": "bounded-exhaustive-safety-and-production-refinement",
        "bounds": {
            "workers": [1, 2, 3],
            "maximum_sequence_depth": depth,
            "maximum_restarts_per_active_window": MODEL_MAX_RESTARTS,
            "maximum_backoff_seconds": MODEL_MAX_BACKOFF_SECONDS,
        },
        "invariants": [
            "at-most-three-live-children",
            "one-child-generation-per-slot",
            "no-respawn-after-stop",
            "restart-budget-exhaustion-fails-closed",
            "backoff-is-exponential-and-capped",
            "stable-runtime-and-window-expiry-reset-crash-history",
            "terminate-grace-precedes-kill-grace",
            "kill-timeout-is-terminal-and-explicit",
        ],
        "exploration": [
            {
                "workers": item.workers,
                "states": item.states,
                "transitions": item.transitions,
                "terminal_states": item.terminal_states,
                "maximum_depth": item.maximum_depth,
                "clean_shutdown_witness": list(item.clean_shutdown_witness),
                "budget_exhaustion_witness": list(
                    item.budget_exhaustion_witness
                ),
                "kill_timeout_witness": list(item.kill_timeout_witness),
            }
            for item in explorations
        ],
        "refinement": refinement,
        "source": {
            "model_sha256": _sha256(Path(__file__).resolve()),
            "production_path": str(PRODUCTION_PATH.relative_to(ROOT)),
            "production_sha256": _sha256(PRODUCTION_PATH),
        },
        "non_claims": [
            "unbounded-liveness",
            "provider-source-completeness",
            "production-deployment",
            "credential-availability",
            "distributed-filesystem-lock-correctness",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--depth",
        type=int,
        default=MODEL_DEPTH,
        choices=range(7, 13),
    )
    parser.add_argument("--receipt", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    receipt = run_verification(args.depth)
    rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt is not None:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
