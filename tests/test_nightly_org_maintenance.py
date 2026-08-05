from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "nightly_org_maintenance.py"
SPEC = importlib.util.spec_from_file_location("nightly_org_maintenance", MODULE_PATH)
assert SPEC and SPEC.loader
maintenance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = maintenance
SPEC.loader.exec_module(maintenance)


def registry_fixture() -> dict:
    return {
        "mappings": [
            {
                "github": {
                    "login": "alpha-org",
                    "account_id": 101,
                    "account_type": "Organization",
                },
                "github_app": {"installation_id": 1001},
                "linear": {
                    "project_id": "project-alpha",
                    "project_name": "github.com/alpha-org",
                    "project_url": "https://linear.app/example/project/alpha",
                },
                "runtime_route": {
                    "default_repository": "alpha-org/alpha-api",
                },
            },
            {
                "github": {
                    "login": "alpha-org-test",
                    "account_id": 102,
                    "account_type": "Organization",
                },
                "github_app": {"installation_id": 1002},
                "linear": {
                    "project_id": "project-alpha-test",
                    "project_name": "github.com/alpha-org-test",
                    "project_url": "https://linear.app/example/project/alpha-test",
                },
                "runtime_route": None,
            },
            {
                "github": {
                    "login": "disabled-org",
                    "account_id": 103,
                    "account_type": "Organization",
                },
                "github_app": {"installation_id": 1003},
                "linear": {
                    "project_id": "project-disabled",
                    "project_name": "github.com/disabled-org",
                    "project_url": "https://linear.app/example/project/disabled",
                },
                "runtime_route": None,
            },
            {
                "github": {
                    "login": "person-login",
                    "account_id": 104,
                    "account_type": "User",
                },
                "github_app": {"installation_id": 1004},
                "linear": {
                    "project_id": "project-person",
                    "project_name": "github.com/person-login",
                    "project_url": "https://linear.app/example/project/person",
                },
                "runtime_route": None,
            },
        ]
    }


def policy_fixture() -> dict:
    return {
        "max_organizations_per_run": 100,
        "priority_owners": ["alpha-org"],
        "disabled_owners": ["disabled-org"],
        "min_new_prs_per_org": 1,
        "max_new_prs_per_org": 3,
        "max_existing_pr_merges_per_org": 3,
        "max_changed_files_per_pr": 40,
        "max_changed_bytes_per_pr": 512000,
        "protected_paths": [".github/workflows/**", "**/migrations/**"],
        "merge_label": "agent:nightly",
    }


def snapshot_fixture() -> dict:
    return {
        "schema_version": "nightly_org_snapshot.v1",
        "owner": "alpha-org",
        "github": {
            "repositories": [
                {
                    "full_name": "alpha-org/alpha-api",
                    "default_branch": "main",
                    "open_pull_requests": [
                        {
                            "number": 7,
                            "title": "Improve validation",
                            "head_sha": "a" * 40,
                            "labels": ["agent:nightly"],
                        }
                    ],
                },
                {
                    "full_name": "alpha-org/alpha-cli",
                    "default_branch": "main",
                    "open_pull_requests": [],
                },
            ]
        },
        "linear": {
            "issues": [
                {
                    "identifier": "DEN-123",
                    "title": "Improve API validation",
                }
            ]
        },
        "policy": {
            "new_pull_requests": {"minimum": 1, "maximum": 3},
            "maximum_existing_pr_merges": 3,
            "max_changed_files_per_pr": 40,
            "max_changed_bytes_per_pr": 512000,
            "protected_paths": [".github/workflows/**", "**/migrations/**"],
            "merge_label": "agent:nightly",
        },
    }


def valid_plan() -> dict:
    return {
        "schema_version": "nightly_org_plan.v1",
        "owner": "alpha-org",
        "summary": "Add one focused validation repair and consider one existing PR.",
        "new_pr_tasks": [
            {
                "repository": "alpha-org/alpha-api",
                "title": "Add malformed request regression coverage",
                "goal": "Add focused regression coverage for malformed request handling without changing the public API.",
                "acceptance": [
                    "Malformed input returns the documented error.",
                    "The focused test suite passes.",
                ],
                "linear_issue": "DEN-123",
                "risk": "low",
                "protected_area": False,
                "source_pr": None,
            }
        ],
        "merge_candidates": [
            {
                "repository": "alpha-org/alpha-api",
                "number": 7,
                "head_sha": "a" * 40,
                "action": "merge_if_green",
                "rationale": "The intent is current and can be independently revalidated.",
            }
        ],
    }


class ScheduleTests(unittest.TestCase):
    def test_lima_midnight_thirty_is_due(self) -> None:
        decision = maintenance.schedule_decision(
            datetime(2026, 8, 5, 5, 30, tzinfo=timezone.utc)
        )
        self.assertTrue(decision.due)
        self.assertEqual(decision.local_time.hour, 0)
        self.assertEqual(decision.local_time.minute, 30)
        self.assertEqual(decision.run_key, "nightly-org-maintenance:2026-08-05")

    def test_other_minute_is_not_due_without_force(self) -> None:
        decision = maintenance.schedule_decision(
            datetime(2026, 8, 5, 5, 31, tzinfo=timezone.utc)
        )
        self.assertFalse(decision.due)
        self.assertTrue(
            maintenance.schedule_decision(
                datetime(2026, 8, 5, 5, 31, tzinfo=timezone.utc), force=True
            ).due
        )

    def test_lima_one_am_is_not_due(self) -> None:
        decision = maintenance.schedule_decision(
            datetime(2026, 8, 5, 6, 0, tzinfo=timezone.utc)
        )
        self.assertFalse(decision.due)


class MatrixTests(unittest.TestCase):
    def test_matrix_uses_registry_and_includes_mapped_test_org(self) -> None:
        matrix = maintenance.build_matrix(registry_fixture(), policy_fixture())
        owners = [item["owner"] for item in matrix["include"]]
        self.assertEqual(owners, ["alpha-org", "alpha-org-test"])
        alpha = matrix["include"][0]
        self.assertEqual(alpha["paired_test_owner"], "alpha-org-test")
        self.assertEqual(alpha["default_repository"], "alpha-org/alpha-api")
        test = matrix["include"][1]
        self.assertTrue(test["is_test_org"])
        self.assertEqual(test["base_owner"], "alpha-org")

    def test_unknown_owner_filter_fails_closed(self) -> None:
        with self.assertRaisesRegex(maintenance.MaintenanceError, "absent from the registry"):
            maintenance.build_matrix(
                registry_fixture(), policy_fixture(), owners=["missing-org"]
            )


class PlanValidationTests(unittest.TestCase):
    def test_valid_plan_is_normalized(self) -> None:
        normalized = maintenance.validate_plan(valid_plan(), snapshot_fixture())
        self.assertEqual(len(normalized["new_pr_tasks"]), 1)
        self.assertEqual(normalized["merge_candidates"][0]["action"], "merge_if_green")

    def test_task_cannot_reference_unmapped_issue(self) -> None:
        plan = valid_plan()
        plan["new_pr_tasks"][0]["linear_issue"] = "DEN-999"
        with self.assertRaisesRegex(maintenance.MaintenanceError, "outside the project snapshot"):
            maintenance.validate_plan(plan, snapshot_fixture())

    def test_repair_candidate_requires_matching_source_task(self) -> None:
        plan = valid_plan()
        plan["merge_candidates"][0]["action"] = "repair_with_replacement_pr"
        with self.assertRaisesRegex(maintenance.MaintenanceError, "matching new_pr_task"):
            maintenance.validate_plan(plan, snapshot_fixture())

    def test_source_head_sha_must_match_snapshot(self) -> None:
        plan = valid_plan()
        plan["new_pr_tasks"][0]["source_pr"] = {
            "repository": "alpha-org/alpha-api",
            "number": 7,
            "head_sha": "b" * 40,
        }
        plan["merge_candidates"][0]["action"] = "repair_with_replacement_pr"
        with self.assertRaisesRegex(maintenance.MaintenanceError, "head SHA changed"):
            maintenance.validate_plan(plan, snapshot_fixture())

    def test_only_one_task_per_repository(self) -> None:
        plan = valid_plan()
        duplicate = json.loads(json.dumps(plan["new_pr_tasks"][0]))
        duplicate["title"] = "Add another malformed request regression test"
        plan["new_pr_tasks"].append(duplicate)
        with self.assertRaisesRegex(maintenance.MaintenanceError, "at most one new PR"):
            maintenance.validate_plan(plan, snapshot_fixture())


class ResultValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.repo = self.workspace / "repositories" / "alpha-org" / "alpha-api"
        self.repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Nightly Test"], cwd=self.repo, check=True
        )
        (self.repo / "README.md").write_text("# Alpha\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.repo, check=True, capture_output=True)
        (self.repo / "README.md").write_text(
            "# Alpha\n\nMalformed requests return a structured error.\n", encoding="utf-8"
        )
        self.plan = maintenance.validate_plan(valid_plan(), snapshot_fixture())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def result(self) -> dict:
        return {
            "schema_version": "nightly_org_result.v1",
            "owner": "alpha-org",
            "summary": "Added focused malformed-request documentation and evidence.",
            "changes": [
                {
                    "repository": "alpha-org/alpha-api",
                    "branch": "agent/nightly-123456-1-request-validation",
                    "title": "Document malformed request behavior",
                    "body": "Documents the existing malformed-request contract and the focused validation evidence used by this bounded change.",
                    "commit_message": "Document malformed request behavior",
                    "requires_human_review": False,
                    "tests": [
                        {
                            "command": "cargo test malformed_request",
                            "outcome": "passed",
                            "evidence": "Focused regression test passed locally.",
                        }
                    ],
                }
            ],
        }

    def test_valid_result_inspects_real_git_changes(self) -> None:
        normalized = maintenance.validate_result(
            self.result(), self.plan, snapshot_fixture(), self.workspace
        )
        change = normalized["changes"][0]
        self.assertEqual(change["changed_files"], ["README.md"])
        self.assertGreater(change["changed_bytes"], 0)

    def test_conflict_marker_is_rejected(self) -> None:
        (self.repo / "README.md").write_text(
            "<<<<<<< HEAD\nleft\n=======\nright\n>>>>>>> branch\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(maintenance.MaintenanceError, "conflict markers"):
            maintenance.validate_result(
                self.result(), self.plan, snapshot_fixture(), self.workspace
            )

    def test_credential_shaped_content_is_rejected(self) -> None:
        (self.repo / "README.md").write_text(
            "token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(maintenance.MaintenanceError, "credential-shaped"):
            maintenance.validate_result(
                self.result(), self.plan, snapshot_fixture(), self.workspace
            )

    def test_known_failed_test_blocks_publication(self) -> None:
        result = self.result()
        result["changes"][0]["tests"][0]["outcome"] = "failed"
        with self.assertRaisesRegex(maintenance.MaintenanceError, "known failing validation"):
            maintenance.validate_result(
                result, self.plan, snapshot_fixture(), self.workspace
            )

    def test_protected_path_requires_explicit_gate(self) -> None:
        workflow = self.repo / ".github" / "workflows" / "ci.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("name: test\n", encoding="utf-8")
        result = self.result()
        with self.assertRaisesRegex(maintenance.MaintenanceError, "protected path"):
            maintenance.validate_result(
                result, self.plan, snapshot_fixture(), self.workspace
            )


class SanitizationTests(unittest.TestCase):
    def test_clean_text_redacts_credential_shaped_content(self) -> None:
        cleaned = maintenance._clean_text(
            "Authorization: Bearer ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
            limit=500,
        )
        self.assertNotIn("ghp_", cleaned)
        self.assertIn("[REDACTED_CREDENTIAL]", cleaned)


class CheckRollupTests(unittest.TestCase):
    def test_checks_must_exist_and_be_green(self) -> None:
        self.assertFalse(maintenance._status_checks_green([]))
        self.assertFalse(
            maintenance._status_checks_green(
                [{"status": "COMPLETED", "conclusion": "FAILURE"}]
            )
        )
        self.assertTrue(
            maintenance._status_checks_green(
                [
                    {"status": "COMPLETED", "conclusion": "SUCCESS"},
                    {"status": "COMPLETED", "conclusion": "SKIPPED"},
                ]
            )
        )
        self.assertTrue(maintenance._status_checks_green([{"state": "SUCCESS"}]))


if __name__ == "__main__":
    unittest.main()
