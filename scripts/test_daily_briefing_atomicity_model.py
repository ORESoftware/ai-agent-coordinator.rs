#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("daily_briefing_atomicity_model.py")
SPEC = importlib.util.spec_from_file_location(
    "daily_briefing_atomicity_model", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "daily-briefing-atomicity-v1.json"
A = "a" * 64
B = "b" * 64
C = "c" * 64
D = "d" * 64
E = "e" * 64
F = "f" * 64
ONE = "1" * 64
TWO = "2" * 64


class DailyBriefingAtomicityModelTests(unittest.TestCase):
    def initial(self) -> object:
        return MODULE.DeliveryState.initial("briefing-test-run", A, B)

    def claim(self, state: object, fence: int = 1, owner: str = C) -> object:
        return MODULE.apply(
            state,
            {"kind": "claim", "fence": fence, "owner_digest": owner},
        )

    def compose(self, state: object, fence: int = 1, digest: str = D) -> object:
        return MODULE.apply(
            state,
            {
                "kind": "compose",
                "fence": fence,
                "input_digest": A,
                "composition_digest": digest,
            },
        )

    def start(self, state: object, fence: int = 1, attempt: str = "attempt-001") -> object:
        return MODULE.apply(
            state,
            {
                "kind": "begin_delivery",
                "fence": fence,
                "destination_digest": E,
                "attempt_id": attempt,
            },
        )

    def test_checked_in_scenarios_pass(self) -> None:
        report = MODULE.run_fixture(MODULE.load_fixture(FIXTURE))
        self.assertEqual(report["model_version"], MODULE.MODEL_VERSION)
        self.assertEqual(report["scenario_count"], 10)
        self.assertRegex(report["receipt_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            [item["final_phase"] for item in report["scenarios"]],
            [
                "delivered",
                "delivered",
                "composed",
                "delivered",
                "delivered",
                "ambiguous",
                "delivery_started",
                "delivered",
                "composed",
                "delivery_started",
            ],
        )

    def test_initial_state_is_canonical_and_valid(self) -> None:
        state = self.initial()
        state.validate()
        self.assertEqual(state.phase, "pending")
        self.assertEqual(state.fence, 0)
        self.assertRegex(MODULE.state_sha256(state), r"^[0-9a-f]{64}$")

    def test_failed_command_preserves_identical_state_hash(self) -> None:
        state = self.claim(self.initial(), fence=4)
        result = MODULE.attempt(
            state,
            {
                "kind": "compose",
                "fence": 3,
                "input_digest": A,
                "composition_digest": D,
            },
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.changed)
        self.assertIs(result.state, state)
        self.assertEqual(result.before_sha256, result.after_sha256)
        self.assertIn("stale or future fence", result.error or "")

    def test_wrong_input_digest_is_failure_atomic(self) -> None:
        state = self.claim(self.initial())
        result = MODULE.attempt(
            state,
            {
                "kind": "compose",
                "fence": 1,
                "input_digest": F,
                "composition_digest": D,
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.before_sha256, result.after_sha256)
        self.assertEqual(result.state.phase, "claimed")

    def test_claim_retry_is_idempotent(self) -> None:
        state = self.claim(self.initial(), fence=9)
        retried = MODULE.attempt(
            state,
            {"kind": "claim", "fence": 9, "owner_digest": C},
        )
        self.assertTrue(retried.ok)
        self.assertFalse(retried.changed)
        self.assertEqual(retried.before_sha256, retried.after_sha256)

    def test_compose_retry_is_idempotent(self) -> None:
        state = self.compose(self.claim(self.initial()))
        retried = MODULE.attempt(
            state,
            {
                "kind": "compose",
                "fence": 1,
                "input_digest": A,
                "composition_digest": D,
            },
        )
        self.assertTrue(retried.ok)
        self.assertFalse(retried.changed)

    def test_delivery_retry_is_idempotent(self) -> None:
        state = self.start(self.compose(self.claim(self.initial())))
        retried = MODULE.attempt(
            state,
            {
                "kind": "begin_delivery",
                "fence": 1,
                "destination_digest": E,
                "attempt_id": "attempt-001",
            },
        )
        self.assertTrue(retried.ok)
        self.assertFalse(retried.changed)

    def test_receipt_retry_is_idempotent(self) -> None:
        state = self.start(self.compose(self.claim(self.initial())))
        state = MODULE.apply(
            state,
            {
                "kind": "record_receipt",
                "fence": 1,
                "attempt_id": "attempt-001",
                "receipt_digest": F,
            },
        )
        retried = MODULE.attempt(
            state,
            {
                "kind": "record_receipt",
                "fence": 1,
                "attempt_id": "attempt-001",
                "receipt_digest": F,
            },
        )
        self.assertTrue(retried.ok)
        self.assertFalse(retried.changed)
        self.assertEqual(retried.state.phase, "delivered")

    def test_takeover_requires_newer_fence(self) -> None:
        state = self.claim(self.initial(), fence=5)
        result = MODULE.attempt(
            state,
            {"kind": "takeover", "fence": 5, "owner_digest": ONE},
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.changed)
        self.assertIn("newer", result.error or "")

    def test_takeover_of_inflight_send_forces_ambiguity(self) -> None:
        state = self.start(self.compose(self.claim(self.initial())))
        state = MODULE.apply(
            state,
            {"kind": "takeover", "fence": 2, "owner_digest": ONE},
        )
        self.assertEqual(state.phase, "ambiguous")
        self.assertEqual(state.fence, 2)
        self.assertEqual(state.attempt_id, "attempt-001")

    def test_positive_reconciliation_reaches_delivered(self) -> None:
        state = self.start(self.compose(self.claim(self.initial())))
        state = MODULE.apply(
            state,
            {"kind": "mark_ambiguous", "fence": 1, "attempt_id": "attempt-001"},
        )
        state = MODULE.apply(
            state,
            {
                "kind": "record_receipt",
                "fence": 1,
                "attempt_id": "attempt-001",
                "receipt_digest": F,
            },
        )
        self.assertEqual(state.phase, "delivered")

    def test_absence_proof_allows_new_attempt_only(self) -> None:
        state = self.start(self.compose(self.claim(self.initial())))
        state = MODULE.apply(
            state,
            {"kind": "mark_ambiguous", "fence": 1, "attempt_id": "attempt-001"},
        )
        state = MODULE.apply(
            state,
            {
                "kind": "prove_absent",
                "fence": 1,
                "attempt_id": "attempt-001",
                "reconciliation_digest": TWO,
            },
        )
        self.assertEqual(state.phase, "composed")
        self.assertEqual(state.reconciled_attempt_id, "attempt-001")
        rejected = MODULE.attempt(
            state,
            {
                "kind": "begin_delivery",
                "fence": 1,
                "destination_digest": E,
                "attempt_id": "attempt-001",
            },
        )
        self.assertFalse(rejected.ok)
        accepted = MODULE.attempt(
            state,
            {
                "kind": "begin_delivery",
                "fence": 1,
                "destination_digest": E,
                "attempt_id": "attempt-002",
            },
        )
        self.assertTrue(accepted.ok)
        self.assertEqual(accepted.state.phase, "delivery_started")

    def test_conflicting_composition_is_atomic(self) -> None:
        state = self.compose(self.claim(self.initial()))
        result = MODULE.attempt(
            state,
            {
                "kind": "compose",
                "fence": 1,
                "input_digest": A,
                "composition_digest": F,
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.state.composition_digest, D)
        self.assertEqual(result.before_sha256, result.after_sha256)

    def test_wrong_receipt_attempt_is_atomic(self) -> None:
        state = self.start(self.compose(self.claim(self.initial())))
        result = MODULE.attempt(
            state,
            {
                "kind": "record_receipt",
                "fence": 1,
                "attempt_id": "attempt-wrong",
                "receipt_digest": F,
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.state.phase, "delivery_started")
        self.assertEqual(result.before_sha256, result.after_sha256)

    def test_unknown_command_kind_fails(self) -> None:
        result = MODULE.attempt(
            self.initial(),
            {"kind": "send_now", "fence": 1},
        )
        self.assertFalse(result.ok)
        self.assertIn("unsupported", result.error or "")

    def test_extra_command_field_fails(self) -> None:
        result = MODULE.attempt(
            self.initial(),
            {
                "kind": "claim",
                "fence": 1,
                "owner_digest": C,
                "unexpected": True,
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("keys mismatch", result.error or "")

    def test_boolean_fence_is_rejected(self) -> None:
        result = MODULE.attempt(
            self.initial(),
            {"kind": "claim", "fence": True, "owner_digest": C},
        )
        self.assertFalse(result.ok)
        self.assertIn("positive integer", result.error or "")

    def test_invalid_digest_is_rejected(self) -> None:
        with self.assertRaisesRegex(MODULE.ProtocolError, "SHA-256"):
            MODULE.DeliveryState.initial("briefing-test-run", "not-a-digest", B)

    def test_invalid_state_invariant_is_rejected(self) -> None:
        state = replace(self.initial(), phase="claimed", fence=1, owner_digest=None)
        with self.assertRaisesRegex(MODULE.ProtocolError, "must have an owner"):
            state.validate()

    def test_state_digest_is_field_order_invariant(self) -> None:
        state = self.compose(self.claim(self.initial()))
        payload = MODULE.asdict(state)
        reordered = dict(reversed(list(payload.items())))
        self.assertEqual(MODULE.sha256(payload), MODULE.sha256(reordered))

    def test_duplicate_fixture_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.json"
            path.write_text('{"schema_version":"a","schema_version":"b"}\n')
            with self.assertRaisesRegex(MODULE.ProtocolError, "duplicate JSON key"):
                MODULE.load_fixture(path)

    def test_fixture_schema_mismatch_fails(self) -> None:
        value = MODULE.load_fixture(FIXTURE)
        value["schema_version"] = "future-version"
        with self.assertRaisesRegex(MODULE.ProtocolError, "unsupported"):
            MODULE.run_fixture(value)

    def test_fixture_expected_error_is_verified(self) -> None:
        value = MODULE.load_fixture(FIXTURE)
        value = copy.deepcopy(value)
        value["scenarios"][2]["steps"][1]["error_contains"] = "wrong fragment"
        with self.assertRaisesRegex(MODULE.ProtocolError, "error mismatch"):
            MODULE.run_fixture(value)

    def test_fixture_expected_phase_is_verified(self) -> None:
        value = MODULE.load_fixture(FIXTURE)
        value = copy.deepcopy(value)
        value["scenarios"][0]["expected_final_phase"] = "composed"
        with self.assertRaisesRegex(MODULE.ProtocolError, "final phase mismatch"):
            MODULE.run_fixture(value)

    def test_fixture_receipt_is_deterministic(self) -> None:
        value = MODULE.load_fixture(FIXTURE)
        first = MODULE.run_fixture(value)
        second = MODULE.run_fixture(copy.deepcopy(value))
        self.assertEqual(first["receipt_sha256"], second["receipt_sha256"])
        self.assertEqual(first["scenarios"], second["scenarios"])


if __name__ == "__main__":
    unittest.main()
