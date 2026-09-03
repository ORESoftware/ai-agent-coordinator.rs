#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = (ROOT / "scripts/bootstrap-lambda-repositories-live.sh").read_text(encoding="utf-8")
FALLBACK = (ROOT / "scripts/gh-graphql-rate-limit-fallback.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/live-lambda-repository-bootstrap-graphql-recovery.yml").read_text(encoding="utf-8")

required_executor_calls = [
    'gh api user --jq .login',
    'gh api user --jq .id',
    'gh api "user/memberships/orgs/$org" --jq .state',
    'gh api "user/memberships/orgs/$org" --jq .role',
    'gh api "repos/$1"',
    'gh api --method POST "orgs/$org/repos" --input -',
    'gh api --method PATCH "repos/$full" --input -',
    'gh api "repos/$full/pulls?state=open&head=$org:$branch&per_page=20"',
    'gh api --method POST "repos/$full/pulls" --input -',
    'gh api "repos/$full/commits/$head/check-runs?per_page=100"',
    'gh api "repos/$full/pulls/$pr_number"',
    'gh api --method PUT "repos/$full/pulls/$pr_number/merge" --input -',
    'gh api "repos/$full/contents/lambda-repository.json?ref=main" --jq .content',
]
for call in required_executor_calls:
    if call not in EXECUTOR:
        raise SystemExit(f"executor API call changed without fallback review: {call}")

required_fallback_contracts = [
    "GET:user)",
    "GET:user/memberships/orgs/*)",
    "GET:repos/*/pulls\\?*)",
    "GET:repos/*/commits/*/check-runs\\?*)",
    "GET:repos/*/pulls/*)",
    "GET:repos/*/contents/*)",
    "GET:repos/*)",
    "POST:orgs/*/repos)",
    "PATCH:repos/*)",
    "POST:repos/*/pulls)",
    "PUT:repos/*/pulls/*/merge)",
    "createRepository(input:",
    "createPullRequest(input:",
    "mergePullRequest(input:",
    "statusCheckRollup",
    "expectedHeadOid",
    "viewerCanCreateRepositories",
    "viewerCanAdminister",
    "initialize_empty_repository",
]
for contract in required_fallback_contracts:
    if contract not in FALLBACK:
        raise SystemExit(f"missing GraphQL fallback contract: {contract}")

for forbidden in [
    "--admin",
    "--force",
    "git push -f",
    "git reset",
    "git rebase",
    "merge_method:REBASE",
]:
    if forbidden in FALLBACK:
        raise SystemExit(f"forbidden recovery behavior: {forbidden}")

credential = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|lin_api_[A-Za-z0-9]{20,}|"
    r"CHAT_BRIDGE_TOKEN|BEGIN [A-Z ]*PRIVATE KEY"
)
for label, text in [("fallback", FALLBACK), ("recovery workflow", WORKFLOW)]:
    if credential.search(text):
        raise SystemExit(f"credential-shaped content in {label}")

if 'REAL_GH="$real_gh"' not in WORKFLOW or 'PATH="$shim_dir:$PATH"' not in WORKFLOW:
    raise SystemExit("recovery workflow does not install the reviewed gh compatibility layer")
if "github.event_name == 'push' && github.ref == 'refs/heads/main'" not in WORKFLOW:
    raise SystemExit("recovery mutation is not restricted to merged main")
if "RSA-OAEP" not in WORKFLOW and "rsa_padding_mode:oaep" not in WORKFLOW:
    raise SystemExit("run-bound encrypted credential relay is missing")

print("GraphQL rate-limit fallback contract validated")
