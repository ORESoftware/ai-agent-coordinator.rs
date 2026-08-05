#!/usr/bin/env bash
set -euo pipefail

umask 077

: "${PR_NUMBER:?PR_NUMBER is required}"
: "${CONTROL_REPOSITORY:?CONTROL_REPOSITORY is required}"
: "${MARKER_PATH:?MARKER_PATH is required}"
: "${TARGET_REPOSITORY:?TARGET_REPOSITORY is required}"
: "${SOURCE_PR:?SOURCE_PR is required}"
: "${WORKFLOW_TOKEN:?WORKFLOW_TOKEN is required}"

readonly expected_owner='ORESoftware'
readonly expected_title='DO NOT MERGE: publish Shared Auth client artifacts v2 with encrypted credential'
readonly expected_branch_prefix='agent/shared-auth-client-artifact-carrier-v2-'
readonly expected_protocol='rsa-oaep-sha256-v2'
readonly workflow_token="$WORKFLOW_TOKEN"

stage='carrier-validation'
owner_token=''
challenge_id=''
response_id=''
work="$(mktemp -d "$RUNNER_TEMP/shared-auth-client-artifact-broker-v2.XXXXXX")"
mkdir -p "$work/source" "$work/artifacts" "$work/logs"

workflow_api() {
  GH_TOKEN="$workflow_token" gh api "$@"
}

owner_api() {
  GH_TOKEN="$owner_token" gh api "$@"
}

cleanup_sensitive_comments() {
  if [[ -n "$response_id" ]]; then
    workflow_api --method DELETE \
      "repos/${CONTROL_REPOSITORY}/issues/comments/${response_id}" >/dev/null 2>&1 || true
  fi
  if [[ -n "$challenge_id" ]]; then
    workflow_api --method DELETE \
      "repos/${CONTROL_REPOSITORY}/issues/comments/${challenge_id}" >/dev/null 2>&1 || true
  fi
}

cleanup() {
  unset owner_token OWNER_TOKEN GH_TOKEN GITHUB_TOKEN WORKFLOW_TOKEN GIT_ASKPASS GIT_TERMINAL_PROMPT
  rm -rf "$work"
}

report_failure() {
  local status=$?
  trap - ERR
  GH_TOKEN="$workflow_token" gh pr comment "$PR_NUMBER" \
    --repo "$CONTROL_REPOSITORY" \
    --body "Shared Auth client artifact publication failed at bounded stage \`${stage}\`. Private source, compiler output, and the owner credential were not uploaded to this public repository." \
    >/dev/null 2>&1 || true
  cleanup_sensitive_comments
  GH_TOKEN="$workflow_token" gh pr close "$PR_NUMBER" \
    --repo "$CONTROL_REPOSITORY" >/dev/null 2>&1 || true
  exit "$status"
}

trap cleanup EXIT
trap report_failure ERR

for command in gh jq openssl tar python3 cargo dart gleam npm; do
  command -v "$command" >/dev/null
 done

pull="$(workflow_api "repos/${CONTROL_REPOSITORY}/pulls/${PR_NUMBER}")"
head_sha="$(jq -er '.head.sha' <<<"$pull")"
main_sha="$(workflow_api "repos/${CONTROL_REPOSITORY}/git/ref/heads/main" --jq '.object.sha')"
[[ "$head_sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$main_sha" =~ ^[0-9a-f]{40}$ ]]

jq -e \
  --arg owner "$expected_owner" \
  --arg title "$expected_title" \
  --arg repository "$CONTROL_REPOSITORY" \
  --arg branch_prefix "$expected_branch_prefix" '
  .draft == true and
  .user.login == $owner and
  .base.ref == "main" and
  .head.repo.full_name == $repository and
  (.head.ref | startswith($branch_prefix)) and
  .title == $title and
  .commits == 1 and
  .changed_files == 1 and
  .additions == 7 and
  .deletions == 0
' <<<"$pull" >/dev/null

commits="$(workflow_api "repos/${CONTROL_REPOSITORY}/pulls/${PR_NUMBER}/commits?per_page=100")"
test "$(jq 'length' <<<"$commits")" -eq 1
parent_sha="$(jq -er '.[0].parents[0].sha' <<<"$commits")"
[[ "$parent_sha" =~ ^[0-9a-f]{40}$ ]]

files="$(workflow_api "repos/${CONTROL_REPOSITORY}/pulls/${PR_NUMBER}/files?per_page=100")"
jq -e --arg path "$MARKER_PATH" '
  length == 1 and
  .[0].filename == $path and
  .[0].status == "added" and
  .[0].changes == 7
' <<<"$files" >/dev/null

encoded="$(
  workflow_api \
    "repos/${CONTROL_REPOSITORY}/contents/${MARKER_PATH}?ref=${head_sha}" \
    --jq '.content'
)"
marker="$(printf '%s' "$encoded" | tr -d '\n' | base64 --decode)"
test "$(wc -l <<<"$marker" | tr -d ' ')" -eq 7

target_repository="$(sed -n 's/^target-repository=//p' <<<"$marker")"
source_pr="$(sed -n 's/^source-pr=//p' <<<"$marker")"
source_sha="$(sed -n 's/^source-sha=//p' <<<"$marker")"
requested_source_sha="$source_sha"
release_tag="$(sed -n 's/^release-tag=//p' <<<"$marker")"
release_channel="$(sed -n 's/^release-channel=//p' <<<"$marker")"
trusted_main="$(sed -n 's/^trusted-main=//p' <<<"$marker")"
protocol="$(sed -n 's/^protocol=//p' <<<"$marker")"

test "$target_repository" = "$TARGET_REPOSITORY"
test "$source_pr" = "$SOURCE_PR"
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]]
test "$trusted_main" = "$parent_sha"
test "$protocol" = "$expected_protocol"
case "$release_channel:$release_tag" in
  rc:v0.1.0-rc.1|final:v0.1.0) ;;
  *) exit 1 ;;
esac

comparison="$(workflow_api "repos/${CONTROL_REPOSITORY}/compare/${parent_sha}...${main_sha}")"
jq -e '(.status == "ahead" or .status == "identical") and .behind_by == 0' \
  <<<"$comparison" >/dev/null

stage='challenge-bootstrap'
private_key="$work/private.pem"
public_key="$work/public.pem"
ciphertext_file="$work/ciphertext.bin"
openssl genpkey -quiet -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$private_key"
chmod 600 "$private_key"
openssl pkey -in "$private_key" -pubout -out "$public_key"
nonce="$(openssl rand -hex 24)"
[[ "$nonce" =~ ^[0-9a-f]{48}$ ]]

challenge_body="$work/challenge.md"
{
  printf '<!-- shared-auth-client-artifact-v2-credential-challenge:%s -->\n' "$nonce"
  printf 'One-time RSA-OAEP-SHA256 challenge for private release `%s` at source revision `%s`. The private key exists only in this runner and is destroyed on exit. Reply with the matching encrypted ciphertext marker.\n\n' "$release_tag" "$source_sha"
  printf '%s\n' '```pem'
  cat "$public_key"
  printf '%s\n' '```'
} >"$challenge_body"
challenge_json="$(
  jq -n --rawfile body "$challenge_body" '{body:$body}' \
    | workflow_api --method POST \
      "repos/${CONTROL_REPOSITORY}/issues/${PR_NUMBER}/comments" --input -
)"
challenge_id="$(jq -er '.id | select(type == "number" and . > 0)' <<<"$challenge_json")"
response_marker="<!-- shared-auth-client-artifact-v2-credential-response:${nonce} -->"

stage='await-encrypted-response'
response_body=''
for _ in $(seq 1 240); do
  comments="$(workflow_api --paginate \
    "repos/${CONTROL_REPOSITORY}/issues/${PR_NUMBER}/comments?per_page=100" --slurp)"
  response_json="$(
    jq -c \
      --arg marker "$response_marker" \
      --arg owner "$expected_owner" \
      --argjson challenge_id "$challenge_id" '
      [
        .[][]
        | select(.id > $challenge_id)
        | select(.user.login == $owner)
        | select(.body | startswith($marker + "\n"))
      ]
      | sort_by(.id)
      | last // empty
    ' <<<"$comments"
  )"
  if [[ -n "$response_json" ]]; then
    response_id="$(jq -er '.id' <<<"$response_json")"
    response_body="$(jq -er '.body' <<<"$response_json")"
    break
  fi
  sleep 5
done

test -n "$response_body"
test "$(grep -c '^ciphertext-base64=' <<<"$response_body")" -eq 1
ciphertext="$(sed -n 's/^ciphertext-base64=//p' <<<"$response_body")"
[[ "$ciphertext" =~ ^[A-Za-z0-9+/=]+$ ]]
test "${#ciphertext}" -le 8192
printf '%s' "$ciphertext" | base64 --decode >"$ciphertext_file"
test -s "$ciphertext_file"

stage='decrypt-ciphertext'
owner_token="$(
  openssl pkeyutl -decrypt \
    -inkey "$private_key" \
    -in "$ciphertext_file" \
    -pkeyopt rsa_padding_mode:oaep \
    -pkeyopt rsa_oaep_md:sha256 \
    -pkeyopt rsa_mgf1_md:sha256 \
    2>/dev/null
)"

stage='validate-owner-token-shape'
test -n "$owner_token"
[[ "$owner_token" != *$'\n'* && "$owner_token" != *$'\r'* && "$owner_token" != *$'\t'* && "$owner_token" != *' '* ]]
[[ "$owner_token" == ghp_* || "$owner_token" == github_pat_* ]]
echo "::add-mask::$owner_token"

stage='validate-owner-login'
owner_login="$(GH_TOKEN="$owner_token" gh api user --jq '.login' 2>/dev/null)"
test "$owner_login" = "$expected_owner"

stage='validate-shared-auth-membership'
membership="$(
  GH_TOKEN="$owner_token" gh api "/user/memberships/orgs/shared-auth" \
    --jq '.role + ":" + .state' 2>/dev/null
)"
test "$membership" = 'admin:active'

stage='validate-source-revision'
commit_json="$(owner_api "repos/${TARGET_REPOSITORY}/commits/${source_sha}")"
test "$(jq -er '.sha' <<<"$commit_json")" = "$source_sha"
source_date="$(jq -er '.commit.committer.date' <<<"$commit_json")"
source_epoch="$(date --date="$source_date" +%s)"
case "$release_channel" in
  rc)
    source_pull="$(owner_api "repos/${TARGET_REPOSITORY}/pulls/${SOURCE_PR}")"
    test "$(jq -er '.state' <<<"$source_pull")" = 'open'
    test "$(jq -er '.head.sha' <<<"$source_pull")" = "$source_sha"
    source_head_repo="$(jq -er '.head.repo.full_name' <<<"$source_pull")"
    source_head_ref="$(jq -er '.head.ref' <<<"$source_pull")"
    test "$source_head_repo" = "$TARGET_REPOSITORY"
    ;;
  final)
    target_main="$(owner_api "repos/${TARGET_REPOSITORY}/git/ref/heads/main" --jq '.object.sha')"
    test "$target_main" = "$source_sha"
    source_head_repo="$TARGET_REPOSITORY"
    source_head_ref='main'
    ;;
esac

stage='clone-exact-private-source'
askpass="$work/git-askpass.sh"
cat >"$askpass" <<'SH'
#!/bin/sh
case "$1" in
  *Username*) printf '%s\n' 'x-access-token' ;;
  *) printf '%s\n' "$OWNER_TOKEN" ;;
esac
SH
chmod 700 "$askpass"
export OWNER_TOKEN="$owner_token"
export GIT_ASKPASS="$askpass"
export GIT_TERMINAL_PROMPT=0
rm -rf "$work/source"
(
  git clone \
    --filter=blob:none \
    --no-tags \
    --single-branch \
    --branch "$source_head_ref" \
    "https://github.com/${source_head_repo}.git" \
    "$work/source"
) >"$work/logs/clone.log" 2>&1
test "$(git -C "$work/source" rev-parse HEAD)" = "$source_sha"
for required in \
  Cargo.toml \
  clients/rust/Cargo.toml \
  clients/ts/package.json \
  clients/dart/pubspec.yaml \
  clients/gleam/gleam.toml; do
  test -f "$work/source/$required"
done

stage='rust-format-apply'
(cd "$work/source" && cargo fmt --all) >"$work/logs/rust.log" 2>&1

stage='rust-format-scope'
mapfile -t formatted_paths < <(git -C "$work/source" diff --name-only)
if ((${#formatted_paths[@]} > 0)); then
  test "$release_channel" = 'rc'
  test "${#formatted_paths[@]}" -le 100
  for formatted_path in "${formatted_paths[@]}"; do
    [[ "$formatted_path" == *.rs ]]
  done
  git -C "$work/source" diff --check

  stage='rust-format-commit'
  git -C "$work/source" config user.name 'ORESoftware artifact automation'
  git -C "$work/source" config user.email 'bot@oresoftware.dev'
  git -C "$work/source" add -u -- ':(glob)**/*.rs'
  git -C "$work/source" diff --quiet
  ! git -C "$work/source" diff --cached --quiet
  git -C "$work/source" commit -m 'style: apply canonical Rust formatting' \
    >"$work/logs/rust-format-commit.log" 2>&1
  source_sha="$(git -C "$work/source" rev-parse HEAD)"
  [[ "$source_sha" =~ ^[0-9a-f]{40}$ ]]
  source_epoch="$(git -C "$work/source" show -s --format=%ct HEAD)"

  stage='rust-format-push'
  git -C "$work/source" push origin "HEAD:${source_head_ref}" \
    >"$work/logs/rust-format-push.log" 2>&1
  test "$(owner_api "repos/${TARGET_REPOSITORY}/pulls/${SOURCE_PR}" --jq '.head.sha')" = "$source_sha"
  GH_TOKEN="$owner_token" gh pr comment "$SOURCE_PR" \
    --repo "$TARGET_REPOSITORY" \
    --body "Applied canonical Rust formatting through the reviewed DEN-2422 artifact broker. New exact head: \`${source_sha}\`. Artifact validation continues from this revision." \
    >/dev/null
fi

stage='rust-format-check'
(cd "$work/source" && cargo fmt --all --check) >>"$work/logs/rust.log" 2>&1

stage='rust-clippy'
(cd "$work/source" && cargo clippy --workspace --all-targets --all-features -- -D warnings) \
  >>"$work/logs/rust.log" 2>&1

stage='rust-tests'
(cd "$work/source" && cargo test --workspace --all-features --exclude shared-auth-client-wasm) \
  >>"$work/logs/rust.log" 2>&1

stage='rust-package'
(cd "$work/source" && cargo package \
  --manifest-path clients/rust/Cargo.toml \
  --allow-dirty \
  --no-verify \
  --no-metadata) >>"$work/logs/rust.log" 2>&1

stage='rust-package-locate'
rust_crate="$(find "$work/source/target" "$work/source/clients/rust/target" \
  -type f -path '*/package/shared-auth-client-0.1.0.crate' -print -quit 2>/dev/null || true)"
test -n "$rust_crate"
install -m 0644 "$rust_crate" "$work/artifacts/shared-auth-client-rust-0.1.0.crate"

stage='typescript-install'
(cd "$work/source/clients/ts" && npm install --ignore-scripts --no-audit --no-fund) \
  >"$work/logs/typescript.log" 2>&1

stage='typescript-check'
(cd "$work/source/clients/ts" && npm run check) >>"$work/logs/typescript.log" 2>&1

stage='typescript-tests'
(cd "$work/source/clients/ts" && npm test) >>"$work/logs/typescript.log" 2>&1

stage='typescript-package-check'
(cd "$work/source/clients/ts" && npm run package:check) >>"$work/logs/typescript.log" 2>&1

stage='typescript-pack'
(cd "$work/source/clients/ts" && npm pack --ignore-scripts --pack-destination "$work/artifacts") \
  >>"$work/logs/typescript.log" 2>&1

stage='typescript-package-locate'
packed="$(find "$work/artifacts" -maxdepth 1 -type f -name '*.tgz' -print -quit)"
test -n "$packed"
mv "$packed" "$work/artifacts/shared-auth-client-typescript-0.1.0.tgz"

stage='dart-dependencies'
(cd "$work/source/clients/dart" && dart pub get) >"$work/logs/dart.log" 2>&1

stage='dart-analysis'
(cd "$work/source/clients/dart" && dart analyze) >>"$work/logs/dart.log" 2>&1

stage='dart-tests'
(cd "$work/source/clients/dart" && dart test) >>"$work/logs/dart.log" 2>&1

stage='dart-package'
(
  cd "$work/source/clients/dart"
  files=(pubspec.yaml lib)
  for optional in README.md LICENSE LICENSE.md CHANGELOG.md; do
    [[ -e "$optional" ]] && files+=("$optional")
  done
  tar --sort=name --mtime="@${source_epoch}" --owner=0 --group=0 --numeric-owner \
    -czf "$work/artifacts/shared_auth_client-dart-0.1.0.tar.gz" "${files[@]}"
) >>"$work/logs/dart.log" 2>&1

stage='gleam-dependencies'
(cd "$work/source/clients/gleam" && gleam deps download) >"$work/logs/gleam.log" 2>&1

stage='gleam-format'
(cd "$work/source/clients/gleam" && gleam format --check) >>"$work/logs/gleam.log" 2>&1

stage='gleam-check'
(cd "$work/source/clients/gleam" && gleam check) >>"$work/logs/gleam.log" 2>&1

stage='gleam-tests'
(cd "$work/source/clients/gleam" && gleam test) >>"$work/logs/gleam.log" 2>&1

stage='gleam-package'
(
  cd "$work/source/clients/gleam"
  files=(gleam.toml src)
  for optional in manifest.toml README.md LICENSE LICENSE.md CHANGELOG.md; do
    [[ -e "$optional" ]] && files+=("$optional")
  done
  tar --sort=name --mtime="@${source_epoch}" --owner=0 --group=0 --numeric-owner \
    -czf "$work/artifacts/shared_auth_client-gleam-0.1.0.tar.gz" "${files[@]}"
) >>"$work/logs/gleam.log" 2>&1

stage='generate-private-provenance'
CONTROL_SHA="$main_sha" \
CONTROL_RUN_ID="$GITHUB_RUN_ID" \
CONTROL_RUN_ATTEMPT="$GITHUB_RUN_ATTEMPT" \
REQUESTED_SOURCE_SHA="$requested_source_sha" \
SOURCE_SHA="$source_sha" \
RELEASE_TAG="$release_tag" \
RELEASE_CHANNEL="$release_channel" \
TARGET_REPOSITORY="$TARGET_REPOSITORY" \
ARTIFACT_DIR="$work/artifacts" \
python3 - <<'PY'
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

root = Path(os.environ["ARTIFACT_DIR"])
packages = sorted(path for path in root.iterdir() if path.is_file())
if len(packages) != 4:
    raise SystemExit(f"expected four package artifacts, found {[p.name for p in packages]}")
entries = []
checksums = []
for path in packages:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    checksums.append(f"{digest}  {path.name}")
    entries.append({"name": path.name, "bytes": path.stat().st_size, "sha256": digest})
(root / "SHA256SUMS").write_text("\n".join(checksums) + "\n")
provenance = {
    "schema_version": 2,
    "source_repository": os.environ["TARGET_REPOSITORY"],
    "requested_source_commit": os.environ["REQUESTED_SOURCE_SHA"],
    "source_commit": os.environ["SOURCE_SHA"],
    "release_tag": os.environ["RELEASE_TAG"],
    "release_channel": os.environ["RELEASE_CHANNEL"],
    "control_repository": "ORESoftware/ai-agent-coordinator.rs",
    "control_commit": os.environ["CONTROL_SHA"],
    "control_run_id": os.environ["CONTROL_RUN_ID"],
    "control_run_attempt": os.environ["CONTROL_RUN_ATTEMPT"],
    "packages": entries,
    "validation": {
        "rust": ["fmt", "clippy", "native tests", "cargo package"],
        "typescript": ["strict check", "node tests", "package surface", "npm pack"],
        "dart": ["analyze", "tests", "deterministic source archive"],
        "gleam": ["format", "check", "tests", "deterministic source archive"],
    },
    "public_log_policy": "private source and compiler/test output suppressed; ephemeral logs destroyed on exit",
}
(root / "PROVENANCE.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
PY

if [[ "$release_channel" == 'rc' ]]; then
  release_heading='Shared Auth clients v0.1.0 release candidate 1'
  release_flag=(--prerelease)
  channel_text='This prerelease validates the exact pull-request head before canonical merge publication.'
else
  release_heading='Shared Auth clients v0.1.0'
  release_flag=()
  channel_text='This is the canonical merge-revision package release.'
fi

cat >"$work/release-notes.md" <<EOF
# ${release_heading}

Revision-scoped package artifacts for Rust, TypeScript, Dart, and Gleam.

- Source repository: \`${TARGET_REPOSITORY}\`
- Exact source commit: \`${source_sha}\`
- Packaging PR: https://github.com/shared-auth/shared-auth-clients/pull/35
- Linear: https://linear.app/denman/issue/DEN-2422/shared-auth-clients-publish-revision-scoped-rust-typescript-dart-and
- Trusted broker run: https://github.com/${CONTROL_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}

The release contains four package files plus \`SHA256SUMS\` and \`PROVENANCE.json\`.

\`\`\`bash
sha256sum --check SHA256SUMS
\`\`\`

${channel_text} The private source, compiler logs, and owner credential were never uploaded to the public control repository.
EOF

stage='publish-private-release'
if GH_TOKEN="$owner_token" gh release view "$release_tag" \
  --repo "$TARGET_REPOSITORY" >/dev/null 2>&1; then
  existing_sha="$(owner_api "repos/${TARGET_REPOSITORY}/git/ref/tags/${release_tag}" --jq '.object.sha')"
  test "$existing_sha" = "$source_sha"
  GH_TOKEN="$owner_token" gh release delete "$release_tag" \
    --repo "$TARGET_REPOSITORY" --cleanup-tag --yes >/dev/null
fi

GH_TOKEN="$owner_token" gh release create "$release_tag" \
  --repo "$TARGET_REPOSITORY" \
  --target "$source_sha" \
  --title "$release_heading" \
  --notes-file "$work/release-notes.md" \
  "${release_flag[@]}" \
  "$work/artifacts"/* >/dev/null

stage='verify-private-release'
release_json="$(owner_api "repos/${TARGET_REPOSITORY}/releases/tags/${release_tag}")"
release_url="$(jq -er '.html_url' <<<"$release_json")"
test "$(jq '.assets | length' <<<"$release_json")" -eq 6
jq -e '
  [.assets[].name] | sort == [
    "PROVENANCE.json",
    "SHA256SUMS",
    "shared-auth-client-rust-0.1.0.crate",
    "shared-auth-client-typescript-0.1.0.tgz",
    "shared_auth_client-dart-0.1.0.tar.gz",
    "shared_auth_client-gleam-0.1.0.tar.gz"
  ]
' <<<"$release_json" >/dev/null
tag_sha="$(owner_api "repos/${TARGET_REPOSITORY}/git/ref/tags/${release_tag}" --jq '.object.sha')"
test "$tag_sha" = "$source_sha"

stage='record-success'
success_body="Published and verified private release [\`${release_tag}\`](${release_url}) for exact source commit \`${source_sha}\`. The release contains four package artifacts, \`SHA256SUMS\`, and \`PROVENANCE.json\`. Public compiler/test output was suppressed and private workspace data was destroyed on runner exit."
GH_TOKEN="$workflow_token" gh pr comment "$PR_NUMBER" \
  --repo "$CONTROL_REPOSITORY" --body "$success_body" >/dev/null
cleanup_sensitive_comments
GH_TOKEN="$workflow_token" gh pr close "$PR_NUMBER" \
  --repo "$CONTROL_REPOSITORY" >/dev/null

{
  echo '## Private Shared Auth client release published'
  echo
  echo "- Release: $release_url"
  echo "- Tag: \`$release_tag\`"
  echo "- Source commit: \`$source_sha\`"
  echo '- Assets: four packages, SHA256SUMS, PROVENANCE.json'
  echo '- Public source/compiler output: none'
} >>"$GITHUB_STEP_SUMMARY"
