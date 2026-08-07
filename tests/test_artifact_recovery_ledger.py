from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
SCRIPTS = ROOT / "scripts"
for path in (TOOLS, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import artifact_recovery_ledger as recovery  # noqa: E402
import build_artifact_recovery_backfill as backfill  # noqa: E402
NOW = "2026-08-07T19:00:00Z"


class ArtifactRecoveryLedgerTests(unittest.TestCase):
    def load_fixture(self) -> dict:
        return backfill.build_fixture()

    def reconcile(self, observation: dict, previous: dict | None = None, *, batch_size: int = 50):
        return recovery.reconcile(
            observation,
            previous,
            now=NOW,
            batch_size=batch_size,
            target_task_id=recovery.DEFAULT_CLI_TASK_ID,
        )

    def test_initial_backfill_classification_and_cli_queue(self) -> None:
        ledger, queue = self.reconcile(self.load_fixture())
        self.assertEqual(ledger["summary"]["entries"], 17)
        self.assertEqual(ledger["summary"]["complete"], 4)
        self.assertEqual(ledger["summary"]["actionable"], 13)
        self.assertEqual(queue["summary"], {
            "items": 13,
            "create_repository": 8,
            "recover_local": 5,
        })
        self.assertTrue(all(item["target_task_id"] == recovery.DEFAULT_CLI_TASK_ID for item in queue["items"]))
        self.assertTrue(all(item["visibility"] in {"public", "private", "internal"} for item in queue["items"]))
        self.assertTrue(all("force push" in item["forbidden"] for item in queue["items"]))

    def test_rerun_is_idempotent_and_does_not_increment_attempts(self) -> None:
        first, first_queue = self.reconcile(self.load_fixture())
        second, second_queue = self.reconcile(self.load_fixture(), first)
        first_attempts = {key: value["attempts"] for key, value in first["entries"].items()}
        second_attempts = {key: value["attempts"] for key, value in second["entries"].items()}
        self.assertEqual(first_attempts, second_attempts)
        self.assertEqual(first_queue["items"], second_queue["items"])
        self.assertEqual(first["summary"], second["summary"])

    def test_material_change_increments_only_changed_entry(self) -> None:
        first, _ = self.reconcile(self.load_fixture())
        changed = self.load_fixture()
        changed["items"][0]["note"] = "Remote evidence was rechecked after a later source change."
        second, _ = self.reconcile(changed, first)
        increments = [
            key
            for key, entry in second["entries"].items()
            if entry["attempts"] != first["entries"][key]["attempts"]
        ]
        self.assertEqual(len(increments), 1)
        self.assertEqual(second["entries"][increments[0]]["attempts"], 2)

    def test_bounded_batch_records_cursor_and_leaves_remaining_for_later(self) -> None:
        ledger, queue = self.reconcile(self.load_fixture(), batch_size=3)
        self.assertEqual(ledger["last_batch"]["processed"], 3)
        self.assertEqual(ledger["last_batch"]["available"], 17)
        self.assertFalse(ledger["last_batch"]["source_complete"])
        self.assertEqual(
            ledger["last_batch"]["next_cursor"],
            "library-created-before:2026-08-01T23:41:07Z",
        )
        self.assertEqual(len(ledger["entries"]), 3)
        self.assertLessEqual(len(queue["items"]), 3)

    def test_default_visibility_is_private_for_unresolved_new_repository(self) -> None:
        observation = self.load_fixture()
        missing = next(item for item in observation["items"] if item["target"]["repository"] == "apme-e2e")
        missing["target"]["visibility"] = None
        observation["items"] = [missing]
        _, queue = self.reconcile(observation)
        self.assertEqual(queue["items"][0]["visibility"], "private")

    def test_branch_without_pull_request_uses_connector_action_not_cli_queue(self) -> None:
        item = copy.deepcopy(self.load_fixture()["items"][0])
        item["intent"]["branch"] = "agent/den-2797-existing-branch"
        item["intent"]["pull_request_required"] = True
        item["local"]["branch"] = "agent/den-2797-existing-branch"
        item["local"]["branches"] = ["agent/den-2797-existing-branch"]
        item["local"]["head_sha"] = item["remote"]["branches"][0]["sha"]
        item["remote"]["branches"].append(
            {"name": "agent/den-2797-existing-branch", "sha": item["local"]["head_sha"]}
        )
        item["claims"] = {
            "repository_url": item["remote"]["repository"]["url"],
            "commit_sha": item["local"]["head_sha"],
            "branch": "agent/den-2797-existing-branch",
            "pull_request_url": None,
        }
        observation = self.load_fixture()
        observation["items"] = [item]
        ledger, queue = self.reconcile(observation)
        entry = next(iter(ledger["entries"].values()))
        self.assertEqual(entry["classification"]["next_action"], "open_draft_pull_request")
        self.assertEqual(queue["items"], [])

    def test_unverified_claim_without_local_recovery_evidence_fails_closed(self) -> None:
        item = copy.deepcopy(self.load_fixture()["items"][0])
        item["claims"]["pull_request_url"] = f"https://github.com/{item['target']['owner']}/{item['target']['repository']}/pull/999"
        observation = self.load_fixture()
        observation["items"] = [item]
        ledger, queue = self.reconcile(observation)
        entry = next(iter(ledger["entries"].values()))
        self.assertEqual(entry["classification"]["status"], "blocked")
        self.assertIn("claimed_pull_request_unverified", entry["classification"]["findings"])
        self.assertEqual(queue["items"], [])

    def test_ordinary_conversation_is_excluded(self) -> None:
        item = copy.deepcopy(self.load_fixture()["items"][0])
        item["origin"]["id"] = "conversation-no-artifact"
        item["target"]["artifact_kind"] = "none"
        item["intent"]["artifact_expected"] = False
        item["local"] = {
            "artifact": None,
            "git_repository": False,
            "remote_present": False,
            "branch": None,
            "branches": [],
            "head_sha": None,
            "dirty_paths": [],
        }
        item["remote"] = {
            "collected": True,
            "repository": {"exists": False, "visibility": None, "default_branch": None, "url": None},
            "branches": [],
            "commits": [],
            "pull_requests": [],
        }
        item["claims"] = {"repository_url": None, "commit_sha": None, "branch": None, "pull_request_url": None}
        observation = self.load_fixture()
        observation["items"] = [item]
        ledger, queue = self.reconcile(observation)
        entry = next(iter(ledger["entries"].values()))
        self.assertEqual(entry["classification"]["status"], "excluded")
        self.assertEqual(queue["items"], [])

    def test_secret_shaped_content_is_rejected(self) -> None:
        observation = self.load_fixture()
        observation["items"][0]["note"] = "token=" + "ghp_" + ("a" * 36)
        with self.assertRaises(recovery.RecoveryError):
            recovery.validate_observation(observation)

    def test_atomic_output_round_trip(self) -> None:
        ledger, queue = self.reconcile(self.load_fixture())
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            queue_path = Path(directory) / "queue.json"
            recovery.atomic_write_json(ledger_path, ledger)
            recovery.atomic_write_json(queue_path, queue)
            self.assertEqual(recovery.validate_ledger(recovery.load_json(ledger_path))["summary"], ledger["summary"])
            self.assertEqual(recovery.load_json(queue_path)["summary"], queue["summary"])


if __name__ == "__main__":
    unittest.main()
