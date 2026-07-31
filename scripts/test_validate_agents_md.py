#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_PATH = Path(__file__).with_name("validate_agents_md.py")
SPEC = importlib.util.spec_from_file_location("validate_agents_md", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AgentsMdValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "repo"
        self.root.mkdir()
        self.write_valid_repository(self.root)

    def write_valid_repository(self, root: Path) -> None:
        canonical = (
            "# Repository agent instructions\n\n"
            "Load instructions root-to-leaf without scanning siblings.\n\n"
            "## Merge policy\n\n"
            "- avoid git rebase in favor of git merge.\n"
            "- Resolve conflicts semantically and scan the whole worktree.\n"
        )
        (root / "agents.md").write_text(canonical, encoding="utf-8")
        for relative_path in MODULE.POINTER_PATHS:
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(MODULE.POINTER_TEXT + "\n", encoding="utf-8")
        (root / "src").mkdir(exist_ok=True)
        (root / "src" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")

    def assert_has_error(self, report: object, fragment: str) -> None:
        errors = getattr(report, "errors")
        self.assertTrue(
            any(fragment in error for error in errors),
            f"expected {fragment!r} in {errors!r}",
        )

    def test_valid_repository_passes_and_emits_snapshot_fields(self) -> None:
        report = MODULE.validate_repository(self.root, self.root / "src")
        self.assertTrue(report.valid, report.errors)
        self.assertIsNotNone(report.canonical_sha256)
        self.assertIn("Repository agent instructions", report.headings)
        self.assertEqual(
            Path(report.instruction_files[-1]),
            (self.root / "agents.md").resolve(),
        )
        self.assertGreater(report.scanned_files, 0)

    def test_missing_canonical_file_fails(self) -> None:
        (self.root / "agents.md").unlink()
        report = MODULE.validate_repository(self.root)
        self.assert_has_error(report, "missing canonical lowercase root agents.md")

    def test_broken_pointer_fails(self) -> None:
        (self.root / ".claude" / "CLAUDE.md").write_text(
            "Use a different file.\n", encoding="utf-8"
        )
        report = MODULE.validate_repository(self.root)
        self.assert_has_error(report, "must contain only the canonical")

    def test_duplicated_canonical_content_in_pointer_fails(self) -> None:
        canonical = (self.root / "agents.md").read_text(encoding="utf-8")
        (self.root / ".gemini" / "GEMINI.md").write_text(
            canonical, encoding="utf-8"
        )
        report = MODULE.validate_repository(self.root)
        self.assert_has_error(report, "must contain only the canonical")

    def test_exact_relative_symlink_pointer_is_supported(self) -> None:
        pointer = self.root / ".openai" / "AGENTS.md"
        pointer.unlink()
        try:
            pointer.symlink_to("../agents.md")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        report = MODULE.validate_repository(self.root)
        self.assertTrue(report.valid, report.errors)

    def test_contradictory_rebase_guidance_fails(self) -> None:
        with (self.root / "agents.md").open("a", encoding="utf-8") as handle:
            handle.write("\nPrefer rebase for a clean history.\n")
        report = MODULE.validate_repository(self.root)
        self.assert_has_error(report, "contradictory or ambiguous rebase guidance")

    def test_nested_discovery_is_root_to_leaf_and_ignores_siblings(self) -> None:
        outer = Path(self.temp_dir.name) / "outer"
        repo = outer / "repo"
        nested = repo / "src" / "deep"
        sibling = outer / "sibling"
        nested.mkdir(parents=True)
        sibling.mkdir(parents=True)
        (outer / "agents.md").write_text("outer\n", encoding="utf-8")
        (repo / "agents.md").write_text("repo\n", encoding="utf-8")
        (sibling / "agents.md").write_text("sibling\n", encoding="utf-8")

        discovered = MODULE.discover_instruction_files(nested)
        self.assertEqual(
            discovered[-2:],
            ((outer / "agents.md").resolve(), (repo / "agents.md").resolve()),
        )
        self.assertNotIn((sibling / "agents.md").resolve(), discovered)

    def test_unreadable_instruction_file_is_reported(self) -> None:
        original = MODULE._read_text
        canonical = (self.root / "agents.md").resolve()

        def guarded(path: Path) -> str:
            if path.resolve() == canonical:
                raise MODULE.DiscoveryError(
                    f"instruction file is unreadable UTF-8: {path}"
                )
            return original(path)

        with mock.patch.object(MODULE, "_read_text", side_effect=guarded):
            report = MODULE.validate_repository(self.root)
        self.assert_has_error(report, "instruction file is unreadable UTF-8")

    def test_symlink_cycle_is_detected(self) -> None:
        loop = Path(self.temp_dir.name) / "loop"
        try:
            loop.symlink_to("loop", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaisesRegex(MODULE.DiscoveryError, "cannot be resolved safely"):
            MODULE.discover_instruction_files(loop)

    def test_conflict_markers_fail_but_git_directory_is_excluded(self) -> None:
        marker = "<" * 7 + " HEAD\n"
        broken = self.root / "src" / "broken.rs"
        broken.write_text(
            marker
            + "left\n"
            + "=" * 7
            + "\nright\n"
            + ">" * 7
            + " topic\n",
            encoding="utf-8",
        )
        report = MODULE.validate_repository(self.root)
        self.assert_has_error(report, "unresolved conflict marker at src/broken.rs:1")

        broken.unlink()
        git_dir = self.root / ".git"
        git_dir.mkdir()
        (git_dir / "ignored").write_text(marker, encoding="utf-8")
        report = MODULE.validate_repository(self.root)
        self.assertTrue(report.valid, report.errors)


if __name__ == "__main__":
    unittest.main()
