from __future__ import annotations

from pathlib import Path

publisher = Path("scripts/publish_shared_auth_client_artifacts.sh")
text = publisher.read_text()


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    '''owner_api() {
  GH_TOKEN="$owner_token" gh api "$@"
}

cleanup_sensitive_comments() {
''',
    '''owner_api() {
  GH_TOKEN="$owner_token" gh api "$@"
}

wait_for_source_pr_head() {
  local expected="$1"
  local observed=''
  for _ in $(seq 1 30); do
    observed="$(owner_api "repos/${TARGET_REPOSITORY}/pulls/${SOURCE_PR}" --jq '.head.sha' 2>/dev/null || true)"
    [[ "$observed" == "$expected" ]] && return 0
    sleep 2
  done
  return 1
}

comment_source_pr_best_effort() {
  local body="$1"
  GH_TOKEN="$owner_token" gh pr comment "$SOURCE_PR" \
    --repo "$TARGET_REPOSITORY" \
    --body "$body" >/dev/null 2>&1 || true
}

cleanup_sensitive_comments() {
''',
    "source PR convergence helpers",
)

replace_once(
    '''  git -C "$work/source" push origin "HEAD:${source_head_ref}" \\
    >"$work/logs/rust-format-push.log" 2>&1
  test "$(owner_api "repos/${TARGET_REPOSITORY}/pulls/${SOURCE_PR}" --jq '.head.sha')" = "$source_sha"
  GH_TOKEN="$owner_token" gh pr comment "$SOURCE_PR" \\
    --repo "$TARGET_REPOSITORY" \\
    --body "Applied canonical Rust formatting through the reviewed DEN-2422 artifact broker. New exact head: \\`${source_sha}\\`. Artifact validation continues from this revision." \\
    >/dev/null
''',
    '''  git -C "$work/source" push origin "HEAD:${source_head_ref}" \\
    >"$work/logs/rust-format-push.log" 2>&1
  wait_for_source_pr_head "$source_sha"
  comment_source_pr_best_effort \\
    "Applied canonical Rust formatting through the reviewed DEN-2422 artifact broker. New exact head: \\`${source_sha}\\`. Artifact validation continues from this revision."
''',
    "Rust push convergence",
)

replace_once(
    '''stage='gleam-dependencies'
(cd "$work/source/clients/gleam" && gleam deps download) >"$work/logs/gleam.log" 2>&1

stage='gleam-format'
(cd "$work/source/clients/gleam" && gleam format --check) >>"$work/logs/gleam.log" 2>&1

stage='gleam-check'
(cd "$work/source/clients/gleam" && gleam check) >>"$work/logs/gleam.log" 2>&1

stage='gleam-tests'
(cd "$work/source/clients/gleam" && gleam test) >>"$work/logs/gleam.log" 2>&1

stage='gleam-package'
''',
    '''stage='gleam-format-apply'
(cd "$work/source/clients/gleam" && gleam format) >"$work/logs/gleam.log" 2>&1

stage='gleam-format-scope'
mapfile -t gleam_formatted_paths < <(git -C "$work/source" diff --name-only)
if ((${#gleam_formatted_paths[@]} > 0)); then
  test "$release_channel" = 'rc'
  test "${#gleam_formatted_paths[@]}" -le 100
  for formatted_path in "${gleam_formatted_paths[@]}"; do
    [[ "$formatted_path" == *.gleam ]]
  done
  git -C "$work/source" diff --check

  stage='gleam-format-commit'
  git -C "$work/source" config user.name 'ORESoftware artifact automation'
  git -C "$work/source" config user.email 'bot@oresoftware.dev'
  git -C "$work/source" add -u -- ':(glob)**/*.gleam'
  git -C "$work/source" diff --quiet
  ! git -C "$work/source" diff --cached --quiet
  git -C "$work/source" commit -m 'style: apply canonical Gleam formatting' \\
    >"$work/logs/gleam-format-commit.log" 2>&1
  source_sha="$(git -C "$work/source" rev-parse HEAD)"
  [[ "$source_sha" =~ ^[0-9a-f]{40}$ ]]
  source_epoch="$(git -C "$work/source" show -s --format=%ct HEAD)"

  stage='gleam-format-push'
  git -C "$work/source" push origin "HEAD:${source_head_ref}" \\
    >"$work/logs/gleam-format-push.log" 2>&1
  wait_for_source_pr_head "$source_sha"
  comment_source_pr_best_effort \\
    "Applied canonical Gleam formatting through the reviewed DEN-2422 artifact broker. New exact head: \\`${source_sha}\\`. Artifact validation continues from this revision."
fi

stage='gleam-format-check'
(cd "$work/source/clients/gleam" && gleam format --check) >>"$work/logs/gleam.log" 2>&1

stage='gleam-dependencies'
(cd "$work/source/clients/gleam" && gleam deps download) >>"$work/logs/gleam.log" 2>&1

stage='gleam-check'
(cd "$work/source/clients/gleam" && gleam check) >>"$work/logs/gleam.log" 2>&1

stage='gleam-tests'
(cd "$work/source/clients/gleam" && gleam test) >>"$work/logs/gleam.log" 2>&1

stage='gleam-package'
''',
    "Gleam RC-only formatting remediation",
)

publisher.write_text(text)

for temporary in [
    Path("scripts/patch_shared_auth_gleam_autoformat.py"),
    Path(".github/workflows/patch-shared-auth-gleam-autoformat.yml"),
]:
    if temporary.exists():
        temporary.unlink()
