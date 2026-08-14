from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from artifact_recovery.common import RecoveryError, canonical_json, sha256_value  # noqa: E402
from artifact_recovery.source_coverage import (  # noqa: E402
    COVERAGE_SCHEMA,
    build_example_source_coverage,
    build_source_coverage_report,
)

NOW = "2026-08-10T18:45:00Z"


class ArtifactRecoverySourceCoverageTests(unittest.TestCase):
    def raw_fixture(self) -> dict:
        return self.raw_fixture_for(
            required=["chatgpt", "claude", "linear", "github", "local_repo"],
            optional=[],
        )

    def raw_fixture_for(self, *, required: list[str], optional: list[str]) -> dict:
        receipts = []
        for source in required + optional:
            receipts.append(
                {
                    "source": source,
                    "source_identity_sha256": sha256_value(
                        {"source": source, "kind": "identity"}
                    ),
                    "capability_sha256": sha256_value(
                        {"source": source, "kind": "capability"}
                    ),
                    "window": {
                        "start": "2026-08-10T17:45:00Z",
                        "end": NOW,
                    },
                    "captured_at": NOW,
                    "freshness": {
                        "watermark_at": NOW,
                        "max_age_seconds": 7200,
                    },
                    "pagination": {
                        "started": True,
                        "complete": True,
                        "last_page_complete": True,
                        "pages_read": 1,
                        "items_read": 0,
                        "terminal_cursor_sha256": None,
                    },
                    "reported_state": "complete",
                    "error_class": None,
                    "retryable": False,
                }
            )
        return {
            "schema_version": COVERAGE_SCHEMA,
            "generated_at": NOW,
            "policy": {
                "required_sources": required,
                "optional_sources": optional,
                "max_clock_skew_seconds": 300,
            },
            "receipts": receipts,
        }

    def receipt(self, fixture: dict, source: str) -> dict:
        return next(item for item in fixture["receipts"] if item["source"] == source)

    def test_complete_report_is_byte_stable_and_digest_bound(self) -> None:
        first = build_source_coverage_report(self.raw_fixture(), now=NOW)
        second = build_source_coverage_report(copy.deepcopy(first), now=NOW)
        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["status"], "complete")
        self.assertTrue(first["summary"]["complete"])
        without_digest = {key: value for key, value in first.items() if key != "report_sha256"}
        self.assertEqual(first["report_sha256"], sha256_value(without_digest))
        self.assertEqual(canonical_json(first), canonical_json(second))

    def test_missing_required_source_is_rejected(self) -> None:
        fixture = self.raw_fixture()
        fixture["receipts"] = [
            receipt for receipt in fixture["receipts"] if receipt["source"] != "claude"
        ]
        with self.assertRaisesRegex(RecoveryError, "missing policy sources"):
            build_source_coverage_report(fixture, now=NOW)

    def test_duplicate_source_is_rejected(self) -> None:
        fixture = self.raw_fixture()
        fixture["receipts"].append(copy.deepcopy(fixture["receipts"][0]))
        with self.assertRaisesRegex(RecoveryError, "duplicate sources"):
            build_source_coverage_report(fixture, now=NOW)

    def test_unfinished_pagination_derives_partial_not_empty_success(self) -> None:
        fixture = self.raw_fixture()
        github = self.receipt(fixture, "github")
        github["pagination"]["complete"] = False
        github["pagination"]["terminal_cursor_sha256"] = "a" * 64
        report = build_source_coverage_report(fixture, now=NOW)
        receipt = next(item for item in report["receipts"] if item["source"] == "github")
        self.assertEqual(receipt["reported_state"], "complete")
        self.assertEqual(receipt["state"], "partial")
        self.assertEqual(receipt["error_class"], "pagination")
        self.assertEqual(report["summary"]["status"], "partial")
        self.assertFalse(report["summary"]["complete"])
        self.assertEqual(report["summary"]["partial_sources"], ["github"])

    def test_partial_last_page_cannot_be_complete(self) -> None:
        fixture = self.raw_fixture()
        self.receipt(fixture, "linear")["pagination"]["last_page_complete"] = False
        report = build_source_coverage_report(fixture, now=NOW)
        states = report["summary"]["source_states"]
        self.assertEqual(states["linear"], "partial")

    def test_stale_watermark_derives_stale_state(self) -> None:
        fixture = self.raw_fixture()
        claude = self.receipt(fixture, "claude")
        claude["freshness"] = {
            "watermark_at": "2026-08-10T12:00:00Z",
            "max_age_seconds": 3600,
        }
        report = build_source_coverage_report(fixture, now=NOW)
        receipt = next(item for item in report["receipts"] if item["source"] == "claude")
        self.assertEqual(receipt["state"], "stale")
        self.assertEqual(receipt["error_class"], "stale_watermark")
        self.assertEqual(report["summary"]["partial_sources"], ["claude"])

    def test_unauthorized_required_source_blocks_run(self) -> None:
        fixture = self.raw_fixture()
        chatgpt = self.receipt(fixture, "chatgpt")
        chatgpt.update(
            reported_state="unauthorized",
            error_class="authorization",
            retryable=False,
        )
        chatgpt["pagination"] = {
            "started": False,
            "complete": False,
            "last_page_complete": False,
            "pages_read": 0,
            "items_read": 0,
            "terminal_cursor_sha256": None,
        }
        report = build_source_coverage_report(fixture, now=NOW)
        self.assertEqual(report["summary"]["status"], "blocked")
        self.assertEqual(report["summary"]["blocked_sources"], ["chatgpt"])

    def test_unavailable_required_source_is_retryable_partial(self) -> None:
        fixture = self.raw_fixture()
        linear = self.receipt(fixture, "linear")
        linear.update(
            reported_state="unavailable",
            error_class="availability",
            retryable=True,
        )
        linear["pagination"]["complete"] = False
        linear["pagination"]["last_page_complete"] = False
        report = build_source_coverage_report(fixture, now=NOW)
        receipt = next(item for item in report["receipts"] if item["source"] == "linear")
        self.assertTrue(receipt["retryable"])
        self.assertEqual(report["summary"]["status"], "partial")

    def test_optional_source_must_be_explicit_but_may_be_excluded(self) -> None:
        fixture = self.raw_fixture_for(
            required=["chatgpt", "claude", "linear", "github", "local_repo"],
            optional=["file_library"],
        )
        optional = self.receipt(fixture, "file_library")
        optional.update(reported_state="excluded", error_class=None, retryable=False)
        optional["pagination"] = {
            "started": False,
            "complete": False,
            "last_page_complete": False,
            "pages_read": 0,
            "items_read": 0,
            "terminal_cursor_sha256": None,
        }
        report = build_source_coverage_report(fixture, now=NOW)
        self.assertEqual(report["summary"]["status"], "complete")
        self.assertEqual(report["summary"]["source_states"]["file_library"], "excluded")

    def test_unexpected_optional_source_cannot_silently_appear(self) -> None:
        fixture = self.raw_fixture()
        extra = copy.deepcopy(fixture["receipts"][0])
        extra["source"] = "file_library"
        extra["source_identity_sha256"] = "b" * 64
        fixture["receipts"].append(extra)
        with self.assertRaisesRegex(RecoveryError, "outside policy"):
            build_source_coverage_report(fixture, now=NOW)

    def test_unknown_fields_are_rejected_before_publication(self) -> None:
        fixture = self.raw_fixture()
        self.receipt(fixture, "chatgpt")["error_detail"] = "token=" + "ghp_" + "a" * 36
        with self.assertRaisesRegex(RecoveryError, "unsupported keys"):
            build_source_coverage_report(fixture, now=NOW)

    def test_credential_shaped_identity_cannot_enter_digest_only_contract(self) -> None:
        fixture = self.raw_fixture()
        self.receipt(fixture, "chatgpt")["source_identity_sha256"] = "ghp_" + "a" * 60
        with self.assertRaisesRegex(RecoveryError, "lowercase SHA-256"):
            build_source_coverage_report(fixture, now=NOW)

    def test_future_capture_beyond_clock_skew_is_rejected(self) -> None:
        fixture = self.raw_fixture()
        self.receipt(fixture, "github")["captured_at"] = "2026-08-10T19:00:01Z"
        with self.assertRaisesRegex(RecoveryError, "clock skew"):
            build_source_coverage_report(fixture, now=NOW)

    def test_tampered_summary_and_digest_are_rejected(self) -> None:
        report = build_source_coverage_report(self.raw_fixture(), now=NOW)
        tampered_summary = copy.deepcopy(report)
        tampered_summary["summary"]["status"] = "partial"
        with self.assertRaisesRegex(RecoveryError, "summary does not match"):
            build_source_coverage_report(tampered_summary, now=NOW)
        tampered_digest = copy.deepcopy(report)
        tampered_digest["report_sha256"] = "0" * 64
        with self.assertRaisesRegex(RecoveryError, "report_sha256"):
            build_source_coverage_report(tampered_digest, now=NOW)

    def test_example_is_synthetic_complete_and_round_trips(self) -> None:
        report = build_example_source_coverage(now=NOW)
        self.assertEqual(report["summary"]["status"], "complete")
        self.assertEqual(len(report["receipts"]), 5)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coverage.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            restored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(build_source_coverage_report(restored, now=NOW), report)


if __name__ == "__main__":
    unittest.main()
