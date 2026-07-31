# memebank-e2e

Cross-platform and provider-conformance qualification for MemeBank.

> **Promotion status:** this directory is a reviewed bootstrap blueprint while `github.com/memebank/memebank-e2e` is blocked on organization authorization. It is not canonical delivery, a provider recommendation, or a release artifact until the exact reviewed history is promoted and verified in that repository.

The first implemented suite is the reproducible OCR, vision, caption, and retrieval benchmark tracked by DEN-1011.

## Run the complete gate

```bash
make agent-check
```

The checked-in benchmark:

- performs no cloud or model calls;
- reads no credentials;
- uses generated, redistribution-safe SVG fixtures;
- evaluates recorded results only;
- rejects changed fixture digests;
- distinguishes local and remote lanes;
- computes OCR, region, tag, caption, retrieval, latency, memory, failure, and cost metrics;
- requires explicit opt-in, consent evidence, and a hard cost cap before accepting a live-provider recording;
- never selects or promotes a winner automatically.

Synthetic candidate results prove the harness and its safety gates. They are deliberately not evidence that any named dependency or provider should be selected.

See `benchmarks/vision/README.md` for the result contract and promotion criteria.
