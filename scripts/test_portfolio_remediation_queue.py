#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("portfolio_remediation_queue.py")
SPEC = importlib.util.spec_from_file_location("portfolio_remediation_queue", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "portfolio-remediation-queue-v1.json"
)


class PortfolioRemediationQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = MODULE.load(FIXTURE)

    def report(self) -> object:
        return MODULE.validate(self.queue)

    def assert_error(self, report: object, fragment: str) -> None:
        errors = getattr(report, "errors")
        self.assertTrue(
            any(fragment in error for error in errors),
            f"expected {fragment!r} in {errors!r}",
        )

    def test_valid_queue(self) -> None:
        report = self.report()
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.repository_count, 5)
        self.assertEqual(report.finding_count, 7)
        self.assertEqual(report.actionable_count, 5)
        self.assertEqual(report.blocked_count, 1)
        self.assertEqual(report.noop_count, 2)
        self.assertRegex(report.queue_sha256 or "", r"^[0-9a-f]{64}$")

    def test_canonical_hash_is_key_order_invariant(self) -> None:
        first = MODULE.canonical_sha256(self.queue)
        reordered = dict(reversed(list(self.queue.items())))
        self.assertEqual(first, MODULE.canonical_sha256(reordered))

    def test_baseline_cannot_follow_generation(self) -> None:
        self.queue["baseline"]["observed_at"] = "2026-09-04T12:00:00-04:00"
        report = self.report()
        self.assert_error(report, "cannot follow generated_at")

    def test_baseline_cannot_be_older_than_31_days(self) -> None:
        self.queue["baseline"]["observed_at"] = "2026-07-01T00:00:00-04:00"
        report = self.report()
        self.assert_error(report, "older than the 31-day")

    def test_repository_count_must_match(self) -> None:
        self.queue["baseline"]["repository_count"] = 6
        report = self.report()
        self.assert_error(report, "repository_count must equal")

    def test_duplicate_repository_identity_fails(self) -> None:
        self.queue["repositories"][1]["repository"] = self.queue[
            "repositories"
        ][0]["repository"]
        report = self.report()
        self.assert_error(report, "duplicate identities")

    def test_repository_identity_format_is_enforced(self) -> None:
        self.queue["repositories"][0]["repository"] = "missing-owner"
        report = self.report()
        self.assert_error(report, "owner/repository identity")

    def test_active_repository_requires_default_branch(self) -> None:
        self.queue["repositories"][0]["default_branch"] = None
        report = self.report()
        self.assert_error(report, "default_branch is required")

    def test_active_repository_requires_observed_head(self) -> None:
        self.queue["repositories"][0]["observed_head"] = None
        report = self.report()
        self.assert_error(report, "observed_head is required")

    def test_unsafe_default_branch_fails(self) -> None:
        self.queue["repositories"][0]["default_branch"] = "feature/../main"
        report = self.report()
        self.assert_error(report, "not a safe Git branch name")

    def test_invalid_observed_head_fails(self) -> None:
        self.queue["repositories"][0]["observed_head"] = "main"
        report = self.report()
        self.assert_error(report, "40-character commit SHA")

    def test_linear_project_host_is_restricted(self) -> None:
        self.queue["repositories"][0]["linear_project_url"] = (
            "https://example.test/project/shared-auth"
        )
        report = self.report()
        self.assert_error(report, "host is not allowed")

    def test_project_urls_reject_queries(self) -> None:
        self.queue["repositories"][0]["github_project_url"] += "?tracking=1"
        report = self.report()
        self.assert_error(report, "without userinfo, query, or fragment")

    def test_unsupported_canonical_role_fails(self) -> None:
        self.queue["repositories"][0]["canonical_role"] = "everything"
        report = self.report()
        self.assert_error(report, "canonical_role is unsupported")

    def test_unsupported_ci_state_fails(self) -> None:
        self.queue["repositories"][0]["ci_state"] = "greenish"
        report = self.report()
        self.assert_error(report, "ci_state is unsupported")

    def test_security_policy_must_be_boolean(self) -> None:
        self.queue["repositories"][0]["security_policy"] = "yes"
        report = self.report()
        self.assert_error(report, "security_policy must be boolean")

    def test_finding_repository_must_exist_in_inventory(self) -> None:
        self.queue["findings"][0]["repository"] = "unknown/repository"
        report = self.report()
        self.assert_error(report, "absent from the inventory")

    def test_duplicate_finding_id_fails(self) -> None:
        self.queue["findings"][1]["id"] = self.queue["findings"][0]["id"]
        report = self.report()
        self.assert_error(report, "duplicate finding id")

    def test_linear_anchor_format_is_enforced(self) -> None:
        self.queue["findings"][0]["linear_anchor"] = "not-an-issue"
        report = self.report()
        self.assert_error(report, "must be a DEN-N identifier")

    def test_github_anchor_must_be_exact_issue_or_pr(self) -> None:
        self.queue["findings"][0]["github_anchor"] = (
            "https://github.com/ORESoftware/ai-agent-coordinator.rs/tree/main"
        )
        report = self.report()
        self.assert_error(report, "exact GitHub issue or pull-request URL")

    def test_github_anchor_rejects_query(self) -> None:
        self.queue["findings"][0]["github_anchor"] += "?x=1"
        report = self.report()
        self.assert_error(report, "without userinfo, query, or fragment")

    def test_nonduplicate_findings_cannot_share_linear_anchor(self) -> None:
        self.queue["findings"][1]["linear_anchor"] = self.queue["findings"][0][
            "linear_anchor"
        ]
        report = self.report()
        self.assert_error(report, "shared by non-duplicate findings")

    def test_nonduplicate_findings_cannot_share_github_anchor(self) -> None:
        self.queue["findings"][1]["github_anchor"] = self.queue["findings"][0][
            "github_anchor"
        ]
        report = self.report()
        self.assert_error(report, "shared by non-duplicate findings")

    def test_canonical_duplicate_can_share_tracking_anchors(self) -> None:
        target = self.queue["findings"][2]
        duplicate = self.queue["findings"][6]
        duplicate["state"] = "superseded"
        duplicate["disposition"] = "noop_duplicate"
        duplicate["duplicate_of"] = target["id"]
        duplicate["linear_anchor"] = target["linear_anchor"]
        duplicate["github_anchor"] = target["github_anchor"]
        report = self.report()
        self.assertTrue(report.valid, report.errors)

    def test_duplicate_target_must_exist(self) -> None:
        duplicate = self.queue["findings"][6]
        duplicate["state"] = "superseded"
        duplicate["disposition"] = "noop_duplicate"
        duplicate["duplicate_of"] = "missing-finding"
        report = self.report()
        self.assert_error(report, "references unknown target")

    def test_duplicate_cannot_target_duplicate(self) -> None:
        first = self.queue["findings"][5]
        second = self.queue["findings"][6]
        first["state"] = "superseded"
        first["disposition"] = "noop_duplicate"
        first["duplicate_of"] = self.queue["findings"][2]["id"]
        second["state"] = "superseded"
        second["disposition"] = "noop_duplicate"
        second["duplicate_of"] = first["id"]
        report = self.report()
        self.assert_error(report, "cannot target another duplicate")

    def test_actionable_disposition_rejects_completed_state(self) -> None:
        self.queue["findings"][0]["state"] = "already_remediated"
        report = self.report()
        self.assert_error(report, "cannot use an actionable disposition")

    def test_noop_blocked_requires_external_blocker(self) -> None:
        self.queue["findings"][5]["state"] = "drifted"
        report = self.report()
        self.assert_error(report, "requires externally_blocked state")

    def test_noop_cannot_declare_branch_or_base(self) -> None:
        self.queue["findings"][5]["remediation"]["branch_name"] = (
            "feat/should-not-exist"
        )
        report = self.report()
        self.assert_error(report, "must not declare a branch or base")

    def test_actionable_branch_is_required(self) -> None:
        self.queue["findings"][0]["remediation"]["branch_name"] = None
        report = self.report()
        self.assert_error(report, "must be a string")

    def test_actionable_branch_must_be_safe(self) -> None:
        self.queue["findings"][0]["remediation"]["branch_name"] = (
            "feat/../../main"
        )
        report = self.report()
        self.assert_error(report, "not a safe Git branch name")

    def test_apply_authorization_fails_closed(self) -> None:
        self.queue["findings"][0]["remediation"]["apply_authorized"] = True
        report = self.report()
        self.assert_error(report, "apply_authorized must be false")

    def test_dry_run_is_required(self) -> None:
        self.queue["findings"][0]["remediation"]["dry_run"] = False
        report = self.report()
        self.assert_error(report, "dry_run must be true")

    def test_finding_scores_are_bounded(self) -> None:
        self.queue["findings"][0]["scores"]["security"] = 101
        report = self.report()
        self.assert_error(report, "security must be an integer in 0..100")

    def test_missing_score_fails_complete_ranking(self) -> None:
        del self.queue["findings"][0]["scores"]["security"]
        report = self.report()
        self.assert_error(report, "complete deterministic ranking data")

    def test_ranks_must_be_contiguous(self) -> None:
        self.queue["findings"][1]["rank"] = 3
        report = self.report()
        self.assert_error(report, "ranks must be unique and contiguous")

    def test_ranks_must_follow_deterministic_priority(self) -> None:
        self.queue["findings"][3]["severity"] = "critical"
        self.queue["findings"][3]["scores"]["security"] = 100
        self.queue["findings"][3]["scores"]["data_integrity"] = 100
        report = self.report()
        self.assert_error(report, "must follow disposition tier")

    def test_unknown_dependency_fails(self) -> None:
        self.queue["findings"][0]["depends_on"] = ["missing-finding"]
        report = self.report()
        self.assert_error(report, "depends on unknown finding")

    def test_self_dependency_fails(self) -> None:
        finding = self.queue["findings"][0]
        finding["depends_on"] = [finding["id"]]
        report = self.report()
        self.assert_error(report, "cannot depend on itself")

    def test_dependency_must_rank_earlier(self) -> None:
        self.queue["findings"][0]["depends_on"] = [
            self.queue["findings"][4]["id"]
        ]
        report = self.report()
        self.assert_error(report, "must rank earlier")

    def test_dependency_cycle_fails(self) -> None:
        first = self.queue["findings"][0]
        fifth = self.queue["findings"][4]
        first["depends_on"] = [fifth["id"]]
        fifth["depends_on"] = [first["id"]]
        report = self.report()
        self.assert_error(report, "dependency cycle")

    def test_global_read_only_flag_is_required(self) -> None:
        self.queue["safety"]["read_only"] = False
        report = self.report()
        self.assert_error(report, "safety.read_only must be true")

    def test_global_merge_authorization_fails_closed(self) -> None:
        self.queue["safety"]["merge_authorized"] = True
        report = self.report()
        self.assert_error(report, "safety.merge_authorized must be false")

    def test_visibility_authorization_fails_closed(self) -> None:
        self.queue["safety"]["visibility_changes_authorized"] = True
        report = self.report()
        self.assert_error(report, "visibility_changes_authorized must be false")

    def test_forbidden_secret_field_fails(self) -> None:
        self.queue["findings"][0]["secret"] = "redacted"
        report = self.report()
        self.assert_error(report, "secret is prohibited")

    def test_unknown_root_field_fails(self) -> None:
        self.queue["unexpected"] = True
        report = self.report()
        self.assert_error(report, "keys mismatch")

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.json"
            path.write_text('{"queue_id":"one","queue_id":"two"}')
            with self.assertRaisesRegex(MODULE.QueueError, "duplicate JSON key"):
                MODULE.load(path)

    def test_invalid_utf8_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.json"
            path.write_bytes(b"\xff\xfe")
            with self.assertRaisesRegex(MODULE.QueueError, "must be UTF-8"):
                MODULE.load(path)

    def test_oversized_queue_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "large.json"
            path.write_bytes(b" " * (MODULE.MAX_BYTES + 1))
            with self.assertRaisesRegex(MODULE.QueueError, "exceeds"):
                MODULE.load(path)


if __name__ == "__main__":
    unittest.main()
