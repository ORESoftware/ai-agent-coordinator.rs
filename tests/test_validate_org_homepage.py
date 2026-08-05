from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "validate_org_homepage.py"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "org-homepage.valid.md"

SPEC = importlib.util.spec_from_file_location("validate_org_homepage", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class OrganizationHomepageValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.valid = FIXTURE_PATH.read_text(encoding="utf-8")

    def validate(self, text: str, *, org: str = "example-org") -> list[str]:
        return VALIDATOR.validate_text(text, expect_org=org)

    def test_valid_fixture_passes(self) -> None:
        self.assertEqual(self.validate(self.valid), [])

    def test_human_entry_point_is_required(self) -> None:
        errors = self.validate(self.valid.replace("### For people", "### Contributors"))
        self.assertIn(
            "profile is missing required heading: ### For people",
            errors,
        )

    def test_machine_relationship_reference_is_required(self) -> None:
        errors = self.validate(
            self.valid.replace("repository-relationships.json", "relationships.json")
        )
        self.assertIn(
            "profile is missing required reference: repository-relationships.json",
            errors,
        )

    def test_unrendered_placeholder_fails(self) -> None:
        errors = self.validate(self.valid + "\n{{UNRENDERED_VALUE}}\n")
        self.assertIn("profile contains an unrendered template placeholder", errors)

    def test_final_newline_is_required(self) -> None:
        errors = self.validate(self.valid.rstrip("\n"))
        self.assertIn("profile must end with a final newline", errors)

    def test_ambiguous_work_must_fail_closed(self) -> None:
        weakened = self.valid.replace("must stop and be reported", "may continue")
        errors = self.validate(weakened)
        self.assertIn(
            "profile does not fail closed on missing or ambiguous context",
            errors,
        )

    def test_secret_shaped_value_fails(self) -> None:
        fake_token = "gh" + "p_" + ("A" * 30)
        errors = self.validate(self.valid + f"\n{fake_token}\n")
        self.assertIn("profile contains a possible GitHub token", errors)

    def test_immutable_owner_id_is_required(self) -> None:
        weakened = self.valid.replace(
            "Immutable GitHub owner ID:",
            "GitHub owner ID:",
        )
        errors = self.validate(weakened)
        self.assertIn(
            "profile does not publish an immutable numeric GitHub owner ID",
            errors,
        )

    def test_semantic_conflict_history_is_required(self) -> None:
        weakened = (
            self.valid.replace("merge base", "common ancestor")
            .replace("both sides", "each side")
            .replace("3–10", "recent")
        )
        errors = self.validate(weakened)
        self.assertIn("profile omits merge-base conflict analysis", errors)
        self.assertIn("profile omits inspection of both conflict sides", errors)
        self.assertIn(
            "profile omits the 3–10 relevant-commit history window",
            errors,
        )

    def test_expected_organization_must_match(self) -> None:
        errors = self.validate(self.valid, org="another-org")
        self.assertIn(
            "profile does not contain the canonical GitHub URL for another-org",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
