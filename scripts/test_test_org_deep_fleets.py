#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from bootstrap_test_org_deep_fleets_live import NoRedirect, git_blob_sha, redact
from deep_test_fleet_templates import (
    BOOTSTRAP_OPERATION,
    EXPECTED_ORGANIZATION_COUNT,
    EXPECTED_REPOSITORY_COUNT,
    EXPECTED_TOTAL,
    generate_repository_files,
    load_fleet,
    run_generated_suite,
    validate_generated_files,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "repository-fleets" / "test-org-deep-fleets.json"


class DeepTestFleetContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fleet = load_fleet(MANIFEST)

    def test_exact_inventory_and_unique_repository_targets(self) -> None:
        self.assertEqual(len(self.fleet.organizations), EXPECTED_ORGANIZATION_COUNT)
        self.assertEqual(len(self.fleet.repositories), EXPECTED_REPOSITORY_COUNT)
        self.assertEqual(self.fleet.total, EXPECTED_TOTAL)
        targets = {
            f"{organization}/{spec.name}"
            for organization in self.fleet.organizations
            for spec in self.fleet.repositories
        }
        self.assertEqual(len(targets), EXPECTED_TOTAL)
        self.assertTrue(all(organization.endswith("-test") for organization in self.fleet.organizations))
        self.assertFalse(self.fleet.live_creation_enabled)

    def test_every_generated_repository_passes_static_contract(self) -> None:
        for organization in self.fleet.organizations:
            for spec in self.fleet.repositories:
                files = generate_repository_files(organization, spec, self.fleet)
                validate_generated_files(files, organization, spec)
                metadata = json.loads(files["project.json"])
                self.assertEqual(metadata["organization"], organization)
                self.assertEqual(metadata["repository"], spec.name)
                self.assertEqual(metadata["bootstrap_operation"], BOOTSTRAP_OPERATION)
                self.assertEqual(metadata["suite"], spec.suite)

    def test_representative_executable_suites_pass(self) -> None:
        organization = self.fleet.organizations[0]
        for spec in self.fleet.repositories:
            with self.subTest(suite=spec.suite):
                run_generated_suite(organization, spec, self.fleet)

    def test_workflows_are_pinned_and_read_only(self) -> None:
        for spec in self.fleet.repositories:
            files = generate_repository_files(self.fleet.organizations[0], spec, self.fleet)
            workflow = files[".github/workflows/deep-tests.yml"]
            self.assertIn("permissions:\n  contents: read", workflow)
            self.assertNotIn("contents: write", workflow)
            for line in workflow.splitlines():
                if "uses:" in line:
                    reference = line.split("uses:", 1)[1].strip()
                    self.assertRegex(reference, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")

    def test_semantic_conflict_policy_is_enforced_in_every_repo(self) -> None:
        for spec in self.fleet.repositories:
            agents = generate_repository_files(
                self.fleet.organizations[0], spec, self.fleet
            )["AGENTS.md"].lower()
            self.assertIn("merge base", agents)
            self.assertIn("3–10", agents)
            self.assertIn("ours", agents)
            self.assertIn("theirs", agents)
            self.assertIn("semantically", agents)

    def test_manifest_mutations_fail_closed(self) -> None:
        source = json.loads(MANIFEST.read_text(encoding="utf-8"))
        mutations = []

        live = copy.deepcopy(source)
        live["live_creation_enabled"] = True
        mutations.append(live)

        unsafe_org = copy.deepcopy(source)
        unsafe_org["organizations"][0] = "not-a-test-org"
        mutations.append(unsafe_org)

        duplicate = copy.deepcopy(source)
        duplicate["organizations"][1] = duplicate["organizations"][0]
        duplicate["organizations"].sort()
        mutations.append(duplicate)

        wrong_owner = copy.deepcopy(source)
        wrong_owner["expected_owner"]["id"] += 1
        mutations.append(wrong_owner)

        for index, mutation in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory(prefix="deep-fleet-mutation-") as raw:
                path = Path(raw) / "manifest.json"
                path.write_text(json.dumps(mutation), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_fleet(path)

    def test_duplicate_json_keys_are_rejected(self) -> None:
        text = MANIFEST.read_text(encoding="utf-8")
        duplicate = text.replace('"schema_version": 1,', '"schema_version": 1,\n  "schema_version": 1,', 1)
        with tempfile.TemporaryDirectory(prefix="deep-fleet-duplicate-") as raw:
            path = Path(raw) / "manifest.json"
            path.write_text(duplicate, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_fleet(path)

    def test_generated_conflicts_and_mutable_actions_are_rejected(self) -> None:
        spec = self.fleet.repositories[0]
        organization = self.fleet.organizations[0]
        files = generate_repository_files(organization, spec, self.fleet)

        conflict = dict(files)
        conflict["README.md"] += "<<<<<<< current\n=======\n>>>>>>> incoming\n"
        with self.assertRaises(ValueError):
            validate_generated_files(conflict, organization, spec)

        mutable = dict(files)
        mutable[".github/workflows/deep-tests.yml"] = mutable[
            ".github/workflows/deep-tests.yml"
        ].replace("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "actions/checkout@main")
        with self.assertRaises(ValueError):
            validate_generated_files(mutable, organization, spec)

    def test_git_blob_hash_matches_git_empty_blob_constant(self) -> None:
        self.assertEqual(git_blob_sha(""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")

    def test_redaction_and_redirect_boundary(self) -> None:
        token = "gh" + "p_" + ("A" * 24)
        self.assertNotIn(token, redact(f"upstream echoed {token}", token))
        handler = NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "found", {}, "https://example.invalid"))

    def test_manifest_digest_is_stable(self) -> None:
        digest = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, hashlib.sha256(MANIFEST.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
