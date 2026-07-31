# Promote the `memebank-e2e` vision benchmark

**Tracking:** DEN-1011, DEN-1030, DEN-1010, DEN-1005, and DEN-1043

The staged `repository-blueprints/memebank/memebank-e2e` tree is reviewed source, not canonical delivery. Promote its exact history only after `github.com/memebank/memebank-e2e` exists and the connected GitHub App can read and write that repository.

## Required evidence

1. Canonical repository ID, URL, reviewed visibility, and initialized `main`.
2. Source coordinator commit SHA containing the blueprint.
3. A focused promotion branch and PR containing only the intended `memebank-e2e` tree.
4. Exact merged head SHA and green `Vision benchmark` workflow.
5. Uploaded deterministic report artifact and its SHA-256.
6. Explicit statement that checked-in candidate results are synthetic.
7. Follow-up corpus issue for real consented fixtures, provider recordings, and current citations.
8. Confirmation that no private data or secret material was introduced.

## History and conflicts

Preserve reviewed source history or record a traceable subtree import. Do not use an untracked archive as delivery evidence. If the target repository has concurrent work, merge semantically: retain compatible corpus provenance, result contracts, budget controls, tests, workflows, and agent instructions from both sides.

## Promotion does not select providers

Merging the harness proves evaluator conformance only. DEN-1011 remains open until real evidence covers at least one local/privacy-preserving lane and one cloud lane for each critical capability where feasible, with current model/API provenance and operational measurements.
