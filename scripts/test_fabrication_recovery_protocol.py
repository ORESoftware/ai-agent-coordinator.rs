#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("fabrication_recovery_protocol.py")
SPEC = importlib.util.spec_from_file_location(
    "fabrication_recovery_protocol", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "fabrication-recovery-protocol-v1.json"
)
WORKER_A = "1" * 64
WORKER_B = "7" * 64
CHECKPOINT_A = "2" * 64
PROVIDER = "3" * 64
CHECKPOINT_B = "4" * 64
ARTIFACT_REF = "5" * 64
FINAL_ARTIFACT = "6" * 64
DLQ = "8" * 64
WRONG = "9" * 64


class FabricationRecoveryProtocolTests(unittest.TestCase):
    def initial(self, max_deliveries: int = 3) -> object:
        return MODULE.JobState.initial(
            "fabrication-job-test",
            "fabrication.message.test",
            max_deliveries,
        )

    def claim(
        self,
        state: object,
        fence: int = 1,
        worker: str = WORKER_A,
        delivery: int = 1,
    ) -> object:
        return MODULE.apply(
            state,
            {
                "kind": "claim",
                "fence": fence,
                "worker_digest": worker,
                "delivery_count": delivery,
            },
        )

    def checkpoint(
        self,
        state: object,
        fence: int = 1,
        worker: str = WORKER_A,
        sequence: int = 1,
        digest: str = CHECKPOINT_A,
        provider: str | None = PROVIDER,
        artifact_ref: str | None = None,
    ) -> object:
        return MODULE.apply(
            state,
            {
                "kind": "checkpoint",
                "fence": fence,
                "worker_digest": worker,
                "sequence": sequence,
                "checkpoint_digest": digest,
                "provider_task_digest": provider,
                "artifact_ref_digest": artifact_ref,
            },
        )

    def complete(
        self,
        state: object,
        fence: int = 1,
        worker: str = WORKER_A,
        artifact: str = FINAL_ARTIFACT,
    ) -> object:
        return MODULE.apply(
            state,
            {
                "kind": "complete",
                "fence": fence,
                "worker_digest": worker,
                "final_artifact_digest": artifact,
            },
        )

    def fail(
        self,
        state: object,
        retryable: bool,
        fence: int = 1,
        worker: str = WORKER_A,
        code: str = "provider_timeout",
    ) -> object:
        return MODULE.apply(
            state,
            {
                "kind": "fail",
                "fence": fence,
                "worker_digest": worker,
                "failure_code": code,
                "retryable": retryable,
            },
        )

    def dead_letter(
        self,
        state: object,
        fence: int = 1,
        worker: str = WORKER_A,
    ) -> object:
        return MODULE.apply(
            state,
            {
                "kind": "dead_letter",
                "fence": fence,
                "worker_digest": worker,
                "dlq_digest": DLQ,
            },
        )

    def ack(self, state: object, digest: str | None = None) -> object:
        terminal = digest if digest is not None else state.terminal_digest()
        return MODULE.apply(
            state,
            {"kind": "ack", "terminal_digest": terminal},
        )

    def test_checked_in_recovery_scenarios_pass(self) -> None:
        report = MODULE.run_fixture(MODULE.load_fixture(FIXTURE))
        self.assertEqual(report["model_version"], MODULE.MODEL_VERSION)
        self.assertEqual(report["scenario_count"], 13)
        self.assertRegex(report["receipt_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            [item["final_phase"] for item in report["scenarios"]],
            [
                "completed",
                "completed",
                "running",
                "running",
                "running",
                "running",
                "completed",
                "dead-lettered",
                "dead-lettered",
                "failed",
                "running",
                "completed",
                "completed",
            ],
        )

    def test_initial_state_is_valid_and_content_free(self) -> None:
        state = self.initial()
        state.validate()
        self.assertEqual(state.phase, "queued")
        self.assertEqual(state.fence, 0)
        self.assertEqual(state.delivery_count, 0)
        self.assertEqual(state.checkpoint_sequence, 0)
        self.assertFalse(state.acked)
        self.assertRegex(MODULE.state_sha256(state), r"^[0-9a-f]{64}$")

    def test_claim_retry_is_idempotent(self) -> None:
        state = self.claim(self.initial())
        result = MODULE.attempt(
            state,
            {
                "kind": "claim",
                "fence": 1,
                "worker_digest": WORKER_A,
                "delivery_count": 1,
            },
        )
        self.assertTrue(result.ok)
        self.assertFalse(result.changed)
        self.assertEqual(result.before_sha256, result.after_sha256)

    def test_takeover_requires_newer_fence(self) -> None:
        state = self.claim(self.initial(max_deliveries=4))
        result = MODULE.attempt(
            state,
            {
                "kind": "claim",
                "fence": 1,
                "worker_digest": WORKER_B,
                "delivery_count": 2,
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("advance both", result.error or "")

    def test_takeover_requires_newer_delivery_count(self) -> None:
        state = self.claim(self.initial(max_deliveries=4))
        result = MODULE.attempt(
            state,
            {
                "kind": "claim",
                "fence": 2,
                "worker_digest": WORKER_B,
                "delivery_count": 1,
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("advance both", result.error or "")

    def test_crash_takeover_preserves_checkpoint(self) -> None:
        state = self.checkpoint(self.claim(self.initial(max_deliveries=4)))
        state = self.claim(state, fence=2, worker=WORKER_B, delivery=2)
        self.assertEqual(state.phase, "running")
        self.assertEqual(state.checkpoint_sequence, 1)
        self.assertEqual(state.checkpoint_digest, CHECKPOINT_A)
        self.assertEqual(state.provider_task_digest, PROVIDER)
        self.assertEqual(state.worker_digest, WORKER_B)

    def test_stale_worker_write_is_failure_atomic(self) -> None:
        state = self.checkpoint(self.claim(self.initial(max_deliveries=4)))
        state = self.claim(state, fence=2, worker=WORKER_B, delivery=2)
        result = MODULE.attempt(
            state,
            {
                "kind": "checkpoint",
                "fence": 1,
                "worker_digest": WORKER_A,
                "sequence": 2,
                "checkpoint_digest": CHECKPOINT_B,
                "provider_task_digest": PROVIDER,
                "artifact_ref_digest": ARTIFACT_REF,
            },
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.changed)
        self.assertEqual(result.before_sha256, result.after_sha256)
        self.assertIs(result.state, state)

    def test_checkpoint_retry_is_idempotent(self) -> None:
        state = self.checkpoint(self.claim(self.initial()))
        result = MODULE.attempt(
            state,
            {
                "kind": "checkpoint",
                "fence": 1,
                "worker_digest": WORKER_A,
                "sequence": 1,
                "checkpoint_digest": CHECKPOINT_A,
                "provider_task_digest": PROVIDER,
                "artifact_ref_digest": None,
            },
        )
        self.assertTrue(result.ok)
        self.assertFalse(result.changed)

    def test_conflicting_checkpoint_is_failure_atomic(self) -> None:
        state = self.checkpoint(self.claim(self.initial()))
        result = MODULE.attempt(
            state,
            {
                "kind": "checkpoint",
                "fence": 1,
                "worker_digest": WORKER_A,
                "sequence": 1,
                "checkpoint_digest": CHECKPOINT_B,
                "provider_task_digest": PROVIDER,
                "artifact_ref_digest": None,
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.before_sha256, result.after_sha256)
        self.assertEqual(result.state.checkpoint_digest, CHECKPOINT_A)

    def test_checkpoint_sequence_cannot_skip(self) -> None:
        state = self.claim(self.initial())
        result = MODULE.attempt(
            state,
            {
                "kind": "checkpoint",
                "fence": 1,
                "worker_digest": WORKER_A,
                "sequence": 2,
                "checkpoint_digest": CHECKPOINT_B,
                "provider_task_digest": None,
                "artifact_ref_digest": None,
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("exactly one", result.error or "")

    def test_provider_and_artifact_references_are_persisted(self) -> None:
        state = self.checkpoint(
            self.claim(self.initial()),
            provider=PROVIDER,
            artifact_ref=ARTIFACT_REF,
        )
        self.assertEqual(state.provider_task_digest, PROVIDER)
        self.assertEqual(state.artifact_ref_digest, ARTIFACT_REF)

    def test_completion_requires_checkpoint(self) -> None:
        state = self.claim(self.initial())
        result = MODULE.attempt(
            state,
            {
                "kind": "complete",
                "fence": 1,
                "worker_digest": WORKER_A,
                "final_artifact_digest": FINAL_ARTIFACT,
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("at least one durable checkpoint", result.error or "")

    def test_completion_retry_is_idempotent(self) -> None:
        state = self.complete(self.checkpoint(self.claim(self.initial())))
        result = MODULE.attempt(
            state,
            {
                "kind": "complete",
                "fence": 1,
                "worker_digest": WORKER_A,
                "final_artifact_digest": FINAL_ARTIFACT,
            },
        )
        self.assertTrue(result.ok)
        self.assertFalse(result.changed)
        self.assertEqual(result.terminal_digest, state.terminal_digest())

    def test_retryable_failure_retry_preserves_checkpoint(self) -> None:
        state = self.checkpoint(self.claim(self.initial()))
        state = self.fail(state, retryable=True)
        state = self.claim(state, fence=2, worker=WORKER_B, delivery=2)
        self.assertEqual(state.phase, "running")
        self.assertEqual(state.checkpoint_sequence, 1)
        self.assertIsNone(state.failure_code)
        self.assertIsNone(state.retryable)

    def test_nonretryable_failure_cannot_be_reclaimed(self) -> None:
        state = self.fail(self.claim(self.initial()), retryable=False)
        result = MODULE.attempt(
            state,
            {
                "kind": "claim",
                "fence": 2,
                "worker_digest": WORKER_B,
                "delivery_count": 2,
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("non-retryable", result.error or "")

    def test_failure_retry_is_idempotent(self) -> None:
        state = self.fail(self.claim(self.initial()), retryable=True)
        result = MODULE.attempt(
            state,
            {
                "kind": "fail",
                "fence": 1,
                "worker_digest": WORKER_A,
                "failure_code": "provider_timeout",
                "retryable": True,
            },
        )
        self.assertTrue(result.ok)
        self.assertFalse(result.changed)

    def test_retryable_failure_cannot_enter_dlq_early(self) -> None:
        state = self.fail(self.claim(self.initial()), retryable=True)
        result = MODULE.attempt(
            state,
            {
                "kind": "dead_letter",
                "fence": 1,
                "worker_digest": WORKER_A,
                "dlq_digest": DLQ,
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("before max deliveries", result.error or "")
        self.assertEqual(result.before_sha256, result.after_sha256)

    def test_retryable_failure_enters_dlq_at_max_delivery(self) -> None:
        state = self.claim(self.initial(), fence=3, delivery=3)
        state = self.fail(state, retryable=True, fence=3)
        state = self.dead_letter(state, fence=3)
        self.assertEqual(state.phase, "dead-lettered")
        self.assertFalse(state.acked)
        self.assertRegex(state.terminal_digest(), r"^[0-9a-f]{64}$")

    def test_nonretryable_failure_enters_dlq_immediately(self) -> None:
        state = self.fail(self.claim(self.initial()), retryable=False)
        state = self.dead_letter(state)
        self.assertEqual(state.phase, "dead-lettered")
        self.assertEqual(state.failure_code, "provider_timeout")

    def test_dead_letter_retry_is_idempotent(self) -> None:
        state = self.dead_letter(
            self.fail(self.claim(self.initial()), retryable=False)
        )
        result = MODULE.attempt(
            state,
            {
                "kind": "dead_letter",
                "fence": 1,
                "worker_digest": WORKER_A,
                "dlq_digest": DLQ,
            },
        )
        self.assertTrue(result.ok)
        self.assertFalse(result.changed)

    def test_ack_requires_committed_terminal_state(self) -> None:
        state = self.claim(self.initial())
        result = MODULE.attempt(
            state,
            {"kind": "ack", "terminal_digest": WRONG},
        )
        self.assertFalse(result.ok)
        self.assertIn("terminal digest requires", result.error or "")
        self.assertEqual(result.before_sha256, result.after_sha256)

    def test_wrong_ack_digest_is_failure_atomic(self) -> None:
        state = self.complete(self.checkpoint(self.claim(self.initial())))
        result = MODULE.attempt(
            state,
            {"kind": "ack", "terminal_digest": WRONG},
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.changed)
        self.assertEqual(result.before_sha256, result.after_sha256)
        self.assertFalse(result.state.acked)

    def test_terminal_digest_is_stable_across_ack(self) -> None:
        state = self.complete(self.checkpoint(self.claim(self.initial())))
        before = state.terminal_digest()
        state = self.ack(state)
        self.assertTrue(state.acked)
        self.assertEqual(before, state.terminal_digest())

    def test_ack_retry_is_idempotent(self) -> None:
        state = self.complete(self.checkpoint(self.claim(self.initial())))
        state = self.ack(state)
        result = MODULE.attempt(
            state,
            {"kind": "ack", "terminal_digest": state.terminal_digest()},
        )
        self.assertTrue(result.ok)
        self.assertFalse(result.changed)
        self.assertTrue(result.state.acked)

    def test_terminal_job_cannot_be_reclaimed(self) -> None:
        state = self.complete(self.checkpoint(self.claim(self.initial())))
        result = MODULE.attempt(
            state,
            {
                "kind": "claim",
                "fence": 2,
                "worker_digest": WORKER_B,
                "delivery_count": 2,
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("terminal job cannot be reclaimed", result.error or "")

    def test_delivery_count_cannot_exceed_max(self) -> None:
        result = MODULE.attempt(
            self.initial(),
            {
                "kind": "claim",
                "fence": 1,
                "worker_digest": WORKER_A,
                "delivery_count": 4,
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("exceeds max_deliveries", result.error or "")

    def test_boolean_delivery_count_is_rejected(self) -> None:
        result = MODULE.attempt(
            self.initial(),
            {
                "kind": "claim",
                "fence": 1,
                "worker_digest": WORKER_A,
                "delivery_count": True,
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("positive integer", result.error or "")

    def test_unknown_command_kind_fails(self) -> None:
        result = MODULE.attempt(
            self.initial(),
            {"kind": "force_complete"},
        )
        self.assertFalse(result.ok)
        self.assertIn("unsupported", result.error or "")

    def test_extra_command_field_fails(self) -> None:
        result = MODULE.attempt(
            self.initial(),
            {
                "kind": "claim",
                "fence": 1,
                "worker_digest": WORKER_A,
                "delivery_count": 1,
                "unexpected": True,
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("keys mismatch", result.error or "")

    def test_invalid_failure_code_fails(self) -> None:
        state = self.claim(self.initial())
        result = MODULE.attempt(
            state,
            {
                "kind": "fail",
                "fence": 1,
                "worker_digest": WORKER_A,
                "failure_code": "BAD FAILURE",
                "retryable": False,
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("invalid failure code", result.error or "")

    def test_invalid_state_invariant_is_rejected(self) -> None:
        state = replace(self.initial(), phase="running", fence=1)
        with self.assertRaisesRegex(MODULE.ProtocolError, "worker_digest"):
            state.validate()

    def test_terminal_digest_requires_terminal_state(self) -> None:
        with self.assertRaisesRegex(MODULE.ProtocolError, "terminal digest requires"):
            self.initial().terminal_digest()

    def test_state_digest_is_field_order_invariant(self) -> None:
        state = self.checkpoint(self.claim(self.initial()))
        payload = MODULE.asdict(state)
        reordered = dict(reversed(list(payload.items())))
        self.assertEqual(MODULE.sha256(payload), MODULE.sha256(reordered))

    def test_duplicate_fixture_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.json"
            path.write_text('{"schema_version":"one","schema_version":"two"}\n')
            with self.assertRaisesRegex(MODULE.ProtocolError, "duplicate JSON key"):
                MODULE.load_fixture(path)

    def test_fixture_schema_mismatch_fails(self) -> None:
        value = MODULE.load_fixture(FIXTURE)
        value["schema_version"] = "future-version"
        with self.assertRaisesRegex(MODULE.ProtocolError, "unsupported"):
            MODULE.run_fixture(value)

    def test_fixture_expected_error_is_enforced(self) -> None:
        value = copy.deepcopy(MODULE.load_fixture(FIXTURE))
        value["scenarios"][2]["steps"][3]["error_contains"] = "wrong"
        with self.assertRaisesRegex(MODULE.ProtocolError, "error mismatch"):
            MODULE.run_fixture(value)

    def test_fixture_expected_phase_is_enforced(self) -> None:
        value = copy.deepcopy(MODULE.load_fixture(FIXTURE))
        value["scenarios"][0]["expected_final_phase"] = "running"
        with self.assertRaisesRegex(MODULE.ProtocolError, "final phase mismatch"):
            MODULE.run_fixture(value)

    def test_fixture_expected_ack_is_enforced(self) -> None:
        value = copy.deepcopy(MODULE.load_fixture(FIXTURE))
        value["scenarios"][0]["expected_acked"] = False
        with self.assertRaisesRegex(MODULE.ProtocolError, "ACK state mismatch"):
            MODULE.run_fixture(value)

    def test_fixture_receipt_is_deterministic(self) -> None:
        value = MODULE.load_fixture(FIXTURE)
        first = MODULE.run_fixture(value)
        second = MODULE.run_fixture(copy.deepcopy(value))
        self.assertEqual(first["receipt_sha256"], second["receipt_sha256"])
        self.assertEqual(first["scenarios"], second["scenarios"])


if __name__ == "__main__":
    unittest.main()
