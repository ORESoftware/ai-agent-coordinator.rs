#!/usr/bin/env python3
"""Attack-regression tests for the sealed MemeBank source-v2 carrier.

These tests intentionally avoid network access and credentials. They exercise the
validator's trust boundaries with synthetic manifests and tarballs while the
workflow separately reconstructs the complete checked-in carrier and Git
histories.
"""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

VALIDATOR_PATH = Path(__file__).with_name("validate_memebank_source_v2.py")
SPEC = importlib.util.spec_from_file_location("validate_memebank_source_v2", VALIDATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

REPOSITORY_ROOT = Path(__file__).parents[1]
CANONICAL_MANIFEST = REPOSITORY_ROOT / MODULE.MANIFEST


class SourceV2ValidatorTests(unittest.TestCase):
    def load_raw_manifest(self) -> dict[str, object]:
        return json.loads(CANONICAL_MANIFEST.read_text(encoding="utf-8"))

    def write_manifest(self, value: dict[str, object]) -> Path:
        temporary = tempfile.TemporaryDirectory(prefix="memebank-source-v2-manifest-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        path = root / MODULE.MANIFEST
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return root

    def make_archive(
        self,
        entries: list[tuple[str, str, bytes | str, int]],
    ) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            for name, kind, payload, mode in entries:
                member = tarfile.TarInfo(name)
                member.mtime = 0
                member.uid = 0
                member.gid = 0
                member.uname = ""
                member.gname = ""
                member.mode = mode
                if kind == "dir":
                    member.type = tarfile.DIRTYPE
                    member.size = 0
                    archive.addfile(member)
                elif kind == "file":
                    assert isinstance(payload, bytes)
                    member.type = tarfile.REGTYPE
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
                elif kind == "symlink":
                    assert isinstance(payload, str)
                    member.type = tarfile.SYMTYPE
                    member.linkname = payload
                    member.size = 0
                    archive.addfile(member)
                else:
                    raise AssertionError(f"unsupported test entry kind: {kind}")
        return buffer.getvalue()

    def payload_manifest(
        self,
        root: Path,
        encoded: bytes,
        *,
        archive: bytes = b"",
        chunk_path: str = "repository-fleets/memebank-source-v2/test.part.txt",
    ) -> dict[str, object]:
        path = root / chunk_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
        return {
            "payload": {
                "encoding": "base64",
                "base64_bytes": len(encoded),
                "chunks": [
                    {
                        "path": chunk_path,
                        "bytes": len(encoded),
                        "sha256": MODULE.digest(encoded),
                    }
                ],
            },
            "archive": {
                "bytes": len(archive),
                "sha256": MODULE.digest(archive),
            },
        }

    def test_checked_in_manifest_passes_structural_validation(self) -> None:
        manifest = MODULE.load_manifest(REPOSITORY_ROOT)
        self.assertEqual(manifest["organization"], "memebank")
        self.assertEqual(manifest["repository_order"], list(MODULE.EXPECTED_REPOSITORIES))
        self.assertEqual(len(manifest["repositories"]), 13)
        self.assertEqual(len(manifest["monorepo"]["gitlinks"]), 11)

    def test_manifest_rejects_identity_and_policy_drift(self) -> None:
        mutations = (
            ("organization", "not-memebank", "organization changed"),
            ("visibility", "public", "visibility must remain private"),
            ("default_branch", "master", "default branch must remain main"),
            ("source_root", "other-root", "root changed"),
        )
        for key, value, message in mutations:
            with self.subTest(key=key):
                raw = self.load_raw_manifest()
                raw[key] = value
                with self.assertRaisesRegex(MODULE.ValidationError, message):
                    MODULE.load_manifest(self.write_manifest(raw))

    def test_manifest_rejects_repository_order_drift(self) -> None:
        raw = self.load_raw_manifest()
        order = raw["repository_order"]
        assert isinstance(order, list)
        order[1], order[2] = order[2], order[1]
        with self.assertRaisesRegex(MODULE.ValidationError, "repository order changed"):
            MODULE.load_manifest(self.write_manifest(raw))

    def test_manifest_rejects_repository_record_drift(self) -> None:
        raw = self.load_raw_manifest()
        repositories = raw["repositories"]
        assert isinstance(repositories, list)
        record = repositories[1]
        assert isinstance(record, dict)
        record["name"] = "renamed-interface"
        with self.assertRaisesRegex(MODULE.ValidationError, "repository records changed"):
            MODULE.load_manifest(self.write_manifest(raw))

    def test_manifest_rejects_tracked_entry_aggregate_drift(self) -> None:
        raw = self.load_raw_manifest()
        raw["tracked_entries"] = int(raw["tracked_entries"]) + 1
        with self.assertRaisesRegex(MODULE.ValidationError, "aggregate is inconsistent"):
            MODULE.load_manifest(self.write_manifest(raw))

    def test_manifest_rejects_gitlink_target_drift(self) -> None:
        raw = self.load_raw_manifest()
        monorepo = raw["monorepo"]
        assert isinstance(monorepo, dict)
        links = monorepo["gitlinks"]
        assert isinstance(links, dict)
        links["apps/mb-cli"] = "0" * 40
        with self.assertRaisesRegex(MODULE.ValidationError, "does not pin its exact child"):
            MODULE.load_manifest(self.write_manifest(raw))

    def test_member_paths_reject_escape_and_git_metadata(self) -> None:
        unsafe_paths = (
            "",
            "/memebank-source-v2/mb-cli/file",
            "memebank-source-v2\\mb-cli\\file",
            "memebank-source-v2/../escape",
            "memebank-source-v2/mb-cli/.git/config",
            "memebank-source-v2//mb-cli/file",
            "other-root/mb-cli/file",
            "memebank-source-v2/mb-cli/file\x00suffix",
        )
        for path in unsafe_paths:
            with self.subTest(path=repr(path)):
                with self.assertRaises(MODULE.ValidationError):
                    MODULE.member_parts(path)

    def test_member_path_accepts_canonical_nested_file(self) -> None:
        self.assertEqual(
            MODULE.member_parts("memebank-source-v2/mb-cli/src/main.rs"),
            ("memebank-source-v2", "mb-cli", "src", "main.rs"),
        )

    def test_extract_rejects_links_and_special_files(self) -> None:
        archive = self.make_archive(
            [
                (
                    "memebank-source-v2/mb-cli/latest",
                    "symlink",
                    "README.md",
                    0o777,
                )
            ]
        )
        with tempfile.TemporaryDirectory(prefix="memebank-source-v2-extract-") as temporary:
            with self.assertRaisesRegex(MODULE.ValidationError, "special files are forbidden"):
                MODULE.extract_archive(archive, Path(temporary) / "out", {"source_files": 0})

    def test_extract_rejects_case_collisions(self) -> None:
        archive = self.make_archive(
            [
                ("memebank-source-v2/mb-cli/README.md", "file", b"one", 0o644),
                ("memebank-source-v2/mb-cli/readme.md", "file", b"two", 0o644),
            ]
        )
        with tempfile.TemporaryDirectory(prefix="memebank-source-v2-extract-") as temporary:
            with self.assertRaisesRegex(MODULE.ValidationError, "case-colliding"):
                MODULE.extract_archive(archive, Path(temporary) / "out", {"source_files": 2})

    def test_extract_rejects_duplicate_paths(self) -> None:
        archive = self.make_archive(
            [
                ("memebank-source-v2/mb-cli/README.md", "file", b"one", 0o644),
                ("memebank-source-v2/mb-cli/README.md", "file", b"two", 0o644),
            ]
        )
        with tempfile.TemporaryDirectory(prefix="memebank-source-v2-extract-") as temporary:
            with self.assertRaisesRegex(MODULE.ValidationError, "duplicate/case-colliding"):
                MODULE.extract_archive(archive, Path(temporary) / "out", {"source_files": 2})

    def test_extract_enforces_member_size_limit(self) -> None:
        archive = self.make_archive(
            [("memebank-source-v2/mb-cli/blob", "file", b"abc", 0o644)]
        )
        with tempfile.TemporaryDirectory(prefix="memebank-source-v2-extract-") as temporary:
            with mock.patch.object(MODULE, "MAX_MEMBER_BYTES", 2):
                with self.assertRaisesRegex(MODULE.ValidationError, "member size"):
                    MODULE.extract_archive(archive, Path(temporary) / "out", {"source_files": 1})

    def test_extract_enforces_total_size_limit(self) -> None:
        archive = self.make_archive(
            [
                ("memebank-source-v2/mb-cli/a", "file", b"ab", 0o644),
                ("memebank-source-v2/mb-cli/b", "file", b"cd", 0o644),
            ]
        )
        with tempfile.TemporaryDirectory(prefix="memebank-source-v2-extract-") as temporary:
            with (
                mock.patch.object(MODULE, "MAX_MEMBER_BYTES", 10),
                mock.patch.object(MODULE, "MAX_TOTAL_BYTES", 3),
                self.assertRaisesRegex(MODULE.ValidationError, "exceeds safety bound"),
            ):
                MODULE.extract_archive(archive, Path(temporary) / "out", {"source_files": 2})

    def test_extract_reconstructs_exact_roots_and_modes(self) -> None:
        entries: list[tuple[str, str, bytes | str, int]] = []
        for index, repository in enumerate(MODULE.EXPECTED_REPOSITORIES):
            mode = 0o755 if index == 0 else 0o644
            entries.append(
                (
                    f"{MODULE.SOURCE_ROOT}/{repository}/fixture-{index}.txt",
                    "file",
                    repository.encode("utf-8"),
                    mode,
                )
            )
        archive = self.make_archive(entries)
        with tempfile.TemporaryDirectory(prefix="memebank-source-v2-extract-") as temporary:
            source = MODULE.extract_archive(
                archive,
                Path(temporary) / "out",
                {"source_files": len(entries)},
            )
            self.assertEqual(
                {entry.name for entry in source.iterdir()},
                set(MODULE.EXPECTED_REPOSITORIES),
            )
            executable = source / MODULE.EXPECTED_REPOSITORIES[0] / "fixture-0.txt"
            ordinary = source / MODULE.EXPECTED_REPOSITORIES[1] / "fixture-1.txt"
            self.assertTrue(os.access(executable, os.X_OK))
            self.assertFalse(ordinary.stat().st_mode & 0o111)

    def test_decode_rejects_chunk_path_escape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="memebank-source-v2-payload-") as temporary:
            root = Path(temporary)
            manifest = {
                "payload": {
                    "encoding": "base64",
                    "base64_bytes": 0,
                    "chunks": [
                        {
                            "path": "../outside.txt",
                            "bytes": 0,
                            "sha256": MODULE.digest(b""),
                        }
                    ],
                },
                "archive": {"bytes": 0, "sha256": MODULE.digest(b"")},
            }
            with self.assertRaisesRegex(MODULE.ValidationError, "escapes repository root"):
                MODULE.decode_payload(root, manifest)

    def test_decode_rejects_missing_and_symlinked_chunks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="memebank-source-v2-payload-") as temporary:
            root = Path(temporary)
            relative = "repository-fleets/memebank-source-v2/test.part.txt"
            manifest = {
                "payload": {
                    "encoding": "base64",
                    "base64_bytes": 0,
                    "chunks": [
                        {
                            "path": relative,
                            "bytes": 0,
                            "sha256": MODULE.digest(b""),
                        }
                    ],
                },
                "archive": {"bytes": 0, "sha256": MODULE.digest(b"")},
            }
            with self.assertRaisesRegex(MODULE.ValidationError, "missing or unsafe"):
                MODULE.decode_payload(root, manifest)

            target = root / "target.txt"
            target.write_bytes(b"")
            chunk = root / relative
            chunk.parent.mkdir(parents=True, exist_ok=True)
            chunk.symlink_to(target)
            with self.assertRaisesRegex(MODULE.ValidationError, "missing or unsafe"):
                MODULE.decode_payload(root, manifest)

    def test_decode_rejects_chunk_integrity_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="memebank-source-v2-payload-") as temporary:
            root = Path(temporary)
            manifest = self.payload_manifest(root, b"YQ==", archive=b"a")
            chunks = manifest["payload"]["chunks"]
            assert isinstance(chunks, list)
            assert isinstance(chunks[0], dict)
            chunks[0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(MODULE.ValidationError, "chunk integrity failed"):
                MODULE.decode_payload(root, manifest)

    def test_decode_requires_strict_base64(self) -> None:
        with tempfile.TemporaryDirectory(prefix="memebank-source-v2-payload-") as temporary:
            root = Path(temporary)
            manifest = self.payload_manifest(root, b"not*base64")
            with self.assertRaisesRegex(MODULE.ValidationError, "not strict base64"):
                MODULE.decode_payload(root, manifest)

    def test_decode_rejects_archive_integrity_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="memebank-source-v2-payload-") as temporary:
            root = Path(temporary)
            manifest = self.payload_manifest(root, b"YWJj", archive=b"abc")
            archive = manifest["archive"]
            assert isinstance(archive, dict)
            archive["sha256"] = "0" * 64
            with self.assertRaisesRegex(MODULE.ValidationError, "archive integrity failed"):
                MODULE.decode_payload(root, manifest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
