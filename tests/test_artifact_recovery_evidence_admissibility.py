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
from artifact_recovery.evidence_admissibility import (  # noqa: E402
    ADMISSIBILITY_SCHEMA,
    build_evidence_admissibility_report,
    build_example_evidence_admissibility,
)

NOW = "2026-08-10T20:00:00Z"


class EvidenceAdmissibilityTests(unittest.TestCase):
    def digest(self, *parts: str) -> str:
        return sha256_value({"fixture": list(parts)})

    def fixture(self) -> dict:
        risk_ttls = {
            "security": {
                "stale_after_seconds": 3600,
                "expires_after_seconds": 21600,
            },
            "deployment": {
                "stale_after_seconds": 7200,
                "expires_after_seconds": 43200,
            },
            "code": {
                "stale_after_seconds": 21600,
                "expires_after_seconds": 86400,
            },
            "documentation": {
                "stale_after_seconds": 604800,
                "expires_after_seconds": 2592000,
            },
        }
        policy_version = sha256_value(
            {"max_clock_skew_seconds": 300, "risk_ttls": risk_ttls}
        )
        subject_identity = self.digest("subject", "pr-146")
        subject_revision = self.digest("revision", "pr-146", "head-1")
        dependencies = {
            "pr_head": self.digest("pr-head", "head-1"),
            "pr_base": self.digest("pr-base", "base-1"),
            "dependency_graph": self.digest("dependencies", "v1"),
            "deployed_image": self.digest("image", "v1"),
            "required_check_set": self.digest("checks", "v1"),
            "workflow_policy": self.digest("workflow", "v1"),
        }
        return {
            "schema_version": ADMISSIBILITY_SCHEMA,
            "generated_at": NOW,
            "policy": {
                "version_sha256": policy_version,
                "max_clock_skew_seconds": 300,
                "risk_ttls": risk_ttls,
            },
            "subjects": [
                {
                    "identity_sha256": subject_identity,
                    "revision_sha256": subject_revision,
                    "dependencies": [
                        {"kind": kind, "digest": digest}
                        for kind, digest in dependencies.items()
                    ],
                }
            ],
            "evidence": [
                {
                    "identity_sha256": self.digest(
                        "evidence", "exact-head", "1"
                    ),
                    "kind": "exact_head_test",
                    "subject_identity_sha256": subject_identity,
                    "subject_revision_sha256": subject_revision,
                    "producer_sha256": self.digest("producer", "ci"),
                    "captured_at": "2026-08-10T19:50:00Z",
                    "policy_version_sha256": policy_version,
                    "payload_sha256": self.digest(
                        "payload", "test-report"
                    ),
                    "risk_class": "security",
                    "owner_sha256": self.digest("owner", "platform"),
                    "dependency_digests": [
                        {
                            "kind": "pr_head",
                            "digest": dependencies["pr_head"],
                        },
                        {
                            "kind": "pr_base",
                            "digest": dependencies["pr_base"],
                        },
                        {
                            "kind": "required_check_set",
                            "digest": dependencies["required_check_set"],
                        },
                        {
                            "kind": "workflow_policy",
                            "digest": dependencies["workflow_policy"],
                        },
                    ],
                }
            ],
        }

    def evidence(self, report: dict, index: int = 0) -> dict:
        return report["evidence"][index]

    def subject(self, fixture: dict) -> dict:
        return fixture["subjects"][0]

    def subject_dependencies(self, fixture: dict) -> dict[str, dict]:
        return {
            item["kind"]: item
            for item in self.subject(fixture)["dependencies"]
        }

    def test_current_report_is_deterministic_digest_bound_and_round_trips(
        self,
    ) -> None:
        first = build_evidence_admissibility_report(self.fixture(), now=NOW)
        second = build_evidence_admissibility_report(
            copy.deepcopy(first), now=NOW
        )
        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["status"], "complete")
        self.assertEqual(first["summary"]["current"], 1)
        self.assertEqual(
            first["recertification_queue"]["summary"]["items"], 0
        )
        without_digest = {
            key: value
            for key, value in first.items()
            if key != "report_sha256"
        }
        self.assertEqual(
            first["report_sha256"], sha256_value(without_digest)
        )
        self.assertEqual(canonical_json(first), canonical_json(second))

    def test_pr_head_change_invalidates_exact_head_evidence(self) -> None:
        fixture = self.fixture()
        self.subject_dependencies(fixture)["pr_head"]["digest"] = (
            self.digest("pr-head", "head-2")
        )
        report = build_evidence_admissibility_report(fixture, now=NOW)
        item = self.evidence(report)
        self.assertEqual(item["state"], "invalidated")
        self.assertEqual(item["changed_dependencies"], ["pr_head"])
        self.assertEqual(report["summary"]["status"], "blocked")
        self.assertEqual(report["summary"]["recertification_items"], 1)

    def test_base_movement_invalidates_base_sensitive_review(self) -> None:
        fixture = self.fixture()
        fixture["evidence"][0]["kind"] = "mergeability_review"
        self.subject_dependencies(fixture)["pr_base"]["digest"] = (
            self.digest("pr-base", "base-2")
        )
        report = build_evidence_admissibility_report(fixture, now=NOW)
        item = self.evidence(report)
        self.assertEqual(item["state"], "invalidated")
        self.assertEqual(item["changed_dependencies"], ["pr_base"])

    def test_deployment_repin_invalidates_runtime_health_without_deleting_history(
        self,
    ) -> None:
        fixture = self.fixture()
        evidence = fixture["evidence"][0]
        evidence["kind"] = "deployment_health"
        evidence["risk_class"] = "deployment"
        evidence["dependency_digests"] = [
            {
                "kind": "deployed_image",
                "digest": self.subject_dependencies(fixture)[
                    "deployed_image"
                ]["digest"],
            }
        ]
        original = copy.deepcopy(evidence)
        self.subject_dependencies(fixture)["deployed_image"]["digest"] = (
            self.digest("image", "v2")
        )
        report = build_evidence_admissibility_report(fixture, now=NOW)
        item = self.evidence(report)
        self.assertEqual(item["state"], "invalidated")
        self.assertEqual(
            item["changed_dependencies"], ["deployed_image"]
        )
        for key, value in original.items():
            self.assertEqual(
                item[key],
                value
                if key != "dependency_digests"
                else sorted(value, key=lambda entry: entry["kind"]),
            )
        immutable = {key: item[key] for key in original}
        self.assertEqual(item["record_sha256"], sha256_value(immutable))

    def test_policy_version_change_invalidates_certification(self) -> None:
        fixture = self.fixture()
        fixture["policy"]["risk_ttls"]["security"][
            "expires_after_seconds"
        ] = 18000
        fixture["policy"]["version_sha256"] = sha256_value(
            {
                "max_clock_skew_seconds": fixture["policy"][
                    "max_clock_skew_seconds"
                ],
                "risk_ttls": fixture["policy"]["risk_ttls"],
            }
        )
        report = build_evidence_admissibility_report(fixture, now=NOW)
        item = self.evidence(report)
        self.assertEqual(item["state"], "invalidated")
        self.assertIn("policy_version_changed", item["reason_codes"])

    def test_policy_digest_must_bind_clock_skew_and_ttls(self) -> None:
        fixture = self.fixture()
        fixture["policy"]["risk_ttls"]["security"][
            "expires_after_seconds"
        ] = 18000
        with self.assertRaisesRegex(RecoveryError, "must bind"):
            build_evidence_admissibility_report(fixture, now=NOW)

    def test_workflow_policy_digest_change_invalidates_certification(
        self,
    ) -> None:
        fixture = self.fixture()
        self.subject_dependencies(fixture)["workflow_policy"]["digest"] = (
            self.digest("workflow", "v2")
        )
        report = build_evidence_admissibility_report(fixture, now=NOW)
        self.assertEqual(self.evidence(report)["state"], "invalidated")
        self.assertEqual(
            self.evidence(report)["changed_dependencies"],
            ["workflow_policy"],
        )

    def test_low_risk_documentation_remains_current_while_security_expires(
        self,
    ) -> None:
        fixture = self.fixture()
        security = fixture["evidence"][0]
        security["captured_at"] = "2026-08-10T12:00:00Z"
        documentation = copy.deepcopy(security)
        documentation["identity_sha256"] = self.digest(
            "evidence", "docs", "1"
        )
        documentation["kind"] = "documentation"
        documentation["risk_class"] = "documentation"
        fixture["evidence"].append(documentation)
        report = build_evidence_admissibility_report(fixture, now=NOW)
        states = {
            item["kind"]: item["state"] for item in report["evidence"]
        }
        self.assertEqual(states["exact_head_test"], "expired")
        self.assertEqual(states["documentation"], "current")
        self.assertEqual(report["summary"]["recertification_items"], 1)

    def test_duplicate_stale_observations_create_one_recertification_item(
        self,
    ) -> None:
        fixture = self.fixture()
        first = fixture["evidence"][0]
        first["captured_at"] = "2026-08-10T18:30:00Z"
        second = copy.deepcopy(first)
        second["identity_sha256"] = self.digest(
            "evidence", "exact-head", "2"
        )
        second["payload_sha256"] = self.digest(
            "payload", "test-report", "2"
        )
        fixture["evidence"].append(second)
        report = build_evidence_admissibility_report(fixture, now=NOW)
        self.assertEqual(report["summary"]["stale"], 2)
        self.assertEqual(report["summary"]["recertification_items"], 1)
        queue_item = report["recertification_queue"]["items"][0]
        self.assertEqual(
            len(queue_item["evidence_identity_sha256s"]), 2
        )
        self.assertEqual(queue_item["observed_states"], ["stale"])

    def test_subject_revision_change_supersedes_without_rebinding(
        self,
    ) -> None:
        fixture = self.fixture()
        original_revision = fixture["evidence"][0][
            "subject_revision_sha256"
        ]
        self.subject(fixture)["revision_sha256"] = self.digest(
            "revision", "pr-146", "head-2"
        )
        report = build_evidence_admissibility_report(fixture, now=NOW)
        item = self.evidence(report)
        self.assertEqual(item["state"], "superseded")
        self.assertEqual(item["subject_revision_sha256"], original_revision)
        self.assertNotEqual(
            item["subject_revision_sha256"],
            item["current_subject_revision_sha256"],
        )

    def test_unresolved_subject_and_dependency_are_fail_closed(self) -> None:
        missing_subject = self.fixture()
        missing_subject["subjects"] = []
        report = build_evidence_admissibility_report(
            missing_subject, now=NOW
        )
        self.assertEqual(self.evidence(report)["state"], "unverifiable")
        self.assertEqual(
            self.evidence(report)["reason_codes"], ["subject_unresolved"]
        )

        missing_dependency = self.fixture()
        missing_dependency_subject = self.subject(missing_dependency)
        missing_dependency_subject["dependencies"] = [
            item
            for item in missing_dependency_subject["dependencies"]
            if item["kind"] != "pr_base"
        ]
        report = build_evidence_admissibility_report(
            missing_dependency, now=NOW
        )
        self.assertEqual(self.evidence(report)["state"], "unverifiable")
        self.assertEqual(
            self.evidence(report)["unresolved_dependencies"], ["pr_base"]
        )

    def test_future_capture_beyond_skew_is_rejected(self) -> None:
        fixture = self.fixture()
        fixture["evidence"][0]["captured_at"] = (
            "2026-08-10T20:05:01Z"
        )
        with self.assertRaisesRegex(RecoveryError, "clock skew"):
            build_evidence_admissibility_report(fixture, now=NOW)

    def test_unknown_fields_duplicate_identities_and_malformed_digests_are_rejected(
        self,
    ) -> None:
        unknown = self.fixture()
        unknown["evidence"][0]["raw_log"] = "not allowed"
        with self.assertRaisesRegex(RecoveryError, "unsupported keys"):
            build_evidence_admissibility_report(unknown, now=NOW)

        duplicate = self.fixture()
        duplicate["evidence"].append(
            copy.deepcopy(duplicate["evidence"][0])
        )
        with self.assertRaisesRegex(RecoveryError, "duplicate identities"):
            build_evidence_admissibility_report(duplicate, now=NOW)

        malformed = self.fixture()
        malformed["evidence"][0]["owner_sha256"] = "ghp_" + "a" * 60
        with self.assertRaisesRegex(RecoveryError, "lowercase SHA-256"):
            build_evidence_admissibility_report(malformed, now=NOW)

    def test_conflicting_owners_for_one_recertification_fingerprint_are_rejected(
        self,
    ) -> None:
        fixture = self.fixture()
        fixture["evidence"][0]["captured_at"] = (
            "2026-08-10T18:30:00Z"
        )
        second = copy.deepcopy(fixture["evidence"][0])
        second["identity_sha256"] = self.digest(
            "evidence", "exact-head", "2"
        )
        second["owner_sha256"] = self.digest("owner", "other")
        fixture["evidence"].append(second)
        with self.assertRaisesRegex(RecoveryError, "conflicting owners"):
            build_evidence_admissibility_report(fixture, now=NOW)

    def test_tampered_assessment_queue_summary_and_report_digest_are_rejected(
        self,
    ) -> None:
        report = build_evidence_admissibility_report(
            self.fixture(), now=NOW
        )

        tampered_assessment = copy.deepcopy(report)
        tampered_assessment["evidence"][0]["state"] = "expired"
        with self.assertRaisesRegex(RecoveryError, "derived assessment"):
            build_evidence_admissibility_report(
                tampered_assessment, now=NOW
            )

        tampered_summary = copy.deepcopy(report)
        tampered_summary["summary"]["status"] = "blocked"
        with self.assertRaisesRegex(
            RecoveryError, "summary does not match"
        ):
            build_evidence_admissibility_report(
                tampered_summary, now=NOW
            )

        tampered_queue = copy.deepcopy(report)
        tampered_queue["recertification_queue"]["queue_sha256"] = (
            "0" * 64
        )
        with self.assertRaisesRegex(
            RecoveryError, "recertification_queue"
        ):
            build_evidence_admissibility_report(tampered_queue, now=NOW)

        tampered_digest = copy.deepcopy(report)
        tampered_digest["report_sha256"] = "0" * 64
        with self.assertRaisesRegex(RecoveryError, "report_sha256"):
            build_evidence_admissibility_report(tampered_digest, now=NOW)

    def test_input_is_not_mutated(self) -> None:
        fixture = self.fixture()
        original = copy.deepcopy(fixture)
        build_evidence_admissibility_report(fixture, now=NOW)
        self.assertEqual(fixture, original)

    def test_example_is_synthetic_complete_and_round_trips(self) -> None:
        report = build_example_evidence_admissibility(now=NOW)
        self.assertEqual(report["summary"]["status"], "complete")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            restored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                build_evidence_admissibility_report(restored, now=NOW),
                report,
            )


if __name__ == "__main__":
    unittest.main()
