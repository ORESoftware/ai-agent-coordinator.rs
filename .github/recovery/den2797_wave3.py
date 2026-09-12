#!/usr/bin/env python3
"""Apply the reviewed DEN-2797 wave-3 cumulative backfill update."""

from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new)


def insert_before_tuple_close(
    text: str,
    *,
    tuple_name: str,
    next_anchor: str,
    addition: str,
) -> str:
    start = text.index(f"{tuple_name} = (")
    end = text.index(next_anchor, start)
    close = text.rfind("\n)", start, end)
    if close < 0:
        raise RuntimeError(f"{tuple_name}: tuple close not found")
    return text[:close] + "\n" + addition.rstrip() + text[close:]


def update_generator() -> None:
    path = Path("scripts/build_artifact_recovery_backfill.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'GENERATED_AT = "2026-08-08T04:16:00Z"',
        'GENERATED_AT = "2026-08-08T04:52:00Z"',
        "generated timestamp",
    )
    text = replace_once(
        text,
        '''MISSING = (
    ("apostille-me", "apme-mcp-server.rs"), ("apostille-me", "apme-e2e"),
    ("embedded-alerts", "eal-mcp-server.rs"), ("embedded-alerts", "eal-e2e"),
    ("evento-globolo", "evgl-mcp-server.rs"), ("evento-globolo", "evgl-e2e"),
    ("hacker-house-medellin", "hhm-mcp-server.rs"), ("hacker-house-medellin", "hhm-e2e"),
)''',
        '''MISSING = (
    ("apostille-me", "apme-e2e"),
    ("embedded-alerts", "eal-e2e"),
    ("evento-globolo", "evgl-e2e"),
    ("hacker-house-medellin", "hhm-e2e"),
)''',
        "missing repository set",
    )

    recovered_additions = '''    {
        "owner": "ORESoftware", "repository": "project-registry", "visibility": "public",
        "origin": "file_00000000f48c820ea276a1c39727b1c0", "observed_at": "2026-08-08T04:44:00Z",
        "artifact": "apply-project-registry-minor-only.py",
        "digest": "5eedf2b100d337b1db00a8bf38eb5aa68806e96ff308709f36921bb27bac306a",
        "locator": "library:file_00000000f48c820ea276a1c39727b1c0", "kind": "file",
        "paths": ["apply-project-registry-minor-only.py"], "artifact_commit": None,
        "main": "a7f8eada86a0b3c7cfcd94010b633ba716ef0f12", "pr": 33,
        "pr_head": "agent/den-2745-minor-only-correction", "pr_state": "merged",
        "note": "DEN-2745 minor-only policy is merged; follow-up PR #34 also landed tracked branch-tip advancement.",
    },
    {
        "owner": "ORESoftware", "repository": "k8s-cluster", "visibility": "public",
        "origin": "file_00000000f48c820ea276a1c39727b1c0", "observed_at": "2026-08-08T04:49:00Z",
        "artifact": "apply-project-registry-minor-only.py",
        "digest": "5eedf2b100d337b1db00a8bf38eb5aa68806e96ff308709f36921bb27bac306a",
        "locator": "library:file_00000000f48c820ea276a1c39727b1c0#k8s-policy", "kind": "file",
        "paths": ["apply-project-registry-minor-only.py"], "artifact_commit": None,
        "main": "e4134cce559732dbad2527e874f7e673cf6dca1f", "pr": 1101,
        "pr_head": "agent/den-2745-branch-tip-policy-v2", "pr_state": "merged",
        "note": "DEN-2745 branch-tip policy landed through PR #1101; default-branch tracked-tip rollout also landed through #1103.",
    },'''
    text = insert_before_tuple_close(
        text,
        tuple_name="RECOVERED",
        next_anchor="\nFLUTTER = {",
        addition=recovered_additions,
    )

    open_additions = '''    {
        "owner": "apostille-me", "repository": "apme-mcp-server.rs", "visibility": "public",
        "origin": ZED_ORIGIN, "observed_at": "2026-08-08T04:46:07Z",
        "artifact": "zed-fleet-reconcile.sh", "digest": ZED_DIGEST,
        "locator": "library:file_000000009c1c822fbca21330abaa93d2",
        "main": "7ab3198e78ce30849c22584ac9afb5007d3ed2ab",
        "branch": "agent/den-2285-recovery-review",
        "branch_sha": "a255bcbafc7a3e6f69b05fd6502f66b43ffb4a43", "pr": 1,
        "note": "Local creation succeeded; green draft PR supplies review evidence while apme-e2e remains absent.",
    },
    {
        "owner": "embedded-alerts", "repository": "eal-mcp-server.rs", "visibility": "public",
        "origin": ZED_ORIGIN, "observed_at": "2026-08-08T04:46:20Z",
        "artifact": "zed-fleet-reconcile.sh", "digest": ZED_DIGEST,
        "locator": "library:file_000000009c1c822fbca21330abaa93d2",
        "main": "05384988c517b19e49022d32945a11c3393de0e4",
        "branch": "agent/den-2287-recovery-review",
        "branch_sha": "0ba444300f16327fc07fe4e2f419031eb87cfad4", "pr": 1,
        "note": "Local creation succeeded; green draft PR supplies review evidence while eal-e2e remains absent.",
    },
    {
        "owner": "evento-globolo", "repository": "evgl-mcp-server.rs", "visibility": "public",
        "origin": ZED_ORIGIN, "observed_at": "2026-08-08T04:46:35Z",
        "artifact": "zed-fleet-reconcile.sh", "digest": ZED_DIGEST,
        "locator": "library:file_000000009c1c822fbca21330abaa93d2",
        "main": "6e69697b525ce696f98a8e74b35c888487240796",
        "branch": "agent/den-2290-recovery-review",
        "branch_sha": "6a2cc7e239243b2a4d580953c576294e5bf4c557", "pr": 1,
        "note": "Local creation succeeded; green draft PR supplies review evidence while evgl-e2e remains absent.",
    },
    {
        "owner": "hacker-house-medellin", "repository": "hhm-mcp-server.rs", "visibility": "public",
        "origin": ZED_ORIGIN, "observed_at": "2026-08-08T04:46:53Z",
        "artifact": "zed-fleet-reconcile.sh", "digest": ZED_DIGEST,
        "locator": "library:file_000000009c1c822fbca21330abaa93d2",
        "main": "9e8850ff7b48b41f46ff62af31ca4d423e5aa7d5",
        "branch": "agent/den-2293-recovery-review",
        "branch_sha": "6eb3b797a73f7d7bd68de3f249793de0caf836cb", "pr": 1,
        "note": "Local creation succeeded; green draft PR supplies review evidence while hhm-e2e remains absent.",
    },'''
    text = insert_before_tuple_close(
        text,
        tuple_name="OPEN_RECOVERIES",
        next_anchor="\n\ndef origin(",
        addition=open_additions,
    )
    text = replace_once(
        text,
        '        "note": "A current semantic successor is already merged; do not open a duplicate recovery branch.",',
        '        "note": value.get("note", "A current semantic successor is already merged; do not open a duplicate recovery branch."),',
        "recovered note override",
    )
    text = replace_once(
        text,
        '        "batch": {"id": "accessible-library-backfill-2026-08-08-wave-2", "complete": False,',
        '        "batch": {"id": "accessible-library-backfill-2026-08-08-wave-3", "complete": False,',
        "batch id",
    )
    path.write_text(text, encoding="utf-8")


def update_tests() -> None:
    path = Path("tests/test_artifact_recovery_ledger.py")
    text = path.read_text(encoding="utf-8")
    for old, new in {
        'self.assertEqual(ledger["summary"]["entries"], 20)': 'self.assertEqual(ledger["summary"]["entries"], 22)',
        'self.assertEqual(ledger["summary"]["complete"], 12)': 'self.assertEqual(ledger["summary"]["complete"], 18)',
        'self.assertEqual(ledger["summary"]["actionable"], 8)': 'self.assertEqual(ledger["summary"]["actionable"], 4)',
        'self.assertEqual(queue["summary"], {"items": 8, "create_repository": 8, "recover_local": 0})': 'self.assertEqual(queue["summary"], {"items": 4, "create_repository": 4, "recover_local": 0})',
        'self.assertEqual(ledger["last_batch"]["available"], 20)': 'self.assertEqual(ledger["last_batch"]["available"], 22)',
    }.items():
        text = replace_once(text, old, new, f"test assertion {old}")
    text = replace_once(
        text,
        '''        expected = {
            "apostille-me/apme-mcp-server.rs", "apostille-me/apme-e2e",
            "embedded-alerts/eal-mcp-server.rs", "embedded-alerts/eal-e2e",
            "evento-globolo/evgl-mcp-server.rs", "evento-globolo/evgl-e2e",
            "hacker-house-medellin/hhm-mcp-server.rs", "hacker-house-medellin/hhm-e2e",
        }''',
        '''        expected = {
            "apostille-me/apme-e2e",
            "embedded-alerts/eal-e2e",
            "evento-globolo/evgl-e2e",
            "hacker-house-medellin/hhm-e2e",
        }''',
        "expected queue set",
    )
    insertion = '''    def test_third_wave_reuses_created_mcp_prs_and_den_2745_merges(self) -> None:
        ledger, queue = self.reconcile(self.fixture())
        entries = {
            entry["observation"]["target"]["identity"]: entry
            for entry in ledger["entries"].values()
        }
        mcp_identities = {
            "apostille-me/apme-mcp-server.rs",
            "embedded-alerts/eal-mcp-server.rs",
            "evento-globolo/evgl-mcp-server.rs",
            "hacker-house-medellin/hhm-mcp-server.rs",
        }
        for identity in mcp_identities:
            with self.subTest(identity=identity):
                entry = entries[identity]
                self.assertEqual(entry["classification"]["status"], "complete")
                pull = entry["observation"]["remote"]["pull_requests"][0]
                self.assertEqual(pull["number"], 1)
                self.assertTrue(pull["draft"])
                self.assertEqual(pull["state"], "open")
        for identity, pull_number in {
            "oresoftware/project-registry": 33,
            "oresoftware/k8s-cluster": 1101,
        }.items():
            with self.subTest(identity=identity):
                entry = entries[identity]
                self.assertEqual(entry["classification"]["status"], "complete")
                self.assertTrue(any(
                    link.endswith(f"/pull/{pull_number}")
                    for link in entry["evidence_links"]
                ))
        queued = {
            f"{item['owner'].lower()}/{item['repository'].lower()}"
            for item in queue["items"]
        }
        self.assertTrue(mcp_identities.isdisjoint(queued))

'''
    text = replace_once(
        text,
        "    def test_identical_rerun_is_byte_stable_and_does_not_increment_attempts(self) -> None:\n",
        insertion + "    def test_identical_rerun_is_byte_stable_and_does_not_increment_attempts(self) -> None:\n",
        "third-wave test insertion",
    )
    path.write_text(text, encoding="utf-8")


def update_workflow() -> None:
    path = Path(".github/workflows/nightly-artifact-recovery.yml")
    text = path.read_text(encoding="utf-8")
    for old, new in {
        "assert ledger['summary']['entries'] == 20": "assert ledger['summary']['entries'] == 22",
        "assert ledger['summary']['complete'] == 12": "assert ledger['summary']['complete'] == 18",
        "assert ledger['summary']['actionable'] == 8": "assert ledger['summary']['actionable'] == 4",
        "assert queue['summary'] == {'items': 8, 'create_repository': 8, 'recover_local': 0}": "assert queue['summary'] == {'items': 4, 'create_repository': 4, 'recover_local': 0}",
    }.items():
        text = replace_once(text, old, new, f"workflow assertion {old}")
    path.write_text(text, encoding="utf-8")


def update_documentation() -> None:
    Path("docs/artifact-recovery-initial-backfill.md").write_text(
        '''# DEN-2797 cumulative artifact-recovery backfill

Generated from three public-safe, bounded accessible-Library waves on 2026-08-07 and 2026-08-08 and refreshed against current GitHub repository, branch, commit, pull-request, workflow, and Linear evidence.

## Result

| Metric | Count |
|---|---:|
| Ledger rows | 22 |
| Verified complete | 18 |
| Actionable | 4 |
| Missing repositories | 4 |
| Existing-repository artifacts already recovered, superseded, merged, or on verified draft PRs | 14 |
| Blocked/ambiguous | 0 |

The deterministic generator is not a transcript export. It retains stable source IDs, owner/repository identities, artifact digests and locators, bounded GitHub evidence, and classification inputs. It contains no prompt body, hidden reasoning, email address, token, private key, or credential assignment.

## First and second waves

The first two waves retain exact evidence for four sealed private repositories, Canonical and Slack artifacts, merged DEN-602 work, and green current-main draft PRs for DEN-99 and DEN-569. Existing remote delivery suppresses duplicate recovery even when product review remains open.

## Third bounded wave

Fresh cross-thread reads changed the local queue materially:

- `apostille-me/apme-mcp-server.rs` now exists publicly and has green draft PR #1 at `a255bcbafc7a3e6f69b05fd6502f66b43ffb4a43`;
- `embedded-alerts/eal-mcp-server.rs` now exists publicly and has green draft PR #1 at `0ba444300f16327fc07fe4e2f419031eb87cfad4`;
- `evento-globolo/evgl-mcp-server.rs` now exists publicly and has green draft PR #1 at `6a2cc7e239243b2a4d580953c576294e5bf4c557`;
- `hacker-house-medellin/hhm-mcp-server.rs` now exists publicly and has green draft PR #1 at `6eb3b797a73f7d7bd68de3f249793de0caf836cb`.

Each PR preserves the published implementation, removes stale post-publication instructions, pins and bounds CI, records immutable recovery evidence, and leaves real Zed lock/frozen-install work open. The sibling E2E repositories remain separate creation targets.

The DEN-2745 local transformer is not replayed. Its intended policy is already merged through `ORESoftware/project-registry#33`, branch-tip follow-up #34, `ORESoftware/k8s-cluster#1101`, and tracked-tip rollout #1103. Current repository reads and merged PR evidence mark both repository targets complete.

## Recovery handoff

Only four genuinely missing repositories now emit deterministic rows for local Codex task `019fd526-f34d-7f72-94fa-2da6185f2d74`:

1. `apostille-me/apme-e2e`
2. `embedded-alerts/eal-e2e`
3. `evento-globolo/evgl-e2e`
4. `hacker-house-medellin/hhm-e2e`

Every row requires a fresh GitHub read, intended-content secret scan, create-only behavior, exact path staging, non-force push, draft PR, and post-write evidence read. Later batches must use the persisted cursor and revisit materially changed sources.

See `docs/nightly-artifact-recovery.md` for the complete contract and evidence inventory.
''',
        encoding="utf-8",
    )


def main() -> None:
    update_generator()
    update_tests()
    update_workflow()
    update_documentation()


if __name__ == "__main__":
    main()
