#!/usr/bin/env python3
"""Collect durable normalized lane results for the daily portfolio briefing.

The collector is deliberately network-free. It accepts one strict manifest and a
single approved local result root, verifies each lane result by size and SHA-256,
normalizes/redacts it through the existing briefing composer contract, and emits
one deterministic input envelope plus a bounded provenance report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import compose_daily_portfolio_briefing as composer

MANIFEST_SCHEMA = "portfolio_briefing_result_manifest.v1"
PROVENANCE_SCHEMA = "portfolio_briefing_provenance.v1"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_LANE_BYTES = 1024 * 1024
MAX_RESULT_PATH = 240
MAX_RUN_ID = 160
MAX_CLOCK_SKEW_SECONDS = 300
MAX_FRESHNESS_SECONDS = 7 * 24 * 60 * 60
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,159}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

TOP_LEVEL_KEYS = {
    "schema_version",
    "generated_at",
    "max_clock_skew_seconds",
    "lanes",
}
LANE_ENTRY_KEYS = {
    "lane",
    "result_path",
    "sha256",
    "bytes",
    "run_id",
    "max_age_seconds",
    "missing_policy",
}
MISSING_POLICIES = {"fail", "unavailable"}


class CollectorError(RuntimeError):
    """A bounded, user-facing manifest, integrity, or freshness failure."""


@dataclass(frozen=True)
class LaneEntry:
    lane: str
    result_path: str
    expected_sha256: str | None
    expected_bytes: int | None
    run_id: str
    max_age_seconds: int
    missing_policy: str


@dataclass(frozen=True)
class Manifest:
    generated_at: str
    generated_instant: datetime
    max_clock_skew_seconds: int
    lanes: tuple[LaneEntry, ...]
    digest: str


def _duplicate_key_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, nested in pairs:
        if key in value:
            raise CollectorError(f"duplicate JSON key: {key}")
        value[key] = nested
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _parse_instant(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise CollectorError(f"{label} must be an ISO-8601 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CollectorError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise CollectorError(f"{label} must include a UTC offset or Z")
    return parsed.astimezone(timezone.utc)


def _format_instant(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CollectorError(f"{label} must be a JSON object")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise CollectorError(f"{label} fields mismatch: missing={missing}, unknown={unknown}")


def _require_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CollectorError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise CollectorError(f"{label} must be between {minimum} and {maximum}")
    return value


def _read_regular_file(path: Path, *, maximum: int, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CollectorError(f"cannot inspect {label}: {path}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise CollectorError(f"{label} must not be a symbolic link")
    if not stat.S_ISREG(metadata.st_mode):
        raise CollectorError(f"{label} must be a regular file")
    if metadata.st_size > maximum:
        raise CollectorError(f"{label} exceeds {maximum} bytes")

    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CollectorError(f"cannot open {label}: {path}: {exc}") from exc

    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise CollectorError(f"{label} changed away from a regular file")
        if before.st_size > maximum:
            raise CollectorError(f"{label} exceeds {maximum} bytes")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    if len(payload) > maximum:
        raise CollectorError(f"{label} exceeds {maximum} bytes")
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after or len(payload) != after.st_size:
        raise CollectorError(f"{label} changed while it was being read")
    return payload


def _parse_json_bytes(payload: bytes, label: str) -> Any:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CollectorError(f"{label} must be UTF-8") from exc
    try:
        return json.loads(text, object_pairs_hook=_duplicate_key_guard)
    except json.JSONDecodeError as exc:
        raise CollectorError(f"{label} is not valid JSON: {exc}") from exc


def _validate_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CollectorError(f"{label} must be a non-empty relative POSIX path")
    if len(value) > MAX_RESULT_PATH:
        raise CollectorError(f"{label} exceeds {MAX_RESULT_PATH} characters")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise CollectorError(f"{label} contains control characters")
    if "\\" in value or "\x00" in value or ":" in value:
        raise CollectorError(f"{label} contains unsupported path characters")
    if composer._redact(value) != value:
        raise CollectorError(f"{label} contains credential-shaped material")
    if value.startswith("/") or value.endswith("/") or "//" in value:
        raise CollectorError(f"{label} must be a normalized relative path")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise CollectorError(f"{label} must not contain empty, dot, or parent segments")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value:
        raise CollectorError(f"{label} must be a normalized relative POSIX path")
    return value


def _validate_root(root: Path) -> Path:
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise CollectorError(f"cannot inspect result root: {root}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise CollectorError("result root must not be a symbolic link")
    if not stat.S_ISDIR(metadata.st_mode):
        raise CollectorError("result root must be a directory")
    try:
        return root.resolve(strict=True)
    except OSError as exc:
        raise CollectorError(f"cannot resolve result root: {root}: {exc}") from exc


def _resolve_result(root: Path, relative: str) -> tuple[Path, bool]:
    candidate = root
    missing = False
    for segment in relative.split("/"):
        candidate = candidate / segment
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            missing = True
            break
        except OSError as exc:
            raise CollectorError(f"cannot inspect result path for {relative}: {exc}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise CollectorError(f"result path for {relative} contains a symbolic link")
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise CollectorError(f"cannot resolve result path for {relative}: {exc}") from exc
    if resolved != root and root not in resolved.parents:
        raise CollectorError(f"result path for {relative} escapes the approved root")
    return candidate, missing


def _load_manifest(path: Path) -> Manifest:
    payload = _read_regular_file(path, maximum=MAX_MANIFEST_BYTES, label="result manifest")
    raw = _require_object(_parse_json_bytes(payload, "result manifest"), "result manifest")
    _require_exact_keys(raw, TOP_LEVEL_KEYS, "result manifest")
    if raw.get("schema_version") != MANIFEST_SCHEMA:
        raise CollectorError(f"result manifest schema_version must be {MANIFEST_SCHEMA}")
    generated = _parse_instant(raw.get("generated_at"), "result manifest.generated_at")
    skew = _require_int(
        raw.get("max_clock_skew_seconds"),
        "result manifest.max_clock_skew_seconds",
        0,
        MAX_CLOCK_SKEW_SECONDS,
    )
    lane_values = raw.get("lanes")
    if not isinstance(lane_values, list):
        raise CollectorError("result manifest.lanes must be a list")
    if len(lane_values) != len(composer.LANE_SPECS):
        raise CollectorError("result manifest must contain exactly eight lane entries")

    entries: list[LaneEntry] = []
    seen_lanes: set[str] = set()
    seen_paths: set[str] = set()
    for index, nested in enumerate(lane_values):
        label = f"result manifest.lanes[{index}]"
        item = _require_object(nested, label)
        _require_exact_keys(item, LANE_ENTRY_KEYS, label)
        lane = item.get("lane")
        if lane not in composer.LANE_SPECS:
            raise CollectorError(f"{label}.lane is not a canonical briefing lane")
        if lane in seen_lanes:
            raise CollectorError(f"duplicate lane entry: {lane}")
        seen_lanes.add(lane)
        result_path = _validate_relative_path(item.get("result_path"), f"{label}.result_path")
        if result_path in seen_paths:
            raise CollectorError(f"duplicate result path: {result_path}")
        seen_paths.add(result_path)
        run_id = item.get("run_id")
        if not isinstance(run_id, str) or len(run_id) > MAX_RUN_ID or not RUN_ID_RE.fullmatch(run_id):
            raise CollectorError(f"{label}.run_id is invalid")
        if composer._redact(run_id) != run_id:
            raise CollectorError(f"{label}.run_id contains credential-shaped material")
        max_age = _require_int(
            item.get("max_age_seconds"),
            f"{label}.max_age_seconds",
            60,
            MAX_FRESHNESS_SECONDS,
        )
        missing_policy = item.get("missing_policy")
        if missing_policy not in MISSING_POLICIES:
            raise CollectorError(f"{label}.missing_policy must be fail or unavailable")
        expected_digest = item.get("sha256")
        expected_bytes = item.get("bytes")
        if (expected_digest is None) != (expected_bytes is None):
            raise CollectorError(f"{label}.sha256 and bytes must both be set or both be null")
        if expected_digest is None:
            if missing_policy != "unavailable":
                raise CollectorError(
                    f"{label} may omit integrity metadata only with unavailable policy"
                )
        else:
            if not isinstance(expected_digest, str) or not SHA256_RE.fullmatch(expected_digest):
                raise CollectorError(f"{label}.sha256 must be lowercase SHA-256")
            expected_bytes = _require_int(
                expected_bytes,
                f"{label}.bytes",
                1,
                MAX_LANE_BYTES,
            )
        entries.append(
            LaneEntry(
                lane=lane,
                result_path=result_path,
                expected_sha256=expected_digest,
                expected_bytes=expected_bytes,
                run_id=run_id,
                max_age_seconds=max_age,
                missing_policy=missing_policy,
            )
        )
    if seen_lanes != set(composer.LANE_SPECS):
        missing = sorted(set(composer.LANE_SPECS) - seen_lanes)
        raise CollectorError(f"result manifest is missing lanes: {', '.join(missing)}")
    normalized_manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "generated_at": _format_instant(generated),
        "max_clock_skew_seconds": skew,
        "lanes": [
            {
                "lane": entry.lane,
                "result_path": entry.result_path,
                "sha256": entry.expected_sha256,
                "bytes": entry.expected_bytes,
                "run_id": entry.run_id,
                "max_age_seconds": entry.max_age_seconds,
                "missing_policy": entry.missing_policy,
            }
            for entry in sorted(entries, key=lambda value: value.lane)
        ],
    }
    return Manifest(
        generated_at=_format_instant(generated),
        generated_instant=generated,
        max_clock_skew_seconds=skew,
        lanes=tuple(sorted(entries, key=lambda value: value.lane)),
        digest=_sha256_json(normalized_manifest),
    )


def _unavailable_lane(lane: str, generated_at: str, reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "source_issue": composer.LANE_SPECS[lane]["source_issue"],
        "generated_at": generated_at,
        "error_summary": reason,
        "items": [],
    }


def _to_input_envelope(normalized: Mapping[str, Any]) -> dict[str, Any]:
    lanes: dict[str, Any] = {}
    for lane_name in composer.LANE_SPECS:
        lane = normalized["lanes"][lane_name]
        items: list[dict[str, Any]] = []
        for item in lane["items"]:
            items.append(
                {
                    "identity": item["identity"],
                    "title": item["title"],
                    "what_changed": item["what_changed"],
                    "why_it_matters": item["why_it_matters"],
                    "confidence": item["confidence"],
                    "source_status": item["source_status"],
                    "relevant_date": item["relevant_date"],
                    "next_action": item["next_action"],
                    "sources": item["sources"],
                    "disposition": item["disposition"],
                    "rank": item["rank"],
                    "material": item["material"],
                }
            )
        lanes[lane_name] = {
            "status": lane["status"],
            "source_issue": lane["source_issue"],
            "generated_at": lane["generated_at"],
            "error_summary": lane["error_summary"],
            "items": items,
        }
    return {
        "schema_version": composer.INPUT_SCHEMA,
        "generated_at": normalized["generated_at"],
        "lanes": lanes,
    }


def collect(manifest_path: Path, result_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _load_manifest(manifest_path)
    root = _validate_root(result_root)
    raw_lanes: dict[str, Any] = {}
    provenance_by_lane: dict[str, dict[str, Any]] = {}

    for entry in manifest.lanes:
        candidate, missing = _resolve_result(root, entry.result_path)
        if missing or not candidate.exists():
            if entry.missing_policy == "fail":
                raise CollectorError(f"required lane result is missing: {entry.lane}")
            raw_lanes[entry.lane] = _unavailable_lane(
                entry.lane,
                manifest.generated_at,
                "Normalized lane result is unavailable.",
            )
            provenance_by_lane[entry.lane] = {
                "lane": entry.lane,
                "run_id": entry.run_id,
                "state": "missing",
                "source_issue": composer.LANE_SPECS[entry.lane]["source_issue"],
                "expected_sha256": entry.expected_sha256,
                "observed_sha256": None,
                "bytes": None,
                "generated_at": None,
                "age_seconds": None,
                "max_age_seconds": entry.max_age_seconds,
            }
            continue
        if entry.expected_sha256 is None or entry.expected_bytes is None:
            raise CollectorError(
                f"lane result exists without manifest integrity metadata: {entry.lane}"
            )
        payload = _read_regular_file(
            candidate,
            maximum=MAX_LANE_BYTES,
            label=f"lane result {entry.lane}",
        )
        if len(payload) != entry.expected_bytes:
            raise CollectorError(
                f"lane result byte count mismatch for {entry.lane}: "
                f"expected {entry.expected_bytes}, observed {len(payload)}"
            )
        observed_digest = _sha256_bytes(payload)
        if observed_digest != entry.expected_sha256:
            raise CollectorError(f"lane result digest mismatch for {entry.lane}")
        lane_value = _require_object(
            _parse_json_bytes(payload, f"lane result {entry.lane}"),
            f"lane result {entry.lane}",
        )
        raw_lanes[entry.lane] = lane_value
        provenance_by_lane[entry.lane] = {
            "lane": entry.lane,
            "run_id": entry.run_id,
            "state": "collected",
            "source_issue": composer.LANE_SPECS[entry.lane]["source_issue"],
            "expected_sha256": entry.expected_sha256,
            "observed_sha256": observed_digest,
            "bytes": len(payload),
            "generated_at": lane_value.get("generated_at"),
            "age_seconds": None,
            "max_age_seconds": entry.max_age_seconds,
        }

    raw_envelope = {
        "schema_version": composer.INPUT_SCHEMA,
        "generated_at": manifest.generated_at,
        "lanes": {lane: raw_lanes[lane] for lane in composer.LANE_SPECS},
    }
    try:
        sanitized = _to_input_envelope(composer.normalize_input(raw_envelope))
    except composer.BriefingError as exc:
        raise CollectorError(f"lane result failed briefing validation: {exc}") from exc

    entry_by_lane = {entry.lane: entry for entry in manifest.lanes}
    for lane_name in composer.LANE_SPECS:
        lane = sanitized["lanes"][lane_name]
        expected_issue = composer.LANE_SPECS[lane_name]["source_issue"]
        if lane["source_issue"] != expected_issue:
            raise CollectorError(
                f"lane {lane_name} source_issue must be {expected_issue!r}"
            )
        provenance = provenance_by_lane[lane_name]
        if provenance["state"] == "missing":
            continue
        lane_instant = _parse_instant(lane["generated_at"], f"lane {lane_name}.generated_at")
        latest_allowed = manifest.generated_instant.timestamp() + manifest.max_clock_skew_seconds
        if lane_instant.timestamp() > latest_allowed:
            raise CollectorError(f"lane result is implausibly in the future: {lane_name}")
        age_seconds = max(0, int((manifest.generated_instant - lane_instant).total_seconds()))
        provenance["generated_at"] = _format_instant(lane_instant)
        provenance["age_seconds"] = age_seconds
        if age_seconds > entry_by_lane[lane_name].max_age_seconds:
            sanitized["lanes"][lane_name] = _unavailable_lane(
                lane_name,
                _format_instant(lane_instant),
                "Normalized lane result exceeded the freshness policy.",
            )
            provenance["state"] = "stale"

    try:
        final_input = _to_input_envelope(composer.normalize_input(sanitized))
    except composer.BriefingError as exc:
        raise CollectorError(f"assembled briefing input failed validation: {exc}") from exc

    provenance = {
        "schema_version": PROVENANCE_SCHEMA,
        "generated_at": manifest.generated_at,
        "manifest_digest": manifest.digest,
        "input_digest": _sha256_json(final_input),
        "counts": {
            "lanes": len(provenance_by_lane),
            "collected": sum(
                value["state"] == "collected" for value in provenance_by_lane.values()
            ),
            "stale": sum(value["state"] == "stale" for value in provenance_by_lane.values()),
            "missing": sum(
                value["state"] == "missing" for value in provenance_by_lane.values()
            ),
        },
        "lanes": [provenance_by_lane[lane] for lane in composer.LANE_SPECS],
    }
    return final_input, provenance


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument("--output-input", required=True, type=Path)
    parser.add_argument("--output-provenance", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        briefing_input, provenance = collect(args.manifest, args.result_root)
        _write_json(args.output_input, briefing_input)
        _write_json(args.output_provenance, provenance)
    except (CollectorError, OSError) as exc:
        print(f"daily portfolio result collection failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
