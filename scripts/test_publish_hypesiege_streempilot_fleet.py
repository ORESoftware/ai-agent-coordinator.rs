from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "repository-fleets" / "hypesiege-streempilot.json"
PUBLISHER_PATH = ROOT / "scripts" / "publish_hypesiege_streempilot_fleet.py"

SPEC = importlib.util.spec_from_file_location("fleet_publisher", PUBLISHER_PATH)
assert SPEC is not None and SPEC.loader is not None
PUBLISHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLISHER)


class FleetManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.repositories = cls.manifest["repositories"]

    def test_fleet_shape_and_counts(self) -> None:
        self.assertEqual(self.manifest["schema_version"], 1)
        self.assertEqual(self.manifest["repository_count"], 32)
        self.assertEqual(
            self.manifest["organizations"],
            {"hypesiege": 15, "streempilot": 17},
        )
        self.assertEqual(len(self.repositories), 32)
        self.assertEqual(
            len({record["full_name"] for record in self.repositories}),
            32,
        )

    def test_records_are_explicit_and_sealed(self) -> None:
        commit_pattern = re.compile(r"^[0-9a-f]{40}$")
        for record in self.repositories:
            with self.subTest(repository=record["full_name"]):
                expected_full_name = f"{record['org']}/{record['name']}"
                self.assertEqual(record["full_name"], expected_full_name)
                self.assertIn(record["org"], PUBLISHER.ALLOWED_ORGS)
                self.assertEqual(record["default_branch"], "main")
                self.assertIn(record["visibility"], {"public", "private"})
                self.assertGreater(record["files"], 0)
                self.assertRegex(record["commit"], commit_pattern)
                self.assertEqual(
                    record["remote"],
                    f"https://github.com/{expected_full_name}.git",
                )
                self.assertTrue(record["description"].strip())

    def test_monorepositories_publish_last(self) -> None:
        for org in sorted(PUBLISHER.ALLOWED_ORGS):
            records = [record for record in self.repositories if record["org"] == org]
            self.assertEqual(records[-1]["kind"], "monorepo")
            self.assertEqual(records[-1]["name"], f"{org}-monorepo")
            self.assertTrue(
                all(record["kind"] != "monorepo" for record in records[:-1])
            )

    def test_loader_and_selector_reject_unknown_repository(self) -> None:
        manifest = PUBLISHER.load_manifest(MANIFEST_PATH)
        selected = PUBLISHER.select_record(
            manifest,
            "hypesiege/hypesiege-api-server.rs",
        )
        self.assertEqual(
            selected["commit"],
            "665de38a82f016b11dd60ddf70428c78605da75b",
        )
        with self.assertRaises(PUBLISHER.PublicationError):
            PUBLISHER.select_record(manifest, "other/example")

    def test_plan_mode_is_network_free(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PUBLISHER_PATH),
                "--manifest",
                str(MANIFEST_PATH),
                "--repository",
                "streempilot/streempilot-monorepo",
            ],
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        plan = json.loads(completed.stdout)
        self.assertEqual(plan["mode"], "plan")
        self.assertEqual(
            plan["commit"],
            "a978ac20f41b09581012c3e8cbb48e2d2674c631",
        )

    def test_execute_requires_exact_confirmation_before_credentials(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PUBLISHER_PATH),
                "--manifest",
                str(MANIFEST_PATH),
                "--repository",
                "hypesiege/hypesiege-monorepo",
                "--execute",
                "--confirm-repository",
                "hypesiege/not-the-monorepo",
            ],
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            "--confirm-repository must exactly equal",
            completed.stderr,
        )


if __name__ == "__main__":
    unittest.main()
