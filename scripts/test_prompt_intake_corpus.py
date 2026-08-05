#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
FIXTURE = REPO_ROOT / "fixtures" / "prompt-intake" / "chatgpt-60-item-corpus.json"
sys.path.insert(0, str(SCRIPT_DIR))

import prompt_intake_corpus as corpus  # noqa: E402
import expand_prompt_intake_fixture as expander  # noqa: E402


class PromptIntakeCorpusTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.fixture = expander.load_compact(FIXTURE)

    def test_60_item_corpus_reproduces_canonical_disposition_without_creates(self) -> None:
        corpus.validate_corpus(self.fixture)
        expected = corpus.derive_expected(self.fixture)
        self.assertEqual(60, expected["items"])
        self.assertEqual(48, expected["live"])
        self.assertEqual(12, expected["reference_only"])
        self.assertEqual(0, expected["planned_creates"])
        self.assertEqual(
            {
                "backlog": 1,
                "done": 8,
                "duplicate": 4,
                "in_progress": 40,
                "todo": 7,
            },
            expected["status_counts"],
        )
        self.assertEqual(
            {"amend_existing": 48, "no_op_reference": 12},
            expected["decision_counts"],
        )

    def test_fixture_is_deterministic_and_public_safe(self) -> None:
        first = corpus.summarize_corpus(self.fixture)
        second = corpus.summarize_corpus(expander.load_compact(FIXTURE))
        self.assertEqual(first, second)
        self.assertEqual(corpus.corpus_digest(self.fixture), first["corpus_digest"])
        serialized = corpus.canonical_json(self.fixture)
        for key in corpus.FORBIDDEN_RECORD_KEYS:
            self.assertNotIn(f'"{key}":', serialized)
        corpus.validate_public_safety(self.fixture)

    def test_duplicate_relations_cover_exactly_the_four_reference_issues(self) -> None:
        duplicate_items = {
            item["canonical_issue"]: item["duplicate_of"]
            for item in self.fixture["items"]
            if item["status"] == "duplicate"
        }
        self.assertEqual(
            {
                "DEN-1042": "DEN-1041",
                "DEN-39": "DEN-28",
                "DEN-43": "DEN-315",
                "DEN-452": "DEN-315",
            },
            duplicate_items,
        )
        relation_pairs = {
            (case["source_issue"], case["target_issue"])
            for case in self.fixture["relation_cases"]
            if case["kind"] == "duplicate_issue"
        }
        self.assertEqual(set(duplicate_items.items()), relation_pairs)

    def test_material_refinement_preserves_only_bounded_relation_metadata(self) -> None:
        refinements = [
            case
            for case in self.fixture["relation_cases"]
            if case["kind"] == "material_refinement"
        ]
        self.assertEqual(1, len(refinements))
        refinement = refinements[0]
        self.assertEqual("DEN-834", refinement["canonical_issue"])
        self.assertEqual("amend_existing", refinement["expected_decision"])
        self.assertGreaterEqual(len(refinement["source_fingerprints"]), 2)
        self.assertTrue(set(refinement["material_fields"]) <= corpus.MATERIAL_FIELDS)

    def test_post_cutoff_case_changes_only_one_expected_decision_and_counters(self) -> None:
        cutoff = corpus.parse_timestamp(self.fixture["cutoff_at"], "cutoff_at")
        observed = cutoff + timedelta(minutes=1)
        source_days = self.fixture["retention_policy"]["source_metadata_days"]
        receipt_days = self.fixture["retention_policy"]["receipt_days"]
        source_fingerprint = hashlib.sha256(b"synthetic-post-cutoff-source").hexdigest()
        recorded = observed + timedelta(seconds=1)
        item = {
            "fixture_id": "chat-061",
            "source": {
                "kind": "synthetic_redacted",
                "observed_at": observed.isoformat().replace("+00:00", "Z"),
                "source_fingerprint": source_fingerprint,
                "retained_until": (observed + timedelta(days=source_days))
                .isoformat()
                .replace("+00:00", "Z"),
            },
            "canonical_issue": "DEN-1613",
            "project_key": "ai-agent-coordinator",
            "priority": "high",
            "status": "in_progress",
            "classification": "repository_work",
            "decision": "amend_existing",
            "exclusion_reason": None,
            "duplicate_of": None,
            "receipt": {
                "kind": "acceptance_oracle",
                "operation_id": hashlib.sha256(b"post-cutoff-operation").hexdigest(),
                "recorded_at": recorded.isoformat().replace("+00:00", "Z"),
                "result_fingerprint": hashlib.sha256(b"post-cutoff-result").hexdigest(),
                "retained_until": (recorded + timedelta(days=receipt_days))
                .isoformat()
                .replace("+00:00", "Z"),
            },
        }
        before = corpus.canonical_json(self.fixture["items"])
        simulation = corpus.simulate_post_cutoff(self.fixture, item)
        after = corpus.canonical_json(self.fixture["items"])
        self.assertEqual(before, after)
        self.assertTrue(simulation["existing_items_unchanged"])
        self.assertEqual(
            {
                "items": 1,
                "live": 1,
                "reference_only": 0,
                "planned_creates": 0,
                "status_counts": {"in_progress": 1},
                "decision_counts": {"amend_existing": 1},
                "classification_counts": {"repository_work": 1},
                "exclusion_counts": {},
            },
            simulation["delta"],
        )
        self.assertEqual("DEN-1613", simulation["post_cutoff_decision"]["canonical_issue"])

    def test_retention_purges_source_metadata_without_corrupting_receipts(self) -> None:
        first = self.fixture["items"][0]
        original_receipt = copy.deepcopy(first["receipt"])
        state = corpus.purge_retained_metadata(
            self.fixture,
            datetime(2026, 9, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(60, state["counts"]["source_metadata_purged"])
        self.assertEqual(0, state["counts"]["receipts_purged"])
        retained = state["items"][0]
        self.assertEqual({"state": "purged"}, retained["source"])
        self.assertEqual("retained", retained["receipt"].pop("state"))
        self.assertEqual(original_receipt, retained["receipt"])
        self.assertEqual(first["canonical_issue"], retained["canonical_issue"])

    def test_receipt_expiry_leaves_only_a_nonsecret_tombstone(self) -> None:
        state = corpus.purge_retained_metadata(
            self.fixture,
            datetime(2027, 9, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(60, state["counts"]["source_metadata_purged"])
        self.assertEqual(60, state["counts"]["receipts_purged"])
        tombstone = state["items"][0]["receipt"]
        self.assertEqual({"state", "receipt_digest"}, set(tombstone))
        self.assertRegex(tombstone["receipt_digest"], r"^[0-9a-f]{64}$")
        self.assertNotIn("operation_id", tombstone)
        self.assertNotIn("result_fingerprint", tombstone)

    def test_telemetry_names_and_labels_are_strictly_bounded(self) -> None:
        telemetry = corpus.build_telemetry(self.fixture)
        corpus.validate_bounded_telemetry(telemetry)
        self.assertEqual(
            corpus.METRIC_NAMES,
            {metric["name"] for metric in telemetry["metrics"]},
        )
        for metric in telemetry["metrics"]:
            self.assertLessEqual(len(metric["labels"]), corpus.MAX_TELEMETRY_LABELS)
            for key, value in metric["labels"].items():
                self.assertIn(key, corpus.METRIC_LABEL_ALLOWLIST)
                self.assertIn(value, corpus.METRIC_LABEL_ALLOWLIST[key])
                self.assertNotRegex(value, r"DEN-[0-9]+")

    def test_public_safety_rejects_credentials_personal_data_and_sensitive_fields(self) -> None:
        credential = "gh" + "p_" + ("A" * 30)
        api_key = "sk" + "-" + ("B" * 30)
        email = "person" + "@" + "example.invalid"
        for unsafe in (
            {"value": credential},
            {"value": api_key},
            {"value": "Bearer " + ("C" * 16)},
            {"value": email},
            {"prompt_body": "not retained"},
            {"private_key": "not retained"},
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(corpus.CorpusError):
                    corpus.validate_public_safety(unsafe)

    def test_loader_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text(
                '{"schema_version":1,"schema_version":1}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(corpus.CorpusError, "duplicate JSON key"):
                corpus.load_json(path)

    def test_validator_rejects_count_drift_and_relation_drift(self) -> None:
        count_drift = copy.deepcopy(self.fixture)
        count_drift["expected"]["items"] = 59
        with self.assertRaisesRegex(corpus.CorpusError, "expected counts drifted"):
            corpus.validate_corpus(count_drift)

        relation_drift = copy.deepcopy(self.fixture)
        relation_drift["relation_cases"][0]["target_issue"] = "DEN-315"
        with self.assertRaisesRegex(corpus.CorpusError, "disagrees with its item"):
            corpus.validate_corpus(relation_drift)

    def test_daily_briefing_is_complete_disjoint_and_reference_safe(self) -> None:
        briefing = self.fixture["daily_briefing"]
        all_ids = (
            set(briefing["do_today"])
            | set(briefing["monitor"])
            | set(briefing["ignore"])
        )
        self.assertEqual(60, len(all_ids))
        self.assertFalse(set(briefing["do_today"]) & set(briefing["monitor"]))
        self.assertFalse(set(briefing["do_today"]) & set(briefing["ignore"]))
        self.assertFalse(set(briefing["monitor"]) & set(briefing["ignore"]))
        ignored_statuses = {
            item["status"]
            for item in self.fixture["items"]
            if item["canonical_issue"] in briefing["ignore"]
        }
        self.assertTrue(ignored_statuses <= corpus.REFERENCE_STATUSES)

    def test_cli_validate_and_summary_are_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary_path = Path(directory) / "summary.json"
            expanded_path = Path(directory) / "expanded.json"
            self.assertEqual(
                0,
                expander.main(
                    [str(FIXTURE), "--output", str(expanded_path)]
                ),
            )
            self.assertEqual(0, corpus.main(["validate", str(expanded_path)]))
            self.assertEqual(
                0,
                corpus.main(
                    [
                        "summarize",
                        str(expanded_path),
                        "--output",
                        str(summary_path),
                    ]
                ),
            )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual("pass", summary["status"])
            self.assertEqual(60, summary["counts"]["items"])
            self.assertEqual(0, summary["counts"]["planned_creates"])
            self.assertEqual(
                {"do_today", "monitor", "ignore"},
                set(summary["daily_briefing"]),
            )


if __name__ == "__main__":
    unittest.main()
