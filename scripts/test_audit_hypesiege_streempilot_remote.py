#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
import unittest

from audit_hypesiege_streempilot_remote import FleetAuditError, audit_fleet


HYPESIEGE = [
    "hypesiege-analytics.rs",
    "hypesiege-api-server.rs",
    "hypesiege-cli.rs",
    "hypesiege-clients",
    "hypesiege-connectors",
    "hypesiege-e2e",
    "hypesiege-flutter-app",
    "hypesiege-infra",
    "hypesiege-interfaces",
    "hypesiege-mcp-server.rs",
    "hypesiege-publishing-worker.rs",
    "hypesiege-scheduler.rs",
    "hypesiege-sync",
    "hypesiege-web-server.rs",
    "hypesiege-monorepo",
]
STREEMPILOT = [
    "streempilot-api-server.rs",
    "streempilot-chat.rs",
    "streempilot-cli.rs",
    "streempilot-clients",
    "streempilot-compositor.rs",
    "streempilot-destinations",
    "streempilot-e2e",
    "streempilot-flutter-app",
    "streempilot-infra",
    "streempilot-interfaces",
    "streempilot-mcp-server.rs",
    "streempilot-media-router.rs",
    "streempilot-recording.rs",
    "streempilot-sync",
    "streempilot-web-server.rs",
    "streempilot-webrtc-adapter.rs",
    "streempilot-monorepo",
]


class RemoteFleetAuditTests(unittest.TestCase):
    def manifest(self) -> dict[str, object]:
        records: list[dict[str, object]] = []
        for org, names, expected_gitlinks in (
            ("hypesiege", HYPESIEGE, 14),
            ("streempilot", STREEMPILOT, 16),
        ):
            for index, name in enumerate(names):
                records.append(
                    {
                        "org": org,
                        "name": name,
                        "full_name": f"{org}/{name}",
                        "kind": "monorepo" if name.endswith("monorepo") else "fixture",
                        "commit": f"{index + (0 if org == 'hypesiege' else 15):040x}",
                        "default_branch": "main",
                        "visibility": "public",
                        "gitlinks": expected_gitlinks if name.endswith("monorepo") else 0,
                    }
                )
        return {
            "schema_version": 2,
            "repository_count": 32,
            "organizations": {"hypesiege": 15, "streempilot": 17},
            "repositories": records,
        }

    def record(self, full_name: str, **overrides: object) -> dict[str, object]:
        value: dict[str, object] = {
            "full_name": full_name,
            "default_branch": "main",
            "visibility": "private",
            "admin": True,
        }
        value.update(overrides)
        return value

    def complete_snapshot(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "captured_at": "2026-08-03T18:00:00Z",
            "repositories": [
                self.record(f"hypesiege/{name}") for name in HYPESIEGE
            ]
            + [self.record(f"StreemPilot/{name}") for name in STREEMPILOT],
            "monorepo_gitlinks": {"hypesiege": 14, "streempilot": 16},
        }

    def partial_snapshot(self) -> dict[str, object]:
        hypesiege_present = [
            "hypesiege-interfaces",
            "hypesiege-api-server.rs",
            "hypesiege-web-server.rs",
            "hypesiege-flutter-app",
            "hypesiege-cli",
            "hypesiege-clients",
            "hypesiege-sync",
            "hypesiege-e2e",
            "hypesiege-mcp-server.rs",
            "hypesiege-infra",
            "hypesiege-monorepo",
        ]
        streempilot_present = [
            "streempilot-interfaces",
            "streempilot-api-server.rs",
            "streempilot-web-server.rs",
            "streempilot-flutter-app",
            "streempilot-cli",
            "streempilot-clients",
            "streempilot-sync",
            "streempilot-e2e",
            "streempilot-mcp-server.rs",
            "streempilot-infra",
            "streempilot-monorepo",
        ]
        return {
            "schema_version": 1,
            "captured_at": "2026-08-03T18:00:00Z",
            "repositories": [
                self.record(f"hypesiege/{name}") for name in hypesiege_present
            ]
            + [self.record(f"StreemPilot/{name}") for name in streempilot_present],
            "monorepo_gitlinks": {"hypesiege": 10, "streempilot": 10},
        }

    def test_complete_private_fleet_passes(self) -> None:
        manifest = self.manifest()
        original = deepcopy(manifest)
        result = audit_fleet(manifest, self.complete_snapshot())
        self.assertTrue(result["complete"])
        self.assertEqual(manifest, original)
        for org in ("hypesiege", "streempilot"):
            summary = result["organizations"][org]
            self.assertTrue(summary["complete"])
            self.assertEqual(summary["missing_canonical"], [])
            self.assertEqual(summary["legacy_aliases"], [])
            self.assertFalse(summary["monorepo_reseal_required"])

    def test_current_partial_snapshot_reports_exact_gaps(self) -> None:
        result = audit_fleet(self.manifest(), self.partial_snapshot())
        self.assertFalse(result["complete"])

        hypesiege = result["organizations"]["hypesiege"]
        self.assertEqual(hypesiege["actual_count"], 11)
        self.assertEqual(hypesiege["canonical_present_count"], 10)
        self.assertEqual(
            hypesiege["missing_canonical"],
            [
                "hypesiege/hypesiege-analytics.rs",
                "hypesiege/hypesiege-cli.rs",
                "hypesiege/hypesiege-connectors",
                "hypesiege/hypesiege-publishing-worker.rs",
                "hypesiege/hypesiege-scheduler.rs",
            ],
        )
        self.assertEqual(
            hypesiege["legacy_aliases"],
            [
                {
                    "actual": "hypesiege/hypesiege-cli",
                    "canonical": "hypesiege/hypesiege-cli.rs",
                }
            ],
        )
        self.assertTrue(hypesiege["monorepo_reseal_required"])

        streempilot = result["organizations"]["streempilot"]
        self.assertEqual(streempilot["actual_count"], 11)
        self.assertEqual(streempilot["canonical_present_count"], 10)
        self.assertEqual(
            streempilot["missing_canonical"],
            [
                "streempilot/streempilot-chat.rs",
                "streempilot/streempilot-cli.rs",
                "streempilot/streempilot-compositor.rs",
                "streempilot/streempilot-destinations",
                "streempilot/streempilot-media-router.rs",
                "streempilot/streempilot-recording.rs",
                "streempilot/streempilot-webrtc-adapter.rs",
            ],
        )
        self.assertEqual(
            streempilot["legacy_aliases"],
            [
                {
                    "actual": "streempilot/streempilot-cli",
                    "canonical": "streempilot/streempilot-cli.rs",
                }
            ],
        )
        self.assertTrue(streempilot["monorepo_reseal_required"])

    def test_legacy_alias_never_satisfies_canonical_identity(self) -> None:
        snapshot = self.complete_snapshot()
        repositories = snapshot["repositories"]
        assert isinstance(repositories, list)
        for record in repositories:
            assert isinstance(record, dict)
            if str(record["full_name"]).casefold() == "hypesiege/hypesiege-cli.rs":
                record["full_name"] = "hypesiege/hypesiege-cli"
        result = audit_fleet(self.manifest(), snapshot)
        summary = result["organizations"]["hypesiege"]
        self.assertIn("hypesiege/hypesiege-cli.rs", summary["missing_canonical"])
        self.assertEqual(len(summary["legacy_aliases"]), 1)
        self.assertFalse(summary["complete"])

    def test_branch_visibility_and_admin_drift_are_independent(self) -> None:
        snapshot = self.complete_snapshot()
        repositories = snapshot["repositories"]
        assert isinstance(repositories, list)
        repositories[0]["default_branch"] = "master"
        repositories[1]["visibility"] = "public"
        repositories[2]["admin"] = False
        result = audit_fleet(self.manifest(), snapshot)
        summary = result["organizations"]["hypesiege"]
        self.assertEqual(summary["default_branch_drift"], ["hypesiege/hypesiege-analytics.rs"])
        self.assertEqual(summary["visibility_drift"], ["hypesiege/hypesiege-api-server.rs"])
        self.assertEqual(summary["admin_access_missing"], ["hypesiege/hypesiege-cli.rs"])

    def test_unexpected_repository_is_not_silently_absorbed(self) -> None:
        snapshot = self.complete_snapshot()
        repositories = snapshot["repositories"]
        assert isinstance(repositories, list)
        repositories.append(self.record("hypesiege/hypesiege-ad-hoc"))
        result = audit_fleet(self.manifest(), snapshot)
        self.assertEqual(
            result["organizations"]["hypesiege"]["unexpected"],
            ["hypesiege/hypesiege-ad-hoc"],
        )
        self.assertFalse(result["complete"])

    def test_case_insensitive_duplicate_remote_identity_fails_closed(self) -> None:
        snapshot = self.complete_snapshot()
        repositories = snapshot["repositories"]
        assert isinstance(repositories, list)
        repositories.append(self.record("HYPESIEGE/HYPESIEGE-INTERFACES"))
        with self.assertRaisesRegex(FleetAuditError, "duplicate repository"):
            audit_fleet(self.manifest(), snapshot)

    def test_malformed_ledger_and_snapshot_fail_closed(self) -> None:
        manifest = self.manifest()
        manifest["repository_count"] = 31
        with self.assertRaisesRegex(FleetAuditError, "repository_count"):
            audit_fleet(manifest, self.complete_snapshot())

        snapshot = self.complete_snapshot()
        snapshot["monorepo_gitlinks"] = {"hypesiege": 14}
        with self.assertRaisesRegex(FleetAuditError, "streempilot gitlink"):
            audit_fleet(self.manifest(), snapshot)


if __name__ == "__main__":
    unittest.main()
