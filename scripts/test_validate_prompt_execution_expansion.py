#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("validate_prompt_execution_expansion.py")
SPEC = importlib.util.spec_from_file_location(
    "validate_prompt_execution_expansion", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

ROOT = Path(__file__).parents[1]
BASE_PATH = ROOT / "fixtures" / "prompt-execution-ledger-2026-09-03.json"
EXPANSION_PATH = (
    ROOT / "fixtures" / "prompt-execution-expansion-2026-09-04.json"
)


class PromptExecutionExpansionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = MODULE.ledger.load(BASE_PATH)
        self.expansion = MODULE.load_expansion(EXPANSION_PATH)

    def report(self) -> object:
        return MODULE.validate_expansion(self.base, self.expansion)

    def assert_error(self, report: object, fragment: str) -> None:
        errors = getattr(report, "errors")
        self.assertTrue(
            any(fragment in error for error in errors),
            f"expected {fragment!r} in {errors!r}",
        )

    def test_checked_in_expansion_is_valid(self) -> None:
        report = self.report()
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.base_workstream_count, 15)
        self.assertEqual(report.added_workstream_count, 5)
        self.assertEqual(report.combined_workstream_count, 20)
        self.assertEqual(
            report.base_sha256,
            "e2ea566eb781b3a7174be4ec2ab06f5c5cb6c4c2047375d9ca541264952117c1",
        )
        self.assertRegex(report.expansion_sha256 or "", r"^[0-9a-f]{64}$")

    def test_requires_exactly_five_added_workstreams(self) -> None:
        self.expansion["added_workstreams"].pop()
        report = self.report()
        self.assert_error(report, "must contain exactly 5 items; got 4")
        self.assert_error(report, "combined workstream count must be 20; got 19")

    def test_base_digest_mismatch_fails(self) -> None:
        self.expansion["base"]["ledger_sha256"] = "0" * 64
        report = self.report()
        self.assert_error(report, "base.ledger_sha256 does not match")

    def test_base_ledger_identity_mismatch_fails(self) -> None:
        self.expansion["base"]["ledger_id"] = "another-ledger"
        report = self.report()
        self.assert_error(report, "base.ledger_id does not match")

    def test_base_count_mismatch_fails(self) -> None:
        self.expansion["base"]["workstream_count"] = 14
        report = self.report()
        self.assert_error(report, "base.workstream_count does not match")

    def test_duplicate_id_with_base_fails(self) -> None:
        self.expansion["added_workstreams"][0]["id"] = self.base["workstreams"][0]["id"]
        report = self.report()
        self.assert_error(report, "duplicate combined workstream id")

    def test_duplicate_id_inside_expansion_fails(self) -> None:
        self.expansion["added_workstreams"][1]["id"] = self.expansion[
            "added_workstreams"
        ][0]["id"]
        report = self.report()
        self.assert_error(report, "duplicate combined workstream id")

    def test_duplicate_github_anchor_with_base_fails(self) -> None:
        self.expansion["added_workstreams"][0]["github_anchors"] = [
            self.base["workstreams"][0]["github_anchors"][0]
        ]
        report = self.report()
        self.assert_error(report, "duplicate combined GitHub anchor")

    def test_duplicate_github_anchor_inside_expansion_fails(self) -> None:
        anchor = self.expansion["added_workstreams"][0]["github_anchors"][0]
        self.expansion["added_workstreams"][1]["github_anchors"] = [anchor]
        report = self.report()
        self.assert_error(report, "duplicate combined GitHub anchor")

    def test_malformed_linear_anchor_fails(self) -> None:
        self.expansion["added_workstreams"][0]["linear_anchors"] = ["bad"]
        report = self.report()
        self.assert_error(report, "must be a DEN-N identifier")

    def test_non_issue_github_url_fails(self) -> None:
        self.expansion["added_workstreams"][0]["github_anchors"] = [
            "https://github.com/ORESoftware/ai-agent-coordinator.rs/tree/main"
        ]
        report = self.report()
        self.assert_error(report, "must be an exact https://github.com")

    def test_query_string_on_source_pr_fails(self) -> None:
        self.expansion["base"]["source_pr"] += "?token=redacted"
        report = self.report()
        self.assert_error(report, "must be an exact https://github.com")

    def test_unknown_dependency_fails(self) -> None:
        self.expansion["added_workstreams"][0]["depends_on"] = ["missing"]
        report = self.report()
        self.assert_error(report, "depends on unknown workstream")

    def test_self_dependency_fails(self) -> None:
        item = self.expansion["added_workstreams"][0]
        item["depends_on"] = [item["id"]]
        report = self.report()
        self.assert_error(report, "cannot depend on itself")

    def test_combined_dependency_cycle_fails(self) -> None:
        first = self.expansion["added_workstreams"][0]
        second = self.expansion["added_workstreams"][1]
        first["depends_on"] = [second["id"]]
        second["depends_on"] = [first["id"]]
        report = self.report()
        self.assert_error(report, "combined workstream dependency cycle")

    def test_safety_flags_fail_closed(self) -> None:
        self.expansion["safety"]["merge_authorized"] = True
        report = self.report()
        self.assert_error(report, "safety.merge_authorized must be false")

    def test_raw_message_field_is_rejected(self) -> None:
        self.expansion["added_workstreams"][0]["raw_message"] = "redacted"
        report = self.report()
        self.assert_error(report, "prohibited raw/sensitive field")
        self.assert_error(report, "keys mismatch")

    def test_unknown_root_field_fails(self) -> None:
        self.expansion["unexpected"] = True
        report = self.report()
        self.assert_error(report, "keys mismatch")

    def test_schema_mismatch_fails(self) -> None:
        self.expansion["schema_version"] = "prompt-execution-expansion/v2"
        report = self.report()
        self.assert_error(report, "schema_version must be")

    def test_invalid_expansion_id_fails(self) -> None:
        self.expansion["expansion_id"] = "INVALID ID"
        report = self.report()
        self.assert_error(report, "expansion_id has an invalid format")

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.json"
            path.write_text('{"schema_version":"one","schema_version":"two"}\n')
            with self.assertRaisesRegex(MODULE.ledger.LedgerError, "duplicate JSON key"):
                MODULE.load_expansion(path)

    def test_secret_like_assignment_is_rejected_during_load(self) -> None:
        value = copy.deepcopy(self.expansion)
        value["safety"]["notes"][0] = "fake token=abcdefghijklmnop"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "secret.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ledger.LedgerError, "prohibited"):
                MODULE.load_expansion(path)

    def test_email_address_is_rejected_during_load(self) -> None:
        value = copy.deepcopy(self.expansion)
        value["safety"]["notes"][0] = "Contact person@example.invalid"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "email.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ledger.LedgerError, "email address"):
                MODULE.load_expansion(path)

    def test_expansion_digest_is_order_invariant(self) -> None:
        first = MODULE.ledger.digest(self.expansion)
        reordered = dict(reversed(list(self.expansion.items())))
        self.assertEqual(first, MODULE.ledger.digest(reordered))


if __name__ == "__main__":
    unittest.main()
