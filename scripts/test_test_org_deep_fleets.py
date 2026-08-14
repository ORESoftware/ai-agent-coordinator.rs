#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import io
import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from bootstrap_test_org_deep_fleets_live import (
    BootstrapError,
    NoRedirect,
    Target,
    classify_zpkg_sha,
    git_blob_sha,
    load_token,
    read_json_response,
    redact,
    verify_check_state,
)
from deep_test_fleet_templates import (
    BOOTSTRAP_OPERATION,
    EXPECTED_ORGANIZATION_COUNT,
    EXPECTED_REPOSITORY_COUNT,
    EXPECTED_TOTAL,
    approved_zpkg_predecessors,
    generate_repository_files,
    legacy_zpkg_manifest,
    load_fleet,
    run_generated_suite,
    validate_generated_files,
    zpkg_manifest,
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
                validate_generated_files(files, organization, spec, self.fleet)
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

    def test_zed_manifests_use_the_current_canonical_contract(self) -> None:
        for organization in self.fleet.organizations:
            for spec in self.fleet.repositories:
                source = generate_repository_files(organization, spec, self.fleet)[".zpkg.toml"]
                manifest = tomllib.loads(source)
                self.assertEqual(manifest["package"]["org"], organization)
                self.assertEqual(manifest["package"]["name"], spec.name)
                self.assertEqual(
                    manifest["package"]["repository"]["url"],
                    f"https://github.com/{organization}/{spec.name}",
                )
                self.assertEqual(set(manifest["scripts"]), {"test"})
                self.assertNotIn("develop", manifest)
                self.assertNotIn("type", manifest["package"])

    def test_legacy_and_current_zed_templates_are_distinct_and_stable(self) -> None:
        organization = self.fleet.organizations[0]
        spec = self.fleet.repositories[0]
        legacy = tomllib.loads(legacy_zpkg_manifest(organization, spec))
        current = tomllib.loads(zpkg_manifest(organization, spec, self.fleet))
        self.assertEqual(legacy["package"]["name"], f"{organization}/{spec.name}")
        self.assertIn("develop", legacy)
        self.assertEqual(current["package"]["org"], organization)
        self.assertEqual(current["package"]["name"], spec.name)
        self.assertNotEqual(
            git_blob_sha(legacy_zpkg_manifest(organization, spec)),
            git_blob_sha(zpkg_manifest(organization, spec, self.fleet)),
        )

        opto = "opto-sync-test"
        opto_manifest = tomllib.loads(zpkg_manifest(opto, spec, self.fleet))
        self.assertEqual(opto_manifest["package"]["version"], "0.2.0")
        self.assertIn("run_cross_language_matrix.mjs", opto_manifest["scripts"]["test"])
        self.assertEqual(len(approved_zpkg_predecessors(opto, spec, self.fleet)), 2)

        zed_test = "zed-pkg-test"
        self.assertEqual(
            len(approved_zpkg_predecessors(zed_test, spec, self.fleet)),
            2,
        )

    def test_live_auth_source_is_explicit(self) -> None:
        with self.assertRaisesRegex(BootstrapError, "choose exactly one"):
            load_token(None, False)
        with tempfile.TemporaryDirectory(prefix="deep-fleet-auth-") as raw:
            token_file = Path(raw) / "token"
            token_file.write_text("not-a-real-token", encoding="utf-8")
            with self.assertRaisesRegex(BootstrapError, "choose exactly one"):
                load_token(token_file, True)

    def test_zed_manifest_source_classification_fails_closed(self) -> None:
        organization = self.fleet.organizations[0]
        spec = self.fleet.repositories[0]
        target = Target(organization, spec)
        current = zpkg_manifest(organization, spec, self.fleet)
        self.assertEqual(
            classify_zpkg_sha(git_blob_sha(current), target, self.fleet, current),
            "already_migrated",
        )
        self.assertEqual(
            classify_zpkg_sha(
                git_blob_sha(legacy_zpkg_manifest(organization, spec)),
                target,
                self.fleet,
                current,
            ),
            "approved_predecessor",
        )
        with self.assertRaisesRegex(BootstrapError, "semantic reconciliation required"):
            classify_zpkg_sha("0" * 40, target, self.fleet, current)

    def test_only_successful_github_actions_verify_checks_are_accepted(self) -> None:
        foreign_success = {
            "name": "verify",
            "status": "completed",
            "conclusion": "success",
            "app": {"slug": "untrusted-app"},
        }
        skipped = {
            "name": "verify",
            "status": "completed",
            "conclusion": "skipped",
            "app": {"slug": "github-actions"},
        }
        success = {
            "name": "verify",
            "status": "completed",
            "conclusion": "success",
            "app": {"slug": "github-actions"},
        }
        self.assertEqual(
            verify_check_state({"check_runs": [foreign_success]}),
            ("pending", "trusted GitHub Actions verify check has not appeared"),
        )
        self.assertEqual(
            verify_check_state({"check_runs": [skipped]}),
            ("failure", "skipped"),
        )
        self.assertEqual(
            verify_check_state({"check_runs": [foreign_success, success]}),
            ("success", "success"),
        )

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
        duplicate = text.replace('"schema_version": 2,', '"schema_version": 2,\n  "schema_version": 2,', 1)
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
            validate_generated_files(conflict, organization, spec, self.fleet)

        mutable = dict(files)
        mutable[".github/workflows/deep-tests.yml"] = mutable[
            ".github/workflows/deep-tests.yml"
        ].replace("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "actions/checkout@main")
        with self.assertRaises(ValueError):
            validate_generated_files(mutable, organization, spec, self.fleet)

        unsafe_publish = dict(files)
        unsafe_publish[".zpkg.toml"] = unsafe_publish[".zpkg.toml"].replace(
            'exclude = [".env", ', "exclude = [", 1
        )
        with self.assertRaisesRegex(ValueError, "canonical repository-specific template"):
            validate_generated_files(unsafe_publish, organization, spec, self.fleet)

    def test_git_blob_hash_matches_git_empty_blob_constant(self) -> None:
        self.assertEqual(git_blob_sha(""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")

    def test_redaction_and_redirect_boundary(self) -> None:
        token = "gh" + "p_" + ("A" * 24)
        self.assertNotIn(token, redact(f"upstream echoed {token}", token))
        handler = NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "found", {}, "https://example.invalid"))

    def test_success_response_reader_does_not_truncate_large_git_trees(self) -> None:
        payload = {"tree": [{"path": "x" * 300_000, "type": "blob"}]}
        encoded = json.dumps(payload).encode("utf-8")
        self.assertGreater(len(encoded), 4096 * 64)
        self.assertEqual(read_json_response(io.BytesIO(encoded)), payload)

    def test_manifest_digest_is_stable(self) -> None:
        digest = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, hashlib.sha256(MANIFEST.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
