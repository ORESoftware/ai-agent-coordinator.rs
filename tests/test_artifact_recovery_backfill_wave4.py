from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_artifact_recovery_backfill_wave4 as wave4  # noqa: E402


class ArtifactRecoveryBackfillWave4Tests(unittest.TestCase):
    def fixture(self) -> dict:
        return wave4.build_fixture()

    def entries(self) -> list[dict]:
        return self.fixture()["items"]

    def test_payload_digest_and_batch_contract(self) -> None:
        raw = wave4.payload_bytes()
        self.assertEqual(wave4.PAYLOAD_SHA256, __import__("hashlib").sha256(raw).hexdigest())
        fixture = json.loads(raw)
        self.assertEqual(fixture["schema_version"], "artifact_recovery_observation.v1")
        self.assertTrue(fixture["batch"]["complete"])
        self.assertIsNone(fixture["batch"]["next_cursor"])
        self.assertEqual(len(fixture["items"]), 20)

    def test_excluded_space_is_not_a_target(self) -> None:
        identities = {
            f"{item['target']['owner'].lower()}/{item['target']['repository'].lower()}"
            for item in self.entries()
        }
        self.assertFalse(any(identity.startswith("dancing-dragons/") for identity in identities))

    def test_every_claim_is_backed_by_remote_evidence(self) -> None:
        for item in self.entries():
            identity = f"{item['target']['owner']}/{item['target']['repository']}"
            with self.subTest(identity=identity):
                remote = item["remote"]
                self.assertTrue(remote["collected"])
                self.assertTrue(remote["repository"]["exists"])
                self.assertIn(item["claims"]["pull_request_url"], {entry["url"] for entry in remote["pull_requests"]})
                self.assertIn(item["claims"]["commit_sha"], {entry["sha"] for entry in remote["commits"]})
                self.assertIn(item["claims"]["branch"], {entry["name"] for entry in remote["branches"]})

    def test_repository_boundary_matrix_is_exact(self) -> None:
        prefix = f"library:{wave4.BOUNDARY_ORIGIN}#"
        boundary = [item for item in self.entries() if item["local"]["artifact"]["locator"].startswith(prefix)]
        self.assertEqual(len(boundary), 13)
        states = [item["remote"]["pull_requests"][0]["state"] for item in boundary]
        self.assertEqual(states.count("merged"), 9)
        self.assertEqual(states.count("open"), 4)
        open_targets = {
            f"{item['target']['owner']}/{item['target']['repository']}"
            for item in boundary if item["remote"]["pull_requests"][0]["state"] == "open"
        }
        self.assertEqual(open_targets, {
            "daedalus-fab/daedalus-monorepo",
            "quaestor-ledger/quaestor-monorepo",
            "scintilla-run/scintilla-run-monorepo",
            "StreemPilot/streempilot-monorepo",
        })

    def test_all_rows_satisfy_current_engine_complete_contract(self) -> None:
        for item in self.entries():
            identity = f"{item['target']['owner']}/{item['target']['repository']}"
            remote_branches = {entry["name"]: entry["sha"] for entry in item["remote"]["branches"]}
            remote_commits = {entry["sha"] for entry in item["remote"]["commits"]} | set(remote_branches.values())
            intended = item["intent"]["branch"]
            with self.subTest(identity=identity):
                self.assertIn(item["local"]["head_sha"], remote_commits)
                self.assertEqual(item["claims"]["repository_url"], item["remote"]["repository"]["url"])
                if intended is not None:
                    self.assertIn(intended, remote_branches)
                    self.assertTrue(any(entry["head"] == intended for entry in item["remote"]["pull_requests"]))

    def test_open_candidates_do_not_claim_merge(self) -> None:
        open_items = [item for item in self.entries() if item["remote"]["pull_requests"][0]["state"] == "open"]
        self.assertEqual(len(open_items), 10)
        for item in open_items:
            pr = item["remote"]["pull_requests"][0]
            self.assertEqual(item["claims"]["branch"], pr["head"])
            self.assertEqual(item["claims"]["commit_sha"], item["local"]["head_sha"])
            self.assertIn("state=open", item["note"])

    def test_shared_auth_uses_existing_fallback_repository_only(self) -> None:
        prefix = f"library:{wave4.SHARED_AUTH_ORIGIN}#"
        shared = [item for item in self.entries() if item["local"]["artifact"]["locator"].startswith(prefix)]
        self.assertEqual(len(shared), 4)
        identities = {
            f"{item['target']['owner']}/{item['target']['repository']}" for item in shared
        }
        self.assertEqual(identities, {
            "shared-auth/shared-auth-server.rs",
            "shared-auth-test/server-api-contract-e2e",
        })
        self.assertNotIn("shared-auth-test/openpgp-provenance-e2e", identities)
        self.assertNotIn("shared-auth-test/kerberos-spnego-e2e", identities)

    def test_namespace_archive_records_semantic_successor(self) -> None:
        item = next(item for item in self.entries() if item["target"]["owner"] == "ORESoftware" and item["target"]["repository"] == "k8s-cluster")
        self.assertEqual(item["local"]["artifact"]["commit_sha"], "4a4ea0e66137c66fc3cc4b177a935374811aa554")
        self.assertEqual(item["remote"]["pull_requests"][0]["head"], "agent/den-2786-namespace-contract")
        self.assertEqual(item["remote"]["pull_requests"][0]["state"], "merged")
        self.assertIn("superseded", item["note"])

    def test_cli_output_is_deterministic_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "wave4.json"
            command = [sys.executable, str(SCRIPTS / "build_artifact_recovery_backfill_wave4.py"), "--output", str(output)]
            subprocess.run(command, check=True)
            first = output.read_bytes()
            self.assertEqual(json.loads(first), self.fixture())
            subprocess.run(command, check=True)
            self.assertEqual(first, output.read_bytes())


if __name__ == "__main__":
    unittest.main()
