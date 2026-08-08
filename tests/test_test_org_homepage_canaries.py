from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import render_test_org_homepage_context as renderer
import validate_test_org_homepage_canaries as validator
import verify_test_org_homepage_context as verifier

REGISTRY_PATH = ROOT / "config" / "test-org-homepage-canaries.yaml"
PROFILE_FIXTURE = ROOT / "tests" / "fixtures" / "org-homepage.valid.md"
REGISTRY_REF = "0" * 40


class TestOrganizationHomepageCanaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = validator.load_registry(REGISTRY_PATH)

    def mutated(self) -> dict:
        return copy.deepcopy(self.registry)

    def test_checked_in_registry_passes(self) -> None:
        self.assertEqual(
            validator.validate_registry(self.registry),
            {
                "canaries": 2,
                "test_organizations": 2,
                "production_parents": 2,
            },
        )

    def test_resolution_is_case_insensitive_and_id_stable(self) -> None:
        by_login = validator.resolve_canary(self.registry, "SCINTILLA-RUN-TEST")
        by_id = validator.resolve_canary(self.registry, 313179056)
        self.assertEqual(by_login, by_id)
        self.assertEqual(by_login["github"]["login"], "scintilla-run-test")

    def test_runtime_route_is_forbidden(self) -> None:
        registry = self.mutated()
        registry["canaries"][0]["runtime_route"] = {
            "default_repository": "scintilla-run-test/protocol-conformance"
        }
        with self.assertRaisesRegex(validator.RegistryError, "runtime_route must be null"):
            validator.validate_registry(registry)

    def test_parent_must_match_non_test_owner(self) -> None:
        registry = self.mutated()
        registry["canaries"][0]["parent_production"]["github_login"] = "file-tunnel"
        with self.assertRaisesRegex(validator.RegistryError, "must match the non-test parent"):
            validator.validate_registry(registry)

    def test_test_and_production_identity_must_differ(self) -> None:
        registry = self.mutated()
        registry["canaries"][0]["parent_production"]["github_account_id"] = 313179056
        with self.assertRaisesRegex(validator.RegistryError, "test and production GitHub IDs"):
            validator.validate_registry(registry)

    def test_duplicate_owner_id_is_rejected(self) -> None:
        registry = self.mutated()
        registry["canaries"][1]["github"]["account_id"] = 313179056
        with self.assertRaisesRegex(validator.RegistryError, "duplicate GitHub account ID"):
            validator.validate_registry(registry)

    def test_github_project_must_belong_to_test_owner(self) -> None:
        registry = self.mutated()
        registry["canaries"][0]["github"]["github_project_url"] = (
            "https://github.com/orgs/file-tunnel-test/projects/1"
        )
        with self.assertRaisesRegex(validator.RegistryError, "organization project"):
            validator.validate_registry(registry)

    def test_secret_shaped_value_is_rejected(self) -> None:
        registry = self.mutated()
        fake = "gh" + "p_" + ("A" * 30)
        registry["canaries"][0]["test_program"]["purpose"] += f" {fake}"
        with self.assertRaisesRegex(validator.RegistryError, "credential-like value"):
            validator.validate_registry(registry)

    def test_loader_rejects_duplicate_keys_and_missing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.yaml"
            duplicate.write_text(
                '{"schema_version":1,"schema_version":1}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(validator.RegistryError, "duplicate JSON key"):
                validator.load_registry(duplicate)

            missing_newline = Path(directory) / "missing-newline.yaml"
            missing_newline.write_text('{"schema_version":1}', encoding="utf-8")
            with self.assertRaisesRegex(validator.RegistryError, "final newline"):
                validator.load_registry(missing_newline)

    def test_renderer_is_deterministic_and_test_only(self) -> None:
        first = renderer.render_bundle(
            self.registry,
            "scintilla-run-test",
            REGISTRY_REF,
        )
        second = renderer.render_bundle(
            self.registry,
            "scintilla-run-test",
            REGISTRY_REF,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            set(first),
            {
                "ORG_CONTEXT.md",
                "agents/org-context.agent.md",
                "project-context.yaml",
                "repository-relationships.json",
                "test-org-context-manifest.json",
            },
        )

        context = json.loads(first["project-context.yaml"])
        self.assertEqual(context["context_kind"], "test_organization_acceptance")
        self.assertIsNone(context["runtime_route"])
        self.assertEqual(context["resolution"]["runtime_routing"], "forbidden")
        self.assertEqual(
            context["parent_production"]["github_login"],
            "scintilla-run",
        )
        self.assertEqual(
            context["authorization"]["repository_access"],
            "not_implied",
        )

        relationships = json.loads(first["repository-relationships.json"])
        self.assertFalse(
            relationships["policies"]["infer_dependencies_from_names"]
        )
        self.assertEqual(
            relationships["declared_relationships"][0]["access"],
            "not_implied",
        )

        manifest = json.loads(first["test-org-context-manifest.json"])
        for path, expected_sha in manifest["files"].items():
            self.assertEqual(
                hashlib.sha256(first[path].encode("utf-8")).hexdigest(),
                expected_sha,
            )
        self.assertIn("3-10 commits", first["ORG_CONTEXT.md"])
        self.assertIn("`ours`, `theirs`", first["ORG_CONTEXT.md"])

    def _write_valid_canary_checkout(self, root: Path) -> dict[str, str]:
        bundle = renderer.render_bundle(
            self.registry,
            "scintilla-run-test",
            REGISTRY_REF,
        )
        renderer.write_bundle(root, bundle)
        profile = PROFILE_FIXTURE.read_text(encoding="utf-8")
        profile = profile.replace("example-org", "scintilla-run-test")
        profile = profile.replace("123456789", "313179056")
        profile = profile.replace(
            "12345678-1234-4abc-8def-123456789abc",
            "f0c7eeef-c061-4d2f-981d-c0f8c3b1fd9c",
        )
        profile_path = root / "profile" / "README.md"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(profile, encoding="utf-8", newline="\n")
        return bundle

    def test_verifier_accepts_exact_bundle_and_valid_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_canary_checkout(root)
            report = verifier.verify_bundle(
                self.registry,
                "scintilla-run-test",
                REGISTRY_REF,
                root,
            )
            self.assertEqual(report["owner"], "scintilla-run-test")
            self.assertEqual(report["parent_production"], "scintilla-run")
            self.assertEqual(len(report["managed_files"]), 5)

    def test_verifier_rejects_drift_and_invalid_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_canary_checkout(root)
            context_path = root / "ORG_CONTEXT.md"
            context_path.write_text(
                context_path.read_text(encoding="utf-8") + "drift\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(verifier.RegistryError, "differs"):
                verifier.verify_bundle(
                    self.registry,
                    "scintilla-run-test",
                    REGISTRY_REF,
                    root,
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_canary_checkout(root)
            profile_path = root / "profile" / "README.md"
            profile_path.write_text(
                profile_path.read_text(encoding="utf-8").replace(
                    "### For AI agents",
                    "### Automation",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                verifier.RegistryError,
                "homepage validation failed",
            ):
                verifier.verify_bundle(
                    self.registry,
                    "scintilla-run-test",
                    REGISTRY_REF,
                    root,
                )


if __name__ == "__main__":
    unittest.main()
