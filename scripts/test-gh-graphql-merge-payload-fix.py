#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts/gh-graphql-merge-payload-fix.sh"
BASE = ROOT / "scripts/gh-graphql-rate-limit-fallback.sh"
EXPECTED_HEAD = "1" * 40
EXPECTED_MERGE = "2" * 40

source = WRAPPER.read_text(encoding="utf-8")
required = [
    "pullRequest{merged mergedAt mergeCommit{oid}}",
    ".data.mergePullRequest.pullRequest.mergeCommit.oid",
    "expectedHeadOid:$expected",
    "mergeMethod:SQUASH",
    "isDraft mergeable state",
]
for token in required:
    if token not in source:
        raise SystemExit(f"missing merge-payload contract: {token}")
if "pullRequest{merged mergedAt} mergeCommit{oid}" in source:
    raise SystemExit("obsolete payload-level mergeCommit selection remains")
if ".data.mergePullRequest.mergeCommit.oid" in source:
    raise SystemExit("obsolete payload-level mergeCommit JSON path remains")
for forbidden in ["--admin", "--force", "git push -f", "git reset", "git rebase", "|| true"]:
    if forbidden in source:
        raise SystemExit(f"forbidden merge behavior: {forbidden}")

with tempfile.TemporaryDirectory(prefix="merge-payload-fix-") as directory:
    root = Path(directory)
    calls = root / "calls.jsonl"
    fake_gh = root / "gh-real"
    pull_response = json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "id": "PR_test",
                        "headRefOid": EXPECTED_HEAD,
                        "isDraft": False,
                        "mergeable": "MERGEABLE",
                        "state": "OPEN",
                    }
                }
            }
        }
    )
    merge_response = json.dumps(
        {
            "data": {
                "mergePullRequest": {
                    "pullRequest": {
                        "merged": True,
                        "mergedAt": "2026-09-03T00:00:00Z",
                        "mergeCommit": {"oid": EXPECTED_MERGE},
                    }
                }
            }
        }
    )
    fake_gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        f"open({str(calls)!r}, 'a', encoding='utf-8').write(json.dumps(args) + '\\n')\n"
        "query = next((arg.split('=', 1)[1] for arg in args if arg.startswith('query=')), '')\n"
        "if 'mutation($pull:ID!' in query:\n"
        f"    print({merge_response!r})\n"
        "elif 'query($owner:String!' in query and 'pullRequest(number:$number)' in query:\n"
        f"    print({pull_response!r})\n"
        "else:\n"
        "    raise SystemExit('unexpected GraphQL query: ' + query)\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    env = os.environ.copy()
    env["REAL_GH"] = str(fake_gh)
    env["BASE_GRAPHQL_FALLBACK"] = str(BASE)
    payload = json.dumps({"sha": EXPECTED_HEAD, "merge_method": "squash"})
    completed = subprocess.run(
        [
            "bash",
            str(WRAPPER),
            "api",
            "--method",
            "PUT",
            "repos/example/example-lambdas/pulls/1/merge",
            "--input",
            "-",
        ],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    result = json.loads(completed.stdout)
    if result != {
        "merged": True,
        "sha": EXPECTED_MERGE,
        "message": "Pull Request successfully merged",
    }:
        raise SystemExit(f"unexpected merge result: {result!r}")

    stale = subprocess.run(
        [
            "bash",
            str(WRAPPER),
            "api",
            "--method",
            "PUT",
            "repos/example/example-lambdas/pulls/1/merge",
            "--input",
            "-",
        ],
        input=json.dumps({"sha": "3" * 40}),
        text=True,
        capture_output=True,
        env=env,
    )
    if stale.returncode == 0 or "head drift" not in stale.stderr:
        raise SystemExit("stale expected head did not fail closed")

    delegation = root / "delegate"
    delegation.write_text(
        "#!/usr/bin/env bash\nprintf 'delegated:%s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    delegation.chmod(0o755)
    delegated_env = env.copy()
    delegated_env["BASE_GRAPHQL_FALLBACK"] = str(delegation)
    delegated = subprocess.run(
        ["bash", str(WRAPPER), "api", "repos/example/example-lambdas"],
        text=True,
        capture_output=True,
        check=True,
        env=delegated_env,
    )
    if delegated.stdout.strip() != "delegated:api repos/example/example-lambdas":
        raise SystemExit(f"non-merge command did not delegate exactly: {delegated.stdout!r}")

print("GraphQL merge-payload fix validated")
