from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from ores_legal_bootstrap.core import (  # noqa: E402
    EXPECTED_LANGUAGE_TARGETS,
    EXPECTED_ORGANIZATION,
    EXPECTED_REPOSITORIES,
    BootstrapError,
    canonical_json_bytes,
    load_manifest,
    manifest_digest,
    render_all,
    repository_spec,
    seed_files,
    validate_manifest,
)
from ores_legal_bootstrap.publication import (  # noqa: E402
    CONFIRMATION,
    GitHubApi,
    build_tree_entries,
    ensure_repository,
    require_apply_authority,
)


class FakeGitHubApi:
    def __init__(self, manifest: dict) -> None:
        self.manifest = manifest
        self.calls: list[tuple[str, str, object]] = []
        self.created_commit_sha = "3" * 40

    def optional(self, path: str):
        self.calls.append(("GET?", path, None))
        if path.startswith("/repos/ores-legal/") and path.count("/") == 3:
            return None
        if "/contents/repository.contract.json" in path:
            repo = path.split("/")[3]
            spec = repository_spec(self.manifest, repo)
            contract = {
                "organization": EXPECTED_ORGANIZATION,
                "repository": repo,
                "kind": spec["kind"],
                "visibility": spec["visibility"],
                "linear_issue": "DEN-319",
                "manifest_digest": manifest_digest(self.manifest),
            }
            return {"encoding": "base64", "content": base64.b64encode(canonical_json_bytes(contract)).decode("ascii")}
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "a" * 40}}
        if path.endswith("/git/ref/heads/alexanderdmills%2Fden-319-bootstrap-ores-legal-fleet"):
            return None
        return None

    def request(self, method: str, path: str, payload=None):
        self.calls.append((method, path, payload))
        if method == "POST" and path == f"/orgs/{EXPECTED_ORGANIZATION}/repos":
            assert isinstance(payload, dict)
            return {
                "full_name": f"{EXPECTED_ORGANIZATION}/{payload['name']}",
                "visibility": payload["visibility"],
                "private": payload["private"],
                "default_branch": "main",
            }
        if method == "GET" and "/git/commits/" in path:
            return {"tree": {"sha": "b" * 40}}
        if method == "POST" and path.endswith("/git/blobs"):
            return {"sha": f"{len(self.calls):040x}"[-40:]}
        if method == "POST" and path.endswith("/git/trees"):
            return {"sha": "c" * 40}
        if method == "POST" and path.endswith("/git/commits"):
            return {"sha": self.created_commit_sha}
        if method == "POST" and path.endswith("/git/refs"):
            return {"ref": "refs/heads/alexanderdmills/den-319-bootstrap-ores-legal-fleet"}
        if method == "GET" and path.endswith("/installation/repositories?per_page=100"):
            return {"repositories": []}
        if method == "PATCH" and path.startswith(f"/repos/{EXPECTED_ORGANIZATION}/"):
            assert isinstance(payload, dict)
            name = path.split("/repos/", 1)[1].split("/", 1)[1]
            return {
                "full_name": f"{EXPECTED_ORGANIZATION}/{name}",
                "visibility": "public" if payload.get("visibility") == "public" else "private",
                "private": payload.get("private"),
                "default_branch": "main",
            }
        if method == "PUT" and path.endswith("/topics"):
            assert isinstance(payload, dict)
            return {"names": payload.get("names", [])}
        raise AssertionError(f"unexpected fake request: {method} {path}")


class OresLegalBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = load_manifest()
        validate_manifest(self.manifest)

    def test_manifest_topology_visibility_and_monorepo_order_are_exact(self) -> None:
        self.assertEqual(self.manifest["organization"], EXPECTED_ORGANIZATION)
        self.assertEqual(
            tuple(repository["name"] for repository in self.manifest["repositories"]),
            EXPECTED_REPOSITORIES,
        )
        self.assertEqual(EXPECTED_REPOSITORIES[-1], "ores-legal-monorepo")
        public = {
            repository["name"]
            for repository in self.manifest["repositories"]
            if repository["visibility"] == "public"
        }
        self.assertEqual(
            public,
            {
                "ores-legal-interfaces",
                "ores-legal-clients",
                "ores-legal-pub-lib-core",
                "ores-legal-docs",
                "ores-legal.github.io",
            },
        )

    def test_schema_authorities_and_storage_contract_reject_drift(self) -> None:
        authorities = self.manifest["contract_authorities"]
        self.assertEqual(authorities["typespec"]["role"], "independent-peer-authority")
        self.assertEqual(authorities["json_schema"]["role"], "independent-peer-authority")
        self.assertEqual(authorities["json_schema"]["draft"], "2020-12")
        self.assertEqual(self.manifest["storage"]["supabase"]["organization"], "oresoftware")
        self.assertEqual(self.manifest["storage"]["neon"]["tenant_column"], "tenant_id")
        changed = json.loads(json.dumps(self.manifest))
        changed["contract_authorities"]["json_schema"]["role"] = "generated"
        with self.assertRaises(BootstrapError):
            validate_manifest(changed)

    def test_render_is_deterministic_and_all_repository_verifiers_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ores-legal-render-a-") as first_value, tempfile.TemporaryDirectory(
            prefix="ores-legal-render-b-"
        ) as second_value:
            first = Path(first_value)
            second = Path(second_value)
            one = render_all(self.manifest, first)
            two = render_all(self.manifest, second)
            self.assertEqual(one["tree_digest"], two["tree_digest"])
            self.assertEqual(one["files"], two["files"])
            for repository in EXPECTED_REPOSITORIES:
                namespace: dict[str, object] = {}
                verifier = first / repository / "scripts" / "verify.py"
                exec(compile(verifier.read_text(encoding="utf-8"), str(verifier), "exec"), namespace)
                namespace["verify"]()
            peer_namespace: dict[str, object] = {}
            peer = first / "ores-legal-interfaces" / "scripts" / "check_peer_authorities.py"
            exec(compile(peer.read_text(encoding="utf-8"), str(peer), "exec"), peer_namespace)
            peer_namespace["main"]()

    def test_interfaces_cover_peer_authorities_wire_contract_and_twenty_languages(self) -> None:
        files = seed_files(self.manifest, "ores-legal-interfaces")
        schema_paths = sorted(
            path
            for path in files
            if path.startswith("json-schema/") and path.endswith(".json")
        )
        self.assertGreaterEqual(len(schema_paths), 3)
        for path in schema_paths:
            schema = json.loads(files[path])
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        typespec = files["typespec/external.tsp"] + files["typespec/internal.tsp"]
        self.assertIn("model Envelope", typespec)
        self.assertIn("model AuditEvent", typespec)
        self.assertIn("message EnvelopeRef", files["protobuf/ores/legal/v1/signing.proto"])
        targets = json.loads(files["language-targets.json"])
        self.assertEqual(targets["languages"], list(EXPECTED_LANGUAGE_TARGETS))
        self.assertIn("independent peer authorities", files["README.md"])
        self.assertIn("peer-authority", files["scripts/check_peer_authorities.py"])

    def test_every_executable_uses_root_flags_contract_and_pinned_rust_binding(self) -> None:
        executable_kinds = {
            "api-server",
            "admin-api-server",
            "web-server",
            "admin-web-server",
            "desktop-app",
            "mcp-server",
        }
        for repository in self.manifest["repositories"]:
            if repository["kind"] not in executable_kinds:
                continue
            files = seed_files(self.manifest, repository["name"])
            self.assertIn(".cli-flags.toml", files, repository["name"])
            self.assertIn("flags2env", files["Cargo.toml"], repository["name"])
            self.assertIn("2310d349cb87dfe7eac88ac67677e029c3e13167", files["Cargo.toml"])
            self.assertIn("parse_or_exit", files["src/main.rs"])

    def test_storage_contract_is_tenant_scoped_and_evidence_is_append_only(self) -> None:
        infra = seed_files(self.manifest, "ores-legal-infra")
        neon = infra["neon/migrations/0001_signing.sql"]
        supabase = infra["supabase/migrations/0001_namespaces.sql"]
        self.assertIn("tenant_id uuid NOT NULL", neon)
        self.assertIn("append_only_audit_events", neon)
        self.assertIn("ores_legal_canonical", supabase)
        self.assertIn("ores_legal_auth", supabase)
        orm = seed_files(self.manifest, "ores-legal-orm-core")
        self.assertIn("diesel", orm["Cargo.toml"])
        self.assertIn("sea-orm", orm["Cargo.toml"])
        self.assertIn("tenant_storage_profile_id", orm["src/lib.rs"])

    def test_shared_signing_components_cover_mash_leptos_dioxus_and_flutter(self) -> None:
        web = seed_files(self.manifest, "ores-legal-web-server.rs")
        flutter = seed_files(self.manifest, "ores-legal-flutter")
        desktop = seed_files(self.manifest, "ores-legal-desktop-app.rs")
        component = web["src/component_contract.rs"]
        for required in ("Framework::Mash", "Framework::Leptos", "Framework::Dioxus", "SigningEvent"):
            self.assertIn(required, component)
        self.assertIn("OresSignatureField", flutter["lib/widgets/ores_signature_field.dart"])
        self.assertIn("dioxus", desktop["Cargo.toml"])

    def test_template_contains_explicit_signature_initial_date_and_consent_fields(self) -> None:
        docs = seed_files(self.manifest, "ores-legal-docs")
        template = docs["legal-templates/external/mutual-confidentiality-agreement.md"]
        for required in (
            "signature",
            "initials",
            "signed_date",
            "consent_checkbox",
            "COUNSEL REVIEW REQUIRED",
        ):
            self.assertIn(required, template)
        threat_model = docs["security/threat-model.md"]
        self.assertIn("signature-image replay", threat_model.lower())
        self.assertIn("append-only", threat_model.lower())

    def test_monorepo_uses_real_gitlinks_for_all_siblings(self) -> None:
        fake = FakeGitHubApi(self.manifest)
        entries = build_tree_entries(fake, self.manifest, "ores-legal-monorepo")
        gitlinks = [entry for entry in entries if entry["mode"] == "160000"]
        self.assertEqual(len(gitlinks), len(EXPECTED_REPOSITORIES) - 1)
        self.assertEqual(
            {entry["path"] for entry in gitlinks},
            {f"apps/{repository}" for repository in EXPECTED_REPOSITORIES[:-1]},
        )
        self.assertTrue(all(entry["type"] == "commit" for entry in gitlinks))

    def test_repository_creation_policy_is_explicit_and_disables_rebase(self) -> None:
        fake = FakeGitHubApi(self.manifest)
        repo = ensure_repository(fake, self.manifest, "ores-legal-interfaces")
        self.assertEqual(repo["visibility"], "public")
        create_payload = next(
            payload
            for method, path, payload in fake.calls
            if method == "POST" and path == f"/orgs/{EXPECTED_ORGANIZATION}/repos"
        )
        assert isinstance(create_payload, dict)
        self.assertEqual(create_payload["visibility"], "public")
        self.assertFalse(create_payload["private"])
        self.assertFalse(create_payload["allow_rebase_merge"])
        self.assertFalse(create_payload["allow_merge_commit"])
        self.assertTrue(create_payload["allow_squash_merge"])
        self.assertTrue(create_payload["delete_branch_on_merge"])

    def test_apply_authority_requires_exact_scope_digest_and_confirmation(self) -> None:
        digest = manifest_digest(self.manifest)
        exact = {
            "ORES_LEGAL_REPOSITORY_ADMIN_ENABLED": "true",
            "ORES_LEGAL_ALLOWED_ORGANIZATION": EXPECTED_ORGANIZATION,
            "ORES_LEGAL_ALLOWED_REPOSITORY": "ores-legal-interfaces",
            "ORES_LEGAL_BOOTSTRAP_ORGANIZATION": EXPECTED_ORGANIZATION,
            "ORES_LEGAL_BOOTSTRAP_REPOSITORY": "ores-legal-interfaces",
            "ORES_LEGAL_MANIFEST_SHA256": digest,
            "ORES_LEGAL_REPOSITORY_ADMIN_TOKEN": "test-token-without-whitespace",
        }
        token = require_apply_authority(
            self.manifest,
            "ores-legal-interfaces",
            EXPECTED_ORGANIZATION,
            CONFIRMATION,
            exact,
        )
        self.assertEqual(token, exact["ORES_LEGAL_REPOSITORY_ADMIN_TOKEN"])

        bad = dict(exact)
        bad["ORES_LEGAL_ALLOWED_REPOSITORY"] = "ores-legal-clients"
        with self.assertRaises(BootstrapError):
            require_apply_authority(
                self.manifest,
                "ores-legal-interfaces",
                EXPECTED_ORGANIZATION,
                CONFIRMATION,
                bad,
            )
        with self.assertRaises(BootstrapError):
            require_apply_authority(
                self.manifest,
                "ores-legal-interfaces",
                EXPECTED_ORGANIZATION,
                "yes-really-merge",
                exact,
            )
        bad_digest = dict(exact)
        bad_digest["ORES_LEGAL_MANIFEST_SHA256"] = "0" * 64
        with self.assertRaises(BootstrapError):
            require_apply_authority(
                self.manifest,
                "ores-legal-interfaces",
                EXPECTED_ORGANIZATION,
                CONFIRMATION,
                bad_digest,
            )

    def test_github_api_base_is_locked_to_public_github(self) -> None:
        with self.assertRaises(BootstrapError):
            GitHubApi("test-token", api_url="http://api.github.com")
        with self.assertRaises(BootstrapError):
            GitHubApi("test-token", api_url="https://example.com")
        with self.assertRaises(BootstrapError):
            GitHubApi("test token")

    def test_cli_plan_and_render_contract_matches_workflow(self) -> None:
        digest = manifest_digest(self.manifest)
        with tempfile.TemporaryDirectory(prefix="ores-legal-cli-test-") as directory:
            root = Path(directory)
            plan_result = root / "plan.json"
            render_result = root / "render.json"
            render_root = root / "rendered"
            script = SCRIPT_DIRECTORY / "bootstrap_ores_legal_fleet_live.py"
            subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "plan",
                    "--manifest-sha256",
                    digest,
                    "--result",
                    str(plan_result),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "render",
                    "--manifest-sha256",
                    digest,
                    "--output",
                    str(render_root),
                    "--result",
                    str(render_result),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(json.loads(plan_result.read_text())["manifest_digest"], digest)
            self.assertEqual(json.loads(render_result.read_text())["manifest_digest"], digest)
            self.assertTrue((render_root / "ores-legal-docs" / "repository.contract.json").is_file())

    def test_coordinator_workflow_and_bootstrap_document_are_pinned(self) -> None:
        repository_root = SCRIPT_DIRECTORY.parent
        workflow = (repository_root / ".github/workflows/bootstrap-ores-legal-fleet.yml").read_text(encoding="utf-8")
        document = (repository_root / "docs/ores-legal-fleet-bootstrap.md").read_text(encoding="utf-8")
        self.assertIn("actions/create-github-app-token@fee1f7d63c2ff003460e3d139729b119787bc349", workflow)
        self.assertIn("owner: ores-legal", workflow)
        self.assertIn("permission-administration: write", workflow)
        self.assertIn("merge-validated-den-319-ores-legal-fleet", workflow)
        self.assertNotRegex(workflow, r"uses:\s+[^\s@]+@(main|master|v\d+)\s*$")
        self.assertIn("does not claim", document)
        self.assertIn("COUNSEL REVIEW REQUIRED", document)
        self.assertIn("twenty", document)
        self.assertIn("mode-`160000`", document)

    def test_no_generated_secret_material_or_conflict_markers(self) -> None:
        rendered = {name: seed_files(self.manifest, name) for name in EXPECTED_REPOSITORIES}
        banned_secret_fragments = ("BEGIN PRIVATE KEY", "ghp_", "github_pat_", "sk_live_", "AKIA")
        for repository, files in rendered.items():
            for path, content in files.items():
                for marker in ("<" * 7, "=" * 7, ">" * 7):
                    self.assertNotIn(marker, content, f"conflict marker in {repository}/{path}")
                for fragment in banned_secret_fragments:
                    self.assertNotIn(fragment, content, f"secret-like material in {repository}/{path}")

    def test_public_urls_do_not_claim_an_unverified_production_domain(self) -> None:
        site = seed_files(self.manifest, "ores-legal.github.io")
        combined = "\n".join(site.values())
        self.assertNotIn("ores-legal.com", combined)
        self.assertIn("https://github.com/ores-legal", combined)
        for repository in (
            "ores-legal-api-server.rs",
            "ores-legal-admin-api-server.rs",
            "ores-legal-web-server.rs",
            "ores-legal-admin-web-server.rs",
            "ores-legal-desktop-app.rs",
            "ores-legal-mcp-server.rs",
        ):
            flags = seed_files(self.manifest, repository)[".cli-flags.toml"]
            self.assertIn("http://127.0.0.1:8080", flags)
            self.assertNotIn("ores-legal.com", flags)


if __name__ == "__main__":
    unittest.main()
