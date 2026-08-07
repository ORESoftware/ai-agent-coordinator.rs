#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.memebank_live import model
from tools.memebank_live.publish import result_summary
from tools.memebank_live.source import provenance_json, write_provenance
from tools.memebank_live.tracking import issue_body


class PublicationContractTests(unittest.TestCase):
    def manifest(self) -> dict[str, object]:
        records = []
        for index, name in enumerate(model.EXPECTED_REPOSITORIES):
            records.append(
                {
                    "name": name,
                    "source_path": name,
                    "expected_tree": f"{index + 1:040x}",
                    "expected_head": f"{index + 101:040x}",
                    "tracked_entries": index + 1,
                    "role": "test-role",
                    "description": f"Description for {name}",
                }
            )
        return {
            "organization": model.ORG,
            "visibility": "private",
            "default_branch": "main",
            "repository_order": list(model.EXPECTED_REPOSITORIES),
            "repositories": records,
            "archive": {"sha256": "a" * 64},
        }

    def test_exact_thirteen_repository_contract(self) -> None:
        records = model.load_records(self.manifest())
        self.assertEqual(
            [record.name for record in records],
            list(model.EXPECTED_REPOSITORIES),
        )
        self.assertEqual(records[0].name, ".github")
        self.assertEqual(records[-1].name, "memebank-monorepo")
        self.assertIn("memebank-media-worker.rs", [record.name for record in records])
        self.assertNotIn("memebank-infra", [record.name for record in records])

    def test_manifest_uses_role_when_description_is_absent(self) -> None:
        document = self.manifest()
        repositories = document["repositories"]
        assert isinstance(repositories, list)
        first = repositories[0]
        assert isinstance(first, dict)
        first.pop("description")
        records = model.load_records(document)
        self.assertEqual(records[0].description, "test-role")

    def test_manifest_rejects_identity_policy_and_order_drift(self) -> None:
        for key, value in (
            ("organization", "other"),
            ("visibility", "public"),
            ("default_branch", "master"),
        ):
            with self.subTest(key=key):
                document = self.manifest()
                document[key] = value
                with self.assertRaises(model.PublicationError):
                    model.load_records(document)
        document = self.manifest()
        order = document["repository_order"]
        assert isinstance(order, list)
        order[1], order[2] = order[2], order[1]
        with self.assertRaisesRegex(model.PublicationError, "order changed"):
            model.load_records(document)

    def test_dotgithub_is_the_only_public_visibility_exception(self) -> None:
        self.assertEqual(model.PUBLIC_VISIBILITY_EXCEPTIONS, {".github"})

    def test_provenance_distinguishes_profile_exception(self) -> None:
        records = model.load_records(self.manifest())
        project = model.Project(
            1,
            "https://github.com/orgs/memebank/projects/1",
            model.PROJECT_TITLE,
        )
        dotgithub = provenance_json(self.manifest(), records[0], 1, project, "123")
        ordinary = provenance_json(self.manifest(), records[1], 2, project, "123")
        self.assertEqual(
            dotgithub["canonical_visibility"],
            "public-profile-exception",
        )
        self.assertEqual(ordinary["canonical_visibility"], "private")

    def test_provenance_is_idempotent_for_same_source_and_project(self) -> None:
        manifest = self.manifest()
        record = model.load_records(manifest)[1]
        project = model.Project(
            1,
            "https://github.com/orgs/memebank/projects/1",
            model.PROJECT_TITLE,
        )
        metadata = {
            "id": 2,
            "html_url": "https://github.com/memebank/mb-interfaces",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model.run(["git", "init", "-q", "-b", "main"], root)
            self.assertTrue(
                write_provenance(
                    root,
                    manifest,
                    record,
                    metadata,
                    project,
                    "run-1",
                )
            )
            self.assertFalse(
                write_provenance(
                    root,
                    manifest,
                    record,
                    metadata,
                    project,
                    "run-2",
                )
            )
            data = json.loads((root / "fleet-publication.json").read_text())
            self.assertEqual(data["approved_source_head"], record.expected_head)
            self.assertEqual(data["github_project"]["number"], 1)

    def test_tracking_issue_body_contains_every_repository(self) -> None:
        project = model.Project(
            1,
            "https://github.com/orgs/memebank/projects/1",
            model.PROJECT_TITLE,
        )
        results = [
            {
                "repository": f"memebank/{name}",
                "visibility": "public" if name == ".github" else "private",
                "initial_source_head": None if name == ".github" else "a" * 40,
                "pull_request": f"https://github.com/memebank/{name}/pull/1",
                "state": "merged",
            }
            for name in model.EXPECTED_REPOSITORIES
        ]
        body = issue_body(project, results)
        for name in model.EXPECTED_REPOSITORIES:
            self.assertIn(f"`memebank/{name}`", body)
        self.assertIn(model.PROJECT_TITLE, body)
        self.assertIn("additive governance exception", body)

    def test_scrub_removes_token_shapes(self) -> None:
        secret = "ghp_" + "A" * 36
        self.assertNotIn(secret, model.scrub(f"error {secret}"))
        self.assertIn("REDACTED", model.scrub(f"error {secret}"))

    def test_summary_counts_partial_publication(self) -> None:
        summary = result_summary(
            [
                {
                    "repository_created": True,
                    "initial_source_pushed": True,
                    "pull_request": "x",
                    "merged": True,
                },
                {
                    "repository_created": False,
                    "initial_source_pushed": False,
                    "pull_request": None,
                    "merged": True,
                },
            ],
            [{"repository": "memebank/x", "error": "blocked"}],
        )
        self.assertEqual(summary["repositories_total"], 3)
        self.assertEqual(summary["repositories_created"], 1)
        self.assertEqual(summary["provenance_pull_requests_merged"], 2)
        self.assertEqual(summary["failures"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
