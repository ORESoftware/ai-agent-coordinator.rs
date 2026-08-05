from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import render_org_homepage as renderer
import render_org_project_context as context_renderer
import validate_org_homepage as validator

TEMPLATE = (ROOT / "templates" / "org-profile-readme.md").read_text(
    encoding="utf-8"
)
REGISTRY = ROOT / "config" / "org-project-registry.yaml"

BASE_CONTEXT = {
    "github": {
        "login": "example-org",
        "account_id": 123456789,
        "account_type": "Organization",
    },
    "linear": {
        "project_id": "12345678-1234-4abc-8def-123456789abc",
        "project_name": "github.com/example-org",
        "project_url": "https://linear.app/example/project/githubcomexample-org-123456789abc",
        "team_id": "87654321-4321-4cba-8fed-cba987654321",
        "team_key": "EX",
    },
    "runtime_route": {
        "default_repository": None,
        "repository_allowlist": [],
    },
    "public_context_only": True,
}

SUMMARY = (
    "Example Organization builds dependable, privacy-conscious services and reusable "
    "software components for teams that need clear documentation, safe automation, "
    "and reviewable operational practices."
)


class OrganizationHomepageRendererTests(unittest.TestCase):
    def render(self, context: dict | None = None, template: str = TEMPLATE) -> str:
        return renderer.render_profile(
            copy.deepcopy(BASE_CONTEXT if context is None else context),
            template,
            display_name="Example Organization",
            summary=SUMMARY,
            public_starting_points=[
                "the [public application](https://github.com/example-org/example-app)"
            ],
            operating_principles=[
                "Preserve reviewed privacy, compatibility, and data-integrity constraints."
            ],
        )

    def test_valid_context_renders_a_valid_profile(self) -> None:
        profile = self.render()
        self.assertEqual(
            validator.validate_text(profile, expect_org="example-org"),
            [],
        )
        self.assertIn("# Example Organization", profile)
        self.assertIn("no reviewed default runtime repository", profile)
        self.assertNotIn("{{", profile)

    def test_default_route_and_allowlist_are_rendered(self) -> None:
        context = copy.deepcopy(BASE_CONTEXT)
        context["runtime_route"] = {
            "default_repository": "example-org/example-api.rs",
            "repository_allowlist": [
                "example-org/example-api.rs",
                "example-org/example-worker.rs",
            ],
        }
        profile = self.render(context)
        self.assertIn(
            "default repository is `example-org/example-api.rs`",
            profile,
        )
        self.assertIn("`example-org/example-worker.rs`", profile)

    def test_default_route_must_appear_in_allowlist(self) -> None:
        context = copy.deepcopy(BASE_CONTEXT)
        context["runtime_route"] = {
            "default_repository": "example-org/example-api.rs",
            "repository_allowlist": ["example-org/example-worker.rs"],
        }
        with self.assertRaisesRegex(
            ValueError,
            "default_repository must appear in repository_allowlist",
        ):
            self.render(context)

    def test_user_account_is_rejected(self) -> None:
        context = copy.deepcopy(BASE_CONTEXT)
        context["github"]["account_type"] = "User"
        with self.assertRaisesRegex(ValueError, "account_type=Organization"):
            self.render(context)

    def test_public_context_flag_is_required(self) -> None:
        context = copy.deepcopy(BASE_CONTEXT)
        context["public_context_only"] = False
        with self.assertRaisesRegex(ValueError, "public_context_only=true"):
            self.render(context)

    def test_unknown_template_placeholder_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown or unrendered placeholders"):
            self.render(template=TEMPLATE + "\n{{UNKNOWN_FIELD}}\n")

    def test_duplicate_json_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project-context.yaml"
            path.write_text(
                '{"github":{"login":"one"},"github":{"login":"two"}}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate JSON key: github"):
                renderer.load_context(path)

    def test_atomic_output_has_exact_rendered_bytes(self) -> None:
        profile = self.render()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile" / "README.md"
            renderer.write_atomic(path, profile)
            self.assertEqual(path.read_text(encoding="utf-8"), profile)
            self.assertTrue(profile.endswith("\n"))

    def test_context_round_trip_through_loader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project-context.yaml"
            path.write_text(
                json.dumps(BASE_CONTEXT, indent=2) + "\n",
                encoding="utf-8",
            )
            loaded = renderer.load_context(path)
            self.assertEqual(loaded, BASE_CONTEXT)
            self.assertEqual(
                validator.validate_text(self.render(loaded), expect_org="example-org"),
                [],
            )

    def test_canonical_context_bundle_uses_homepage_contract(self) -> None:
        registry = context_renderer.load_registry(REGISTRY)
        bundle = context_renderer.render_bundle(
            registry,
            "fiducia-cloud",
            "0" * 40,
        )
        profile = bundle["profile/README.md"]
        self.assertEqual(
            validator.validate_text(profile, expect_org="fiducia-cloud"),
            [],
        )
        self.assertIn("### For people", profile)
        self.assertIn("### For AI agents", profile)
        self.assertIn("repository-relationships.json", profile)
        self.assertIn("Immutable GitHub owner ID", profile)


if __name__ == "__main__":
    unittest.main()
