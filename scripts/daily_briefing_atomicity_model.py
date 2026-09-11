#!/usr/bin/env python3
"""Deterministic reference model for failure-atomic daily briefing delivery."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

MODEL_VERSION = "daily-briefing-atomicity/v1"
FIXTURE_VERSION = "daily-briefing-atomicity-fixture/v1"
MAX_BYTES = 256 * 1024
MAX_SCENARIOS = 64
MAX_STEPS = 128
DIGEST = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{2,127}$")
PHASES = {
    "pending",
    "claimed",
    "composed",
    "delivery_started",
    "ambiguous",
    "delivered",
}
COMMAND_FIELDS = {
    "claim": {"kind", "fence", "owner_digest"},
    "compose": {"kind", "fence", "input_digest", "composition_digest"},
    "begin_delivery": {"kind", "fence", "destination_digest", "attempt_id"},
    "mark_ambiguous": {"kind", "fence", "attempt_id"},
    "record_receipt": {"kind", "fence", "attempt_id", "receipt_digest"},
    "takeover": {"kind", "fence", "owner_digest"},
    "prove_absent": {
        "kind",
        "fence",
        "attempt_id",
        "reconciliation_digest",
    },
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


def _expect_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ProtocolError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _expect_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ProtocolError(f"{field} has an invalid identifier")
    return value


def _expect_fence(value: Any, field: str = "fence") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProtocolError(f"{field} must be a positive integer")
    return value


def _expect_exact_keys(
    value: Mapping[str, Any], expected: set[str], field: str
) -> None:
    actual = set(value)
    if actual != expected:
        raise ProtocolError(
            f"{field} keys mismatch: "
            f"missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


@dataclass(frozen=True)
class DeliveryState:
    model_version: str
    run_id: str
    input_digest: str
    policy_digest: str
    phase: str
    fence: int
    owner_digest: str | None
    composition_digest: str | None
    destination_digest: str | None
    attempt_id: str | None
    receipt_digest: str | None
    reconciled_attempt_id: str | None
    reconciliation_digest: str | None

    @classmethod
    def initial(
        cls, run_id: str, input_digest: str, policy_digest: str
    ) -> "DeliveryState":
        state = cls(
            model_version=MODEL_VERSION,
            run_id=_expect_identifier(run_id, "run_id"),
            input_digest=_expect_digest(input_digest, "input_digest"),
            policy_digest=_expect_digest(policy_digest, "policy_digest"),
            phase="pending",
            fence=0,
            owner_digest=None,
            composition_digest=None,
            destination_digest=None,
            attempt_id=None,
            receipt_digest=None,
            reconciled_attempt_id=None,
            reconciliation_digest=None,
        )
        state.validate()
        return state

    def validate(self) -> None:
        if self.model_version != MODEL_VERSION:
            raise ProtocolError("state model_version is unsupported")
        _expect_identifier(self.run_id, "state.run_id")
        _expect_digest(self.input_digest, "state.input_digest")
        _expect_digest(self.policy_digest, "state.policy_digest")
        if self.phase not in PHASES:
            raise ProtocolError(f"unsupported phase: {self.phase}")
        if isinstance(self.fence, bool) or not isinstance(self.fence, int):
            raise ProtocolError("state.fence must be an integer")
        if self.phase == "pending":
            if self.fence != 0 or self.owner_digest is not None:
                raise ProtocolError("pending state cannot have an owner or fence")
            if any(
                value is not None
                for value in (
                    self.composition_digest,
                    self.destination_digest,
                    self.attempt_id,
                    self.receipt_digest,
                    self.reconciled_attempt_id,
                    self.reconciliation_digest,
                )
            ):
                raise ProtocolError("pending state contains delivery data")
            return
        _expect_fence(self.fence, "state.fence")
        if self.owner_digest is None:
            raise ProtocolError("non-pending state must have an owner")
        _expect_digest(self.owner_digest, "state.owner_digest")
        if (self.reconciled_attempt_id is None) != (
            self.reconciliation_digest is None
        ):
            raise ProtocolError("reconciliation identity and digest must be paired")
        if self.reconciled_attempt_id is not None:
            _expect_identifier(
                self.reconciled_attempt_id, "state.reconciled_attempt_id"
            )
            _expect_digest(
                self.reconciliation_digest, "state.reconciliation_digest"
            )
        if self.phase == "claimed":
            if any(
                value is not None
                for value in (
                    self.composition_digest,
                    self.destination_digest,
                    self.attempt_id,
                    self.receipt_digest,
                    self.reconciled_attempt_id,
                    self.reconciliation_digest,
                )
            ):
                raise ProtocolError("claimed state contains composed or delivery data")
            return
        _expect_digest(self.composition_digest, "state.composition_digest")
        if self.phase == "composed":
            if any(
                value is not None
                for value in (
                    self.destination_digest,
                    self.attempt_id,
                    self.receipt_digest,
                )
            ):
                raise ProtocolError("composed state contains an active delivery")
            return
        _expect_digest(self.destination_digest, "state.destination_digest")
        _expect_identifier(self.attempt_id, "state.attempt_id")
        if self.phase in {"delivery_started", "ambiguous"}:
            if self.receipt_digest is not None:
                raise ProtocolError(
                    f"{self.phase} state cannot contain a delivery receipt"
                )
            return
        if self.phase == "delivered":
            _expect_digest(self.receipt_digest, "state.receipt_digest")


@dataclass(frozen=True)
class AttemptResult:
    ok: bool
    changed: bool
    state: DeliveryState
    before_sha256: str
    command_sha256: str
    after_sha256: str
    error: str | None


def state_sha256(state: DeliveryState) -> str:
    state.validate()
    return sha256(asdict(state))


def _command(command: Mapping[str, Any]) -> tuple[str, int]:
    if not isinstance(command, Mapping):
        raise ProtocolError("command must be an object")
    kind = command.get("kind")
    if not isinstance(kind, str) or kind not in COMMAND_FIELDS:
        raise ProtocolError("command kind is unsupported")
    _expect_exact_keys(command, COMMAND_FIELDS[kind], f"command.{kind}")
    return kind, _expect_fence(command.get("fence"))


def _require_fence(state: DeliveryState, fence: int) -> None:
    if fence != state.fence:
        raise ProtocolError(
            f"stale or future fence: expected {state.fence}, got {fence}"
        )


def apply(state: DeliveryState, command: Mapping[str, Any]) -> DeliveryState:
    """Validate completely, then return one immutable replacement state."""
    state.validate()
    kind, fence = _command(command)

    if kind == "claim":
        owner = _expect_digest(command.get("owner_digest"), "owner_digest")
        if state.phase == "pending":
            next_state = replace(
                state,
                phase="claimed",
                fence=fence,
                owner_digest=owner,
            )
        elif fence == state.fence and owner == state.owner_digest:
            return state
        else:
            raise ProtocolError("run is already claimed; use takeover")

    elif kind == "takeover":
        owner = _expect_digest(command.get("owner_digest"), "owner_digest")
        if state.phase == "pending":
            raise ProtocolError("pending run must be claimed, not taken over")
        if fence <= state.fence:
            raise ProtocolError("takeover fence must be newer than current fence")
        phase = "ambiguous" if state.phase == "delivery_started" else state.phase
        next_state = replace(
            state,
            phase=phase,
            fence=fence,
            owner_digest=owner,
        )

    elif kind == "compose":
        _require_fence(state, fence)
        input_digest = _expect_digest(command.get("input_digest"), "input_digest")
        composition = _expect_digest(
            command.get("composition_digest"), "composition_digest"
        )
        if input_digest != state.input_digest:
            raise ProtocolError("composition input does not match run input")
        if state.phase == "claimed":
            next_state = replace(
                state,
                phase="composed",
                composition_digest=composition,
            )
        elif state.phase in {
            "composed",
            "delivery_started",
            "ambiguous",
            "delivered",
        } and state.composition_digest == composition:
            return state
        else:
            raise ProtocolError("composition conflicts with durable state")

    elif kind == "begin_delivery":
        _require_fence(state, fence)
        destination = _expect_digest(
            command.get("destination_digest"), "destination_digest"
        )
        attempt_id = _expect_identifier(command.get("attempt_id"), "attempt_id")
        if state.reconciled_attempt_id == attempt_id:
            raise ProtocolError("a reconciled-absent attempt ID cannot be reused")
        if state.phase == "composed":
            next_state = replace(
                state,
                phase="delivery_started",
                destination_digest=destination,
                attempt_id=attempt_id,
                receipt_digest=None,
            )
        elif state.phase in {
            "delivery_started",
            "ambiguous",
            "delivered",
        } and (
            state.destination_digest == destination
            and state.attempt_id == attempt_id
        ):
            return state
        else:
            raise ProtocolError("another delivery attempt is already durable")

    elif kind == "mark_ambiguous":
        _require_fence(state, fence)
        attempt_id = _expect_identifier(command.get("attempt_id"), "attempt_id")
        if state.attempt_id != attempt_id:
            raise ProtocolError("ambiguous marker does not match active attempt")
        if state.phase == "delivery_started":
            next_state = replace(state, phase="ambiguous")
        elif state.phase == "ambiguous":
            return state
        else:
            raise ProtocolError("only an in-flight delivery can become ambiguous")

    elif kind == "record_receipt":
        _require_fence(state, fence)
        attempt_id = _expect_identifier(command.get("attempt_id"), "attempt_id")
        receipt = _expect_digest(command.get("receipt_digest"), "receipt_digest")
        if state.attempt_id != attempt_id:
            raise ProtocolError("receipt does not match active attempt")
        if state.phase in {"delivery_started", "ambiguous"}:
            next_state = replace(
                state,
                phase="delivered",
                receipt_digest=receipt,
            )
        elif state.phase == "delivered" and state.receipt_digest == receipt:
            return state
        else:
            raise ProtocolError("receipt conflicts with durable delivery state")

    elif kind == "prove_absent":
        _require_fence(state, fence)
        attempt_id = _expect_identifier(command.get("attempt_id"), "attempt_id")
        evidence = _expect_digest(
            command.get("reconciliation_digest"), "reconciliation_digest"
        )
        if state.phase == "ambiguous" and state.attempt_id == attempt_id:
            next_state = replace(
                state,
                phase="composed",
                destination_digest=None,
                attempt_id=None,
                receipt_digest=None,
                reconciled_attempt_id=attempt_id,
                reconciliation_digest=evidence,
            )
        elif (
            state.phase == "composed"
            and state.reconciled_attempt_id == attempt_id
            and state.reconciliation_digest == evidence
        ):
            return state
        else:
            raise ProtocolError(
                "absence proof must match the current ambiguous attempt"
            )

    else:  # pragma: no cover - _command rejects unknown kinds.
        raise ProtocolError(f"unsupported command kind: {kind}")

    next_state.validate()
    return next_state


def attempt(state: DeliveryState, command: Mapping[str, Any]) -> AttemptResult:
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
            error=str(exc),
        )
    after = state_sha256(next_state)
    return AttemptResult(
        ok=True,
        changed=after != before,
        state=next_state,
        before_sha256=before,
        command_sha256=command_digest,
        after_sha256=after,
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
            {"id", "initial", "steps", "expected_final_phase"},
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
            {"run_id", "input_digest", "policy_digest"},
            f"scenario.{scenario_id}.initial",
        )
        state = DeliveryState.initial(
            initial["run_id"], initial["input_digest"], initial["policy_digest"]
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
                {"command", "expect_ok", "expect_changed", "error_contains"},
                f"scenario.{scenario_id}.steps[{step_index}]",
            )
            command = step.get("command")
            if not isinstance(command, dict):
                raise ProtocolError(
                    f"scenario {scenario_id} step {step_index} command must be an object"
                )
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
                    "error": result.error,
                }
            )
        expected_phase = scenario.get("expected_final_phase")
        if expected_phase not in PHASES or state.phase != expected_phase:
            raise ProtocolError(
                f"scenario {scenario_id} final phase mismatch: "
                f"expected {expected_phase}, got {state.phase}"
            )
        receipts.append(
            {
                "scenario_id": scenario_id,
                "final_phase": state.phase,
                "final_state_sha256": state_sha256(state),
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
            print(f"daily briefing atomicity validation failed: {exc}", file=sys.stderr)
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
