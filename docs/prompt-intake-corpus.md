# Prompt-intake acceptance corpus

This repository keeps a public-safe acceptance oracle for the ChatGPT-thread reconciliation work tracked by **DEN-834** and **DEN-1613**.

The oracle captures the disposition of the audited 60-item window without copying any chat text. It proves that a deterministic rerun updates existing canonical Linear work, preserves completed and duplicate references, and plans **zero new issue creates**.

## Files

- `fixtures/prompt-intake/chatgpt-60-item-corpus.json` — compact, synthetic, redacted acceptance data.
- `scripts/expand_prompt_intake_fixture.py` — strict compact-fixture expander.
- `scripts/prompt_intake_corpus.py` — strict validator, summarizer, retention controller, telemetry builder, and post-cutoff simulator.
- `scripts/test_prompt_intake_corpus.py` — positive and negative regression coverage.
- `.github/workflows/prompt-intake-corpus.yml` — exact-head CI and retained machine-readable evidence.

## Safety boundary

The checked-in fixture uses a compact row format. The expander deterministically derives synthetic source metadata and acceptance-oracle receipts before validation. The fixture contains only:

- synthetic fixture identifiers and deterministic SHA-256 source fingerprints;
- canonical Linear issue identifiers, project keys, priority, status, classification, and duplicate-target enums;
- known duplicate relations and one bounded material-refinement relation;
- acceptance-oracle receipts derived from nonsecret fixture fields;
- fixed, synthetic latency samples for telemetry-contract tests.

It deliberately excludes prompt bodies, chat titles, thread or message identifiers, user or account identifiers, email addresses, credentials, private channel history, model responses, and hidden reasoning.

The compact loader and expanded-corpus validator reject duplicate JSON keys, unknown schema fields, unbounded strings, credential-shaped values, email-shaped values, sensitive record keys, relation drift, count drift, and high-cardinality telemetry labels.

## Acceptance invariants

The checked-in corpus must always validate to:

- 60 total items;
- 48 live canonical issues;
- 12 reference-only items;
- 40 `in_progress`, 7 `todo`, 1 `backlog`, 8 `done`, and 4 `duplicate`;
- 48 `amend_existing` decisions and 12 `no_op_reference` decisions;
- zero `create_new` decisions;
- exact duplicate targets for `DEN-1042`, `DEN-39`, `DEN-452`, and `DEN-43`;
- at least one material-refinement case that updates an existing issue.

Any change to those facts must be an intentional corpus-version change backed by a new audit, not a casual fixture edit.

## Local validation

From the repository root:

```bash
python3 -m py_compile \
  scripts/expand_prompt_intake_fixture.py \
  scripts/prompt_intake_corpus.py \
  scripts/test_prompt_intake_corpus.py
python3 scripts/test_prompt_intake_corpus.py -v
python3 scripts/expand_prompt_intake_fixture.py \
  fixtures/prompt-intake/chatgpt-60-item-corpus.json \
  --output /tmp/prompt-intake-expanded.json
python3 scripts/prompt_intake_corpus.py validate \
  /tmp/prompt-intake-expanded.json
python3 scripts/prompt_intake_corpus.py summarize \
  /tmp/prompt-intake-expanded.json \
  --output /tmp/prompt-intake-summary.json
```

The summary is compatible with the daily portfolio briefing contract: it emits stable counts, a bounded telemetry block, and disjoint `do_today`, `monitor`, and `ignore` issue buckets.

## Post-cutoff detection

`simulate-post-cutoff` accepts one synthetic expanded record whose observation time is later than the corpus cutoff. It does not mutate the base fixture. The output records only the new decision and the expected counter delta, and verifies that planned creates remain zero.

The regression test uses `DEN-1613` as the new canonical destination. The item is distinct from the historical 60-row corpus but is still an existing Linear issue, so the expected action is `amend_existing`, not `create_new`.

## Retention and deletion

The fixture policy retains synthetic source metadata for 30 days and acceptance-oracle receipts for 365 days.

`purge` produces a derived retention state:

- expired source metadata is replaced by a `purged` state with no observation time or source fingerprint;
- unexpired receipts remain byte-for-byte intact;
- expired receipts become a nonsecret digest tombstone;
- canonical issue identity remains available so deletion does not corrupt audit continuity.

Example:

```bash
python3 scripts/prompt_intake_corpus.py purge \
  /tmp/prompt-intake-expanded.json \
  --now 2026-09-15T00:00:00Z \
  --output /tmp/prompt-intake-retention.json
```

The generated retention state is evidence only. It is not a replacement for the original version-controlled acceptance fixture.

## Telemetry contract

The telemetry output uses a fixed metric-name set and at most two labels per metric. Label keys and values are allowlisted enums. Linear issue IDs, project names, source fingerprints, prompt content, credential material, and personal data are forbidden as metric labels.

The bounded metric set covers:

- scanned records;
- reference-only exclusions;
- evidence failures;
- issue updates and creates;
- duplicate prevention;
- ambiguity;
- race repair;
- fixed-sample scan latency percentiles.

This corpus is an acceptance oracle, not production telemetry. Runtime adapters should emit the same bounded dimensions from real execution without importing the synthetic sample values.

## Updating the corpus

1. Complete a new audited reconciliation window.
2. Update canonical issue states and real duplicate relations in Linear first.
3. Edit only compact, nonsecret row fields and bounded relation cases; never paste source text.
4. Update `snapshot_at`, `cutoff_at`, expected counts, briefing buckets, and relation cases.
5. Expand the fixture and run the full focused suite twice, verifying identical summaries and digest.
6. Inspect the compact and expanded diffs for prompt text, personal data, credential-shaped strings, URLs, and unexpected schema growth.
7. Land the change through a feature branch and pull request linked to the owning Linear issue.
