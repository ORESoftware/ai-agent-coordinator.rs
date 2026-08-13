# Artifact-recovery source coverage

Tracking observed artifacts is not proof that every configured source was read. A recovery run can reconcile the subset it saw while a ChatGPT, Claude, Linear, GitHub, file-library, or local-repository source was unauthorized, unavailable, stale, or only partially paged. `artifact_recovery_source_coverage.v1` closes that false-completeness boundary.

## Contract

Each required and optional source has exactly one public-safe receipt. The receipt contains only source and capability digests, the authorized observation window, capture and watermark timestamps, pagination counts/completion, a normalized state, a bounded error class, and retry disposition. It contains no raw chat content, prompt text, hidden reasoning, repository payload, credential, account identifier, email address, or free-form provider error.

The validator rejects unknown fields, duplicate or missing sources, malformed digests, unsupported states, inconsistent timestamps, clock skew, impossible pagination, and tampered summaries or report digests. It derives two fail-closed corrections:

* a source reported as `complete` becomes `partial` when pagination never started, did not finish, or ended on an incomplete page;
* a source reported as `complete` becomes `stale` when its high-water mark exceeds the source-specific freshness budget.

The aggregate is `complete` only when every required source is effectively `complete`. Required `unauthorized`, `not_configured`, or `excluded` sources block the run. Required `partial`, `unavailable`, or `stale` sources make the run partial. Optional sources must still appear explicitly; they may be `not_configured` or `excluded` without weakening required-source policy.

## Commands

```bash
python3 tools/artifact_recovery_source_coverage.py aggregate \
  --input raw-source-receipts.json \
  --output source-coverage.json \
  --now 2026-08-10T18:45:00Z

python3 tools/artifact_recovery_source_coverage.py validate \
  source-coverage.json \
  --now 2026-08-10T18:45:00Z
```

`example` emits a deterministic synthetic fixture for CI contract testing only. It is not evidence that real ChatGPT, Claude, Linear, GitHub, or local-repository reads occurred:

```bash
python3 tools/artifact_recovery_source_coverage.py example \
  --output source-coverage-ci-fixture.json \
  --now 2026-08-10T18:45:00Z
```

## Integration boundary

The nightly validation workflow compiles and tests the validator, verifies the JSON schema, renders the synthetic contract fixture, validates its canonical digest, and uploads it with the existing bounded recovery evidence. Source adapters must next emit real raw receipts before the runtime may use a successful coverage report to claim a complete scan or “nothing to recover.” Until those adapters are wired, the synthetic artifact must remain clearly named and must not be interpreted as live run evidence.

Refs DEN-3434 and DEN-2797.
