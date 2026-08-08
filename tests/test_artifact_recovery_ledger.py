from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "tools", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import artifact_recovery_ledger as recovery  # noqa: E402
import build_artifact_recovery_backfill as backfill  # noqa: E402

NOW = "2026-08-07T20:15:00Z"


class ArtifactRecoveryLedgerTests(unittest.TestCase):
    def fixture(self) -> dict:
        return backfill.build_fixture()

    def reconcile(self, value: dict, previous: dict | None = None, size: int = 50):
        return recovery.reconcile(
            value, previous, now=NOW, batch_size=size,
            target_task_id=recovery.DEFAULT_CLI_TASK_ID,
        )

    def test_refreshed_backfill_routes_only_genuinely_missing_repositories(self) -> None:
        ledger, queue = self.reconcile(self.fixture())
        self.assertEqual(ledger["summary"]["entries"], 17)
        self.assertEqual(ledger["summary"]["complete"], 9)
        self.assertEqual(ledger["summary"]["actionable"], 8)
        self.assertEqual(ledger["summary"]["blocked"], 0)
        self.assertEqual(queue["summary"], {"items": 8, "create_repository": 8, "recover_local": 0})
        expected = {
            "apostille-me/apme-mcp-server.rs", "apostille-me/apme-e2e",
            "embedded-alerts/eal-mcp-server.rs", "embedded-alerts/eal-e2e",
            "evento-globolo/evgl-mcp-server.rs", "evento-globolo/evgl-e2e",
            "hacker-house-medellin/hhm-mcp-server.rs", "hacker-house-medellin/hhm-e2e",
        }
        self.assertEqual({f"{item['owner']}/{item['repository']}" for item in queue["items"]}, expected)
        self.assertTrue(all(item["target_task_id"] == recovery.DEFAULT_CLI_TASK_ID for item in queue["items"]))
        self.assertTrue(all(item["visibility"] == "public" for item in queue["items"]))

    def test_existing_artifacts_reuse_remote_pr_evidence(self) -> None:
        ledger, queue = self.reconcile(self.fixture())
        identities = {
            "canonical-cloud/canonical-api-server.rs", "canonical-cloud/canonical-infra",
            "canonical-cloud/canonical-lib", "canonical-cloud/canonical-flutter",
            "oresoftware/slack-ores-integrations",
        }
        entries = {
            entry["observation"]["target"]["identity"]: entry
            for entry in ledger["entries"].values()
        }
        for identity in identities:
            with self.subTest(identity=identity):
                entry = entries[identity]
                self.assertEqual(entry["classification"]["status"], "complete")
                self.assertEqual(entry["classification"]["next_action"], "none")
                self.assertTrue(any("/pull/" in link for link in entry["evidence_links"]))
        queued = {f"{item['owner'].lower()}/{item['repository'].lower()}" for item in queue["items"]}
        self.assertTrue(identities.isdisjoint(queued))

    def test_identical_rerun_is_byte_stable_and_does_not_increment_attempts(self) -> None:
        first, first_queue = self.reconcile(self.fixture())
        second, second_queue = self.reconcile(self.fixture(), first)
        self.assertEqual(first, second)
        self.assertEqual(first_queue, second_queue)

    def test_material_change_increments_only_changed_entry(self) -> None:
        first, _ = self.reconcile(self.fixture())
        changed = self.fixture(); changed["items"][0]["note"] += " Refreshed."
        second, _ = self.reconcile(changed, first)
        changed_keys = [
            key for key in first["entries"]
            if first["entries"][key]["attempts"] != second["entries"][key]["attempts"]
        ]
        self.assertEqual(len(changed_keys), 1)
        self.assertEqual(second["entries"][changed_keys[0]]["attempts"], 2)

    def test_bounded_batch_preserves_cursor(self) -> None:
        ledger, queue = self.reconcile(self.fixture(), size=3)
        self.assertEqual(ledger["last_batch"]["processed"], 3)
        self.assertEqual(ledger["last_batch"]["available"], 17)
        self.assertEqual(ledger["last_batch"]["next_cursor"], "library-created-before:2026-08-01T23:41:07Z")
        self.assertLessEqual(len(queue["items"]), 3)

    def test_unspecified_new_repository_visibility_defaults_private(self) -> None:
        value = self.fixture()
        item = next(item for item in value["items"] if item["target"]["repository"] == "apme-e2e")
        item["target"]["visibility"] = None; value["items"] = [item]
        _, queue = self.reconcile(value)
        self.assertEqual(queue["items"][0]["visibility"], "private")

    def test_published_branch_without_pr_uses_connector_lane(self) -> None:
        item = copy.deepcopy(self.fixture()["items"][0])
        branch = "agent/den-2797-existing-branch"; sha = item["local"]["head_sha"]
        item["intent"].update(branch=branch, pull_request_required=True)
        item["local"].update(branch=branch, branches=[branch])
        item["remote"]["branches"].append({"name": branch, "sha": sha})
        item["claims"].update(branch=branch, pull_request_url=None)
        value = self.fixture(); value["items"] = [item]
        ledger, queue = self.reconcile(value)
        entry = next(iter(ledger["entries"].values()))
        self.assertEqual(entry["classification"]["next_action"], "open_draft_pull_request")
        self.assertEqual(queue["items"], [])

    def test_unverified_claim_fails_closed(self) -> None:
        item = copy.deepcopy(self.fixture()["items"][0])
        owner, repo = item["target"]["owner"], item["target"]["repository"]
        item["claims"]["pull_request_url"] = f"https://github.com/{owner}/{repo}/pull/999"
        value = self.fixture(); value["items"] = [item]
        ledger, queue = self.reconcile(value)
        entry = next(iter(ledger["entries"].values()))
        self.assertEqual(entry["classification"]["status"], "blocked")
        self.assertIn("claimed_pull_request_unverified", entry["classification"]["findings"])
        self.assertEqual(queue["items"], [])

    def test_ordinary_conversation_is_excluded(self) -> None:
        item = copy.deepcopy(self.fixture()["items"][0])
        item["origin"]["id"] = "conversation-no-artifact"
        item["target"]["artifact_kind"] = "none"; item["intent"]["artifact_expected"] = False
        item["local"] = {"artifact": None, "git_repository": False, "remote_present": False,
            "branch": None, "branches": [], "head_sha": None, "dirty_paths": []}
        item["remote"] = {"collected": True, "repository": {"exists": False, "visibility": None,
            "default_branch": None, "url": None}, "branches": [], "commits": [], "pull_requests": []}
        item["claims"] = {"repository_url": None, "commit_sha": None, "branch": None, "pull_request_url": None}
        value = self.fixture(); value["items"] = [item]
        ledger, queue = self.reconcile(value)
        self.assertEqual(next(iter(ledger["entries"].values()))["classification"]["status"], "excluded")
        self.assertEqual(queue["items"], [])

    def test_secret_shaped_content_is_rejected(self) -> None:
        value = self.fixture(); value["items"][0]["note"] = "token=" + "ghp_" + "a" * 36
        with self.assertRaises(recovery.RecoveryError):
            recovery.validate_observation(value)

    def test_atomic_output_round_trip(self) -> None:
        ledger, queue = self.reconcile(self.fixture())
        with tempfile.TemporaryDirectory() as directory:
            ledger_path, queue_path = Path(directory) / "ledger.json", Path(directory) / "queue.json"
            recovery.atomic_write_json(ledger_path, ledger); recovery.atomic_write_json(queue_path, queue)
            self.assertEqual(recovery.validate_ledger(recovery.load_json(ledger_path))["summary"], ledger["summary"])
            self.assertEqual(recovery.load_json(queue_path)["summary"], queue["summary"])


if __name__ == "__main__":
    unittest.main()
