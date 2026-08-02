from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import render_org_project_context as renderer  # noqa: E402
import validate_org_project_registry as registry_module  # noqa: E402


class OrgProjectRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = registry_module.load_registry(
            ROOT / "config" / "org-project-registry.yaml"
        )

    def test_checked_in_registry_is_valid(self) -> None:
        counts = registry_module.validate_registry(self.registry)
        self.assertEqual(counts["mappings"], 30)
        self.assertEqual(counts["repository_overrides"], 6)
        self.assertEqual(counts["runtime_routes"], 13)
        self.assertEqual(counts["unmapped"], 7)

    def test_duplicate_linear_project_id_fails_closed(self) -> None:
        changed = copy.deepcopy(self.registry)
        changed["mappings"][1]["linear"]["project_id"] = changed["mappings"][0][
            "linear"
        ]["project_id"]
        with self.assertRaisesRegex(
            registry_module.RegistryError, "duplicate owner-level Linear project ID"
        ):
            registry_module.validate_registry(changed)

    def test_case_insensitive_alias_collision_fails_closed(self) -> None:
        changed = copy.deepcopy(self.registry)
        changed["mappings"][1]["github"]["aliases"].append(
            changed["mappings"][0]["github"]["aliases"][0].upper()
        )
        with self.assertRaisesRegex(registry_module.RegistryError, "maps to both"):
            registry_module.validate_registry(changed)

    def test_unmapped_owner_cannot_also_be_mapped(self) -> None:
        changed = copy.deepcopy(self.registry)
        changed["unmapped_installed_organizations"][0]["github"]["account_id"] = (
            changed["mappings"][0]["github"]["account_id"]
        )
        with self.assertRaisesRegex(registry_module.RegistryError, "account_id collides"):
            registry_module.validate_registry(changed)

    def test_repository_override_requires_mapped_owner(self) -> None:
        changed = copy.deepcopy(self.registry)
        changed["repository_overrides"][0]["repository"] = "unknown-owner/example"
        with self.assertRaisesRegex(registry_module.RegistryError, "unmapped GitHub owner"):
            registry_module.validate_registry(changed)

    def test_ambiguity_policy_cannot_be_weakened(self) -> None:
        changed = copy.deepcopy(self.registry)
        changed["resolution"]["on_ambiguous"] = "pick_first"
        with self.assertRaisesRegex(registry_module.RegistryError, "on_ambiguous"):
            registry_module.validate_registry(changed)

    def test_public_registry_rejects_credential_markers(self) -> None:
        changed = copy.deepcopy(self.registry)
        changed["mappings"][0]["linear"]["project_name"] = "ghp_not-a-real-value"
        with self.assertRaisesRegex(registry_module.RegistryError, "credential-like"):
            registry_module.validate_registry(changed)

    def test_owner_alias_resolves_case_insensitively(self) -> None:
        project = registry_module.resolve_project(self.registry, "GITHUB.COM/STREEMPILOT")
        self.assertEqual(project["project_id"], "3f5bd157-4424-42cc-94d0-0bed993cdc1d")

    def test_exact_repository_override_precedes_owner_project(self) -> None:
        project = registry_module.resolve_project(
            self.registry, "ORESoftware", "ORESoftware/k8s-cluster"
        )
        self.assertEqual(project["project_id"], "18c58338-cf36-4fe6-8c71-245a795f8661")

    def test_renderer_emits_public_safe_fiducia_bundle(self) -> None:
        bundle = renderer.render_bundle(
            self.registry, "fiducia-cloud", registry_ref="agent/DEN-629-test"
        )
        self.assertEqual(
            set(bundle),
            {
                "README.md",
                "project-context.yaml",
                "profile/README.md",
                "agents/org-context.agent.md",
            },
        )
        context = json.loads(bundle["project-context.yaml"])
        self.assertEqual(context["github"]["account_id"], 297262292)
        self.assertEqual(
            context["linear"]["project_id"],
            "d9e89bd3-19da-47f3-9bf7-6dc8cc910b70",
        )
        self.assertIsNone(context["runtime_route"])
        self.assertTrue(context["public_context_only"])

    def test_renderer_carries_reviewed_runtime_route(self) -> None:
        bundle = renderer.render_bundle(self.registry, "shared-auth")
        context = json.loads(bundle["project-context.yaml"])
        self.assertEqual(
            context["runtime_route"]["default_repository"],
            "shared-auth/shared-auth-mcp-server.rs",
        )

    def test_unknown_owner_is_rejected(self) -> None:
        with self.assertRaisesRegex(registry_module.RegistryError, "0 matches"):
            registry_module.resolve_owner(self.registry, "not-a-mapped-owner")


if __name__ == "__main__":
    unittest.main()
