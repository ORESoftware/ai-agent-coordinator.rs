#!/usr/bin/env bash
set -Eeuo pipefail

required=(
  GITHUB_REPOSITORY
  GH_CONFIG_DIR
  PR_URL
  PR_AUTHOR
  PR_HEAD_REPO
  PR_HEAD_REF
  PR_HEAD_SHA
  COMMENT_TOKEN
  EXPECTED_LOGIN
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "::error::Missing required environment variable: $name"
    exit 1
  fi
done

readonly EXPECTED_REPOSITORY="ORESoftware/ai-agent-coordinator.rs"
readonly EXPECTED_BRANCH="agent/hypesiege-streempilot-live-publish-20260731"
readonly CODE_CONTEXT_PREFIX="hypesiege-streempilot/gh-device-auth"

if [[ "$GITHUB_REPOSITORY" != "$EXPECTED_REPOSITORY" ]]; then
  echo "::error::Unexpected bootstrap repository: $GITHUB_REPOSITORY"
  exit 1
fi
if [[ "$PR_AUTHOR" != "$EXPECTED_LOGIN" ]]; then
  echo "::error::Unexpected pull request author: $PR_AUTHOR"
  exit 1
fi
if [[ "$PR_HEAD_REPO" != "$GITHUB_REPOSITORY" ]]; then
  echo "::error::Bootstrap pull request must originate in the same private repository."
  exit 1
fi
if [[ "$PR_HEAD_REF" != "$EXPECTED_BRANCH" ]]; then
  echo "::error::Unexpected bootstrap branch: $PR_HEAD_REF"
  exit 1
fi
mkdir -p "$GH_CONFIG_DIR"
auth_log="$RUNNER_TEMP/gh-auth-device.log"
work_root="$RUNNER_TEMP/hypesiege-streempilot-bootstrap"
: >"$auth_log"
auth_pid=""
code_context="$CODE_CONTEXT_PREFIX"

cleanup() {
  if [[ -n "$auth_pid" ]] && kill -0 "$auth_pid" 2>/dev/null; then
    kill "$auth_pid" 2>/dev/null || true
    wait "$auth_pid" 2>/dev/null || true
  fi
  rm -f "$auth_log"
  rm -rf "$GH_CONFIG_DIR" "$work_root"
}
trap cleanup EXIT

publish_status() {
  local state="$1"
  local description="$2"
  local context="${3:-$CODE_CONTEXT_PREFIX}"
  GH_TOKEN="$COMMENT_TOKEN" gh api --method POST \
    "repos/$GITHUB_REPOSITORY/statuses/$PR_HEAD_SHA" \
    -f state="$state" \
    -f context="$context" \
    -f description="$description" \
    -f target_url="$PR_URL" >/dev/null
}

(
  env -u GH_TOKEN -u GITHUB_TOKEN \
    GH_PROMPT_DISABLED=1 \
    NO_COLOR=1 \
    BROWSER=/bin/false \
    gh auth login \
      --hostname github.com \
      --git-protocol https \
      --web \
      --scopes repo,read:org,workflow \
      --insecure-storage
) >"$auth_log" 2>&1 &
auth_pid=$!

code=""
for _ in $(seq 1 30); do
  code="$(tr -d '\r' <"$auth_log" | grep -Eo '[A-Z0-9]{4}-[A-Z0-9]{4}' | head -n1 || true)"
  if [[ -n "$code" ]]; then
    break
  fi
  if ! kill -0 "$auth_pid" 2>/dev/null; then
    break
  fi
  sleep 1
done

if [[ -z "$code" ]]; then
  if kill -0 "$auth_pid" 2>/dev/null; then
    kill "$auth_pid" 2>/dev/null || true
  fi
  wait "$auth_pid" 2>/dev/null || true
  auth_pid=""
  publish_status failure "GitHub CLI did not emit a device code" || true
  echo "::error::GitHub CLI did not emit a device authorization code."
  sed -E 's/[A-Z0-9]{4}-[A-Z0-9]{4}/[REDACTED-CODE]/g' "$auth_log" >&2 || true
  exit 1
fi

code_context="$CODE_CONTEXT_PREFIX/$code"
publish_status pending "Authorize at github.com/login/device" "$code_context"
echo "::notice::Device authorization code published in the private commit status."

set +e
wait "$auth_pid"
auth_rc=$?
set -e
auth_pid=""

if [[ "$auth_rc" -ne 0 ]]; then
  publish_status failure "GitHub CLI device authorization failed or expired" "$code_context" || true
  sed -E 's/[A-Z0-9]{4}-[A-Z0-9]{4}/[REDACTED-CODE]/g' "$auth_log" >&2 || true
  exit "$auth_rc"
fi
rm -f "$auth_log"

actual_login="$(env -u GH_TOKEN -u GITHUB_TOKEN gh api user --jq .login)"
if [[ "$actual_login" != "$EXPECTED_LOGIN" ]]; then
  publish_status failure "Authenticated as $actual_login, not $EXPECTED_LOGIN" "$code_context" || true
  echo "::error::Expected login $EXPECTED_LOGIN but authenticated as $actual_login."
  exit 1
fi

auth_headers="$(env -u GH_TOKEN -u GITHUB_TOKEN gh api --include user 2>/dev/null | tr -d '\r')"
for required_scope in repo read:org workflow; do
  if ! grep -Eqi "^x-oauth-scopes:.*(^|,|[[:space:]])${required_scope}([,[:space:]]|$)" <<<"$auth_headers"; then
    publish_status failure "Authorized token is missing $required_scope scope" "$code_context" || true
    echo "::error::GitHub device authorization did not grant required scope: $required_scope"
    exit 1
  fi
done
unset auth_headers

for organization in hypesiege StreemPilot; do
  role="$(env -u GH_TOKEN -u GITHUB_TOKEN gh api \
    "user/memberships/orgs/$organization" --jq .role 2>/dev/null || true)"
  state="$(env -u GH_TOKEN -u GITHUB_TOKEN gh api \
    "user/memberships/orgs/$organization" --jq .state 2>/dev/null || true)"
  if [[ "$state" != "active" || "$role" != "admin" ]]; then
    publish_status failure "$EXPECTED_LOGIN lacks active admin access to $organization" "$code_context" || true
    echo "::error::$EXPECTED_LOGIN requires active admin membership in $organization (state=${state:-unavailable}, role=${role:-unavailable})."
    exit 1
  fi
done

publish_status success "Authorized as $EXPECTED_LOGIN for both organizations" "$code_context"
publish_status pending "Creating and pushing the sealed 32-repository fleet"

sudo mkdir -p /mnt/data
sudo chown "$(id -u):$(id -g)" /mnt/data
mkdir -p "$work_root"
cat repository-fleets/hypesiege-streempilot/generator.py.gz.b64.part-* \
  | base64 --decode \
  | gzip --decompress >"$work_root/generate.py"
python3 -m py_compile "$work_root/generate.py"
python3 "$work_root/generate.py"

publisher="/mnt/data/hypesiege-streempilot-fleet/publish.py"
python3 - <<'PY'
from pathlib import Path
path = Path('/mnt/data/hypesiege-streempilot-fleet/publish.py')
lines = path.read_text(encoding='utf-8').splitlines()
expected = [
    "        askpass.write_text('#!/bin/sh",
    "case \"$1\" in *Username*) echo x-access-token;; *) echo \"$GITHUB_REPOSITORY_ADMIN_TOKEN\";; esac",
    "')",
]
if lines[70:73] != expected:
    raise SystemExit(f'unexpected publisher transport defect: {lines[70:73]!r}')
lines[70:73] = [
    '        askpass.write_text(',
    "            '#!/bin/sh\\ncase \"$1\" in *Username*) echo x-access-token;; *) echo \"$GITHUB_REPOSITORY_ADMIN_TOKEN\";; esac\\n'",
    '        )',
]
path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
PY
python3 -m py_compile "$publisher"

token="$(env -u GH_TOKEN -u GITHUB_TOKEN gh auth token)"
if [[ -z "$token" ]]; then
  publish_status failure "GitHub CLI did not return an authenticated token" || true
  exit 1
fi
echo "::add-mask::$token"
export GITHUB_REPOSITORY_ADMIN_TOKEN="$token"
export GIT_TERMINAL_PROMPT=0

python3 "$publisher" --execute --org hypesiege
python3 "$publisher" --execute --org streempilot

python3 - <<'PY'
import json
import os
import pathlib
import subprocess

root = pathlib.Path('/mnt/data/hypesiege-streempilot-fleet')
manifest = json.loads((root / 'MANIFEST.json').read_text(encoding='utf-8'))
errors = []
for record in manifest['repositories']:
    process = subprocess.run(
        ['gh', 'api', f"repos/{record['full_name']}/commits/main", '--jq', '.sha'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={k: v for k, v in os.environ.items() if k not in {'GH_TOKEN', 'GITHUB_TOKEN'}},
    )
    actual = process.stdout.strip()
    if process.returncode != 0:
        errors.append(f"{record['full_name']}: verification failed")
    elif actual != record['commit']:
        errors.append(f"{record['full_name']}: {actual} != {record['commit']}")
if errors:
    raise SystemExit('\n'.join(errors))
print(f"verified {len(manifest['repositories'])}/{len(manifest['repositories'])} remote main heads")
PY

unset GITHUB_REPOSITORY_ADMIN_TOKEN token
publish_status success "Created and verified all 32 repository main heads"
