# Daily portfolio result collector

The daily portfolio briefing composer consumes normalized lane envelopes. It does not connect to GitHub, Linear, email, job boards, browsers, ChatGPT, or a delivery destination. `tools/collect_daily_portfolio_results.py` supplies the boundary between those child workflows and the deterministic composer.

The collector is intentionally network-free. It reads one strict manifest and exactly eight normalized result files beneath one approved local root, verifies their integrity and freshness, applies the existing composer validation and redaction contract, and writes:

- one `portfolio_briefing_input.v1` document for `compose_daily_portfolio_briefing.py`;
- one bounded `portfolio_briefing_provenance.v1` document for evidence and parent/child correlation.

It never fetches raw source systems and never delivers the briefing.

## Canonical lanes

Every manifest must contain each lane exactly once:

| Lane | Required source issue |
|---|---|
| `must_act_today` | none |
| `github_linear` | none |
| `engineering_research` | `DEN-828` |
| `ai_technology` | none |
| `career` | `DEN-826` |
| `inbox_relationships` | `DEN-830` |
| `business_growth` | none |
| `prompt_coverage` | `DEN-834` |

The collector rejects unknown, missing, or duplicate lanes. A lane with a required source issue cannot substitute another issue identifier.

## Manifest contract

Schema: `portfolio_briefing_result_manifest.v1`

```json
{
  "schema_version": "portfolio_briefing_result_manifest.v1",
  "generated_at": "2026-08-05T13:00:00Z",
  "max_clock_skew_seconds": 300,
  "lanes": [
    {
      "lane": "career",
      "result_path": "career/2026-08-05.json",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "bytes": 2048,
      "run_id": "career:scheduled:2026-08-05",
      "max_age_seconds": 3600,
      "missing_policy": "unavailable"
    }
  ]
}
```

The example shows one entry; a valid manifest has eight.

### Field rules

- `generated_at` is the parent collection instant in UTC or another offset-aware ISO-8601 form.
- `max_clock_skew_seconds` is from 0 through 300. A child result later than this allowance fails closed.
- `lane` is one canonical lane name.
- `result_path` is a normalized relative POSIX path beneath the approved result root. Absolute paths, backslashes, drive prefixes, empty components, `.` and `..` are rejected.
- `sha256` is the lowercase digest of the exact result-file bytes.
- `bytes` is the exact result-file length and cannot exceed 1 MiB.
- `run_id` is a bounded child-run identifier used only for provenance and parent/child correlation.
- `max_age_seconds` is from 60 seconds through seven days.
- `missing_policy` is `fail` or `unavailable`.

`sha256` and `bytes` must either both be present or both be `null`. They may be `null` only when `missing_policy` is `unavailable`. An existing file without integrity metadata is rejected; the null form represents an expected absence, not an unchecked read.

## Lane result contract

Each result file is one lane object from `portfolio_briefing_input.v1`, not a complete eight-lane input:

```json
{
  "status": "ok",
  "source_issue": "DEN-826",
  "generated_at": "2026-08-05T12:45:00Z",
  "error_summary": null,
  "items": []
}
```

The existing composer owns item validation, source URL sanitization, bounded text handling, sensitive-assignment redaction, rank validation, and item-count limits. Consequently, the collector cannot create a second, weaker interpretation of the briefing schema.

A child should write its result atomically, compute the digest and length from the final bytes, and only then publish or replace the manifest that references it.

## Missing, degraded, and stale results

- `missing_policy: fail` makes an absent result a collection failure.
- `missing_policy: unavailable` converts an absent result into a bounded `unavailable` lane with no items.
- A present result older than `max_age_seconds` is also converted to `unavailable`; stale evidence is never silently treated as current.
- A child-produced `degraded` result is retained as degraded after schema validation.
- A child-produced `unavailable` result must contain no items and must have a bounded error summary.

The generated provenance distinguishes `collected`, `stale`, and `missing`. It records the configured freshness limit and observed age but does not copy item bodies.

## File-system boundary

The result root must be a real directory, not a symbolic link. The collector:

- accepts only relative POSIX result paths;
- verifies every existing path component is not a symbolic link;
- rejects paths resolving outside the approved root;
- opens final files with `O_NOFOLLOW` where the platform provides it;
- accepts regular files only, not directories, sockets, devices, or FIFOs;
- enforces the size cap before and during the read;
- compares device, inode, size, modification time, and change time before and after the read;
- rejects a file that changes during the read;
- verifies exact byte length and SHA-256 before parsing JSON;
- rejects duplicate JSON keys and unknown manifest fields.

Result roots should be private to the coordinator worker and mounted read-only for the collector. Child writers should publish into a separate writable location and promote complete results atomically.

## Provenance contract

Schema: `portfolio_briefing_provenance.v1`

The report contains:

- parent `generated_at`;
- deterministic semantic manifest digest;
- deterministic assembled-input digest;
- counts for eight total lanes and collected/stale/missing states;
- one record per canonical lane with lane, child run ID, state, required source issue, expected/observed digest, byte count, child generation time, observed age, and freshness limit.

It deliberately excludes result paths, item titles, item bodies, source URLs, raw prompts, messages, credentials, and private account identifiers. The report is suitable for short-lived CI and run evidence, not as a replacement for the child result or durable delivery ledger.

## Local use

```bash
python3 -m py_compile \
  tools/compose_daily_portfolio_briefing.py \
  tools/collect_daily_portfolio_results.py \
  tests/test_daily_portfolio_result_collector.py

python3 -m unittest -v tests/test_daily_portfolio_result_collector.py

python3 tools/collect_daily_portfolio_results.py \
  --manifest /var/lib/ai-agent-coordinator/briefing/manifest.json \
  --result-root /var/lib/ai-agent-coordinator/briefing/results \
  --output-input /var/lib/ai-agent-coordinator/briefing/input.json \
  --output-provenance /var/lib/ai-agent-coordinator/briefing/provenance.json
```

The collector writes outputs only after all required validation succeeds. Its output files should be written to a private run-specific directory and supplied to the composer in the same logical parent run.

## Worker sequence

1. Obtain the parent run identity and fixed collection instant.
2. Wait for or resolve the eight durable child-result references.
3. Construct the exact manifest using final byte lengths and digests.
4. Run the collector with a read-only result root.
5. Retain the bounded provenance report with the parent run evidence.
6. Pass only the assembled input to the deterministic composer.
7. Hand the resulting plan to the fenced delivery transaction implemented under `DEN-2334`.

The collector does not mark child jobs complete, advance the scheduled comparison baseline, or send a destination request.

## Recovery and rollback

A retry with unchanged manifest semantics and unchanged result bytes produces the same manifest and input digests. A repaired child result must use a new digest; operators should preserve the prior evidence and explicitly supersede the parent attempt rather than editing an already-delivered run.

To roll back this collector, disable the parent briefing enqueue switch and retain the result files and manifest for diagnosis. Do not bypass digest, freshness, symlink, or schema checks to force a briefing through. A manual run must use an explicit manual identity and must not replace the scheduled comparison baseline.

## Retention

Recommended defaults:

- normalized child result and manifest: retain according to the owning child workflow, typically 14 to 30 days;
- bounded provenance and generated input: 30 days with the parent run evidence;
- delivery receipt and transactional state: follow the durable policy implemented by `DEN-2334`;
- raw source-system content: never copy it into this collector's manifest, provenance, logs, or telemetry.
