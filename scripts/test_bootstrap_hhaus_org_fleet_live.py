from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_PATH = Path(__file__).with_name("bootstrap_hhaus_org_fleet_live.py")
SPEC = importlib.util.spec_from_file_location("hhaus_bootstrap_cli", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to import H/HAUS bootstrap module")
bootstrap = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bootstrap
SPEC.loader.exec_module(bootstrap)


class ManifestContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = bootstrap.load_manifest()

    def test_sealed_manifest_validates(self) -> None:
        bootstrap.validate_manifest(self.manifest)
        self.assertEqual(
            bootstrap.manifest_digest(self.manifest),
            "f7c91354489f76cd790b635cf3c613313b19304264abbe6e2dda2d4268164a31",
        )

    def test_repository_order_is_dependency_safe_and_exact(self) -> None:
        self.assertEqual(
            tuple(repo["name"] for repo in self.manifest["repositories"]),
            bootstrap.EXPECTED_REPOSITORIES,
        )

    def test_language_matrix_has_seventeen_unique_targets(self) -> None:
        targets = self.manifest["language_targets"]
        self.assertEqual(len(targets), 17)
        self.assertEqual(len(targets), len(set(targets)))
        self.assertTrue({"rust", "typescript", "dart", "go", "gleam"}.issubset(targets))

    def test_rate_limit_layers_are_exact_and_ordered(self) -> None:
        self.assertEqual(
            tuple(self.manifest["required_rate_limit_layers"]),
            bootstrap.EXPECTED_RATE_LIMIT_LAYERS,
        )

    def test_platform_dependency_drift_fails_closed(self) -> None:
        changed = copy.deepcopy(self.manifest)
        changed["platform_dependencies"]["middleware"] = "example/other-middleware"
        with self.assertRaisesRegex(bootstrap.BootstrapError, "platform dependency map drifted"):
            bootstrap.validate_manifest(changed)

    def test_backend_orm_leak_fails_closed(self) -> None:
        changed = copy.deepcopy(self.manifest)
        clients = next(repo for repo in changed["repositories"] if repo["name"] == "hhaus-clients")
        clients["internal_dependencies"].append("hhaus-orm-core")
        with self.assertRaisesRegex(bootstrap.BootstrapError, "internal dependency topology drifted"):
            bootstrap.validate_manifest(changed)

    def test_dependency_cycle_fails_closed(self) -> None:
        changed = copy.deepcopy(self.manifest)
        interfaces = next(repo for repo in changed["repositories"] if repo["name"] == "hhaus-interfaces")
        interfaces["internal_dependencies"] = ["hhaus-lib-core"]
        original = bootstrap.EXPECTED_INTERNAL_DEPENDENCIES["hhaus-interfaces"]
        with mock.patch.dict(
            bootstrap.EXPECTED_INTERNAL_DEPENDENCIES,
            {"hhaus-interfaces": ["hhaus-lib-core"]},
        ):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "dependency cycle"):
                bootstrap.validate_manifest(changed)
        self.assertEqual(bootstrap.EXPECTED_INTERNAL_DEPENDENCIES["hhaus-interfaces"], original)


class ScaffoldContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = bootstrap.load_manifest()

    def test_every_repository_scaffold_validates(self) -> None:
        for name in bootstrap.EXPECTED_REPOSITORIES:
            with self.subTest(repository=name):
                files = bootstrap.seed_files(self.manifest, name)
                bootstrap.validate_seed_files(self.manifest, name, files)

    def test_interfaces_publish_all_seventeen_language_targets(self) -> None:
        files = bootstrap.seed_files(self.manifest, "hhaus-interfaces")
        package = tomllib.loads(files[".zpkg.toml"])
        targets = package["targets"]
        self.assertEqual(len(targets) - 1, 17)
        for language in self.manifest["language_targets"]:
            self.assertIn(f"generated/{language}/README.md", files)

    def test_clients_publish_all_seventeen_language_targets(self) -> None:
        files = bootstrap.seed_files(self.manifest, "hhaus-clients")
        package = tomllib.loads(files[".zpkg.toml"])
        self.assertEqual(len(package["targets"]) - 1, 17)
        for language in self.manifest["language_targets"]:
            self.assertIn(f"clients/{language}/README.md", files)
        matrix = json.loads(files["language-targets.json"])
        self.assertEqual(
            matrix["imports"],
            ["hhaus-org/hhaus-interfaces", "hhaus-org/hhaus-lib-core"],
        )

    def test_peer_authority_enum_drift_is_rejected(self) -> None:
        files = bootstrap.seed_files(self.manifest, "hhaus-interfaces")
        schema = json.loads(files["json-schema/hhaus.contract.schema.json"])
        schema["$defs"]["RateLimitFailureMode"]["enum"].remove("localOnly")
        files["json-schema/hhaus.contract.schema.json"] = json.dumps(schema, sort_keys=True)
        with self.assertRaisesRegex(bootstrap.BootstrapError, "peer-authority validation failed"):
            bootstrap.validate_seed_files(self.manifest, "hhaus-interfaces", files)

    def test_credential_shaped_content_is_rejected(self) -> None:
        files = bootstrap.seed_files(self.manifest, "hhaus-lib-core")
        files["accidental-secret.txt"] = "ghp_" + ("A" * 40)
        with self.assertRaisesRegex(bootstrap.BootstrapError, "credential-shaped content"):
            bootstrap.validate_seed_files(self.manifest, "hhaus-lib-core", files)

    def test_render_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_result = bootstrap.render_all(self.manifest, Path(first))
            second_result = bootstrap.render_all(self.manifest, Path(second))
            self.assertEqual(first_result, second_result)
            for name in bootstrap.EXPECTED_REPOSITORIES:
                first_contract = (Path(first) / name / "repository.contract.json").read_bytes()
                second_contract = (Path(second) / name / "repository.contract.json").read_bytes()
                self.assertEqual(first_contract, second_contract)

    def test_sync_keeps_orm_as_backend_adapter_only(self) -> None:
        files = bootstrap.seed_files(self.manifest, "hhaus-sync")
        contract = json.loads(files["repository.contract.json"])
        self.assertNotIn("hhaus-orm-core", contract["internal_dependencies"])
        self.assertEqual(contract["backend_internal_dependencies"], ["hhaus-orm-core"])


class LiveAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = bootstrap.load_manifest()
        self.digest = bootstrap.manifest_digest(self.manifest)

    def test_apply_is_disabled_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "administration is disabled"):
                bootstrap.require_apply_authority(self.manifest)

    def test_apply_requires_exact_manifest_and_repository_confirmation(self) -> None:
        environment = {
            "HHAUS_REPOSITORY_ADMIN_ENABLED": "true",
            "HHAUS_REPOSITORY_ADMIN_ALLOWED_ORGS": "hhaus-org",
            "HHAUS_BOOTSTRAP_REPOSITORY": "hhaus-interfaces",
            "HHAUS_BOOTSTRAP_CONFIRM_REPOSITORY": "hhaus-org/hhaus-interfaces",
            "HHAUS_BOOTSTRAP_CONFIRM_MANIFEST_SHA256": self.digest,
            "HHAUS_REPOSITORY_ADMIN_TOKEN": "unit-test-token",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                bootstrap.require_apply_authority(self.manifest),
                ("hhaus-interfaces", "unit-test-token", False),
            )

    def test_merge_requires_exact_repository_and_digest_confirmation(self) -> None:
        environment = {
            "HHAUS_REPOSITORY_ADMIN_ENABLED": "true",
            "HHAUS_REPOSITORY_ADMIN_ALLOWED_ORGS": "hhaus-org",
            "HHAUS_BOOTSTRAP_REPOSITORY": "hhaus-sync",
            "HHAUS_BOOTSTRAP_CONFIRM_REPOSITORY": "hhaus-org/hhaus-sync",
            "HHAUS_BOOTSTRAP_CONFIRM_MANIFEST_SHA256": self.digest,
            "HHAUS_BOOTSTRAP_MERGE": "true",
            "HHAUS_BOOTSTRAP_CONFIRM_MERGE": f"hhaus-org/hhaus-sync@{self.digest}",
            "HHAUS_REPOSITORY_ADMIN_TOKEN": "unit-test-token",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                bootstrap.require_apply_authority(self.manifest),
                ("hhaus-sync", "unit-test-token", True),
            )
        environment["HHAUS_BOOTSTRAP_CONFIRM_MERGE"] = "wrong"
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "merge confirmation"):
                bootstrap.require_apply_authority(self.manifest)

    def test_github_client_rejects_untrusted_api_hosts(self) -> None:
        with self.assertRaisesRegex(bootstrap.BootstrapError, "exactly https://api.github.com"):
            bootstrap.GitHubApi("unit-test-token", "https://api.github.com.attacker.example")

    def test_github_client_rejects_ambiguous_paths_without_network_access(self) -> None:
        client = bootstrap.GitHubApi("unit-test-token")
        for path in ("//attacker.example/repos", "/repos/example#fragment", "/repos\\example"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(bootstrap.BootstrapError, "invalid GitHub API path"):
                    client.request("GET", path)

    def test_github_error_redacts_active_token(self) -> None:
        token = "unit-test-token-value"
        client = bootstrap.GitHubApi(token)
        self.assertEqual(client._safe_error(f"failure {token}"), "failure [REDACTED]")

    def test_result_file_contains_no_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "result.json"
            value = {"mode": "plan", "manifest_digest": self.digest}
            with mock.patch.dict(
                os.environ,
                {"HHAUS_BOOTSTRAP_RESULT_PATH": str(result_path)},
                clear=True,
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    bootstrap.write_result(value)
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), value)
            text = result_path.read_text(encoding="utf-8")
            self.assertNotIn("token", text.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
