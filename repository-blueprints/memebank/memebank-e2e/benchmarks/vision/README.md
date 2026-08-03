# MemeBank vision benchmark

This harness evaluates **recorded observations** from OCR, object/tagging, captioning, and retrieval candidates. It deliberately does not contain provider SDKs, network calls, model downloads, credentials, or production recommendations.

## Candidate planning inventory

The enforceable research backlog is `candidates/candidate-inventory.json`. It records the required TypeScript, Go, Rust, and cloud candidates, exact or unresolved identities, benchmark capabilities, deployment targets, evidence links, disposition state, and production-dependency status.

Validate it from the coordinator repository root:

```bash
python3 -m unittest -v scripts/test_validate_memebank_vision_candidates.py
python3 scripts/validate_memebank_vision_candidates.py
```

The inventory is intentionally separate from `candidates/candidates.json`. The former defines what DEN-1011 must investigate; the latter describes only candidates that have an actual synthetic, replayed, shadow, or live result set consumable by this evaluator. Moving a candidate from the inventory into recorded benchmark results requires pinned provenance and does not itself authorize production use.

## What the checked-in fixtures prove

The generated SVG corpus covers:

- outlined two-line meme text;
- English, Spanish/Portuguese diacritics, and Japanese text;
- prompt-like adversarial text embedded in an image;
- two near-duplicate templates with changed text.

The two candidate runs are synthetic references. One local lane passes the fixture gates; one remote lane intentionally fails prompt-injection resistance. This proves that the harness reports a failed safety gate rather than averaging it away.

These five fixtures are not a production-quality corpus. DEN-1011 remains open until consented, redistribution-safe real-image cohorts and current provider/model evidence are added.

## Inputs

- `corpus/manifest.json`: split, cohort, asset digest, license, live-provider consent, OCR truth, regions, tags, caption facts, adversarial marker, and retrieval relevance.
- `candidates/candidate-inventory.json`: required candidate identities, capabilities, deployment targets, evidence, and disposition controls.
- `candidates/candidates.json`: runtime lane, privacy route, capabilities, verification status, immutable artifact/API provenance, processor version, license, checksum, and owner for candidates with recorded results.
- `results/recorded-results.json`: run metadata and one observation per evaluation item.
- `hardware/*.json`: explicit execution environment.
- `policies/gates.json`: immutable evaluation split, IoU threshold, Recall@K, safety/quality gates, and live-provider budget/consent policy.

## Metrics

The evaluator emits:

- character and word error rates;
- region precision, recall, and matched IoU;
- micro tag precision, recall, and F1;
- caption schema validity, fact precision/recall, and prompt-injection resistance;
- Recall@K, MRR, and nDCG for declared relevance judgments;
- p50/p95 latency, serial throughput, peak RSS, failure/retry rates, total cost, and cost per 1,000 assets;
- per-candidate hard-gate results and lane coverage.

It does not collapse these into a popularity score or automatically select a winner.

## Add a recorded candidate

1. Add or amend a candidate descriptor with honest verification status.
2. Pin every local model/runtime artifact by source, version, license, SHA-256, processor version, redistribution terms, and owner.
3. For a cloud API, record the exact API/model version, region, SDK or REST contract, verification date, retention terms, and pricing snapshot in the descriptor or an attached evidence record.
4. Generate exactly one observation for every item in the evaluation split.
5. Record the actual hardware profile and run mode.
6. Run `make agent-check`.
7. Review cohort-level failures instead of tuning the final-test thresholds.

Never label a synthetic or replayed result as live.

## Live-provider recordings

The evaluator still performs no network calls. It can validate an already-recorded live run only when all of these are true:

- `recording_mode` is `live` and `live_provider` is true;
- the candidate is explicitly live-capable;
- every asset has consent and an allowed redistribution license;
- a non-empty consent record is present;
- candidate, policy, and CLI cost ceilings all pass;
- the operator passes both:

```bash
python3 benchmarks/vision/scripts/evaluate.py \
  --root benchmarks/vision \
  --allow-live-provider \
  --max-live-cost-usd 1.00
```

The provider adapter that creates such a recording belongs in a separate, reviewed execution path with its own no-secret-in-arguments rules and kill switch.

## Decision boundary

A candidate may be recommended only after human review of:

- real corpus quality by cohort and language;
- model/API provenance and license;
- processor fidelity and checksums;
- privacy, region, retention, deletion, and credential scope;
- cold start, throughput, memory, image/container size, concurrency, and failure behavior;
- current price and deprecation risk;
- index/version migration requirements;
- conformance with `mb-interfaces` observation and embedding-space contracts.

The report always emits `automatic_winner: null` and `promotion_ready: false` for the checked-in synthetic evidence.
