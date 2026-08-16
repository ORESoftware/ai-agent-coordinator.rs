from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "semantic_merge_guard.py"
SWEEP = ROOT / "docs" / "semantic-merge-incident-sweep-2026-08-09.json"


def run(command: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


class RepositoryFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        run(["git", "init", "-b", "main"], root)
        run(["git", "config", "user.name", "Semantic Guard Test"], root)
        run(["git", "config", "user.email", "semantic-guard@example.invalid"], root)

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self, message: str) -> str:
        run(["git", "add", "--all"], self.root)
        run(["git", "commit", "-m", message], self.root)
        return run(["git", "rev-parse", "HEAD"], self.root).stdout.strip()

    def branch(self, name: str, start: str = "HEAD") -> None:
        run(["git", "switch", "-c", name, start], self.root)

    def switch(self, name: str) -> None:
        run(["git", "switch", name], self.root)

    def head(self) -> str:
        return run(["git", "rev-parse", "HEAD"], self.root).stdout.strip()

    def heads(self) -> str:
        return run(
            ["git", "for-each-ref", "--format=%(refname):%(objectname)", "refs/heads"],
            self.root,
        ).stdout


class SemanticMergeGuardTests(unittest.TestCase):
    def invoke(self, repo: Path, base: str, head: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        report = repo / "review.json"
        result = run(
            [
                sys.executable,
                str(GUARD),
                "--repo",
                str(repo),
                "--base",
                base,
                "--head",
                head,
                "--output",
                str(report),
                "--history-depth",
                "3",
            ],
            repo,
            check=False,
        )
        self.assertTrue(report.is_file(), result.stderr)
        return result, json.loads(report.read_text(encoding="utf-8"))

    def make_repo(self) -> tuple[tempfile.TemporaryDirectory[str], RepositoryFixture]:
        directory = tempfile.TemporaryDirectory()
        return directory, RepositoryFixture(Path(directory.name))

    def test_conflict_fails_closed_and_preserves_stage_blobs_without_source_content(self) -> None:
        directory, repo = self.make_repo()
        self.addCleanup(directory.cleanup)
        sentinel = "DO_NOT_SERIALIZE_THIS_SOURCE_SENTINEL"
        repo.write(
            "src/session.rs",
            "pub struct Session {\n    pub user_id: String,\n}\n",
        )
        base = repo.commit("base")
        repo.branch("feature", base)
        repo.write(
            "src/session.rs",
            f"pub struct Session {{\n    pub user_id: String,\n    pub auth_time: u64, // {sentinel}\n}}\n",
        )
        repo.write("docs/feature.md", "feature-side context\n")
        head = repo.commit("feature auth time")
        repo.switch("main")
        repo.write(
            "src/session.rs",
            "pub struct Session {\n    pub user_id: String,\n    pub auth_time: i64,\n}\n",
        )
        repo.write("README.md", "base-side context\n")
        repo.commit("main auth time")
        original_head = repo.head()
        original_refs = repo.heads()

        result, report = self.invoke(repo.root, "main", head)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report["status"], "manual_resolution_required")
        self.assertFalse(report["safe_to_publish"])
        self.assertFalse(report["safe_to_open_review_pr"])
        self.assertEqual(report["changed_paths"], ["README.md", "docs/feature.md", "src/session.rs"])
        self.assertEqual(report["conflict_paths"], ["src/session.rs"])
        self.assertEqual(set(report["conflicts"][0]["stages"]), {"1", "2", "3"})
        self.assertEqual(repo.head(), original_head)
        self.assertEqual(repo.heads(), original_refs)
        self.assertNotIn(sentinel, json.dumps(report))
        self.assertFalse(report["invariants"]["commit_created"])
        self.assertFalse(report["invariants"]["force_push_used"])

    def test_clean_preview_produces_tree_without_updating_any_branch(self) -> None:
        directory, repo = self.make_repo()
        self.addCleanup(directory.cleanup)
        repo.write("README.md", "base\n")
        base = repo.commit("base")
        repo.branch("feature", base)
        repo.write("src/lib.rs", "pub fn answer() -> u8 { 42 }\n")
        head = repo.commit("add library")
        repo.switch("main")
        repo.write("README.md", "base\nmain documentation\n")
        repo.commit("main docs")
        original_head = repo.head()
        original_refs = repo.heads()

        result, report = self.invoke(repo.root, "main", head)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["status"], "clean_preview")
        self.assertFalse(report["safe_to_publish"])
        self.assertTrue(report["safe_to_open_review_pr"])
        self.assertRegex(report["preview_tree"], r"^[0-9a-f]{40}$")
        self.assertEqual(report["findings"], [])
        self.assertEqual(repo.head(), original_head)
        self.assertEqual(repo.heads(), original_refs)
        self.assertEqual(report["changed_paths"], ["README.md", "src/lib.rs"])
        self.assertEqual(report["preview_changed_paths"], ["src/lib.rs"])

    def test_clean_merge_with_duplicate_toml_table_is_rejected(self) -> None:
        directory, repo = self.make_repo()
        self.addCleanup(directory.cleanup)
        repo.write("Cargo.toml", '[package]\nname = "demo"\nversion = "0.1.0"\n')
        base = repo.commit("base")
        repo.branch("feature", base)
        repo.write(
            "Cargo.toml",
            '[package]\nname = "demo"\nversion = "0.1.0"\n\n[package]\nedition = "2021"\n',
        )
        head = repo.commit("duplicate package table")
        repo.switch("main")

        result, report = self.invoke(repo.root, "main", head)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["status"], "semantic_validation_failed")
        self.assertIn("duplicate_toml_table", {item["kind"] for item in report["findings"]})

    def test_clean_merge_with_conflict_marker_is_rejected(self) -> None:
        directory, repo = self.make_repo()
        self.addCleanup(directory.cleanup)
        repo.write("README.md", "base\n")
        base = repo.commit("base")
        repo.branch("feature", base)
        repo.write("notes.txt", "<<<<<<< current\nleft\n=======\nright\n>>>>>>> incoming\n")
        head = repo.commit("accidental marker")
        repo.switch("main")

        result, report = self.invoke(repo.root, "main", head)

        self.assertEqual(result.returncode, 2)
        self.assertIn("unresolved_conflict_marker", {item["kind"] for item in report["findings"]})

    def test_duplicate_rust_fields_at_same_block_depth_are_rejected(self) -> None:
        directory, repo = self.make_repo()
        self.addCleanup(directory.cleanup)
        repo.write("README.md", "base\n")
        base = repo.commit("base")
        repo.branch("feature", base)
        repo.write(
            "src/session.rs",
            "pub struct Session {\n    pub auth_time: i64,\n    pub user_id: String,\n    pub auth_time: u64,\n}\n",
        )
        head = repo.commit("corrupt duplicate field")
        repo.switch("main")

        result, report = self.invoke(repo.root, "main", head)

        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "duplicate_rust_field_at_same_block_depth",
            {item["kind"] for item in report["findings"]},
        )

    def test_incident_inventory_is_deterministic_and_complete(self) -> None:
        payload = json.loads(SWEEP.read_text(encoding="utf-8"))
        repositories = payload["repositories"]
        commits = [commit for repository in repositories for commit in repository["commits"]]
        self.assertEqual(payload["commit_count"], 50)
        self.assertEqual(payload["repository_count"], 13)
        self.assertEqual(len(repositories), 13)
        self.assertEqual(len(commits), 50)
        self.assertEqual(len(commits), len(set(commits)))
        self.assertTrue(all(len(commit) == 40 for commit in commits))
        self.assertTrue(all(repository["build_status"] == "not_run" for repository in repositories))


if __name__ == "__main__":
    unittest.main()
