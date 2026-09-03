#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRIGGER_PATH = ROOT / ".github/repository-bootstrap-trigger/msgint-lambdas-exact-head-proof-20260903.txt"
WORKFLOW_PATH = ROOT / ".github/workflows/msgint-lambda-external-exact-head-proof.yml"

EXPECTED_TRIGGER = """issue=183
repository=messaging-intel/msgint-lambdas
pull_request=1
branch=feat/issue-183-rust-lambda-foundation
head_sha=96a2279e57b127b1662c840256044bd87b9acacc
mode=external-exact-head-proof
armed=true
one_time=true
"""

trigger = TRIGGER_PATH.read_text(encoding="utf-8")
workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
if trigger != EXPECTED_TRIGGER:
    raise SystemExit("external-proof trigger differs from the reviewed exact target")

required = [
    "github.event_name == 'push' && github.ref == 'refs/heads/main'",
    "TARGET_REPOSITORY: messaging-intel/msgint-lambdas",
    "TARGET_PULL_REQUEST: \"1\"",
    "TARGET_BRANCH: feat/issue-183-rust-lambda-foundation",
    "TARGET_HEAD_SHA: 96a2279e57b127b1662c840256044bd87b9acacc",
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    "rsa_padding_mode:oaep",
    "rsa_oaep_md:sha256",
    "git -c http.extraheader=\"AUTHORIZATION: basic $auth_header\" clone",
    "cargo +1.88.0 fmt --all -- --check",
    "cargo +1.88.0 clippy --locked --all-targets --all-features -- -D warnings",
    "cargo +1.88.0 test --locked --all-targets --all-features",
    "cargo +1.88.0 check --locked --target x86_64-unknown-linux-gnu --bins",
    "cargo +1.88.0 check --locked --target aarch64-unknown-linux-gnu --bins",
    "python3 scripts/verify_repository.py",
    "git rev-parse HEAD^{tree}",
    "Destroy run-bound credential material",
]
for item in required:
    if item not in workflow:
        raise SystemExit(f"external-proof workflow is missing required contract: {item}")

for forbidden in [
    "mergePullRequest",
    "/pulls/1/merge",
    "/statuses/",
    "--admin",
    "git push -f",
    "git reset --hard",
    "git rebase",
    "visibility: PUBLIC",
    "change-visibility",
]:
    if forbidden in workflow:
        raise SystemExit(f"external-proof workflow contains forbidden behavior: {forbidden}")

credential = re.compile(
    r"gh" r"[pousr]_[A-Za-z0-9]{20,}|lin_" r"api_[A-Za-z0-9]{20,}|"
    r"CHAT_" r"BRIDGE_TOKEN|BEGIN " r"[A-Z ]*PRIVATE KEY"
)
if credential.search(trigger):
    raise SystemExit("credential-shaped content in trigger")

conflict = re.compile(r"^(<{7}|={7}|>{7})", re.MULTILINE)
for label, text in (("trigger", trigger), ("workflow", workflow)):
    if conflict.search(text):
        raise SystemExit(f"unresolved merge marker in {label}")

print("Messaging Intel external exact-head proof contract validated")
