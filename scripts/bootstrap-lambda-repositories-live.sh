#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${MANIFEST:-$ROOT/repository-fleets/lambda-repositories-2026-09-03.tsv}"
EXPECTED_MANIFEST_SHA256="d5025256feb8834a258199052f1b261358a0ee8cecce4b266b91eab00d7e3df3"
MODE="${1:-validate}"
TRACKING_REPOSITORY="ORESoftware/ai-agent-coordinator.rs"
TRACKING_ISSUE=183
FEATURE_BRANCH="feat/issue-183-rust-lambda-foundation"
WORK_ROOT="${WORK_ROOT:-$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/lambda-repositories.XXXXXX") }"
RECEIPT="${RECEIPT:-$ROOT/lambda-rollout-receipt.json}"
RESULTS="$WORK_ROOT/results.tsv"
PENDING="$WORK_ROOT/pending.tsv"

EXPECTED_REPOSITORIES=(
  shared-auth/shared-auth-lambdas
  scintilla-run/scintilla-lambdas
  fiducia-cloud/fiducia-lambdas
  opto-sync/opto-sync-lambdas
  quaestor-ledger/quaestor-lambdas
  messaging-intel/msgint-lambdas
  3FA-app/3fa-lambdas
  sonus-auris/sonus-auris-lambdas
  zed-pkg/zed-lambdas
  canonical-cloud/canonical-lambdas
)
EXPECTED_CHECKS=(
  contract
  quality
  architecture-x86_64-unknown-linux-gnu
  architecture-aarch64-unknown-linux-gnu
)

cleanup() {
  if [[ "${KEEP_WORK_ROOT:-0}" != 1 ]]; then
    rm -rf -- "$WORK_ROOT"
  fi
}
trap cleanup EXIT

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
log() { printf '[lambda-bootstrap] %s\n' "$*" >&2; }

for command in bash python3 sha256sum; do
  command -v "$command" >/dev/null 2>&1 || fail "$command is required"
done
if [[ "$MODE" == apply ]]; then
  for command in git gh jq cargo rustup base64; do
    command -v "$command" >/dev/null 2>&1 || fail "$command is required"
  done
fi
[[ -f "$MANIFEST" ]] || fail "manifest not found: $MANIFEST"
[[ "$MODE" == validate || "$MODE" == apply ]] || fail "mode must be validate or apply"

observed_manifest_sha="$(sha256sum "$MANIFEST" | awk '{print $1}')"
[[ "$observed_manifest_sha" == "$EXPECTED_MANIFEST_SHA256" ]] || \
  fail "manifest digest mismatch: $observed_manifest_sha"

mkdir -p -- "$WORK_ROOT"
printf 'repository\tcreated\tstate\tbranch\thead_sha\tpr_number\tpr_url\tmerge_sha\tdetail\n' > "$RESULTS"
printf 'repository\tcreated\tbranch\thead_sha\tpr_number\tpr_url\n' > "$PENDING"

slugify() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g'
}

package_names() {
  local repo=$1 package crate
  package="$(slugify "$repo")"
  if [[ "$package" =~ ^[0-9] ]]; then
    package="ores-$package"
  fi
  crate="${package//-/_}"
  printf '%s\t%s\n' "$package" "$crate"
}

render_repo() {
  local dir=$1 org=$2 repo=$3 lib_core=$4 orm_core=$5 description=$6
  local package crate
  IFS=$'\t' read -r package crate < <(package_names "$repo")
  mkdir -p "$dir/src/bin" "$dir/.github/workflows" "$dir/docs" "$dir/scripts"

  cat > "$dir/README.md" <<README
# $repo

$description

This repository owns independently deployable Rust Lambda entrypoints shared by multiple servers in the **$org** organization. A handler coupled to one private server route may remain in that server's \`src/lambdas\`; cross-server functions belong here.

## Release boundary

- AWS custom runtime: \`provided.al2023\`.
- Required architectures: \`x86_64\` and \`arm64\`.
- ZIP artifacts contain one executable named \`bootstrap\` at the archive root.
- OCI images contain runtime artifacts only; compilers, source trees, package caches, credentials, and decrypted configuration are forbidden.
- Domain and persistence code must come from reviewed, immutable shared-core releases or exact commit SHAs. Mutable branches are not accepted.

The initial health handler is deliberately dependency-light. Production functions arrive through focused pull requests with authorization, idempotency, bounded input, timeout, retry, telemetry, and exact-head artifact tests.
README

  cat > "$dir/AGENTS.md" <<AGENTS
# AGENTS.md

Follow the canonical policy in \`ORESoftware/my-ai/AGENTS.md\` and resolve conflicts semantically using relevant history.

Repository-specific rules:

- Keep each Lambda entrypoint independently buildable and deployable.
- Do not initialize an ORM pool, UI framework, HTTP listener, or unrelated service graph during cold start.
- Import shared domain and ORM code through immutable package coordinates or exact commit SHAs.
- Never commit credentials, plaintext environment files, participant data, tokens, or production payloads.
- Bind every artifact to the exact source commit and target architecture.
- Treat \`$lib_core\` and \`$orm_core\` as discovery candidates until their exact package and reviewed revision are pinned.
AGENTS

  cat > "$dir/Cargo.toml" <<CARGO
[package]
name = "$package"
version = "0.1.0"
edition = "2021"
rust-version = "1.88"
publish = false

[lib]
name = "$crate"
path = "src/lib.rs"

[dependencies]
lambda_runtime = "=1.3.0"
serde = { version = "=1.0.219", features = ["derive"] }
serde_json = "=1.0.140"
tokio = { version = "=1.46.1", features = ["macros", "rt-multi-thread"] }

[[bin]]
name = "health"
path = "src/bin/health.rs"

[profile.release]
codegen-units = 1
lto = "fat"
opt-level = "z"
panic = "abort"
strip = "symbols"

[lints.rust]
unsafe_code = "forbid"
CARGO

  cat > "$dir/src/lib.rs" <<'RUST'
use serde::Serialize;

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct HealthReceipt<'a> {
    pub schema_version: &'a str,
    pub status: &'a str,
}

pub const fn health_receipt() -> HealthReceipt<'static> {
    HealthReceipt {
        schema_version: "ores.lambda-health.v1",
        status: "ok",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn health_contract_is_stable() {
        let receipt = health_receipt();
        assert_eq!(receipt.schema_version, "ores.lambda-health.v1");
        assert_eq!(receipt.status, "ok");
    }
}
RUST

  cat > "$dir/src/bin/health.rs" <<RUST
use lambda_runtime::{run, service_fn, Error, LambdaEvent};
use serde_json::{json, Value};
use ${crate}::health_receipt;

async fn handler(_event: LambdaEvent<Value>) -> Result<Value, Error> {
    Ok(json!(health_receipt()))
}

#[tokio::main]
async fn main() -> Result<(), Error> {
    run(service_fn(handler)).await
}
RUST

  cat > "$dir/lambda-repository.json" <<JSON
{
  "schemaVersion": "ores.lambda-repository.v1",
  "organization": "$org",
  "repository": "$repo",
  "runtime": "provided.al2023",
  "architectures": ["x86_64", "arm64"],
  "artifactContract": {
    "zipRootExecutable": "bootstrap",
    "compilerInRuntimeArtifactAllowed": false,
    "sourceInRuntimeArtifactAllowed": false,
    "credentialsInArtifactAllowed": false,
    "exactSourceCommitRequired": true
  },
  "sharedCoreDependencies": {
    "domainCandidate": "$lib_core",
    "ormCandidate": "$orm_core",
    "resolution": "pin_exact_reviewed_package_or_commit_before_use"
  },
  "ownership": {
    "crossServerFunctions": "this_repository",
    "routeLocalFunctions": "owning_server_src_lambdas_allowed"
  }
}
JSON

  cat > "$dir/docs/architecture.md" <<ARCH
# Lambda repository architecture

## Ownership rule

Use this repository when a function is independently deployed and reused by more than one server or application. A handler coupled to one server's private route may remain under that server's \`src/lambdas\` directory.

## Shared code

Candidate domain source: \`$lib_core\`  
Candidate ORM source: \`$orm_core\`

These names are discovery candidates, not mutable dependencies. Resolve the actual package or crate and pin a reviewed release or exact commit before importing it. This repository must not copy ORM entities or reach through a monorepo checkout.

## Cold-start boundary

Parse and validate the event first. Initialize only the provider verifier, client, or ORM resources required for an accepted operation, and reuse them on warm invocations. Authentication disagreement, stale revocation evidence, invalid input, cancellation, and retries must not create duplicate durable side effects.

## Artifact boundary

Build output is architecture-specific. A ZIP has exactly one executable \`bootstrap\` at its root. OCI final stages retain only the runtime executable and required runtime libraries or certificates. Every release manifest records source SHA, target architecture, runtime, checksum, and toolchain.
ARCH

  cat > "$dir/.zpkg.toml" <<ZPKG
schema_version = 1

[package]
name = "$repo"
kind = "rust-lambda-repository"

[toolchains.rust]
version = "1.88.0"
lockfile = "Cargo.lock"

[contracts]
runtime = "provided.al2023"
architectures = ["x86_64", "arm64"]

[policies]
immutable_dependencies = true
exact_source_commit_artifacts = true
ZPKG

  cat > "$dir/.gitattributes" <<'ATTR'
* text=auto eol=lf
*.rs text eol=lf
*.toml text eol=lf
*.yml text eol=lf
*.yaml text eol=lf
*.json text eol=lf
*.md text eol=lf
ATTR

  cat > "$dir/.gitignore" <<'IGNORE'
/target
.env
.env.*
!.env.example
*.pem
*.key
*.zip
lambda-rollout-receipt.json
IGNORE

  cat > "$dir/scripts/verify_repository.py" <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    ".gitattributes",
    ".github/workflows/ci.yml",
    ".gitignore",
    ".zpkg.toml",
    "AGENTS.md",
    "Cargo.lock",
    "Cargo.toml",
    "README.md",
    "docs/architecture.md",
    "lambda-repository.json",
    "src/bin/health.rs",
    "src/lib.rs",
}
missing = sorted(path for path in REQUIRED if not (ROOT / path).is_file())
if missing:
    raise SystemExit(f"missing required files: {missing}")

metadata = json.loads((ROOT / "lambda-repository.json").read_text(encoding="utf-8"))
if metadata.get("schemaVersion") != "ores.lambda-repository.v1":
    raise SystemExit("invalid lambda repository schemaVersion")
if metadata.get("runtime") != "provided.al2023":
    raise SystemExit("runtime must be provided.al2023")
if metadata.get("architectures") != ["x86_64", "arm64"]:
    raise SystemExit("architectures must be x86_64 and arm64")
if metadata.get("artifactContract", {}).get("zipRootExecutable") != "bootstrap":
    raise SystemExit("ZIP root executable must be bootstrap")

credential = re.compile(
    r"gh" r"[pousr]_[A-Za-z0-9]{20,}|lin_" r"api_[A-Za-z0-9]{20,}|"
    r"CHAT_" r"BRIDGE_TOKEN|BEGIN " r"[A-Z ]*PRIVATE KEY"
)
conflict = re.compile(r"^(<{7}|={7}|>{7})", re.MULTILINE)
for path in ROOT.rglob("*"):
    if not path.is_file() or ".git" in path.parts or path.stat().st_size > 1_000_000:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    if credential.search(text):
        raise SystemExit(f"credential-shaped content in {path.relative_to(ROOT)}")
    if conflict.search(text):
        raise SystemExit(f"merge conflict marker in {path.relative_to(ROOT)}")
print(f"validated {metadata['organization']}/{metadata['repository']}")
PY
  chmod 0755 "$dir/scripts/verify_repository.py"

  cat > "$dir/.github/workflows/ci.yml" <<'YAML'
name: Rust Lambda CI

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: lambda-ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  contract:
    name: contract
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          persist-credentials: false
      - name: Verify repository contract
        run: python3 scripts/verify_repository.py

  quality:
    name: quality
    runs-on: ubuntu-24.04
    timeout-minutes: 25
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          persist-credentials: false
      - name: Install pinned Rust toolchain
        run: rustup toolchain install 1.88.0 --profile minimal --component clippy,rustfmt --no-self-update
      - name: Format
        run: cargo +1.88.0 fmt --all -- --check
      - name: Clippy
        run: cargo +1.88.0 clippy --locked --all-targets --all-features -- -D warnings
      - name: Test
        run: cargo +1.88.0 test --locked --all-targets --all-features

  architecture-check:
    name: architecture-${{ matrix.target }}
    runs-on: ubuntu-24.04
    timeout-minutes: 25
    strategy:
      fail-fast: false
      matrix:
        target:
          - x86_64-unknown-linux-gnu
          - aarch64-unknown-linux-gnu
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          persist-credentials: false
      - name: Install target
        run: |
          rustup toolchain install 1.88.0 --profile minimal --no-self-update
          rustup target add --toolchain 1.88.0 "${{ matrix.target }}"
      - name: Check target
        run: cargo +1.88.0 check --locked --target "${{ matrix.target }}" --bins
YAML
}

validate_manifest() {
  local index=0 org repo lib_core orm_core description full rendered
  while IFS=$'\t' read -r org repo lib_core orm_core description; do
    [[ "$org" == organization ]] && continue
    full="$org/$repo"
    [[ "$full" == "${EXPECTED_REPOSITORIES[$index]}" ]] || \
      fail "manifest entry $index differs from reviewed allowlist: $full"
    [[ "$repo" == *-lambdas ]] || fail "repository must end in -lambdas: $full"
    rendered="$WORK_ROOT/validate-$(slugify "$full")"
    render_repo "$rendered" "$org" "$repo" "$lib_core" "$orm_core" "$description"
    python3 -m py_compile "$rendered/scripts/verify_repository.py"
    touch "$rendered/Cargo.lock"
    python3 "$rendered/scripts/verify_repository.py"
    index=$((index + 1))
  done < "$MANIFEST"
  [[ "$index" -eq 10 ]] || fail "manifest must contain exactly ten repositories"
}

validate_manifest
if [[ "$MODE" == validate ]]; then
  log "validated exact ten-organization Lambda repository plan"
  exit 0
fi

[[ -n "${GH_TOKEN:-}" ]] || fail "GH_TOKEN is required in apply mode"
export GH_PROMPT_DISABLED=1 GIT_TERMINAL_PROMPT=0
operator_login="$(gh api user --jq .login)"
operator_id="$(gh api user --jq .id)"
[[ "$operator_login" == ORESoftware && "$operator_id" == 11139560 ]] || \
  fail "authenticated GitHub operator does not match reviewed identity"

while IFS=$'\t' read -r org repo _ _ _; do
  [[ "$org" == organization ]] && continue
  state="$(gh api "user/memberships/orgs/$org" --jq .state)"
  role="$(gh api "user/memberships/orgs/$org" --jq .role)"
  [[ "$state" == active && "$role" == admin ]] || \
    fail "active admin membership required for $org"
done < "$MANIFEST"

gh auth setup-git >/dev/null

repo_exists() {
  gh api "repos/$1" >/dev/null 2>&1
}

verify_initial_tree() {
  local dir=$1 full=$2 paths unexpected
  paths="$(git -C "$dir" ls-tree -r --name-only origin/main)"
  if grep -qx 'lambda-repository.json' <<<"$paths"; then
    return 2
  fi
  unexpected="$(grep -Ev '^(README\.md|LICENSE|\.gitignore|SECURITY\.md)?$' <<<"$paths" || true)"
  [[ -z "$unexpected" ]] || fail "existing repository collision in $full: $unexpected"
  return 0
}

local_validate() {
  local dir=$1 target
  cargo +1.88.0 generate-lockfile --manifest-path "$dir/Cargo.toml"
  python3 "$dir/scripts/verify_repository.py"
  cargo +1.88.0 fmt --manifest-path "$dir/Cargo.toml" --all -- --check
  cargo +1.88.0 clippy --manifest-path "$dir/Cargo.toml" --locked --all-targets --all-features -- -D warnings
  cargo +1.88.0 test --manifest-path "$dir/Cargo.toml" --locked --all-targets --all-features
  for target in x86_64-unknown-linux-gnu aarch64-unknown-linux-gnu; do
    cargo +1.88.0 check --manifest-path "$dir/Cargo.toml" --locked --target "$target" --bins
  done
}

open_or_reuse_pr() {
  local full=$1 org=$2 branch=$3 head=$4 pr_json
  pr_json="$(gh api "repos/$full/pulls?state=open&head=$org:$branch&per_page=20")"
  if [[ "$(jq 'length' <<<"$pr_json")" -gt 0 ]]; then
    jq -c '.[0]' <<<"$pr_json"
    return
  fi
  jq -n \
    --arg title 'feat(issue-183): bootstrap Rust Lambda repository' \
    --arg body $'## Summary\n\n- establish this organization-owned `*-lambdas` repository;\n- add a dependency-light Rust health Lambda;\n- enforce `provided.al2023`, x86_64, and arm64 contracts;\n- keep compilers, source, credentials, and decrypted configuration out of runtime artifacts;\n- require immutable shared-core dependencies and exact-source-SHA evidence; and\n- add conflict-marker, credential-shape, formatting, Clippy, unit, and cross-target checks.\n\n## Cold-start boundary\n\nThe health entrypoint does not initialize an ORM, UI framework, listener, or unrelated service graph. Production functions must preserve that narrow initialization boundary.\n\n## Validation\n\nThe bootstrap runner executed the same repository verifier, Rust formatting, Clippy, tests, and both architecture checks before push. Merge remains conditional on exact-head GitHub Actions success.\n\nTracking: ORESoftware/ai-agent-coordinator.rs#183' \
    --arg head "$branch" \
    '{title:$title,body:$body,head:$head,base:"main",draft:false,maintainer_can_modify:true}' | \
    gh api --method POST "repos/$full/pulls" --input -
}

while IFS=$'\t' read -r org repo lib_core orm_core description; do
  [[ "$org" == organization ]] && continue
  full="$org/$repo"
  created=false
  if ! repo_exists "$full"; then
    log "creating private repository $full"
    jq -n \
      --arg name "$repo" \
      --arg description "$description" \
      '{name:$name,description:$description,private:true,auto_init:true,has_issues:true,has_projects:false,has_wiki:false,allow_squash_merge:true,allow_merge_commit:true,allow_rebase_merge:false,delete_branch_on_merge:true}' | \
      gh api --method POST "orgs/$org/repos" --input - >/dev/null
    created=true
  else
    log "reusing existing repository $full"
  fi

  jq -n \
    --arg description "$description" \
    '{description:$description,has_issues:true,has_projects:false,has_wiki:false,allow_squash_merge:true,allow_merge_commit:true,allow_rebase_merge:false,delete_branch_on_merge:true}' | \
    gh api --method PATCH "repos/$full" --input - >/dev/null

  dir="$WORK_ROOT/$(slugify "$full")"
  rm -rf -- "$dir"
  gh repo clone "$full" "$dir" -- --quiet
  git -C "$dir" fetch --all --prune
  git -C "$dir" config user.name 'ORES Lambda Repository Bootstrap'
  git -C "$dir" config user.email 'noreply@oresoftware.com'
  git -C "$dir" switch main
  git -C "$dir" merge --ff-only origin/main

  if verify_initial_tree "$dir" "$full"; then
    :
  else
    verify_code=$?
    if [[ "$verify_code" -eq 2 ]]; then
      python3 "$dir/scripts/verify_repository.py"
      printf '%s\t%s\talready_complete\t\t\t\t\t\tverified existing Lambda repository contract on main\n' \
        "$full" "$created" >> "$RESULTS"
      continue
    fi
    exit "$verify_code"
  fi

  if git -C "$dir" ls-remote --exit-code --heads origin "$FEATURE_BRANCH" >/dev/null 2>&1; then
    git -C "$dir" switch --track -c "$FEATURE_BRANCH" "origin/$FEATURE_BRANCH"
    git -C "$dir" merge --no-edit origin/main
  else
    git -C "$dir" switch -c "$FEATURE_BRANCH" origin/main
  fi

  render_repo "$dir" "$org" "$repo" "$lib_core" "$orm_core" "$description"
  local_validate "$dir"
  git -C "$dir" add -- \
    .gitattributes .github/workflows/ci.yml .gitignore .zpkg.toml AGENTS.md \
    Cargo.lock Cargo.toml README.md docs/architecture.md lambda-repository.json \
    scripts/verify_repository.py src/bin/health.rs src/lib.rs
  if [[ -n "$(git -C "$dir" status --porcelain)" ]]; then
    git -C "$dir" commit -m 'feat(issue-183): bootstrap Rust Lambda repository'
  fi
  head="$(git -C "$dir" rev-parse HEAD)"
  git -C "$dir" push -u origin "$FEATURE_BRANCH"
  pr_json="$(open_or_reuse_pr "$full" "$org" "$FEATURE_BRANCH" "$head")"
  pr_number="$(jq -r .number <<<"$pr_json")"
  pr_url="$(jq -r .html_url <<<"$pr_json")"
  observed_head="$(jq -r .head.sha <<<"$pr_json")"
  [[ "$observed_head" == "$head" ]] || fail "PR head drift for $full"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$full" "$created" "$FEATURE_BRANCH" "$head" "$pr_number" "$pr_url" >> "$PENDING"
done < "$MANIFEST"

wait_for_checks() {
  local full=$1 head=$2 deadline now json name status conclusion count failures success
  deadline=$(( $(date +%s) + ${CHECK_TIMEOUT_SECONDS:-2700} ))
  while true; do
    now="$(date +%s)"
    (( now < deadline )) || fail "timed out waiting for checks on $full"
    json="$(gh api "repos/$full/commits/$head/check-runs?per_page=100")"
    count=0
    failures=0
    success=0
    for name in "${EXPECTED_CHECKS[@]}"; do
      status="$(jq -r --arg name "$name" '.check_runs[] | select(.name==$name) | .status' <<<"$json" | tail -n1)"
      conclusion="$(jq -r --arg name "$name" '.check_runs[] | select(.name==$name) | .conclusion // ""' <<<"$json" | tail -n1)"
      if [[ -n "$status" ]]; then
        count=$((count + 1))
      fi
      if [[ "$status" == completed && "$conclusion" == success ]]; then
        success=$((success + 1))
      elif [[ "$status" == completed && -n "$conclusion" && "$conclusion" != success ]]; then
        failures=$((failures + 1))
      fi
    done
    (( failures == 0 )) || fail "exact-head check failure on $full"
    if (( count == ${#EXPECTED_CHECKS[@]} && success == ${#EXPECTED_CHECKS[@]} )); then
      return
    fi
    sleep 10
  done
}

while IFS=$'\t' read -r full created branch head pr_number pr_url; do
  [[ "$full" == repository ]] && continue
  log "waiting for exact-head CI: $full#$pr_number"
  wait_for_checks "$full" "$head"

  for _ in $(seq 1 30); do
    pr_json="$(gh api "repos/$full/pulls/$pr_number")"
    observed_head="$(jq -r .head.sha <<<"$pr_json")"
    mergeable="$(jq -r '.mergeable // "unknown"' <<<"$pr_json")"
    draft="$(jq -r .draft <<<"$pr_json")"
    [[ "$observed_head" == "$head" ]] || fail "PR head changed before merge for $full"
    [[ "$draft" == false ]] || fail "PR unexpectedly draft for $full"
    if [[ "$mergeable" == true ]]; then
      break
    fi
    [[ "$mergeable" != false ]] || fail "PR is not mergeable for $full"
    sleep 2
  done
  [[ "$mergeable" == true ]] || fail "mergeability stayed unknown for $full"

  merge_json="$(jq -n \
    --arg sha "$head" \
    '{sha:$sha,merge_method:"squash",commit_title:"feat(issue-183): bootstrap Rust Lambda repository"}' | \
    gh api --method PUT "repos/$full/pulls/$pr_number/merge" --input -)"
  [[ "$(jq -r .merged <<<"$merge_json")" == true ]] || \
    fail "merge rejected for $full: $(jq -r .message <<<"$merge_json")"
  merge_sha="$(jq -r .sha <<<"$merge_json")"

  metadata="$(gh api "repos/$full/contents/lambda-repository.json?ref=main" --jq .content | tr -d '\n' | base64 -d)"
  expected_org="${full%%/*}"
  expected_repo="${full#*/}"
  [[ "$(jq -r .organization <<<"$metadata")" == "$expected_org" ]] || fail "main org mismatch for $full"
  [[ "$(jq -r .repository <<<"$metadata")" == "$expected_repo" ]] || fail "main repo mismatch for $full"

  printf '%s\t%s\tmerged\t%s\t%s\t%s\t%s\t%s\texact-head local and GitHub Actions gates passed; squash merged\n' \
    "$full" "$created" "$branch" "$head" "$pr_number" "$pr_url" "$merge_sha" >> "$RESULTS"
done < "$PENDING"

python3 - "$RESULTS" "$RECEIPT" "$EXPECTED_MANIFEST_SHA256" <<'PY'
import csv, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
source, destination = map(Path, sys.argv[1:3])
plan_sha = sys.argv[3]
with source.open(newline='', encoding='utf-8') as handle:
    rows = list(csv.DictReader(handle, delimiter='\t'))
completed = [row for row in rows if row['state'] in {'merged', 'already_complete'}]
organizations = {row['repository'].split('/', 1)[0].lower() for row in completed}
receipt = {
    'schemaVersion': 'ores.lambda-repository-rollout-receipt.v1',
    'generatedAt': datetime.now(timezone.utc).isoformat(),
    'planSha256': plan_sha,
    'sourceCommit': os.getenv('GITHUB_SHA'),
    'workflowRunId': os.getenv('GITHUB_RUN_ID'),
    'complete': len(completed) == 10 and len(organizations) == 10,
    'repositories': rows,
}
destination.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n', encoding='utf-8')
print(json.dumps(receipt, indent=2, sort_keys=True))
if not receipt['complete']:
    raise SystemExit('fewer than ten distinct organizations completed the rollout')
PY
