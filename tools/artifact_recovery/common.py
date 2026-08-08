"""Public-safe validation primitives for artifact recovery."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

OBSERVATION_SCHEMA = "artifact_recovery_observation.v1"
LEDGER_SCHEMA = "artifact_recovery_ledger.v1"
CLI_QUEUE_SCHEMA = "artifact_recovery_cli_queue.v1"
SUMMARY_SCHEMA = "artifact_recovery_summary.v1"
DEFAULT_CLI_TASK_ID = "019fd526-f34d-7f72-94fa-2da6185f2d74"
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_ITEMS = 10_000
MAX_BATCH_SIZE = 250
MAX_STRING = 512
MAX_PATHS = 100

OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
BRANCH_RE = re.compile(r"^(?!/)(?!.*(?:\.\.|//|@\{|\\))[A-Za-z0-9._/-]{1,200}(?<![./])$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ORIGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")
HTTPS_GITHUB_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"(?:/(?P<kind>commit|tree|pull)/(?P<value>[A-Za-z0-9._/-]+))?/?$"
)

TOKEN_RE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b")
BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_ -]?key|password|private[_ -]?key|secret|token)\b\s*[:=]\s*\S+"
)
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")

FINDING_ORDER = (
    "ownership_ambiguous",
    "remote_evidence_incomplete",
    "repository_missing",
    "artifact_only",
    "repository_has_no_remote",
    "changes_uncommitted",
    "commits_unpushed",
    "branch_not_created",
    "branch_not_published",
    "branch_without_pull_request",
    "claimed_repository_unverified",
    "claimed_commit_unverified",
    "claimed_branch_unverified",
    "claimed_pull_request_unverified",
)
FINDING_RANK = {name: index for index, name in enumerate(FINDING_ORDER)}


class RecoveryError(ValueError):
    """Raised when recovery metadata violates the durable contract."""


def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    payload = path.read_bytes()
    if len(payload) > MAX_INPUT_BYTES:
        raise RecoveryError(f"{path} exceeds {MAX_INPUT_BYTES} bytes")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicate_pairs)
    except UnicodeDecodeError as exc:
        raise RecoveryError(f"{path} must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise RecoveryError(f"{path} is invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise RecoveryError(f"{path} root must be an object")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RecoveryError(f"{field} must be a non-empty ISO-8601 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RecoveryError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise RecoveryError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def now_utc(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return parse_timestamp(value, "--now")


def expect_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecoveryError(f"{field} must be an object")
    return value


def expect_list(value: Any, field: str, maximum: int = MAX_ITEMS) -> list[Any]:
    if not isinstance(value, list):
        raise RecoveryError(f"{field} must be an array")
    if len(value) > maximum:
        raise RecoveryError(f"{field} exceeds {maximum} items")
    return value


def expect_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise RecoveryError(f"{field} must be a boolean")
    return value


def expect_string(value: Any, field: str, maximum: int = MAX_STRING) -> str:
    if not isinstance(value, str) or not value:
        raise RecoveryError(f"{field} must be a non-empty string")
    if len(value) > maximum:
        raise RecoveryError(f"{field} exceeds {maximum} characters")
    if any(ord(char) < 0x20 for char in value):
        raise RecoveryError(f"{field} contains a control character")
    return value


def optional_string(value: Any, field: str, maximum: int = MAX_STRING) -> str | None:
    if value is None:
        return None
    return expect_string(value, field, maximum)


def walk(value: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, nested in value.items():
            yield from walk(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from walk(nested, f"{path}[{index}]")


def validate_public_safety(value: Mapping[str, Any]) -> None:
    for path, nested in walk(value):
        if not isinstance(nested, str):
            continue
        if TOKEN_RE.search(nested):
            raise RecoveryError(f"credential-shaped token at {path}")
        if BEARER_RE.search(nested):
            raise RecoveryError(f"bearer credential at {path}")
        if PRIVATE_KEY_RE.search(nested):
            raise RecoveryError(f"private key material at {path}")
        if SECRET_ASSIGNMENT_RE.search(nested):
            raise RecoveryError(f"secret assignment at {path}")
        if EMAIL_RE.search(nested):
            raise RecoveryError(f"email-shaped personal data at {path}")
        if len(nested) > 8_192:
            raise RecoveryError(f"unbounded string at {path}")


def normalize_repo(owner: str, repository: str) -> str:
    if not OWNER_RE.fullmatch(owner):
        raise RecoveryError(f"invalid GitHub owner: {owner}")
    if not REPO_RE.fullmatch(repository):
        raise RecoveryError(f"invalid GitHub repository: {repository}")
    return f"{owner.lower()}/{repository.lower()}"


def normalize_branch(value: Any, field: str) -> str | None:
    branch = optional_string(value, field, 200)
    if branch is not None and not BRANCH_RE.fullmatch(branch):
        raise RecoveryError(f"{field} has an invalid Git ref shape")
    return branch


def normalize_sha(value: Any, field: str) -> str | None:
    sha = optional_string(value, field, 40)
    if sha is not None and not SHA40_RE.fullmatch(sha):
        raise RecoveryError(f"{field} must be a lowercase full Git SHA")
    return sha


def normalize_visibility(value: Any, field: str) -> str | None:
    if value is None:
        return None
    visibility = expect_string(value, field, 16)
    if visibility not in {"public", "private", "internal"}:
        raise RecoveryError(f"{field} must be public, private, internal, or null")
    return visibility


def validate_github_url(value: str, field: str, repo_identity: str) -> tuple[str, str | None, str | None]:
    match = HTTPS_GITHUB_RE.fullmatch(value)
    if not match:
        raise RecoveryError(f"{field} is not a canonical GitHub URL")
    identity = normalize_repo(match.group("owner"), match.group("repo"))
    if identity != repo_identity:
        raise RecoveryError(f"{field} points outside {repo_identity}")
    return identity, match.group("kind"), match.group("value")


def normalize_artifact(value: Any, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    artifact = expect_object(value, field)
    allowed = {"kind", "name", "sha256", "locator", "commit_sha", "paths"}
    extra = set(artifact) - allowed
    if extra:
        raise RecoveryError(f"{field} has unsupported keys: {sorted(extra)}")
    kind = expect_string(artifact.get("kind"), f"{field}.kind", 32)
    if kind not in {"archive", "file", "git_bundle", "draft", "directory"}:
        raise RecoveryError(f"{field}.kind is unsupported")
    name = expect_string(artifact.get("name"), f"{field}.name", 200)
    digest = optional_string(artifact.get("sha256"), f"{field}.sha256", 64)
    if digest is not None and not SHA256_RE.fullmatch(digest):
        raise RecoveryError(f"{field}.sha256 must be lowercase SHA-256")
    locator = optional_string(artifact.get("locator"), f"{field}.locator", 300)
    commit_sha = normalize_sha(artifact.get("commit_sha"), f"{field}.commit_sha")
    paths_raw = artifact.get("paths", [])
    paths = expect_list(paths_raw, f"{field}.paths", MAX_PATHS)
    normalized_paths: list[str] = []
    for index, path in enumerate(paths):
        item = expect_string(path, f"{field}.paths[{index}]", 300)
        if item.startswith("/") or ".." in Path(item).parts:
            raise RecoveryError(f"{field}.paths[{index}] must be a safe relative path")
        normalized_paths.append(item)
    return {
        "kind": kind,
        "name": name,
        "sha256": digest,
        "locator": locator,
        "commit_sha": commit_sha,
        "paths": normalized_paths,
    }


def ledger_key(item: Mapping[str, Any]) -> str:
    origin = item["origin"]
    identity = item["target"]["identity"]
    return f"{origin['source']}:{origin['id']}::{identity}"
