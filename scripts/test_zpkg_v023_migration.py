from __future__ import annotations

import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_zpkg_v023_migration_plan import (  # noqa: E402
    INTERFACE_REVISION,
    git_blob_sha,
    transform,
)
from publish_zpkg_v023_migration import (  # noqa: E402
    Ledger,
    MigrationError,
    Mutation,
    RepositoryPlan,
    check_state,
    classify_paths,
    load_plan,
    plan_digest,
    target_dirs_exist,
    validate_owner,
)

PLAN = ROOT / "repository-fleets" / "zed-v023-manifest-migration.json"


class FakeGitHub:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def get(self, path: str):  # noqa: ANN201
        self.path = path
        return 200, self.payload, {}


class RoutedFakeGitHub:
    def __init__(self, payloads: dict[str, dict[str, object]]) -> None:
        self.payloads = payloads
        self.paths: list[str] = []

    def get(self, path: str):  # noqa: ANN201
        self.paths.append(path)
        return 200, self.payloads[path], {}


def mutation(content: str, *, before: str = "a" * 40) -> Mutation:
    return Mutation(
        repository="acme/widget",
        default_branch="main",
        private=False,
        fork=False,
        path=".zpkg.toml",
        source_blob=before,
        proposed_blob=git_blob_sha(content),
        proposal_path=Path("proposal.toml"),
        phase=2,
        recipes=("test",),
        content=content,
    )


class TransformationTests(unittest.TestCase):
    def test_scripts_are_preserved_in_one_supported_test_command(self) -> None:
        source = '''[package]
org = "acme"
name = "widget"
version = "0.1.0"
description = "fixture"
license = "MIT"
language = "rust"

[package.repository]
vcs = "git"
url = "https://github.com/acme/widget"

[scripts]
test = "cargo test"
lint = "cargo clippy"
format = "cargo fmt --check"
'''
        proposed, recipes = transform(source)
        self.assertEqual(
            tomllib.loads(proposed)["scripts"],
            {"test": "cargo test && cargo clippy && cargo fmt --check"},
        )
        self.assertIn("collapse-scripts-to-test", recipes)

    def test_whole_repository_target_uses_canonical_package_name(self) -> None:
        source = '''[package]
org = "acme"
name = "widget"
version = "0.1.0"
description = "fixture"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://github.com/acme/widget"

[targets.repository]
dir = "."
name = "widget-repository"
'''
        proposed, recipes = transform(source)
        self.assertNotIn('name = "widget-repository"', proposed)
        self.assertIn("canonical-root-target-name", recipes)

    def test_legacy_e2e_identity_is_split_without_losing_dependencies(self) -> None:
        source = '''[package]
name = "embedded-alerts/eal-e2e"
version = "0.1.0"

[install]
dir = ".vendor/.zed"

[dependencies]
"embedded-alerts/eal-clients" = "^0.1.0"
"embedded-alerts/eal-interfaces" = "^0.1.0"
"embedded-alerts/eal-libs" = "^0.1.0"
"embedded-alerts/eal-cli" = "^0.1.0"
'''
        proposed, recipes = transform(source)
        parsed = tomllib.loads(proposed)
        self.assertEqual(parsed["package"]["org"], "embedded-alerts")
        self.assertEqual(parsed["package"]["name"], "eal-e2e")
        self.assertEqual(len(parsed["dependencies"]), 4)
        self.assertIn("split-legacy-e2e-identity", recipes)

    def test_graph_coordinates_are_canonicalized_but_blocked_roles_remain(self) -> None:
        source = '''[package]
org = "fiducia"
name = "consumer"
version = "0.1.0"
description = "fixture"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://github.com/fiducia-cloud/consumer"

[dependencies]
"fiducia/fiducia-clients" = "*"
"shared-auth/shared-auth-cli" = "^0.1.0"
'''
        proposed, _ = transform(source)
        parsed = tomllib.loads(proposed)
        self.assertEqual(parsed["package"]["org"], "fiducia-cloud")
        self.assertIn("fiducia-cloud/fiducia-clients", parsed["dependencies"])
        self.assertIn("shared-auth/shared-auth-cli", parsed["dependencies"])


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw, cls.plans = load_plan(PLAN)

    def test_plan_counts_and_provenance_are_exact(self) -> None:
        snapshot = self.raw["snapshot"]
        self.assertEqual(snapshot["manifest_instances"], 656)
        self.assertEqual(snapshot["repositories"], 620)
        self.assertEqual(snapshot["changed_unique_blobs"], 160)
        self.assertEqual(snapshot["mutation_instances"], 171)
        self.assertEqual(snapshot["mutation_repositories"], 155)
        self.assertEqual(self.raw["zed"]["interface_revision"], INTERFACE_REVISION)
        self.assertEqual(
            self.raw["immutable_snapshots"],
            [
                {
                    "path": "proof/den-2612/source/.zpkg.toml",
                    "proof_source_blob": "9fb75f27a614b3a15dd6fe23a6f72fbf0a28e5ba",
                    "reason": (
                        "DEN-2612 preserves byte-identical source evidence from "
                        "3FA-app/3fa-clients PR 37; proof-source.json and "
                        "verify_snapshot.py require this historical blob"
                    ),
                    "repository": "3fa-app-test/clients-consumer-matrix",
                    "snapshot_blob": "1a05a2c6850a375eb5720ff6bf23883b0a5fb63d",
                }
            ],
        )
        self.assertRegex(plan_digest(PLAN), r"[0-9a-f]{64}\Z")

    def test_every_proposal_is_content_addressed_and_structurally_isolated(self) -> None:
        proposals = {item["source_blob"]: item for item in self.raw["proposals"]}
        self.assertEqual(len(proposals), 160)
        for source_blob, proposal in proposals.items():
            path = PLAN.parent / proposal["file"]
            content = path.read_text(encoding="utf-8")
            self.assertEqual(git_blob_sha(content), proposal["proposed_blob"], source_blob)
            parsed = tomllib.loads(content)
            target_dirs = [target["dir"] for target in parsed.get("targets", {}).values()]
            self.assertEqual(len(target_dirs), len(set(target_dirs)), source_blob)
            self.assertEqual(set(parsed.get("scripts", {})), {"test"} if "scripts" in parsed else set())

    def test_source_generators_are_phase_one(self) -> None:
        phase_one = {plan.full_name.lower() for plan in self.plans.values() if plan.phase == 1}
        self.assertIn("oresoftware/ai-agent-coordinator.rs", phase_one)
        self.assertIn("apostille-me/.github", phase_one)
        self.assertNotIn("apostille-me/apme-e2e", phase_one)
        self.assertEqual(len(phase_one), 8)

    def test_plan_rejects_tampered_proposal_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            raw = json.loads(PLAN.read_text(encoding="utf-8"))
            proposal = raw["proposals"][0]
            raw["proposals"] = [proposal]
            raw["mutations"] = [
                item
                for item in raw["mutations"]
                if item["source_blob"] == proposal["source_blob"]
            ]
            raw["snapshot"]["mutation_instances"] = len(raw["mutations"])
            raw["snapshot"]["mutation_repositories"] = len(
                {item["repository"].lower() for item in raw["mutations"]}
            )
            source = PLAN.parent / proposal["file"]
            destination = temporary / "proposal.toml"
            destination.write_text(source.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
            proposal["file"] = destination.name
            for item in raw["mutations"]:
                item["proposal"] = destination.name
            plan = temporary / "plan.json"
            plan.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(Exception, "proposal bytes drift"):
                load_plan(plan)


class PublisherPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = '''[package]
org = "acme"
name = "widget"
version = "0.1.0"
description = "fixture"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://github.com/acme/widget"

[targets.rust]
dir = "src"
adapter = "rust"
'''
        self.item = mutation(self.content)
        self.plan = RepositoryPlan(
            full_name="acme/widget",
            default_branch="main",
            private=False,
            fork=False,
            phase=2,
            mutations=(self.item,),
        )

    def test_path_classification_rejects_partial_or_unknown_state(self) -> None:
        self.assertEqual(classify_paths(self.plan, {".zpkg.toml": "a" * 40}), "source")
        self.assertEqual(
            classify_paths(self.plan, {".zpkg.toml": self.item.proposed_blob}), "proposed"
        )
        with self.assertRaisesRegex(Exception, "unreviewed manifest blob"):
            classify_paths(self.plan, {".zpkg.toml": "b" * 40})

    def test_target_directory_must_exist_in_repository_tree(self) -> None:
        target_dirs_exist(self.item, {".zpkg.toml": "a" * 40, "src/lib.rs": "b" * 40})
        with self.assertRaisesRegex(Exception, "is absent"):
            target_dirs_exist(self.item, {".zpkg.toml": "a" * 40})

    def test_check_policy_fails_closed(self) -> None:
        success = {
            "check_runs": [
                {
                    "name": "verify",
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"slug": "github-actions"},
                }
            ]
        }
        self.assertEqual(check_state(FakeGitHub(success), self.plan, "c" * 40)[0], "success")
        failure = json.loads(json.dumps(success))
        failure["check_runs"][0]["conclusion"] = "failure"
        self.assertEqual(check_state(FakeGitHub(failure), self.plan, "c" * 40)[0], "failure")
        self.assertEqual(check_state(FakeGitHub({"check_runs": []}), self.plan, "c" * 40)[0], "none")

    def test_authenticated_user_namespace_is_accepted_without_org_lookup(self) -> None:
        github = RoutedFakeGitHub(
            {"/users/ORESoftware": {"login": "ORESoftware", "id": 7, "type": "User"}}
        )
        validate_owner(github, "ORESoftware", {"login": "ORESoftware", "id": 7})
        self.assertEqual(github.paths, ["/users/ORESoftware"])

    def test_foreign_user_namespace_is_rejected(self) -> None:
        github = RoutedFakeGitHub(
            {"/users/outsider": {"login": "outsider", "id": 8, "type": "User"}}
        )
        with self.assertRaisesRegex(MigrationError, "foreign user namespace"):
            validate_owner(github, "outsider", {"login": "ORESoftware", "id": 7})

    def test_administered_organization_namespace_is_accepted(self) -> None:
        github = RoutedFakeGitHub(
            {
                "/users/zed-pkg": {"login": "zed-pkg", "id": 9, "type": "Organization"},
                "/orgs/zed-pkg": {"login": "zed-pkg", "id": 9},
                "/user/memberships/orgs/zed-pkg": {"state": "active", "role": "admin"},
            }
        )
        validate_owner(github, "zed-pkg", {"login": "ORESoftware", "id": 7})
        self.assertEqual(len(github.paths), 3)

    def test_ledger_records_the_full_preflight_boundary(self) -> None:
        ledger = Ledger(
            mode="preflight",
            phase=1,
            plan_sha256="d" * 64,
            preflight_complete=True,
            preflight_repositories_total=155,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            ledger.write(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(payload["preflight_complete"])
        self.assertEqual(payload["preflight_repositories_total"], 155)


if __name__ == "__main__":
    unittest.main()
