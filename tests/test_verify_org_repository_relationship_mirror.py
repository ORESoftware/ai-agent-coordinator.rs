#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_org_repository_relationship_mirror.py"
SPEC = importlib.util.spec_from_file_location("verify_org_repository_relationship_mirror", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)

REGISTRY_REF = "a" * 40


def conflict_policy() -> dict:
    return {
        "mode": "semantic_conceptual_merge",
        "directive_verbatim": VALIDATOR.MANDATORY_DIRECTIVE,
        "history_lookback_commits": {
            "minimum": 3,
            "maximum": 10,
            "when_available": True,
            "inspect_both_sides": True,
            "inspect_merge_base": True,
            "path_scoped_history": True,
        },
        "context_scope": sorted(VALIDATOR.REQUIRED_CONTEXT_SCOPE),
        "forbidden_shortcuts": sorted(VALIDATOR.REQUIRED_FORBIDDEN_SHORTCUTS),
    }


def context(runtime: bool = False, derived: bool = True) -> dict:
    source = {
        "repository": VALIDATOR.EXPECTED_REGISTRY_REPOSITORY,
        "path": VALIDATOR.EXPECTED_REGISTRY_PATH,
        "ref": "d3e03ecc2e175a7f6261523d35c73ac775c49942",
        "ref_type": "commit",
        "immutable": True,
        "canonical_sha256": "d" * 64,
    }
    value = {
        "schema_version": 1,
        "github": {
            "login": "example-org",
            "account_id": 123456,
            "account_type": "Organization",
        },
        "linear": {
            "project_id": "00000000-0000-4000-8000-000000000001",
            "project_name": "github.com/example-org",
            "project_url": "https://linear.app/example/project/example-org",
            "workspace_slug": "example",
            "team_id": "00000000-0000-4000-8000-000000000002",
            "team_key": "EX",
        },
        "resolution": {
            "on_unmapped": "reject",
            "on_ambiguous": "reject",
            "repository_selection": "explicit_or_runtime_allowlist",
        },
        "runtime_route": None,
        "git_conflict_resolution": conflict_policy(),
    }
    value["derived_from" if derived else "generated_from"] = source
    if runtime:
        value["runtime_route"] = {
            "default_repository": "example-org/runtime.rs",
            "repository_allowlist": ["example-org/runtime.rs"],
        }
    return value


def relationships(runtime: bool = False) -> dict:
    owner = "example-org"
    repository = f"{owner}/.github"
    linear = context(runtime=runtime)["linear"]
    items = [
        {
            "kind": "generated_from",
            "source": {"id": repository, "type": "github_repository"},
            "target": {
                "id": (
                    f"{VALIDATOR.EXPECTED_REGISTRY_REPOSITORY}:"
                    f"{VALIDATOR.EXPECTED_REGISTRY_PATH}@{REGISTRY_REF}"
                ),
                "type": "github_file",
            },
        },
        {
            "kind": "governs_public_context_for",
            "source": {"id": repository, "type": "github_repository"},
            "target": {
                "id": owner,
                "type": "github_owner",
                "account_id": 123456,
                "account_type": "Organization",
            },
        },
        {
            "kind": "mirrors_linear_project",
            "source": {"id": repository, "type": "github_repository"},
            "target": {
                "id": linear["project_id"],
                "name": linear["project_name"],
                "url": linear["project_url"],
                "type": "linear_project",
            },
        },
    ]
    default_repository = None
    allowlist: list[str] = []
    if runtime:
        default_repository = "example-org/runtime.rs"
        allowlist = [default_repository]
        items.extend(
            [
                {
                    "kind": "defaults_runtime_routing_to",
                    "source": {"id": repository, "type": "github_repository"},
                    "target": {"id": default_repository, "type": "github_repository"},
                },
                {
                    "kind": "permits_runtime_routing_to",
                    "source": {"id": repository, "type": "github_repository"},
                    "target": {"id": default_repository, "type": "github_repository"},
                },
            ]
        )
    items.sort(key=lambda item: (item["kind"], item["target"]["id"]))
    return {
        "schema_version": 1,
        "generated_from": {
            "repository": VALIDATOR.EXPECTED_REGISTRY_REPOSITORY,
            "path": VALIDATOR.EXPECTED_REGISTRY_PATH,
            "ref": REGISTRY_REF,
            "ref_type": "commit",
            "immutable": True,
            "canonical_sha256": "d" * 64,
            "url": (
                f"https://github.com/{VALIDATOR.EXPECTED_REGISTRY_REPOSITORY}/blob/"
                f"{REGISTRY_REF}/{VALIDATOR.EXPECTED_REGISTRY_PATH}"
            ),
        },
        "git_conflict_resolution": conflict_policy(),
        "github": {
            "login": owner,
            "account_id": 123456,
            "account_type": "Organization",
            "aliases": [owner],
        },
        "governance": {
            "automatic_agent_instruction_inheritance": False,
            "public_context_only": True,
            "repository": repository,
            "repository_local_instruction_mirror_required": True,
            "repository_scope": f"{owner}/*",
        },
        "linear": linear,
        "relationships": items,
        "repository_selection": {
            "default_repository": default_repository,
            "linear_project_overrides": [],
            "on_ambiguous": "reject",
            "on_unmapped": "reject",
            "policy": "explicit_or_runtime_allowlist",
            "runtime_allowlist": allowlist,
            "unregistered_dependencies": "unknown_not_assumed",
        },
    }


def write_json(root: Path, name: str, value: dict, canonical: bool = True) -> None:
    if canonical:
        text = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    else:
        text = json.dumps(value) + "\n"
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class OrgRepositoryRelationshipMirrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="repository-relationships-")
        self.root = Path(self.temp.name)
        self.relationships = relationships()
        self.context = context()
        self.flush()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def flush(self, canonical: bool = True) -> None:
        write_json(
            self.root,
            "repository-relationships.json",
            self.relationships,
            canonical=canonical,
        )
        write_json(self.root, "project-context.yaml", self.context)

    def assert_fails(self, needle: str) -> None:
        with self.assertRaisesRegex(VALIDATOR.RelationshipValidationError, needle):
            VALIDATOR.validate(
                self.root / "repository-relationships.json",
                self.root / "project-context.yaml",
                expected_owner="example-org",
                expected_registry_ref=REGISTRY_REF,
            )

    def test_valid_derived_context_without_runtime_route(self) -> None:
        result = VALIDATOR.validate(
            self.root / "repository-relationships.json",
            self.root / "project-context.yaml",
            expected_owner="example-org",
            expected_registry_ref=REGISTRY_REF,
        )
        self.assertEqual("example-org", result["owner"])
        self.assertEqual(3, result["relationship_count"])

    def test_valid_generated_context_with_runtime_route(self) -> None:
        self.relationships = relationships(runtime=True)
        self.context = context(runtime=True, derived=False)
        self.flush()
        result = VALIDATOR.validate(
            self.root / "repository-relationships.json",
            self.root / "project-context.yaml",
            expected_owner="example-org",
            expected_registry_ref=REGISTRY_REF,
        )
        self.assertEqual(5, result["relationship_count"])

    def test_registry_ref_must_be_expected_immutable_merge_commit(self) -> None:
        self.relationships["generated_from"]["ref"] = "main"
        self.flush()
        self.assert_fails("immutable commit")

    def test_wrong_expected_owner_fails(self) -> None:
        with self.assertRaisesRegex(
            VALIDATOR.RelationshipValidationError, "does not match expected owner"
        ):
            VALIDATOR.validate(
                self.root / "repository-relationships.json",
                self.root / "project-context.yaml",
                expected_owner="other-org",
                expected_registry_ref=REGISTRY_REF,
            )

    def test_directive_must_be_preserved_verbatim(self) -> None:
        self.relationships["git_conflict_resolution"]["directive_verbatim"] = "merge carefully"
        self.flush()
        self.assert_fails("mandatory directive verbatim")

    def test_automatic_instruction_inheritance_cannot_be_claimed(self) -> None:
        self.relationships["governance"]["automatic_agent_instruction_inheritance"] = True
        self.flush()
        self.assert_fails("inheritance")

    def test_github_account_id_drift_fails(self) -> None:
        self.relationships["github"]["account_id"] += 1
        self.flush()
        self.assert_fails("GitHub identity drift")

    def test_linear_project_drift_fails(self) -> None:
        self.relationships["linear"]["project_id"] = "00000000-0000-4000-8000-999999999999"
        self.flush()
        self.assert_fails("Linear identity drift")

    def test_runtime_route_cannot_be_invented(self) -> None:
        self.relationships["repository_selection"]["default_repository"] = "example-org/other"
        self.flush()
        self.assert_fails("default runtime repository drift")

    def test_missing_required_relationship_fails(self) -> None:
        self.relationships["relationships"] = [
            item
            for item in self.relationships["relationships"]
            if item["kind"] != "mirrors_linear_project"
        ]
        self.flush()
        self.assert_fails("mirrors_linear_project")

    def test_duplicate_relationship_fails(self) -> None:
        self.relationships["relationships"].append(
            deepcopy(self.relationships["relationships"][0])
        )
        self.flush()
        self.assert_fails("duplicate relationship")

    def test_noncanonical_json_fails(self) -> None:
        self.flush(canonical=False)
        self.assert_fails("deterministic canonical JSON")

    def test_secret_marker_fails(self) -> None:
        token = "gh" + "p_" + "A" * 36
        self.relationships["github"]["aliases"].append(token)
        self.flush()
        self.assert_fails("GitHub token")

    def test_symlink_substitution_fails(self) -> None:
        real = self.root / "real-relationships.json"
        real.write_text(
            json.dumps(self.relationships, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        relationship = self.root / "repository-relationships.json"
        relationship.unlink()
        try:
            os.symlink(real.name, relationship)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assert_fails("refusing symlink")


if __name__ == "__main__":
    unittest.main()
