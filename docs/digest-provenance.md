# Opportunity and research digest provenance

## Goal

Weekly opportunity and engineering-research digests must be reproducible review queues, not unverified summaries or side-effecting agents. This contract stores bounded metadata, source identities, evidence hashes, explicit interpretation, and deterministic rankings while keeping applications, outreach, dependency changes, and all other external mutations disabled.

## Common envelope

Both digest kinds use `evidence-digest/v1` and require:

- one fixed collection window and timezone;
- an immutable policy SHA-256;
- a generated timestamp inside the window;
- strict kind-specific records;
- canonical HTTPS URLs without user information, tracking queries, or fragments;
- deterministic canonical JSON hashing; and
- false safety flags for credentials, personal data, applications, outreach, and external mutations.

Inputs are UTF-8, duplicate-key rejecting, size bounded, and exact-schema validated.

## Engineering opportunity queue

Opportunity records are accepted only from an employer career site, official applicant-tracking system, or employer API. Each record binds:

- canonical company and requisition identities;
- title, locations, remote policy, employment type, and role family;
- verified, posted, and optional expiration timestamps;
- optional compensation with source URL;
- a bounded fit score with must-have/preferred matches, gaps, and unknowns;
- the content and fit-evidence SHA-256 values; and
- related Fiducia opportunity identifiers.

Duplicate requisitions, duplicate URLs, stale or future evidence, unsupported roles, inconsistent compensation, and unranked records fail closed.

A company with any relevant Fiducia grant, credit, accelerator, sponsorship, conference, open-source, cloud, or AI-program identifier must have **exactly two or three** engineering roles in the queue. All records for that company must use one consistent Fiducia opportunity set. The program opportunity and the engineering roles remain separate records and separate consequential-action boundaries.

Ranks are derived from fit score descending, with company and requisition identity as deterministic tie-breakers.

## Engineering research shortlist

Research records admit only primary-source classes: peer-reviewed papers, official preprints, standards, specifications, official documentation, official release notes, and repository releases.

Every shortlist entry contains:

- authors or maintainers;
- a stable identifier and canonical URL;
- publication, update, and retrieval dates;
- source and summary SHA-256 values;
- supported portfolio themes;
- source-bound factual claims, each with a fragment hash;
- generated interpretation kept separate from source facts;
- an explicit uncertainty list;
- optional proposed experiments; and
- bounded relevance, novelty, evidence-quality, implementation-cost, and falsifiability scores.

Withdrawn or non-active sources cannot enter the shortlist. Supersession links must resolve inside the bounded digest and must be acyclic. Rankings follow a deterministic weighted composite with item ID as the final tie-breaker.

## Commands

```sh
python3 -m py_compile \
  scripts/digest_provenance.py \
  scripts/validate_digest_provenance.py \
  scripts/test_digest_provenance.py
python3 -m unittest -v scripts/test_digest_provenance.py

python3 scripts/validate_digest_provenance.py \
  fixtures/opportunity-digest-v1.json \
  --expect-kind opportunity --json

python3 scripts/validate_digest_provenance.py \
  fixtures/research-digest-v1.json \
  --expect-kind research --json
```

The checked-in fixtures use synthetic organizations, sources, roles, papers, URLs, and content-free hashes. They are conformance evidence only.

## Safety boundary

No record authorizes an application, recruiter message, account creation, résumé change, personal-data submission, code modification, dependency update, deployment, or other external mutation. A human-reviewed workflow must separately authorize any consequential action.

Tracking: `DEN-826`, `DEN-828`, `DEN-256`, and `DEN-812`.
