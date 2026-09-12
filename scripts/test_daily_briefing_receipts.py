#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("daily_briefing_receipts.py")
SPEC = importlib.util.spec_from_file_location("daily_briefing_receipts", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "daily-briefing-lanes-v1.json"


class LaneManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = MODULE.load_json(FIXTURE)

    def report(self) -> object:
        return MODULE.validate_lane_manifest(self.manifest)

    def assert_error(self, report: object, fragment: str) -> None:
        errors = getattr(report, "errors")
        self.assertTrue(
            any(fragment in error for error in errors),
            f"expected {fragment!r} in {errors!r}",
        )

    def test_complete_manifest_is_ready(self) -> None:
        report = self.report()
        self.assertTrue(report.valid, report.errors)
        self.assertTrue(report.ready_for_composition)
        self.assertEqual(report.expected_lane_count, 8)
        self.assertEqual(report.receipt_count, 8)
        self.assertEqual(report.blocking_lanes, ())
        self.assertRegex(report.manifest_sha256 or "", r"^[0-9a-f]{64}$")
        self.assertRegex(report.envelope_sha256 or "", r"^[0-9a-f]{64}$")

    def test_envelope_digest_is_receipt_order_invariant(self) -> None:
        first = self.report().envelope_sha256
        self.manifest["receipts"].reverse()
        second = self.report()
        self.assertTrue(second.valid, second.errors)
        self.assertEqual(first, second.envelope_sha256)

    def test_missing_receipt_fails_closed(self) -> None:
        self.manifest["receipts"].pop()
        report = self.report()
        self.assertFalse(report.valid)
        self.assert_error(report, "missing lane receipts")
        self.assert_error(report, "receipt count must equal")

    def test_duplicate_lane_fails(self) -> None:
        self.manifest["receipts"][1]["lane_id"] = "github"
        report = self.report()
        self.assert_error(report, "duplicate lane IDs")

    def test_duplicate_expected_lane_fails(self) -> None:
        self.manifest["expected_lanes"][1] = "github"
        report = self.report()
        self.assert_error(report, "expected_lanes must not contain duplicates")

    def test_blocked_lane_is_valid_but_not_ready(self) -> None:
        receipt = self.manifest["receipts"][0]
        receipt.update(
            {
                "status": "blocked",
                "result_sha256": None,
                "result_bytes": None,
                "item_count": None,
                "reason_code": "source-authorization-unavailable",
            }
        )
        report = self.report()
        self.assertTrue(report.valid, report.errors)
        self.assertFalse(report.ready_for_composition)
        self.assertEqual(report.blocking_lanes, ("github",))

    def test_non_ready_lane_cannot_claim_results(self) -> None:
        receipt = self.manifest["receipts"][0]
        receipt["status"] = "failed"
        receipt["reason_code"] = "collector-error"
        report = self.report()
        self.assert_error(report, "must not claim result bytes")

    def test_success_requires_positive_item_count(self) -> None:
        self.manifest["receipts"][0]["item_count"] = 0
        report = self.report()
        self.assert_error(report, "must be positive for success")

    def test_no_results_requires_zero_items(self) -> None:
        self.manifest["receipts"][3]["item_count"] = 1
        report = self.report()
        self.assert_error(report, "must be zero for no_results")

    def test_ready_lane_cannot_claim_reason_code(self) -> None:
        self.manifest["receipts"][0]["reason_code"] = "unexpected"
        report = self.report()
        self.assert_error(report, "reason_code must be null")

    def test_observation_before_window_fails(self) -> None:
        self.manifest["receipts"][0]["observed_at"] = "2026-09-03T06:59:59-04:00"
        report = self.report()
        self.assert_error(report, "precedes the scheduled window")

    def test_observation_at_end_fails(self) -> None:
        self.manifest["receipts"][0]["observed_at"] = "2026-09-04T07:00:00-04:00"
        report = self.report()
        self.assert_error(report, "must fall inside the scheduled window")

    def test_retention_must_follow_observation(self) -> None:
        self.manifest["receipts"][0]["retained_until"] = self.manifest[
            "receipts"
        ][0]["observed_at"]
        report = self.report()
        self.assert_error(report, "retained_until must be after")

    def test_duplicate_operation_id_fails(self) -> None:
        self.manifest["receipts"][1]["operation_id"] = self.manifest[
            "receipts"
        ][0]["operation_id"]
        report = self.report()
        self.assert_error(report, "duplicate operation_id")

    def test_duplicate_path_identity_fails(self) -> None:
        self.manifest["receipts"][1]["path_identity"] = self.manifest[
            "receipts"
        ][0]["path_identity"]
        report = self.report()
        self.assert_error(report, "duplicate path_identity")

    def test_bad_commit_fails(self) -> None:
        self.manifest["receipts"][0]["producer_commit"] = "main"
        report = self.report()
        self.assert_error(report, "40-character commit SHA")

    def test_bad_digest_fails(self) -> None:
        self.manifest["policy_sha256"] = "not-a-digest"
        report = self.report()
        self.assert_error(report, "must be a string of length 64..64")

    def test_safety_flags_fail_closed(self) -> None:
        self.manifest["safety"]["delivery_authorized"] = True
        report = self.report()
        self.assert_error(report, "safety.delivery_authorized must be false")

    def test_unknown_field_fails(self) -> None:
        self.manifest["unexpected"] = True
        report = self.report()
        self.assert_error(report, "keys mismatch")

    def test_raw_payload_field_is_prohibited(self) -> None:
        self.manifest["receipts"][0]["raw_payload"] = "redacted"
        report = self.report()
        self.assert_error(report, "is prohibited")

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.json"
            path.write_text('{"schema_version":"one","schema_version":"two"}')
            with self.assertRaisesRegex(MODULE.ContractError, "duplicate JSON key"):
                MODULE.load_json(path)

    def test_invalid_utf8_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_bytes(b"\xff\xfe")
            with self.assertRaisesRegex(MODULE.ContractError, "must be UTF-8"):
                MODULE.load_json(path)

    def test_oversized_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "large.json"
            path.write_bytes(b" " * (MODULE.MAX_MANIFEST_BYTES + 1))
            with self.assertRaisesRegex(MODULE.ContractError, "exceeds"):
                MODULE.load_json(path)


class DeliveryTransitionTests(unittest.TestCase):
    def state(self, state: str = "prepared") -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": MODULE.DELIVERY_SCHEMA,
            "briefing_id": "portfolio-briefing-2026-09-04",
            "state": state,
            "content_sha256": "a" * 64,
            "destination_id": "approved-destination",
            "operation_id": "b" * 64,
            "fencing_token": 1,
            "attempt": 1,
            "receipt_id": None,
            "remote_identity_sha256": None,
            "failure_class": None,
        }
        if state in {"retryable", "terminal_failed", "reconciling"}:
            result["failure_class"] = "test-failure"
        if state == "delivered":
            result["receipt_id"] = "c" * 64
            result["remote_identity_sha256"] = "d" * 64
        return result

    def command(
        self,
        kind: str,
        *,
        operation_id: str = "b" * 64,
        fencing_token: int = 1,
        receipt_id: str | None = None,
        remote_identity_sha256: str | None = None,
        failure_class: str | None = None,
    ) -> dict[str, object]:
        return {
            "kind": kind,
            "operation_id": operation_id,
            "fencing_token": fencing_token,
            "receipt_id": receipt_id,
            "remote_identity_sha256": remote_identity_sha256,
            "failure_class": failure_class,
        }

    def test_claim_begin_and_accept(self) -> None:
        prepared = self.state()
        claimed = MODULE.transition_delivery(
            prepared,
            self.command(
                "claim", operation_id="e" * 64, fencing_token=2
            ),
        )
        self.assertEqual(claimed["state"], "claimed")
        delivering = MODULE.transition_delivery(
            claimed,
            self.command(
                "begin_delivery", operation_id="e" * 64, fencing_token=2
            ),
        )
        delivered = MODULE.transition_delivery(
            delivering,
            self.command(
                "accept_receipt",
                operation_id="e" * 64,
                fencing_token=2,
                receipt_id="f" * 64,
                remote_identity_sha256="1" * 64,
            ),
        )
        self.assertEqual(delivered["state"], "delivered")
        self.assertEqual(delivered["receipt_id"], "f" * 64)

    def test_transition_never_mutates_input(self) -> None:
        prepared = self.state()
        snapshot = copy.deepcopy(prepared)
        MODULE.transition_delivery(
            prepared,
            self.command("claim", operation_id="e" * 64, fencing_token=2),
        )
        self.assertEqual(prepared, snapshot)

    def test_failed_transition_never_mutates_input(self) -> None:
        claimed = self.state("claimed")
        snapshot = copy.deepcopy(claimed)
        with self.assertRaises(MODULE.ContractError):
            MODULE.transition_delivery(
                claimed,
                self.command("begin_delivery", fencing_token=999),
            )
        self.assertEqual(claimed, snapshot)

    def test_retryable_failure_requires_new_fence_before_retry(self) -> None:
        delivering = self.state("delivering")
        retryable = MODULE.transition_delivery(
            delivering,
            self.command(
                "record_retryable_failure", failure_class="temporary-timeout"
            ),
        )
        self.assertEqual(retryable["state"], "retryable")
        with self.assertRaisesRegex(MODULE.ContractError, "strictly newer"):
            MODULE.transition_delivery(
                retryable,
                self.command("claim", operation_id="e" * 64, fencing_token=1),
            )
        reclaimed = MODULE.transition_delivery(
            retryable,
            self.command("claim", operation_id="e" * 64, fencing_token=2),
        )
        self.assertEqual(reclaimed["attempt"], 2)

    def test_ambiguous_response_requires_reconciliation(self) -> None:
        delivering = self.state("delivering")
        reconciling = MODULE.transition_delivery(
            delivering,
            self.command("mark_ambiguous", failure_class="response-lost"),
        )
        self.assertEqual(reconciling["state"], "reconciling")
        with self.assertRaisesRegex(MODULE.ContractError, "claim is illegal"):
            MODULE.transition_delivery(
                reconciling,
                self.command("claim", operation_id="e" * 64, fencing_token=2),
            )
        delivered = MODULE.transition_delivery(
            reconciling,
            self.command(
                "accept_receipt",
                receipt_id="c" * 64,
                remote_identity_sha256="d" * 64,
            ),
        )
        self.assertEqual(delivered["state"], "delivered")

    def test_reconciled_not_delivered_becomes_retryable(self) -> None:
        reconciling = self.state("reconciling")
        retryable = MODULE.transition_delivery(
            reconciling,
            self.command("reconcile_not_delivered"),
        )
        self.assertEqual(retryable["state"], "retryable")
        self.assertEqual(
            retryable["failure_class"], "reconciled_not_delivered"
        )

    def test_stale_operation_is_rejected(self) -> None:
        with self.assertRaisesRegex(MODULE.ContractError, "does not own"):
            MODULE.transition_delivery(
                self.state("claimed"),
                self.command("begin_delivery", operation_id="e" * 64),
            )

    def test_terminal_delivered_state_rejects_new_delivery(self) -> None:
        with self.assertRaisesRegex(MODULE.ContractError, "illegal from delivered"):
            MODULE.transition_delivery(
                self.state("delivered"),
                self.command("begin_delivery"),
            )

    def test_terminal_failure_is_valid_and_terminal(self) -> None:
        failed = MODULE.transition_delivery(
            self.state("claimed"),
            self.command(
                "record_terminal_failure", failure_class="destination-revoked"
            ),
        )
        self.assertEqual(failed["state"], "terminal_failed")
        with self.assertRaisesRegex(MODULE.ContractError, "illegal from"):
            MODULE.transition_delivery(failed, self.command("begin_delivery"))

    def test_invalid_receipt_rejects_candidate_atomically(self) -> None:
        delivering = self.state("delivering")
        snapshot = copy.deepcopy(delivering)
        with self.assertRaises(MODULE.ContractError):
            MODULE.transition_delivery(
                delivering,
                self.command(
                    "accept_receipt",
                    receipt_id="short",
                    remote_identity_sha256="d" * 64,
                ),
            )
        self.assertEqual(delivering, snapshot)

    def test_unknown_command_field_fails(self) -> None:
        command = self.command(
            "claim", operation_id="e" * 64, fencing_token=2
        )
        command["unexpected"] = True
        with self.assertRaisesRegex(MODULE.ContractError, "keys mismatch"):
            MODULE.transition_delivery(self.state(), command)

    def test_invalid_current_state_fails_before_transition(self) -> None:
        current = self.state()
        current["state"] = "unknown"
        with self.assertRaisesRegex(MODULE.ContractError, "unsupported"):
            MODULE.transition_delivery(
                current,
                self.command("claim", operation_id="e" * 64, fencing_token=2),
            )


if __name__ == "__main__":
    unittest.main()
