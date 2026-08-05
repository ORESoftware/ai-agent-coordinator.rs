#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "daily_portfolio_briefing_input.json"
sys.path.insert(0, str(TOOLS_DIR))

import collect_daily_portfolio_results as collector  # noqa: E402
import compose_daily_portfolio_briefing as composer  # noqa: E402


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class DailyPortfolioResultCollectorTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "results"
        self.root.mkdir()
        self.manifest_path = self.base / "manifest.json"
        self.source_input = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.manifest = {
            "schema_version": collector.MANIFEST_SCHEMA,
            "generated_at": self.source_input["generated_at"],
            "max_clock_skew_seconds": 300,
            "lanes": [],
        }
        for lane_name in composer.LANE_SPECS:
            self._write_lane(lane_name, self.source_input["lanes"][lane_name])
        self._write_manifest()

    def _entry(self, lane_name: str) -> dict[str, object]:
        return next(
            entry
            for entry in self.manifest["lanes"]
            if entry["lane"] == lane_name
        )

    def _lane_path(self, lane_name: str) -> Path:
        return self.root / f"{lane_name}.json"

    def _write_lane(self, lane_name: str, value: object) -> None:
        payload = _canonical_bytes(value)
        path = self._lane_path(lane_name)
        path.write_bytes(payload)
        existing = [
            entry
            for entry in self.manifest["lanes"]
            if entry["lane"] != lane_name
        ]
        existing.append(
            {
                "lane": lane_name,
                "result_path": path.name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "run_id": f"fixture:{lane_name}",
                "max_age_seconds": 3600,
                "missing_policy": "unavailable",
            }
        )
        self.manifest["lanes"] = existing

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(self.manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _collect(self) -> tuple[dict[str, object], dict[str, object]]:
        self._write_manifest()
        return collector.collect(self.manifest_path, self.root)

    def test_collects_exact_eight_lane_input_deterministically(self) -> None:
        first_input, first_provenance = self._collect()
        self.manifest["lanes"].reverse()
        second_input, second_provenance = self._collect()

        expected = collector._to_input_envelope(
            composer.normalize_input(self.source_input)
        )
        self.assertEqual(expected, first_input)
        self.assertEqual(first_input, second_input)
        self.assertEqual(first_provenance, second_provenance)
        self.assertEqual(
            {"lanes": 8, "collected": 8, "stale": 0, "missing": 0},
            first_provenance["counts"],
        )
        self.assertEqual(
            hashlib.sha256(_canonical_bytes(first_input)).hexdigest(),
            first_provenance["input_digest"],
        )
        serialized = json.dumps(first_provenance, sort_keys=True)
        self.assertNotIn("result_path", serialized)
        self.assertNotIn("Platform engineering role", serialized)
        self.assertNotIn(str(self.root), serialized)

    def test_missing_lane_can_be_explicitly_unavailable(self) -> None:
        self._lane_path("career").unlink()
        briefing_input, provenance = self._collect()

        lane = briefing_input["lanes"]["career"]
        self.assertEqual("unavailable", lane["status"])
        self.assertEqual([], lane["items"])
        self.assertEqual("DEN-826", lane["source_issue"])
        self.assertEqual(1, provenance["counts"]["missing"])
        record = next(item for item in provenance["lanes"] if item["lane"] == "career")
        self.assertEqual("missing", record["state"])
        self.assertIsNone(record["observed_sha256"])

    def test_missing_required_lane_fails_closed(self) -> None:
        self._lane_path("career").unlink()
        self._entry("career")["missing_policy"] = "fail"
        with self.assertRaisesRegex(collector.CollectorError, "required lane result is missing"):
            self._collect()

    def test_stale_lane_becomes_unavailable_with_provenance(self) -> None:
        lane = copy.deepcopy(self.source_input["lanes"]["engineering_research"])
        lane["generated_at"] = "2026-07-31T10:00:00Z"
        self._write_lane("engineering_research", lane)
        self._entry("engineering_research")["max_age_seconds"] = 1800

        briefing_input, provenance = self._collect()
        collected = briefing_input["lanes"]["engineering_research"]
        self.assertEqual("unavailable", collected["status"])
        self.assertEqual([], collected["items"])
        self.assertIn("freshness policy", collected["error_summary"])
        self.assertEqual(1, provenance["counts"]["stale"])
        record = next(
            item
            for item in provenance["lanes"]
            if item["lane"] == "engineering_research"
        )
        self.assertEqual("stale", record["state"])
        self.assertGreater(record["age_seconds"], record["max_age_seconds"])

    def test_digest_and_byte_count_mismatches_fail_closed(self) -> None:
        path = self._lane_path("career")
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(collector.CollectorError, "byte count mismatch"):
            self._collect()

        self._write_lane("career", self.source_input["lanes"]["career"])
        self._entry("career")["sha256"] = "0" * 64
        with self.assertRaisesRegex(collector.CollectorError, "digest mismatch"):
            self._collect()

    def test_existing_result_without_integrity_metadata_fails(self) -> None:
        entry = self._entry("career")
        entry["sha256"] = None
        entry["bytes"] = None
        entry["missing_policy"] = "unavailable"
        with self.assertRaisesRegex(
            collector.CollectorError,
            "exists without manifest integrity metadata",
        ):
            self._collect()

    def test_symlink_result_and_symlink_root_are_rejected(self) -> None:
        target = self.base / "outside.json"
        target.write_bytes(self._lane_path("career").read_bytes())
        self._lane_path("career").unlink()
        try:
            self._lane_path("career").symlink_to(target)
        except (OSError, NotImplementedError) as exc:  # pragma: no cover - non-POSIX
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaisesRegex(collector.CollectorError, "symbolic link"):
            self._collect()

        link_root = self.base / "result-link"
        link_root.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(collector.CollectorError, "result root must not"):
            collector.collect(self.manifest_path, link_root)

    def test_manifest_paths_are_strict_relative_posix_paths(self) -> None:
        invalid = (
            "../outside.json",
            "/absolute.json",
            "nested//file.json",
            "nested/./file.json",
            "nested/../file.json",
            "C:/result.json",
            "nested\\file.json",
            "nested/\nfile.json",
            "nested/" + "gh" + "p_" + ("A" * 30) + ".json",
        )
        original = self._entry("career")["result_path"]
        for value in invalid:
            with self.subTest(value=value):
                self._entry("career")["result_path"] = value
                with self.assertRaises(collector.CollectorError):
                    self._collect()
                self._entry("career")["result_path"] = original

    def test_unknown_duplicate_and_incomplete_manifest_fields_fail(self) -> None:
        self.manifest["unexpected"] = True
        with self.assertRaisesRegex(collector.CollectorError, "fields mismatch"):
            self._collect()
        del self.manifest["unexpected"]

        duplicate = copy.deepcopy(self.manifest["lanes"][0])
        self.manifest["lanes"][-1] = duplicate
        with self.assertRaisesRegex(collector.CollectorError, "duplicate lane entry"):
            self._collect()

        raw = (
            '{"schema_version":"portfolio_briefing_result_manifest.v1",'
            '"schema_version":"portfolio_briefing_result_manifest.v1"}'
        )
        self.manifest_path.write_text(raw, encoding="utf-8")
        with self.assertRaisesRegex(collector.CollectorError, "duplicate JSON key"):
            collector.collect(self.manifest_path, self.root)

    def test_manifest_bounds_and_future_timestamp_are_enforced(self) -> None:
        self._entry("career")["bytes"] = collector.MAX_LANE_BYTES + 1
        with self.assertRaisesRegex(collector.CollectorError, "must be between"):
            self._collect()

        self._write_lane("career", self.source_input["lanes"]["career"])
        lane = copy.deepcopy(self.source_input["lanes"]["career"])
        lane["generated_at"] = "2026-07-31T12:40:01Z"
        self._write_lane("career", lane)
        with self.assertRaisesRegex(collector.CollectorError, "implausibly in the future"):
            self._collect()

    def test_source_issue_contract_is_enforced(self) -> None:
        lane = copy.deepcopy(self.source_input["lanes"]["career"])
        lane["source_issue"] = "DEN-999"
        self._write_lane("career", lane)
        with self.assertRaisesRegex(collector.CollectorError, "source_issue must be DEN-826"):
            self._collect()

    def test_sensitive_values_are_redacted_before_output(self) -> None:
        lane = copy.deepcopy(self.source_input["lanes"]["career"])
        lane["items"][0]["title"] = "password=sample-sensitive-value"
        self._write_lane("career", lane)

        briefing_input, provenance = self._collect()
        serialized_input = json.dumps(briefing_input, sort_keys=True)
        serialized_provenance = json.dumps(provenance, sort_keys=True)
        self.assertNotIn("sample-sensitive-value", serialized_input)
        self.assertNotIn("sample-sensitive-value", serialized_provenance)
        self.assertIn("password=[REDACTED]", serialized_input)

    def test_run_id_rejects_credential_shaped_values(self) -> None:
        self._entry("career")["run_id"] = "gh" + "p_" + ("A" * 30)
        with self.assertRaisesRegex(
            collector.CollectorError,
            "credential-shaped material",
        ):
            self._collect()

    def test_changed_during_read_is_detected(self) -> None:
        path = self._lane_path("career")
        metadata = path.stat()
        before = types.SimpleNamespace(
            st_mode=metadata.st_mode,
            st_dev=metadata.st_dev,
            st_ino=metadata.st_ino,
            st_size=metadata.st_size,
            st_mtime_ns=metadata.st_mtime_ns,
            st_ctime_ns=metadata.st_ctime_ns,
        )
        after = types.SimpleNamespace(**vars(before))
        after.st_mtime_ns += 1
        with mock.patch.object(collector.os, "fstat", side_effect=[before, after]):
            with self.assertRaisesRegex(collector.CollectorError, "changed while"):
                collector._read_regular_file(
                    path,
                    maximum=collector.MAX_LANE_BYTES,
                    label="race fixture",
                )

    def test_cli_writes_machine_readable_input_and_provenance(self) -> None:
        output_input = self.base / "output" / "input.json"
        output_provenance = self.base / "output" / "provenance.json"
        self._write_manifest()
        result = collector.main(
            [
                "--manifest",
                str(self.manifest_path),
                "--result-root",
                str(self.root),
                "--output-input",
                str(output_input),
                "--output-provenance",
                str(output_provenance),
            ]
        )
        self.assertEqual(0, result)
        briefing_input = json.loads(output_input.read_text(encoding="utf-8"))
        provenance = json.loads(output_provenance.read_text(encoding="utf-8"))
        composer.normalize_input(briefing_input)
        self.assertEqual(collector.PROVENANCE_SCHEMA, provenance["schema_version"])
        self.assertEqual(8, provenance["counts"]["collected"])


def emit_example(output_directory: Path) -> None:
    with tempfile.TemporaryDirectory() as directory:
        case = DailyPortfolioResultCollectorTests(methodName="test_collects_exact_eight_lane_input_deterministically")
        case.temporary = tempfile.TemporaryDirectory(dir=directory)
        try:
            case.base = Path(case.temporary.name)
            case.root = case.base / "results"
            case.root.mkdir()
            case.manifest_path = case.base / "manifest.json"
            case.source_input = json.loads(FIXTURE.read_text(encoding="utf-8"))
            case.manifest = {
                "schema_version": collector.MANIFEST_SCHEMA,
                "generated_at": case.source_input["generated_at"],
                "max_clock_skew_seconds": 300,
                "lanes": [],
            }
            for lane_name in composer.LANE_SPECS:
                case._write_lane(lane_name, case.source_input["lanes"][lane_name])
            case._write_manifest()
            briefing_input, provenance = case._collect()
            output_directory.mkdir(parents=True, exist_ok=True)
            collector._write_json(output_directory / "input.json", briefing_input)
            collector._write_json(output_directory / "provenance.json", provenance)
            redacted_manifest = copy.deepcopy(case.manifest)
            for lane in redacted_manifest["lanes"]:
                lane["result_path"] = f"results/{lane['lane']}.json"
            collector._write_json(output_directory / "manifest.example.json", redacted_manifest)
        finally:
            case.temporary.cleanup()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--emit-example":
        emit_example(Path(sys.argv[2]))
        raise SystemExit(0)
    unittest.main()
