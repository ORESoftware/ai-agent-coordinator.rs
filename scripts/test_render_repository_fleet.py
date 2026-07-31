#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("render_repository_fleet.py")
SPEC = importlib.util.spec_from_file_location("render_repository_fleet", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

MANIFEST_PATH = Path(__file__).parents[1] / "repository-fleets" / "memebank.json"


class RepositoryFleetTests(unittest.TestCase):
    def load_raw(self) -> dict[str, object]:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def write_manifest(self, raw: dict[str, object]) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "fleet.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return path

    def resolve_visibility(
        self,
        raw: dict[str, object],
        visibility: str = "private",
    ) -> dict[str, object]:
        repositories = raw["repositories"]
        assert isinstance(repositories, list)
        for repository in repositories:
            assert isinstance(repository, dict)
            repository["visibility"] = visibility
        return raw

    def test_canonical_manifest_is_plan_only_until_visibility_review(self) -> None:
        manifest = MODULE.load_manifest(MANIFEST_PATH)
        output = MODULE.render(
            manifest,
            mode="plan",
            repository_name=None,
            confirmation=None,
        )
        self.assertEqual(output["organization"], "memebank")
        self.assertFalse(output["ready_for_dry_run"])
        self.assertFalse(output["ready_for_live"])
        self.assertEqual(len(output["blockers"]), 12)
        self.assertIn(".github", manifest.deferred_repositories)
        self.assertNotIn(
            "memebank/.github",
            [repository["full_name"] for repository in output["repositories"]],
        )
        self.assertEqual(
            output["repositories"][0]["full_name"],
            "memebank/mb-interfaces",
        )
        self.assertEqual(
            output["repositories"][-1]["full_name"],
            "memebank/memebank-monorepo",
        )

    def test_dry_run_fails_closed_when_visibility_is_unresolved(self) -> None:
        manifest = MODULE.load_manifest(MANIFEST_PATH)
        with self.assertRaisesRegex(ValueError, "visibility is decided"):
            MODULE.render(
                manifest,
                mode="dry-run",
                repository_name=None,
                confirmation=None,
            )

    def test_dry_run_renders_all_requests_after_visibility_review(self) -> None:
        path = self.write_manifest(self.resolve_visibility(self.load_raw()))
        manifest = MODULE.load_manifest(path)
        output = MODULE.render(
            manifest,
            mode="dry-run",
            repository_name=None,
            confirmation=None,
        )
        self.assertEqual(len(output["requests"]), 12)
        self.assertTrue(all(request["dry_run"] for request in output["requests"]))
        self.assertNotIn("confirm_repository", output["requests"][0])
        self.assertNotIn(
            "memebank/.github",
            [
                f"{request['organization']}/{request['name']}"
                for request in output["requests"]
            ],
        )

    def test_live_rendering_requires_switch_single_repo_and_exact_confirmation(self) -> None:
        raw = self.resolve_visibility(self.load_raw())
        raw["live_creation_enabled"] = True
        path = self.write_manifest(raw)
        manifest = MODULE.load_manifest(path)

        with self.assertRaisesRegex(ValueError, "requires --repository"):
            MODULE.render(
                manifest,
                mode="live",
                repository_name=None,
                confirmation=None,
            )
        with self.assertRaisesRegex(ValueError, "requires --confirm-repository"):
            MODULE.render(
                manifest,
                mode="live",
                repository_name="mb-infra",
                confirmation="memebank/not-mb-infra",
            )

        output = MODULE.render(
            manifest,
            mode="live",
            repository_name="mb-infra",
            confirmation="memebank/mb-infra",
        )
        request = output["request"]
        self.assertFalse(request["dry_run"])
        self.assertEqual(request["confirm_repository"], "memebank/mb-infra")

    def test_forbidden_legacy_infra_alias_is_rejected(self) -> None:
        raw = self.load_raw()
        repositories = raw["repositories"]
        assert isinstance(repositories, list)
        repositories[0]["name"] = "memebank-infra"
        path = self.write_manifest(raw)
        with self.assertRaisesRegex(ValueError, "forbidden repositories"):
            MODULE.load_manifest(path)

    def test_orchestration_monorepo_must_be_last(self) -> None:
        raw = self.load_raw()
        repositories = raw["repositories"]
        assert isinstance(repositories, list)
        repositories[0]["order"], repositories[-1]["order"] = (
            repositories[-1]["order"],
            repositories[0]["order"],
        )
        path = self.write_manifest(raw)
        with self.assertRaisesRegex(ValueError, "monorepo must be created last"):
            MODULE.load_manifest(path)


if __name__ == "__main__":
    unittest.main()
