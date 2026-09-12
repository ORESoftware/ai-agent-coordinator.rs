#!/usr/bin/env python3
"""Deterministic recovery oracle for fenced fabrication workers and broker ACKs."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

MODEL_VERSION = "fabrication-recovery-protocol/v1"
FIXTURE_VERSION = "fabrication-recovery-fixture/v1"
MAX_BYTES = 512 * 1024
MAX_SCENARIOS = 64
MAX_STEPS = 160
PHASES = {"queued", "running", "failed", "completed", "dead-lettered"}
DIGEST = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{2,127}$")
FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
COMMAND_FIELDS = {
    "claim": {"kind", "fence", "worker_digest", "delivery_count"},
    "checkpoint": {
        "kind",
        "fence",
        "worker_digest",
        "sequence",
        "checkpoint_digest",
        "provider_task_digest",
        "artifact_ref_digest",
    },
    "complete": {
        "kind",
        "fence",
        "worker_digest",
        "final_artifact_digest",
    },
    "fail": {
        "kind",
        "fence",
        "worker_digest",
        "failure_code",
        "retryable",
    },
    "dead_letter": {"kind", "fence", "worker_digest", "dlq_digest"},
    "ack": {"kind", "terminal_digest"},
}


class ProtocolError(ValueError):
    pass


def _pairs(items: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ProtocolError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _expect_exact_keys(
    value: Mapping[str, Any], expected: set[str], field: str
) -> None:
    actual = set(value)
    if actual != expected:
        raise ProtocolError(
            f"{field} keys mismatch: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _expect_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ProtocolError(f"{field} has an invalid identifier")
    return value


def _expect_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ProtocolError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _optional_digest(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _expect_digest(value, field)


def _expect_positive(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProtocolError(f"{field} must be a positive integer")
    return value


def _expect_nonnegative(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError(f"{field} must be a non-negative integer")
    return value


def _expect_failure_code(value: Any, field: str) -> str:
    if not isinstance(value, str) or not FAILURE_CODE.fullmatch(value):
        raise ProtocolError(f"{field} has an invalid failure code")
    return value


@dataclass(frozen=True)
class JobState:
    model_version: str
    job_id: str
    message_id: str
    max_deliveries: int
    phase: str
    fence: int
    worker_digest: str | None
    delivery_count: int
    checkpoint_sequence: int
    checkpoint_digest: str | None
    provider_task_digest: str | None
    artifact_ref_digest: str | None
    final_artifact_digest: str | None
    failure_code: str | None
    retryable: bool | None
    dlq_digest: str | None
    acked: bool

    @classmethod
    def initial(
        cls,
        job_id: str,
        message_id: str,
        max_deliveries: int,
    ) -> "JobState":
        state = cls(
            model_version=MODEL_VERSION,
            job_id=_expect_identifier(job_id, "job_id"),
            message_id=_expect_identifier(message_id, "message_id"),
            max_deliveries=_expect_positive(max_deliveries, "max_deliveries"),
            phase="queued",
            fence=0,
            worker_digest=None,
            delivery_count=0,
            checkpoint_sequence=0,
            checkpoint_digest=None,
            provider_task_digest=None,
            artifact_ref_digest=None,
            final_artifact_digest=None,
            failure_code=None,
            retryable=None,
            dlq_digest=None,
            acked=False,
        )
        state.validate()
        return state

    def validate(self) -> None:
        if self.model_version != MODEL_VERSION:
            raise ProtocolError("state model_version is unsupported")
        _expect_identifier(self.job_id, "state.job_id")
        _expect_identifier(self.message_id, "state.message_id")
        _expect_positive(self.max_deliveries, "state.max_deliveries")
        if self.phase not in PHASES:
            raise ProtocolError(f"unsupported state phase: {self.phase}")
        _expect_nonnegative(self.fence, "state.fence")
        _expect_nonnegative(self.delivery_count, "state.delivery_count")
        _expect_nonnegative(
            self.checkpoint_sequence, "state.checkpoint_sequence"
        )
        if self.delivery_count > self.max_deliveries:
            raise ProtocolError("state delivery_count exceeds max_deliveries")
        if self.checkpoint_sequence == 0:
            if any(
                value is not None
                for value in (
                    self.checkpoint_digest,
                    self.provider_task_digest,
                    self.artifact_ref_digest,
                )
            ):
                raise ProtocolError(
                    "checkpoint metadata requires a positive checkpoint sequence"
                )
        else:
            _expect_digest(self.checkpoint_digest, "state.checkpoint_digest")
            _optional_digest(
                self.provider_task_digest, "state.provider_task_digest"
            )
            _optional_digest(
                self.artifact_ref_digest, "state.artifact_ref_digest"
            )
        if self.phase == "queued":
            if self.fence != 0 or self.worker_digest is not None:
                raise ProtocolError("queued state cannot have an active lease")
            if self.delivery_count != 0:
                raise ProtocolError("initial queued state must have zero deliveries")
            if any(
                value is not None
                for value in (
                    self.final_artifact_digest,
                    self.failure_code,
                    self.retryable,
                    self.dlq_digest,
                )
            ) or self.acked:
                raise ProtocolError("queued state contains terminal metadata")
            return
        _expect_positive(self.fence, "state.fence")
        _expect_positive(self.delivery_count, "state.delivery_count")
        _expect_digest(self.worker_digest, "state.worker_digest")
        if self.phase == "running":
            if any(
                value is not None
                for value in (
                    self.final_artifact_digest,
                    self.failure_code,
                    self.retryable,
                    self.dlq_digest,
                )
            ) or self.acked:
                raise ProtocolError("running state contains terminal metadata")
            return
        if self.phase == "failed":
            _expect_failure_code(self.failure_code, "state.failure_code")
            if not isinstance(self.retryable, bool):
                raise ProtocolError("failed state requires retryable boolean")
            if self.final_artifact_digest is not None or self.dlq_digest is not None:
                raise ProtocolError("failed state contains incompatible terminal data")
            if self.acked:
                raise ProtocolError("failed state cannot be broker-acked")
            return
        if self.phase == "completed":
            _expect_digest(
                self.final_artifact_digest, "state.final_artifact_digest"
            )
            if any(
                value is not None
                for value in (self.failure_code, self.retryable, self.dlq_digest)
            ):
                raise ProtocolError("completed state contains failure or DLQ data")
            return
        if self.phase == "dead-lettered":
            _expect_failure_code(self.failure_code, "state.failure_code")
            if not isinstance(self.retryable, bool):
                raise ProtocolError(
                    "dead-lettered state requires original retryable boolean"
                )
            _expect_digest(self.dlq_digest, "state.dlq_digest")
            if self.final_artifact_digest is not None:
                raise ProtocolError("dead-lettered state contains final artifact")

    def terminal_digest(self) -> str:
        self.validate()
        if self.phase not in {"completed", "dead-lettered"}:
            raise ProtocolError("terminal digest requires committed terminal state")
        return sha256(
            {
                "model_version": self.model_version,
                "job_id": self.job_id,
                "message_id": self.message_id,
                "phase": self.phase,
                "fence": self.fence,
                "delivery_count": self.delivery_count,
                "checkpoint_sequence": self.checkpoint_sequence,
                "checkpoint_digest": self.checkpoint_digest,
                "provider_task_digest": self.provider_task_digest,
                "artifact_ref_digest": self.artifact_ref_digest,
                "final_artifact_digest": self.final_artifact_digest,
                "failure_code": self.failure_code,
                "retryable": self.retryable,
                "dlq_digest": self.dlq_digest,
            }
        )


@dataclass(frozen=True)
class AttemptResult:
    ok: bool
    changed: bool
    state: JobState
    before_sha256: str
    command_sha256: str
    after_sha256: str
    terminal_digest: str | None
    error: str | None


def state_sha256(state: JobState) -> str:
    state.validate()
    return sha256(asdict(state))


def _parse_command(command: Mapping[str, Any]) -> str:
    if not isinstance(command, Mapping):
        raise ProtocolError("command must be an object")
    kind = command.get("kind")
    if not isinstance(kind, str) or kind not in COMMAND_FIELDS:
        raise ProtocolError("command kind is unsupported")
    _expect_exact_keys(command, COMMAND_FIELDS[kind], f"command.{kind}")
    return kind


def _require_owner(
    state: JobState,
    fence: Any,
    worker_digest: Any,
) -> tuple[int, str]:
    parsed_fence = _expect_positive(fence, "fence")
    parsed_worker = _expect_digest(worker_digest, "worker_digest")
    if state.phase == "queued":
        raise ProtocolError("queued job has no active owner")
    if parsed_fence != state.fence or parsed_worker != state.worker_digest:
        raise ProtocolError("stale or non-owning worker mutation")
    return parsed_fence, parsed_worker


def apply(state: JobState, command: Mapping[str, Any]) -> JobState:
    """Fully validate a command before returning one immutable replacement state."""
    state.validate()
    kind = _parse_command(command)

    if kind == "claim":
        fence = _expect_positive(command.get("fence"), "fence")
        worker = _expect_digest(command.get("worker_digest"), "worker_digest")
        delivery = _expect_positive(
            command.get("delivery_count"), "delivery_count"
        )
        if delivery > state.max_deliveries:
            raise ProtocolError("delivery_count exceeds max_deliveries")
        if state.phase == "queued":
            if fence <= state.fence or delivery <= state.delivery_count:
                raise ProtocolError("initial claim must advance fence and delivery")
            next_state = replace(
                state,
                phase="running",
                fence=fence,
                worker_digest=worker,
                delivery_count=delivery,
            )
        elif (
            state.phase == "running"
            and fence == state.fence
            and worker == state.worker_digest
            and delivery == state.delivery_count
        ):
            return state
        elif state.phase == "running":
            if fence <= state.fence or delivery <= state.delivery_count:
                raise ProtocolError(
                    "takeover must advance both fence and delivery_count"
                )
            next_state = replace(
                state,
                fence=fence,
                worker_digest=worker,
                delivery_count=delivery,
            )
        elif state.phase == "failed":
            if state.retryable is not True:
                raise ProtocolError("non-retryable failure cannot be reclaimed")
            if fence <= state.fence or delivery <= state.delivery_count:
                raise ProtocolError(
                    "retry claim must advance both fence and delivery_count"
                )
            next_state = replace(
                state,
                phase="running",
                fence=fence,
                worker_digest=worker,
                delivery_count=delivery,
                failure_code=None,
                retryable=None,
            )
        else:
            raise ProtocolError("terminal job cannot be reclaimed for execution")

    elif kind == "checkpoint":
        fence, worker = _require_owner(
            state, command.get("fence"), command.get("worker_digest")
        )
        if state.phase != "running":
            raise ProtocolError("checkpoint requires running state")
        sequence = _expect_positive(command.get("sequence"), "sequence")
        checkpoint = _expect_digest(
            command.get("checkpoint_digest"), "checkpoint_digest"
        )
        provider = _optional_digest(
            command.get("provider_task_digest"), "provider_task_digest"
        )
        artifact_ref = _optional_digest(
            command.get("artifact_ref_digest"), "artifact_ref_digest"
        )
        if sequence == state.checkpoint_sequence:
            if (
                checkpoint == state.checkpoint_digest
                and provider == state.provider_task_digest
                and artifact_ref == state.artifact_ref_digest
            ):
                return state
            raise ProtocolError("checkpoint sequence conflicts with durable data")
        if sequence <= state.checkpoint_sequence:
            raise ProtocolError("checkpoint sequence is stale")
        if sequence != state.checkpoint_sequence + 1:
            raise ProtocolError("checkpoint sequence must advance by exactly one")
        next_state = replace(
            state,
            fence=fence,
            worker_digest=worker,
            checkpoint_sequence=sequence,
            checkpoint_digest=checkpoint,
            provider_task_digest=provider,
            artifact_ref_digest=artifact_ref,
        )

    elif kind == "complete":
        fence, worker = _require_owner(
            state, command.get("fence"), command.get("worker_digest")
        )
        artifact = _expect_digest(
            command.get("final_artifact_digest"), "final_artifact_digest"
        )
        if (
            state.phase == "completed"
            and state.final_artifact_digest == artifact
            and fence == state.fence
            and worker == state.worker_digest
        ):
            return state
        if state.phase != "running":
            raise ProtocolError("completion requires running state")
        if state.checkpoint_sequence == 0:
            raise ProtocolError("completion requires at least one durable checkpoint")
        next_state = replace(
            state,
            phase="completed",
            final_artifact_digest=artifact,
        )

    elif kind == "fail":
        fence, worker = _require_owner(
            state, command.get("fence"), command.get("worker_digest")
        )
        failure = _expect_failure_code(
            command.get("failure_code"), "failure_code"
        )
        retryable = command.get("retryable")
        if not isinstance(retryable, bool):
            raise ProtocolError("retryable must be boolean")
        if (
            state.phase == "failed"
            and state.failure_code == failure
            and state.retryable is retryable
            and fence == state.fence
            and worker == state.worker_digest
        ):
            return state
        if state.phase != "running":
            raise ProtocolError("failure recording requires running state")
        next_state = replace(
            state,
            phase="failed",
            failure_code=failure,
            retryable=retryable,
        )

    elif kind == "dead_letter":
        fence, worker = _require_owner(
            state, command.get("fence"), command.get("worker_digest")
        )
        dlq = _expect_digest(command.get("dlq_digest"), "dlq_digest")
        if (
            state.phase == "dead-lettered"
            and state.dlq_digest == dlq
            and fence == state.fence
            and worker == state.worker_digest
        ):
            return state
        if state.phase != "failed":
            raise ProtocolError("dead-lettering requires committed failure state")
        if state.retryable is True and state.delivery_count < state.max_deliveries:
            raise ProtocolError(
                "retryable failure cannot enter DLQ before max deliveries"
            )
        next_state = replace(
            state,
            phase="dead-lettered",
            dlq_digest=dlq,
        )

    elif kind == "ack":
        expected = _expect_digest(
            command.get("terminal_digest"), "terminal_digest"
        )
        actual = state.terminal_digest()
        if expected != actual:
            raise ProtocolError("broker ACK does not match committed terminal state")
        if state.acked:
            return state
        next_state = replace(state, acked=True)

    else:  # pragma: no cover - command parser rejects unknown kinds.
        raise ProtocolError(f"unsupported command kind: {kind}")

    next_state.validate()
    return next_state


def attempt(state: JobState, command: Mapping[str, Any]) -> AttemptResult:
    before = state_sha256(state)
    command_digest = sha256(command)
    try:
        next_state = apply(state, command)
    except ProtocolError as exc:
        return AttemptResult(
            ok=False,
            changed=False,
            state=state,
            before_sha256=before,
            command_sha256=command_digest,
            after_sha256=before,
            terminal_digest=None,
            error=str(exc),
        )
    after = state_sha256(next_state)
    terminal = (
        next_state.terminal_digest()
        if next_state.phase in {"completed", "dead-lettered"}
        else None
    )
    return AttemptResult(
        ok=True,
        changed=after != before,
        state=next_state,
        before_sha256=before,
        command_sha256=command_digest,
        after_sha256=after,
        terminal_digest=terminal,
        error=None,
    )


def load_fixture(path: str | Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    if len(raw) > MAX_BYTES:
        raise ProtocolError(f"fixture exceeds {MAX_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("fixture must be UTF-8") from exc
    try:
        value = json.loads(text, object_pairs_hook=_pairs)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid fixture JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ProtocolError("fixture root must be an object")
    return value


def run_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    _expect_exact_keys(value, {"schema_version", "scenarios"}, "fixture")
    if value.get("schema_version") != FIXTURE_VERSION:
        raise ProtocolError("fixture schema_version is unsupported")
    scenarios = value.get("scenarios")
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= MAX_SCENARIOS:
        raise ProtocolError("fixture scenarios must be a bounded non-empty array")
    receipts: list[dict[str, Any]] = []
    for scenario_index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            raise ProtocolError(f"scenario {scenario_index} must be an object")
        _expect_exact_keys(
            scenario,
            {"id", "initial", "steps", "expected_final_phase", "expected_acked"},
            f"scenario[{scenario_index}]",
        )
        scenario_id = _expect_identifier(
            scenario.get("id"), f"scenario[{scenario_index}].id"
        )
        initial = scenario.get("initial")
        if not isinstance(initial, dict):
            raise ProtocolError(f"scenario {scenario_id} initial must be an object")
        _expect_exact_keys(
            initial,
            {"job_id", "message_id", "max_deliveries"},
            f"scenario.{scenario_id}.initial",
        )
        state = JobState.initial(
            initial["job_id"], initial["message_id"], initial["max_deliveries"]
        )
        steps = scenario.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
            raise ProtocolError(f"scenario {scenario_id} has invalid steps")
        step_receipts: list[dict[str, Any]] = []
        for step_index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ProtocolError(
                    f"scenario {scenario_id} step {step_index} must be an object"
                )
            _expect_exact_keys(
                step,
                {"command", "terminal_digest_from_state", "expect_ok", "expect_changed", "error_contains"},
                f"scenario.{scenario_id}.steps[{step_index}]",
            )
            command = step.get("command")
            if not isinstance(command, dict):
                raise ProtocolError(
                    f"scenario {scenario_id} step {step_index} command must be an object"
                )
            command = dict(command)
            use_terminal = step.get("terminal_digest_from_state")
            if not isinstance(use_terminal, bool):
                raise ProtocolError("terminal_digest_from_state must be boolean")
            if use_terminal:
                if command.get("kind") != "ack":
                    raise ProtocolError(
                        "terminal_digest_from_state is valid only for ACK commands"
                    )
                command["terminal_digest"] = state.terminal_digest()
            result = attempt(state, command)
            if step.get("expect_ok") is not result.ok:
                raise ProtocolError(
                    f"scenario {scenario_id} step {step_index} ok mismatch"
                )
            if step.get("expect_changed") is not result.changed:
                raise ProtocolError(
                    f"scenario {scenario_id} step {step_index} change mismatch"
                )
            error_contains = step.get("error_contains")
            if error_contains is not None:
                if not isinstance(error_contains, str) or not error_contains:
                    raise ProtocolError("error_contains must be null or non-empty text")
                if result.error is None or error_contains not in result.error:
                    raise ProtocolError(
                        f"scenario {scenario_id} step {step_index} error mismatch"
                    )
            elif result.error is not None:
                raise ProtocolError(
                    f"scenario {scenario_id} step {step_index} had unexpected error"
                )
            state = result.state
            step_receipts.append(
                {
                    "ok": result.ok,
                    "changed": result.changed,
                    "before_sha256": result.before_sha256,
                    "command_sha256": result.command_sha256,
                    "after_sha256": result.after_sha256,
                    "terminal_digest": result.terminal_digest,
                    "error": result.error,
                }
            )
        expected_phase = scenario.get("expected_final_phase")
        if expected_phase not in PHASES or state.phase != expected_phase:
            raise ProtocolError(
                f"scenario {scenario_id} final phase mismatch: "
                f"expected {expected_phase}, got {state.phase}"
            )
        expected_acked = scenario.get("expected_acked")
        if not isinstance(expected_acked, bool) or state.acked is not expected_acked:
            raise ProtocolError(
                f"scenario {scenario_id} final ACK state mismatch"
            )
        receipts.append(
            {
                "scenario_id": scenario_id,
                "final_phase": state.phase,
                "final_acked": state.acked,
                "final_state_sha256": state_sha256(state),
                "terminal_digest": (
                    state.terminal_digest()
                    if state.phase in {"completed", "dead-lettered"}
                    else None
                ),
                "steps": step_receipts,
            }
        )
    return {
        "model_version": MODEL_VERSION,
        "fixture_version": FIXTURE_VERSION,
        "scenario_count": len(receipts),
        "receipt_sha256": sha256(receipts),
        "scenarios": receipts,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("fixture", type=Path)
    result.add_argument("--json", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = run_fixture(load_fixture(args.fixture))
    except (ProtocolError, OSError) as exc:
        if args.json:
            print(json.dumps({"valid": False, "errors": [str(exc)]}, sort_keys=True))
        else:
            print(f"fabrication recovery validation failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"valid": True, **report}, sort_keys=True))
    else:
        print("valid=true")
        print(f"scenarios={report['scenario_count']}")
        print(f"receipt_sha256={report['receipt_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
