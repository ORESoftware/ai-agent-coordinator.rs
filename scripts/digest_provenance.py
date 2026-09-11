#!/usr/bin/env python3
"""Strict provenance contracts for opportunity and research digests."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

SCHEMA = "evidence-digest/v1"
MAX_BYTES = 768 * 1024
MAX_TEXT = 2_000
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SLUG = re.compile(r"^[a-z][a-z0-9-]{2,95}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
ROOT_KEYS = {
    "schema_version",
    "digest_id",
    "kind",
    "generated_at",
    "window",
    "policy_sha256",
    "items",
    "safety",
}
WINDOW_KEYS = {"start", "end", "timezone"}
SAFETY_KEYS = {
    "contains_credentials",
    "contains_personal_data",
    "applications_authorized",
    "outreach_authorized",
    "external_mutations_authorized",
    "notes",
}
OPPORTUNITY_KEYS = {
    "id",
    "rank",
    "company_id",
    "company_name",
    "requisition_id",
    "title",
    "canonical_url",
    "source_kind",
    "verified_at",
    "posted_at",
    "expires_at",
    "locations",
    "remote_policy",
    "employment_type",
    "role_family",
    "compensation",
    "fit",
    "fiducia_opportunity_ids",
    "content_sha256",
}
COMPENSATION_KEYS = {
    "currency",
    "minimum",
    "maximum",
    "period",
    "evidence_url",
}
FIT_KEYS = {
    "score",
    "must_have_matches",
    "preferred_matches",
    "must_have_gaps",
    "preferred_gaps",
    "unknowns",
    "evidence_sha256",
}
RESEARCH_KEYS = {
    "id",
    "rank",
    "title",
    "authors",
    "source_kind",
    "canonical_url",
    "stable_identifier",
    "publication_date",
    "updated_at",
    "retrieved_at",
    "source_sha256",
    "status",
    "supersedes",
    "themes",
    "claims",
    "interpretation",
    "uncertainty",
    "proposed_experiments",
    "scores",
    "summary_sha256",
}
CLAIM_KEYS = {"statement", "source_fragment_sha256"}
SCORE_KEYS = {
    "relevance",
    "novelty",
    "evidence_quality",
    "implementation_cost",
    "falsifiability",
}
OPPORTUNITY_SOURCES = {"employer_careers", "official_ats", "employer_api"}
REMOTE_POLICIES = {"remote", "hybrid", "onsite", "location_dependent", "unknown"}
EMPLOYMENT_TYPES = {"full_time", "part_time", "contract", "temporary", "internship"}
ROLE_FAMILIES = {
    "platform",
    "cloud",
    "sre",
    "devops",
    "sdk_tooling",
    "observability",
    "distributed_systems",
    "full_stack_infrastructure",
    "developer_relations",
    "sales_engineering",
    "engineering_leadership",
}
COMPENSATION_PERIODS = {"hour", "month", "year"}
RESEARCH_SOURCES = {
    "peer_reviewed_paper",
    "official_preprint",
    "standard",
    "specification",
    "official_documentation",
    "official_release_notes",
    "repository_release",
}
RESEARCH_THEMES = {
    "rust",
    "distributed-systems",
    "formal-methods",
    "databases",
    "observability",
    "rpc-schema-tooling",
    "ai-agent-safety",
    "developer-infrastructure",
}


class DigestError(ValueError):
    """Raised when a digest cannot be parsed safely."""


@dataclass(frozen=True)
class DigestReport:
    valid: bool
    digest_kind: str | None
    digest_sha256: str | None
    item_count: int
    company_count: int
    fiducia_company_count: int
    errors: tuple[str, ...]


def _pairs(items: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise DigestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load(path: str | Path) -> dict[str, Any]:
    payload = Path(path).read_bytes()
    if len(payload) > MAX_BYTES:
        raise DigestError(f"digest exceeds {MAX_BYTES} bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DigestError("digest must be UTF-8") from exc
    try:
        value = json.loads(text, object_pairs_hook=_pairs)
    except json.JSONDecodeError as exc:
        raise DigestError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise DigestError("digest root must be an object")
    return value


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _object(
    value: Any, expected: set[str], path: str, errors: list[str]
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return None
    actual = set(value)
    if actual != expected:
        errors.append(
            f"{path} keys mismatch: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    return value


def _text(
    value: Any,
    path: str,
    errors: list[str],
    minimum: int = 1,
    maximum: int = MAX_TEXT,
) -> str | None:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        errors.append(
            f"{path} must be a string of length {minimum}..{maximum}"
        )
        return None
    return value


def _slug(value: Any, path: str, errors: list[str]) -> str | None:
    text = _text(value, path, errors, 3, 96)
    if text is not None and not SLUG.fullmatch(text):
        errors.append(f"{path} has an invalid slug format")
        return None
    return text


def _sha(value: Any, path: str, errors: list[str]) -> str | None:
    text = _text(value, path, errors, 64, 64)
    if text is not None and not SHA256.fullmatch(text):
        errors.append(f"{path} must be lowercase SHA-256")
        return None
    return text


def _timestamp(value: Any, path: str, errors: list[str]) -> datetime | None:
    text = _text(value, path, errors, 20, 64)
    if text is None:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{path} must be ISO-8601")
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        errors.append(f"{path} must include a UTC offset")
        return None
    return result


def _optional_timestamp(
    value: Any, path: str, errors: list[str]
) -> datetime | None:
    if value is None:
        return None
    return _timestamp(value, path, errors)


def _date(value: Any, path: str, errors: list[str]) -> date | None:
    text = _text(value, path, errors, 10, 10)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        errors.append(f"{path} must be YYYY-MM-DD")
        return None


def _integer(
    value: Any,
    path: str,
    errors: list[str],
    minimum: int,
    maximum: int,
) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        errors.append(f"{path} must be an integer in {minimum}..{maximum}")
        return None
    return value


def _number(
    value: Any,
    path: str,
    errors: list[str],
    minimum: float,
    maximum: float,
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{path} must be a number")
        return None
    number = float(value)
    if not minimum <= number <= maximum:
        errors.append(f"{path} must be in {minimum}..{maximum}")
        return None
    return number


def _strings(
    value: Any,
    path: str,
    errors: list[str],
    minimum: int,
    maximum: int,
    item_minimum: int = 1,
    item_maximum: int = 500,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        errors.append(f"{path} must contain {minimum}..{maximum} strings")
        return []
    result: list[str] = []
    for index, raw in enumerate(value):
        text = _text(
            raw,
            f"{path}[{index}]",
            errors,
            item_minimum,
            item_maximum,
        )
        if text is not None:
            result.append(text)
    if len(result) != len(set(result)):
        errors.append(f"{path} must not contain duplicates")
    return result


def _url(value: Any, path: str, errors: list[str]) -> str | None:
    text = _text(value, path, errors, 12, 1_000)
    if text is None:
        return None
    parsed = urlparse(text)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
    ):
        errors.append(
            f"{path} must be canonical HTTPS without userinfo, query, or fragment"
        )
        return None
    return text


def _walk_safe(value: Any, path: str, errors: list[str]) -> None:
    forbidden = {
        "resume",
        "cover_letter",
        "email_address",
        "phone_number",
        "application_payload",
        "outreach_message",
        "access_token",
        "api_key",
        "authorization",
        "credential",
        "secret",
        "raw_source",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                errors.append(f"{path} contains a non-string key")
            elif key.casefold() in forbidden:
                errors.append(f"{path}.{key} is prohibited")
            _walk_safe(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_safe(child, f"{path}[{index}]", errors)
    elif isinstance(value, str) and len(value) > MAX_TEXT:
        errors.append(f"{path} exceeds the text bound")


def _validate_window(
    root: Mapping[str, Any], errors: list[str]
) -> tuple[datetime | None, datetime | None]:
    window = _object(root.get("window"), WINDOW_KEYS, "window", errors) or {}
    start = _timestamp(window.get("start"), "window.start", errors)
    end = _timestamp(window.get("end"), "window.end", errors)
    _text(window.get("timezone"), "window.timezone", errors, 3, 64)
    if start is not None and end is not None and start >= end:
        errors.append("window.start must be before window.end")
    return start, end


def _validate_safety(root: Mapping[str, Any], errors: list[str]) -> None:
    safety = _object(root.get("safety"), SAFETY_KEYS, "safety", errors) or {}
    for key in (
        "contains_credentials",
        "contains_personal_data",
        "applications_authorized",
        "outreach_authorized",
        "external_mutations_authorized",
    ):
        if safety.get(key) is not False:
            errors.append(f"safety.{key} must be false")
    _strings(safety.get("notes"), "safety.notes", errors, 1, 12, 8, 500)


def _validate_compensation(
    value: Any, path: str, errors: list[str]
) -> None:
    if value is None:
        return
    compensation = _object(value, COMPENSATION_KEYS, path, errors) or {}
    currency = _text(compensation.get("currency"), f"{path}.currency", errors, 3, 3)
    if currency is not None and not CURRENCY.fullmatch(currency):
        errors.append(f"{path}.currency must be an uppercase ISO-style code")
    minimum = _number(
        compensation.get("minimum"), f"{path}.minimum", errors, 0, 100_000_000
    )
    maximum = _number(
        compensation.get("maximum"), f"{path}.maximum", errors, 0, 100_000_000
    )
    if minimum is not None and maximum is not None and minimum > maximum:
        errors.append(f"{path}.minimum must not exceed maximum")
    period = compensation.get("period")
    if period not in COMPENSATION_PERIODS:
        errors.append(f"{path}.period is unsupported")
    _url(compensation.get("evidence_url"), f"{path}.evidence_url", errors)


def _validate_fit(value: Any, path: str, errors: list[str]) -> int | None:
    fit = _object(value, FIT_KEYS, path, errors) or {}
    score = _integer(fit.get("score"), f"{path}.score", errors, 0, 100)
    _strings(
        fit.get("must_have_matches"),
        f"{path}.must_have_matches",
        errors,
        0,
        24,
        2,
        300,
    )
    _strings(
        fit.get("preferred_matches"),
        f"{path}.preferred_matches",
        errors,
        0,
        24,
        2,
        300,
    )
    _strings(
        fit.get("must_have_gaps"),
        f"{path}.must_have_gaps",
        errors,
        0,
        24,
        2,
        300,
    )
    _strings(
        fit.get("preferred_gaps"),
        f"{path}.preferred_gaps",
        errors,
        0,
        24,
        2,
        300,
    )
    _strings(fit.get("unknowns"), f"{path}.unknowns", errors, 0, 24, 2, 300)
    _sha(fit.get("evidence_sha256"), f"{path}.evidence_sha256", errors)
    return score


def _validate_opportunities(
    items: Sequence[Any],
    start: datetime | None,
    end: datetime | None,
    errors: list[str],
) -> tuple[int, int]:
    ids: set[str] = set()
    requisitions: set[tuple[str, str]] = set()
    urls: set[str] = set()
    ranks: list[tuple[int, int, str, str]] = []
    company_roles: dict[str, int] = {}
    company_fiducia: dict[str, tuple[str, ...]] = {}
    for index, raw in enumerate(items):
        path = f"items[{index}]"
        item = _object(raw, OPPORTUNITY_KEYS, path, errors) or {}
        item_id = _slug(item.get("id"), f"{path}.id", errors)
        if item_id is not None:
            if item_id in ids:
                errors.append(f"duplicate item id: {item_id}")
            ids.add(item_id)
        rank = _integer(item.get("rank"), f"{path}.rank", errors, 1, 10_000)
        company = _slug(item.get("company_id"), f"{path}.company_id", errors)
        _text(item.get("company_name"), f"{path}.company_name", errors, 2, 160)
        requisition = _text(
            item.get("requisition_id"), f"{path}.requisition_id", errors, 1, 128
        )
        _text(item.get("title"), f"{path}.title", errors, 4, 200)
        url = _url(item.get("canonical_url"), f"{path}.canonical_url", errors)
        if url is not None:
            if url in urls:
                errors.append(f"duplicate canonical_url: {url}")
            urls.add(url)
        if company is not None and requisition is not None:
            identity = (company, requisition)
            if identity in requisitions:
                errors.append(f"duplicate requisition identity: {identity}")
            requisitions.add(identity)
        if item.get("source_kind") not in OPPORTUNITY_SOURCES:
            errors.append(f"{path}.source_kind must be an official first-party source")
        verified = _timestamp(item.get("verified_at"), f"{path}.verified_at", errors)
        posted = _optional_timestamp(item.get("posted_at"), f"{path}.posted_at", errors)
        expires = _optional_timestamp(item.get("expires_at"), f"{path}.expires_at", errors)
        if verified is not None and start is not None and verified < start:
            errors.append(f"{path}.verified_at precedes the digest window")
        if verified is not None and end is not None and verified >= end:
            errors.append(f"{path}.verified_at must fall inside the digest window")
        if posted is not None and verified is not None and posted > verified:
            errors.append(f"{path}.posted_at must not follow verified_at")
        if expires is not None and verified is not None and expires <= verified:
            errors.append(f"{path}.expires_at must follow verified_at")
        _strings(item.get("locations"), f"{path}.locations", errors, 1, 16, 2, 120)
        if item.get("remote_policy") not in REMOTE_POLICIES:
            errors.append(f"{path}.remote_policy is unsupported")
        if item.get("employment_type") not in EMPLOYMENT_TYPES:
            errors.append(f"{path}.employment_type is unsupported")
        if item.get("role_family") not in ROLE_FAMILIES:
            errors.append(f"{path}.role_family is unsupported")
        _validate_compensation(item.get("compensation"), f"{path}.compensation", errors)
        score = _validate_fit(item.get("fit"), f"{path}.fit", errors)
        fiducia = _strings(
            item.get("fiducia_opportunity_ids"),
            f"{path}.fiducia_opportunity_ids",
            errors,
            0,
            12,
            3,
            96,
        )
        for number, opportunity_id in enumerate(fiducia):
            if not SLUG.fullmatch(opportunity_id):
                errors.append(
                    f"{path}.fiducia_opportunity_ids[{number}] has an invalid slug"
                )
        _sha(item.get("content_sha256"), f"{path}.content_sha256", errors)
        if company is not None:
            company_roles[company] = company_roles.get(company, 0) + 1
            normalized_fiducia = tuple(sorted(fiducia))
            previous = company_fiducia.get(company)
            if previous is None:
                company_fiducia[company] = normalized_fiducia
            elif previous != normalized_fiducia:
                errors.append(
                    f"company {company} must use one consistent Fiducia opportunity set"
                )
        if None not in (rank, score, company, requisition):
            ranks.append((int(rank), -int(score), str(company), str(requisition)))

    expected_ranks = list(range(1, len(items) + 1))
    actual_ranks = sorted(rank for rank, _score, _company, _req in ranks)
    if actual_ranks != expected_ranks:
        errors.append("opportunity ranks must be unique and contiguous from 1")
    declared_order = [
        (rank, score, company, requisition)
        for rank, score, company, requisition in ranks
    ]
    deterministic = sorted(ranks, key=lambda entry: (entry[1], entry[2], entry[3]))
    for expected_rank, entry in enumerate(deterministic, start=1):
        if entry[0] != expected_rank:
            errors.append(
                "opportunity ranks must follow fit score descending with "
                "company/requisition tie-breaking"
            )
            break
    if len(declared_order) != len(items):
        errors.append("every opportunity item must have a valid rank and fit identity")

    fiducia_companies = 0
    for company, opportunity_ids in company_fiducia.items():
        if opportunity_ids:
            fiducia_companies += 1
            role_count = company_roles.get(company, 0)
            if role_count not in {2, 3}:
                errors.append(
                    f"Fiducia company {company} must have exactly 2 or 3 job roles; "
                    f"got {role_count}"
                )
    return len(company_roles), fiducia_companies


def _research_composite(scores: Mapping[str, int]) -> int:
    return (
        scores["relevance"] * 30
        + scores["novelty"] * 20
        + scores["evidence_quality"] * 25
        + (100 - scores["implementation_cost"]) * 10
        + scores["falsifiability"] * 15
    )


def _validate_research(
    items: Sequence[Any],
    start: datetime | None,
    end: datetime | None,
    errors: list[str],
) -> None:
    ids: set[str] = set()
    identifiers: set[str] = set()
    urls: set[str] = set()
    ranks: list[tuple[int, int, str]] = []
    supersession: dict[str, list[str]] = {}
    for index, raw in enumerate(items):
        path = f"items[{index}]"
        item = _object(raw, RESEARCH_KEYS, path, errors) or {}
        item_id = _slug(item.get("id"), f"{path}.id", errors)
        if item_id is not None:
            if item_id in ids:
                errors.append(f"duplicate item id: {item_id}")
            ids.add(item_id)
        rank = _integer(item.get("rank"), f"{path}.rank", errors, 1, 10_000)
        _text(item.get("title"), f"{path}.title", errors, 4, 300)
        _strings(item.get("authors"), f"{path}.authors", errors, 1, 64, 2, 160)
        if item.get("source_kind") not in RESEARCH_SOURCES:
            errors.append(f"{path}.source_kind must be a recognized primary source")
        url = _url(item.get("canonical_url"), f"{path}.canonical_url", errors)
        if url is not None:
            if url in urls:
                errors.append(f"duplicate canonical_url: {url}")
            urls.add(url)
        identifier = _text(
            item.get("stable_identifier"),
            f"{path}.stable_identifier",
            errors,
            3,
            300,
        )
        if identifier is not None:
            if identifier in identifiers:
                errors.append(f"duplicate stable_identifier: {identifier}")
            identifiers.add(identifier)
        published = _date(
            item.get("publication_date"), f"{path}.publication_date", errors
        )
        updated = _optional_timestamp(item.get("updated_at"), f"{path}.updated_at", errors)
        retrieved = _timestamp(
            item.get("retrieved_at"), f"{path}.retrieved_at", errors
        )
        if retrieved is not None and start is not None and retrieved < start:
            errors.append(f"{path}.retrieved_at precedes the digest window")
        if retrieved is not None and end is not None and retrieved >= end:
            errors.append(f"{path}.retrieved_at must fall inside the digest window")
        if published is not None and retrieved is not None and published > retrieved.date():
            errors.append(f"{path}.publication_date cannot be in the future")
        if updated is not None and retrieved is not None and updated > retrieved:
            errors.append(f"{path}.updated_at cannot follow retrieved_at")
        _sha(item.get("source_sha256"), f"{path}.source_sha256", errors)
        if item.get("status") != "active":
            errors.append(f"{path}.status must be active for shortlist inclusion")
        supersedes = _strings(
            item.get("supersedes"), f"{path}.supersedes", errors, 0, 16, 3, 96
        )
        if item_id is not None:
            supersession[item_id] = supersedes
        themes = _strings(item.get("themes"), f"{path}.themes", errors, 1, 8, 3, 96)
        for number, theme in enumerate(themes):
            if theme not in RESEARCH_THEMES:
                errors.append(f"{path}.themes[{number}] is unsupported")
        claims = item.get("claims")
        if not isinstance(claims, list) or not 1 <= len(claims) <= 24:
            errors.append(f"{path}.claims must contain 1..24 source-bound claims")
            claims = []
        for claim_index, raw_claim in enumerate(claims):
            claim_path = f"{path}.claims[{claim_index}]"
            claim = _object(raw_claim, CLAIM_KEYS, claim_path, errors) or {}
            _text(claim.get("statement"), f"{claim_path}.statement", errors, 8, 500)
            _sha(
                claim.get("source_fragment_sha256"),
                f"{claim_path}.source_fragment_sha256",
                errors,
            )
        _strings(
            item.get("interpretation"),
            f"{path}.interpretation",
            errors,
            1,
            16,
            8,
            500,
        )
        _strings(
            item.get("uncertainty"),
            f"{path}.uncertainty",
            errors,
            1,
            16,
            8,
            500,
        )
        _strings(
            item.get("proposed_experiments"),
            f"{path}.proposed_experiments",
            errors,
            0,
            16,
            8,
            500,
        )
        score_object = _object(item.get("scores"), SCORE_KEYS, f"{path}.scores", errors) or {}
        scores: dict[str, int] = {}
        for name in SCORE_KEYS:
            score = _integer(
                score_object.get(name), f"{path}.scores.{name}", errors, 0, 100
            )
            if score is not None:
                scores[name] = score
        _sha(item.get("summary_sha256"), f"{path}.summary_sha256", errors)
        if rank is not None and item_id is not None and len(scores) == len(SCORE_KEYS):
            ranks.append((rank, -_research_composite(scores), item_id))

    if sorted(rank for rank, _score, _id in ranks) != list(range(1, len(items) + 1)):
        errors.append("research ranks must be unique and contiguous from 1")
    deterministic = sorted(ranks, key=lambda entry: (entry[1], entry[2]))
    for expected_rank, entry in enumerate(deterministic, start=1):
        if entry[0] != expected_rank:
            errors.append(
                "research ranks must follow the deterministic composite score "
                "with item-id tie-breaking"
            )
            break
    if len(ranks) != len(items):
        errors.append("every research item must have complete deterministic scores")

    for item_id, dependencies in supersession.items():
        for dependency in dependencies:
            if dependency == item_id:
                errors.append(f"research item {item_id} cannot supersede itself")
            elif dependency not in ids:
                errors.append(
                    f"research item {item_id} supersedes unknown item {dependency}"
                )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item_id: str, stack: list[str]) -> None:
        if item_id in visited:
            return
        if item_id in visiting:
            cycle = stack[stack.index(item_id) :] + [item_id]
            errors.append(f"research supersession cycle: {' -> '.join(cycle)}")
            return
        visiting.add(item_id)
        stack.append(item_id)
        for dependency in supersession.get(item_id, []):
            if dependency in supersession:
                visit(dependency, stack)
        stack.pop()
        visiting.remove(item_id)
        visited.add(item_id)

    for item_id in supersession:
        visit(item_id, [])


def validate(value: Mapping[str, Any]) -> DigestReport:
    errors: list[str] = []
    _walk_safe(value, "$", errors)
    root = _object(value, ROOT_KEYS, "$", errors) or {}
    if root.get("schema_version") != SCHEMA:
        errors.append(f"schema_version must be {SCHEMA}")
    _slug(root.get("digest_id"), "digest_id", errors)
    kind = _text(root.get("kind"), "kind", errors, 3, 32)
    if kind not in {"opportunity", "research"}:
        errors.append("kind must be opportunity or research")
    generated = _timestamp(root.get("generated_at"), "generated_at", errors)
    start, end = _validate_window(root, errors)
    if generated is not None and start is not None and generated < start:
        errors.append("generated_at precedes the digest window")
    if generated is not None and end is not None and generated >= end:
        errors.append("generated_at must fall inside the digest window")
    _sha(root.get("policy_sha256"), "policy_sha256", errors)
    items = root.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        errors.append("items must contain 1..200 records")
        items = []
    company_count = 0
    fiducia_company_count = 0
    if kind == "opportunity":
        company_count, fiducia_company_count = _validate_opportunities(
            items, start, end, errors
        )
    elif kind == "research":
        _validate_research(items, start, end, errors)
    _validate_safety(root, errors)
    try:
        checksum = canonical_sha256(value)
    except (TypeError, ValueError) as exc:
        errors.append(f"digest cannot be canonically serialized: {exc}")
        checksum = None
    return DigestReport(
        valid=not errors,
        digest_kind=kind,
        digest_sha256=checksum,
        item_count=len(items),
        company_count=company_count,
        fiducia_company_count=fiducia_company_count,
        errors=tuple(errors),
    )
