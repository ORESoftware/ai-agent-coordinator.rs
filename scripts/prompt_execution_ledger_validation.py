#!/usr/bin/env python3
"""Semantic validation for prompt execution ledgers."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from prompt_execution_ledger import (
    COVERAGE,
    COUNTS,
    LINEAR,
    PRIORITIES,
    RECEIPTS,
    ROOT,
    SAFETY,
    SCHEMA,
    SEGMENT,
    SHA,
    SLUG,
    STATUSES,
    WINDOW,
    WORK,
    WORKSTREAMS,
    Report,
    _count,
    _cycles,
    _github,
    _keys,
    _list,
    _strings,
    _text,
    _time,
    _walk,
    digest,
)


def validate(value: Mapping[str, Any], mode: str = "closure") -> Report:
    if mode not in {"planning", "closure"}:
        raise ValueError("mode must be planning or closure")
    errors: list[str] = []
    warnings: list[str] = []
    _walk(value, "$", errors)
    root = _keys(value, ROOT, "$", errors) or {}
    if root.get("schema_version") != SCHEMA:
        errors.append(f"schema_version must be {SCHEMA}")
    ledger_id = _text(root.get("ledger_id"), "ledger_id", errors, 5, 96)
    if ledger_id and (not SLUG.fullmatch(ledger_id)):
        errors.append("ledger_id has an invalid format")
    generated = _time(root.get("generated_at"), "generated_at", errors)
    window = _keys(root.get("window"), WINDOW, "window", errors) or {}
    start = _time(window.get("start"), "window.start", errors)
    end = _time(window.get("end"), "window.end", errors)
    _text(window.get("timezone"), "window.timezone", errors, 3, 64)
    _text(window.get("selection_basis"), "window.selection_basis", errors, 12, 500)
    if start and end:
        if start >= end:
            errors.append("window.start must be before window.end")
        if generated and (not start <= generated < end):
            errors.append("generated_at must fall within the ledger window")
    incomplete: list[str] = []
    intervals: list[tuple[datetime, datetime, str]] = []
    declared: list[datetime] = []
    segment_ids: set[str] = set()
    for index, raw in enumerate(
        _list(root.get("source_segments"), "source_segments", errors, 1, 64) or []
    ):
        path = f"source_segments[{index}]"
        item = _keys(raw, SEGMENT, path, errors) or {}
        sid = _text(item.get("segment_id"), f"{path}.segment_id", errors, 5, 96)
        if sid:
            if not SLUG.fullmatch(sid):
                errors.append(f"{path}.segment_id has an invalid format")
            if sid in segment_ids:
                errors.append(f"duplicate source segment id: {sid}")
            segment_ids.add(sid)
        left = _time(item.get("start"), f"{path}.start", errors)
        right = _time(item.get("end"), f"{path}.end", errors)
        if left and right:
            if left >= right:
                errors.append(f"{path} start must be before end")
            else:
                intervals.append((left, right, sid or path))
                declared.append(left)
        complete = item.get("complete")
        if not isinstance(complete, bool):
            errors.append(f"{path}.complete must be boolean")
            complete = False
        receipt = _text(
            item.get("receipt_type"), f"{path}.receipt_type", errors, 3, 64
        )
        if receipt and receipt not in RECEIPTS:
            errors.append(f"{path}.receipt_type is unsupported")
        anchor = _text(
            item.get("receipt_anchor"), f"{path}.receipt_anchor", errors, 5, 300
        )
        if anchor and (not LINEAR.fullmatch(anchor)):
            _github(anchor, f"{path}.receipt_anchor", errors)
        assertion = _text(
            item.get("assertion_sha256"),
            f"{path}.assertion_sha256",
            errors,
            64,
            64,
        )
        if assertion and (not SHA.fullmatch(assertion)):
            errors.append(f"{path}.assertion_sha256 must be lowercase SHA-256")
        if complete:
            if item.get("incomplete_reason") is not None:
                errors.append(f"{path}.incomplete_reason must be null when complete")
            counts = (
                _keys(
                    item.get("record_counts"),
                    COUNTS,
                    f"{path}.record_counts",
                    errors,
                )
                or {}
            )
            parsed = {
                name: _count(
                    counts.get(name), f"{path}.record_counts.{name}", errors
                )
                for name in COUNTS
            }
            if (
                parsed.get("human_messages") is not None
                and parsed.get("empty_or_attachment_only") is not None
                and parsed["empty_or_attachment_only"] > parsed["human_messages"]
            ):
                errors.append(
                    f"{path}.record_counts.empty_or_attachment_only cannot exceed human_messages"
                )
        else:
            if item.get("record_counts") is not None:
                errors.append(f"{path}.record_counts must be null when incomplete")
            if (
                _text(
                    item.get("incomplete_reason"),
                    f"{path}.incomplete_reason",
                    errors,
                    12,
                    500,
                )
                and sid
            ):
                incomplete.append(sid)
    if declared != sorted(declared):
        errors.append("source_segments must be ordered chronologically")
    intervals.sort()
    if intervals and start and end:
        if intervals[0][0] != start:
            errors.append("source_segments must begin exactly at window.start")
        if intervals[-1][1] != end:
            errors.append("source_segments must end exactly at window.end")
        for previous, current in zip(intervals, intervals[1:]):
            if previous[1] < current[0]:
                errors.append(
                    f"source segment gap between {previous[2]} and {current[2]}"
                )
            if previous[1] > current[0]:
                errors.append(
                    f"source segment overlap between {previous[2]} and {current[2]}"
                )
    verified = end
    for left, _right, sid in intervals:
        if sid in incomplete:
            verified = left
            break
    work_items = _list(root.get("workstreams"), "workstreams", errors, 0, 1000) or []
    if len(work_items) != WORKSTREAMS:
        errors.append(
            f"workstreams must contain exactly {WORKSTREAMS} items; got {len(work_items)}"
        )
    ids: set[str] = set()
    github_seen: set[str] = set()
    graph: dict[str, list[str]] = {}
    for index, raw in enumerate(work_items):
        path = f"workstreams[{index}]"
        item = _keys(raw, WORK, path, errors) or {}
        wid = _text(item.get("id"), f"{path}.id", errors, 3, 48)
        if wid:
            if not SLUG.fullmatch(wid):
                errors.append(f"{path}.id has an invalid format")
            if wid in ids:
                errors.append(f"duplicate workstream id: {wid}")
            ids.add(wid)
        _text(item.get("title"), f"{path}.title", errors, 8, 160)
        if item.get("priority") not in PRIORITIES:
            errors.append(f"{path}.priority is unsupported")
        if item.get("status") not in STATUSES:
            errors.append(f"{path}.status is unsupported")
        for number, anchor in enumerate(
            _strings(
                item.get("linear_anchors"),
                f"{path}.linear_anchors",
                errors,
                1,
                8,
                5,
                32,
            )
        ):
            if not LINEAR.fullmatch(anchor):
                errors.append(
                    f"{path}.linear_anchors[{number}] must be a DEN-N identifier"
                )
        for number, anchor in enumerate(
            _strings(
                item.get("github_anchors"),
                f"{path}.github_anchors",
                errors,
                1,
                8,
                20,
                300,
            )
        ):
            _github(anchor, f"{path}.github_anchors[{number}]", errors)
            if anchor in github_seen:
                errors.append(
                    f"duplicate primary GitHub anchor across workstreams: {anchor}"
                )
            github_seen.add(anchor)
        _strings(
            item.get("acceptance_checks"),
            f"{path}.acceptance_checks",
            errors,
            2,
            12,
            12,
            500,
        )
        dependencies = _strings(
            item.get("depends_on"), f"{path}.depends_on", errors, 0, 14, 3, 48
        )
        if wid:
            graph[wid] = dependencies
        _text(
            item.get("safety_boundary"),
            f"{path}.safety_boundary",
            errors,
            12,
            500,
        )
    for workstream_id, dependencies in graph.items():
        for dependency in dependencies:
            if dependency == workstream_id:
                errors.append(f"workstream {workstream_id} cannot depend on itself")
            elif dependency not in ids:
                errors.append(
                    f"workstream {workstream_id} depends on unknown workstream {dependency}"
                )
    for cycle in _cycles(graph):
        errors.append(f"workstream dependency cycle: {cycle}")
    coverage = _keys(root.get("coverage"), COVERAGE, "coverage", errors) or {}
    source_complete = coverage.get("source_complete")
    if not isinstance(source_complete, bool):
        errors.append("coverage.source_complete must be boolean")
        source_complete = False
    if source_complete != (not incomplete):
        errors.append(
            "coverage.source_complete must equal the completeness of all source segments"
        )
    stated_verified = _time(
        coverage.get("verified_through"), "coverage.verified_through", errors
    )
    if stated_verified and verified and (stated_verified != verified):
        errors.append(
            "coverage.verified_through must equal the first incomplete start or window.end"
        )
    values: dict[str, int | None] = {}
    for name in (
        "actionable_prompt_count",
        "mapped_prompt_count",
        "unresolved_prompt_count",
    ):
        values[name] = (
            None
            if coverage.get(name) is None
            else _count(coverage.get(name), f"coverage.{name}", errors)
        )
    actionable, mapped, unresolved = values.values()
    if source_complete and None in values.values():
        errors.append("complete source coverage requires all prompt counts")
    if None not in values.values() and mapped + unresolved != actionable:
        errors.append(
            "mapped_prompt_count + unresolved_prompt_count must equal actionable_prompt_count"
        )
    blockers = _strings(
        coverage.get("closure_blockers"),
        "coverage.closure_blockers",
        errors,
        0,
        64,
        4,
        160,
    )
    if set(incomplete) - set(blockers):
        errors.append("coverage.closure_blockers must include incomplete segments")
    closure = bool(
        source_complete
        and actionable is not None
        and (mapped == actionable)
        and (unresolved == 0)
        and (not blockers)
    )
    safety = _keys(root.get("safety"), SAFETY, "safety", errors) or {}
    for name in (
        "contains_raw_messages",
        "contains_credentials",
        "live_mutations_authorized",
        "merge_authorized",
    ):
        if safety.get(name) is not False:
            errors.append(f"safety.{name} must be false")
    if safety.get("source_gap_blocks_closure") is not True:
        errors.append("safety.source_gap_blocks_closure must be true")
    _strings(safety.get("notes"), "safety.notes", errors, 1, 12, 12, 500)
    if incomplete:
        warnings.append(
            "source coverage is incomplete; planning is valid but closure is blocked"
        )
    if mode == "closure" and (not closure):
        errors.append("closure mode requires complete source coverage and zero blockers")
    try:
        checksum: str | None = digest(value)
    except (TypeError, ValueError) as exc:
        errors.append(f"ledger cannot be serialized: {exc}")
        checksum = None
    return Report(
        not errors,
        closure and (not errors),
        mode,
        checksum,
        len(work_items),
        tuple(incomplete),
        tuple(errors),
        tuple(warnings),
    )
