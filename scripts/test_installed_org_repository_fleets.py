#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("render_repository_fleet.py")
SPEC = importlib.util.spec_from_file_location("render_repository_fleet", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

ROOT = Path(__file__).parents[1]
INSTALLED = {
    "apostille-me": ("DEN-1951", 5, "apostille-me-libs", "apostille-me-monorepo"),
    "evento-globolo": ("DEN-1889", 12, "evgl-interfaces", "evento-globolo-monorepo"),
    "embedded-alerts": ("DEN-1949", 12, "eal-interfaces", "embedded-alerts-monorepo"),
    "hacker-house-medellin": (
        "DEN-1950",
        12,
        "hhm-interfaces",
        "hacker-house-medellin-monorepo",
    ),
}


class InstalledOrganizationFleetTests(unittest.TestCase):
    def manifest(self, organization: str):
        return MODULE.load_manifest(ROOT / "repository-fleets" / f"{organization}.json")

    def test_installed_org_manifests_are_dry_run_ready_but_not_live(self) -> None:
        for organization, (issue, count, first, last) in INSTALLED.items():
            with self.subTest(organization=organization):
                manifest = self.manifest(organization)
                plan = MODULE.render(
                    manifest,
                    mode="plan",
                    repository_name=None,
                    confirmation=None,
                )
                self.assertEqual(plan["organization"], organization)
                self.assertEqual(plan["tracking_issue"], issue)
                self.assertTrue(plan["ready_for_dry_run"])
                self.assertFalse(plan["ready_for_live"])
                self.assertEqual(plan["blockers"], [])
                self.assertEqual(len(plan["repositories"]), count)
                self.assertEqual(
                    plan["repositories"][0]["full_name"],
                    f"{organization}/{first}",
                )
                self.assertEqual(
                    plan["repositories"][-1]["full_name"],
                    f"{organization}/{last}",
                )
                self.assertIn(".github", manifest.deferred_repositories)

                dry_run = MODULE.render(
                    manifest,
                    mode="dry-run",
                    repository_name=None,
                    confirmation=None,
                )
                self.assertEqual(len(dry_run["requests"]), count)
                self.assertTrue(
                    all(request["dry_run"] for request in dry_run["requests"])
                )
                self.assertTrue(
                    all(
                        "confirm_repository" not in request
                        for request in dry_run["requests"]
                    )
                )
                self.assertTrue(
                    all(
                        request["visibility"] == "public"
                        for request in dry_run["requests"]
                    )
                )

    def test_infra_repositories_carry_bounded_cloudflare_worker_scope(self) -> None:
        for organization in INSTALLED:
            with self.subTest(organization=organization):
                manifest = self.manifest(organization)
                infra = [
                    repository
                    for repository in manifest.repositories
                    if repository.name.endswith("-infra")
                ]
                self.assertEqual(len(infra), 1)
                self.assertIsNotNone(infra[0].description)
                self.assertIn("bounded Cloudflare Worker", infra[0].description)

    def test_live_rendering_stays_disabled_even_with_exact_confirmation(self) -> None:
        for organization, (_, _, first, _) in INSTALLED.items():
            with self.subTest(organization=organization):
                manifest = self.manifest(organization)
                with self.assertRaisesRegex(
                    ValueError,
                    "live_creation_enabled is false",
                ):
                    MODULE.render(
                        manifest,
                        mode="live",
                        repository_name=first,
                        confirmation=f"{organization}/{first}",
                    )

    def test_liberty_cal_is_plan_only_until_org_install_and_visibility_review(self) -> None:
        manifest = self.manifest("liberty-cal")
        plan = MODULE.render(
            manifest,
            mode="plan",
            repository_name=None,
            confirmation=None,
        )
        self.assertEqual(plan["tracking_issue"], "DEN-1948")
        self.assertFalse(plan["ready_for_dry_run"])
        self.assertFalse(plan["ready_for_live"])
        self.assertEqual(
            plan["blockers"],
            ["visibility decision required for liberty-cal/liberty-cal"],
        )
        self.assertEqual(len(plan["repositories"]), 1)
        with self.assertRaisesRegex(ValueError, "visibility is decided"):
            MODULE.render(
                manifest,
                mode="dry-run",
                repository_name=None,
                confirmation=None,
            )


if __name__ == "__main__":
    unittest.main()
