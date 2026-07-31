# mb-interfaces

Versioned, language-neutral contracts for MemeBank APIs, durable events, manifests, storage capabilities, search, synchronization, and image-intelligence provenance.

> **Promotion status:** this directory is a reviewed bootstrap blueprint while `github.com/memebank/mb-interfaces` is blocked on organization authorization. It is not the canonical package until its exact history is promoted to that repository and verified on GitHub. Do not publish packages from this staging path.

## Contract ownership

Source-of-truth files live under:

- `openapi/v1/openapi.json` for authenticated HTTP operations;
- `schemas/v1/` for durable JSON contracts;
- `fixtures/v1/` for sanitized compatibility fixtures;
- `compatibility/v1-baseline.json` for the initial non-breaking-change floor.

Generated Rust, Dart, TypeScript, Go, or other language models are outputs. They must never become an independent hand-edited source of truth.

## Safety invariants

- Durable manifests contain stable IDs and content digests, never provider credentials, presigned URLs, or durable object-store URLs.
- AI-derived metadata always records provider, model, immutable model revision or checksum, recipe, source derivative, and observation status.
- Native visual embeddings remain distinct from embeddings derived from OCR, captions, or tags.
- Pagination cursors and synchronization cursors are separate concepts.
- Provider capabilities are explicit; adapters must not pretend Google Drive, OneDrive, Apple document providers, S3, and R2 share identical semantics.
- OCR and generated captions are untrusted data and cannot become tool instructions or authorization decisions.

## Agent check

A clean checkout needs only Python 3.12 or newer for the contract gate:

```bash
make agent-check
```

The check parses JSON with duplicate-key rejection, resolves every local `$ref`, validates fixtures, checks the compatibility floor, scans durable contracts for secret-bearing fields, and emits a deterministic machine-readable report.

To write the report explicitly:

```bash
python3 scripts/validate_contracts.py --report .artifacts/contract-report.json
```

## Versioning

The package follows semantic versioning:

- patch: clarifications and additive fixture coverage that do not change accepted data;
- minor: backward-compatible optional fields, enum extensions explicitly designed as open sets, or new operations;
- major: required-field removal/addition, incompatible type or semantic changes, identifier reinterpretation, or cursor/version resets.

Any intentional breaking change must introduce a new versioned schema/API surface instead of weakening the committed v1 baseline.

## Promotion evidence

Promotion to `memebank/mb-interfaces` is complete only when Linear contains the canonical repository URL and ID, first `main` commit SHA, reviewed pull request, exact merged head SHA, green contract workflow, and package/version evidence. A generated archive or this staging directory alone is not delivery.
