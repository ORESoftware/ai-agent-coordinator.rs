#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("validate_prompt_execution_ledger.py")
SPEC = importlib.util.spec_from_file_location(
    "validate_prompt_execution_ledger", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "prompt-execution-ledger-2026-09-03.json"
)


class PromptExecutionLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = MODULE.load_ledger(FIXTURE_PATH)

    def report(self, *, mode: str = "planning") -> object:
        return MODULE.validate_ledger(self.ledger, mode=mode)

    def assert_error(self, report: object, fragment: str) -> None:
        errors = getattr(report, "errors")
        self.assertTrue(
            any(fragment in error for error in errors),
            f"expected {fragment!r} in {errors!r}",
        )

    def mark_source_complete(self) -> None:
        segment = self.ledger["source_segments"][1]
        segment["complete"] = True
        segment["receipt_type"] = "google_chat_bridge"
        segment["receipt_anchor"] = "DEN-3921"
        segment["record_counts"] = {
            "human_messages": 120,
            "threads": 100,
            "replies": 20,
            "bots": 0,
            "deleted": 0,
            "empty_or_attachment_only": 2,
        }
        segment["incomplete_reason"] = None

        coverage = self.ledger["coverage"]
        coverage["source_complete"] = True
        coverage["verified_through"] = self.ledger["window"]["end"]
        coverage["actionable_prompt_count"] = 87
        coverage["mapped_prompt_count"] = 87
        coverage["unresolved_prompt_count"] = 0
        coverage["closure_blockers"] = []

    def test_current_fixture_is_valid_planning_evidence(self) -> None:
        report = self.report(mode="planning")
        self.assertTrue(report.valid, report.errors)
        self.assertFalse(report.closure_ready)
        self.assertEqual(report.workstream_count, 15)
        self.assertEqual(
            report.incomplete_segments,
            ("chat-2026-08-25-through-2026-09-04",),
        )
        self.assertRegex(report.ledger_sha256 or "", r"^[0-9a-f]{64}$")

    def test_current_fixture_fails_closed(self) -> None:
        report = self.report(mode="closure")
        self.assertFalse(report.valid)
        self.assertFalse(report.closure_ready)
        self.assert_error(report, "closure mode requires complete source coverage")

    def test_complete_source_and_mapping_can_close(self) -> None:
        self.mark_source_complete()
        report = self.report(mode="closure")
        self.assertTrue(report.valid, report.errors)
        self.assertTrue(report.closure_ready)
        self.assertEqual(report.incomplete_segments, ())

    def test_workstream_count_is_exact(self) -> None:
        self.ledger["workstreams"].pop()
        report = self.report()
        self.assert_error(report, "must contain exactly 15 items; got 14")

    def test_duplicate_workstream_id_fails(self) -> None:
        self.ledger["workstreams"][1]["id"] = self.ledger["workstreams"][0]["id"]
        report = self.report()
        self.assert_error(report, "duplicate workstream id")

    def test_missing_linear_anchor_fails(self) -> None:
        self.ledger["workstreams"][0]["linear_anchors"] = []
        report = self.report()
        self.assert_error(report, "linear_anchors must contain between 1 and 8 items")

    def test_malformed_linear_anchor_fails(self) -> None:
        self.ledger["workstreams"][0]["linear_anchors"] = ["not-an-issue"]
        report = self.report()
        self.assert_error(report, "must be a DEN-N identifier")

    def test_non_issue_github_url_fails(self) -> None:
        self.ledger["workstreams"][0]["github_anchors"] = [
            "https://github.com/ORESoftware/ai-agent-coordinator.rs/tree/main"
        ]
        report = self.report()
        self.assert_error(report, "must be an exact https://github.com")

    def test_github_query_string_fails(self) -> None:
        self.ledger["workstreams"][0]["github_anchors"] = [
            "https://github.com/ORESoftware/ai-agent-coordinator.rs/issues/192?x=1"
        ]
        report = self.report()
        self.assert_error(report, "must be an exact https://github.com")

    def test_duplicate_github_anchor_across_workstreams_fails(self) -> None:
        self.ledger["workstreams"][1]["github_anchors"] = copy.deepcopy(
            self.ledger["workstreams"][0]["github_anchors"]
        )
        report = self.report()
        self.assert_error(report, "duplicate primary GitHub anchor")

    def test_source_gap_fails(self) -> None:
        self.ledger["source_segments"][1]["start"] = "2026-08-26T00:00:00-04:00"
        report = self.report()
        self.assert_error(report, "source segment gap")

    def test_source_overlap_fails(self) -> None:
        self.ledger["source_segments"][1]["start"] = "2026-08-24T00:00:00-04:00"
        report = self.report()
        self.assert_error(report, "source segment overlap")

    def test_source_segments_must_be_declared_in_chronological_order(self) -> None:
        self.ledger["source_segments"].reverse()
        report = self.report()
        self.assert_error(report, "source_segments must be ordered chronologically")

    def test_source_must_cover_window_edges(self) -> None:
        self.ledger["source_segments"][0]["start"] = "2026-08-03T00:00:00-04:00"
        report = self.report()
        self.assert_error(report, "must begin exactly at window.start")

    def test_unknown_dependency_fails(self) -> None:
        self.ledger["workstreams"][0]["depends_on"] = ["missing-workstream"]
        report = self.report()
        self.assert_error(report, "depends on unknown workstream")

    def test_dependency_cycle_fails(self) -> None:
        first = self.ledger["workstreams"][0]
        second = self.ledger["workstreams"][1]
        first["depends_on"] = [second["id"]]
        second["depends_on"] = [first["id"]]
        report = self.report()
        self.assert_error(report, "workstream dependency cycle")

    def test_safety_flags_fail_closed(self) -> None:
        self.ledger["safety"]["merge_authorized"] = True
        report = self.report()
        self.assert_error(report, "safety.merge_authorized must be false")

    def test_raw_message_field_is_prohibited(self) -> None:
        self.ledger["workstreams"][0]["raw_message"] = "redacted"
        report = self.report()
        self.assert_error(report, "prohibited raw/sensitive field")
        self.assert_error(report, "keys mismatch")

    def test_complete_coverage_requires_balanced_counts(self) -> None:
        self.mark_source_complete()
        self.ledger["coverage"]["mapped_prompt_count"] = 86
        report = self.report()
        self.assert_error(report, "mapped_prompt_count + unresolved_prompt_count")

    def test_coverage_completeness_must_match_segments(self) -> None:
        self.ledger["coverage"]["source_complete"] = True
        report = self.report()
        self.assert_error(report, "must equal the completeness of all source segments")

    def test_incomplete_segment_must_be_a_closure_blocker(self) -> None:
        self.ledger["coverage"]["closure_blockers"] = [
            "independent-source-receipt-required"
        ]
        report = self.report()
        self.assert_error(report, "must include incomplete segments")

    def test_generated_at_must_fall_inside_window(self) -> None:
        self.ledger["generated_at"] = "2026-09-04T00:00:01-04:00"
        report = self.report()
        self.assert_error(report, "generated_at must fall within the ledger window")

    def test_canonical_digest_is_order_invariant(self) -> None:
        digest = MODULE.canonical_sha256(self.ledger)
        reordered = OrderedDict(reversed(list(self.ledger.items())))
        self.assertEqual(digest, MODULE.canonical_sha256(reordered))

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.json"
            path.write_text('{"schema_version":"one","schema_version":"two"}\n')
            with self.assertRaisesRegex(MODULE.LedgerError, "duplicate JSON key"):
                MODULE.load_ledger(path)

    def test_secret_like_assignment_is_rejected_before_parsing(self) -> None:
        self.ledger["safety"]["notes"][0] = "fake token=abcdefghijklmnop"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "secret.json"
            path.write_text(json.dumps(self.ledger), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.LedgerError, "prohibited"):
                MODULE.load_ledger(path)

    def test_email_address_is_rejected(self) -> None:
        self.ledger["safety"]["notes"][0] = "Contact person@example.invalid"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "email.json"
            path.write_text(json.dumps(self.ledger), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.LedgerError, "email address"):
                MODULE.load_ledger(path)

    def test_unknown_root_field_fails(self) -> None:
        self.ledger["unexpected"] = True
        report = self.report()
        self.assert_error(report, "keys mismatch")


if __name__ == "__main__":
    unittest.main()
