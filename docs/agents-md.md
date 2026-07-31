# Hierarchical `agents.md` contract

This repository hosts a portable, read-only validator for the lowercase hierarchical `agents.md` convention tracked by DEN-292 and uses it for the coordinator rollout tracked by DEN-305.

## Local use

```bash
python3 scripts/validate_agents_md.py --repo-root . --start-dir src
python3 scripts/validate_agents_md.py --repo-root . --start-dir src --json
python3 -m unittest -v scripts/test_validate_agents_md.py
```

The JSON report includes the canonical file digest, headings, discovered root-to-leaf instruction paths, scanned-file count, and validation errors. Save reports outside the repository when a before/after comparison is needed; the validator never rewrites instruction files.

## Contract

A conforming repository has:

- one regular lowercase root `agents.md`;
- the exact sentence `avoid git rebase in favor of git merge.`;
- no contradictory guidance that permits or prefers rebasing;
- `.claude/CLAUDE.md`, `.gemini/GEMINI.md`, and `.openai/AGENTS.md` containing only the canonical relative pointer, or exact `../agents.md` symlinks;
- root-to-leaf ancestor discovery from resolved `$PWD`, without sibling scans;
- inode/path deduplication, unreadable-file reporting, and symlink-cycle failure;
- a repository-wide unresolved-conflict-marker scan that excludes only `.git`.

The unit suite includes positive coverage and negative fixtures for missing files, broken pointers, duplicated tool instructions, contradictory rebase guidance, nested hierarchy ordering, unreadable instructions, symlink cycles, and conflict markers.

## Reusable GitHub Action

This repository exposes a composite action at:

```yaml
- uses: ORESoftware/ai-agent-coordinator.rs/.github/actions/validate-agents-md@<immutable-commit>
  with:
    repo-root: ${{ github.workspace }}
    start-dir: ${{ github.workspace }}/src
```

Consumers must replace `<immutable-commit>` with a reviewed commit SHA. Do not use a moving branch or tag for governance enforcement.

The action uses the validator bundled at the same immutable revision. It performs no network requests, writes, or repository mutation.

## Tool pointers

Regular pointer files must contain exactly:

```text
Canonical instructions: `../agents.md`. Also load every ancestor `agents.md` from filesystem root to `$PWD`, in root-to-leaf order.
```

Exact relative symlinks to `../agents.md` are also accepted. Any duplicated canonical content or alternate target fails validation.
