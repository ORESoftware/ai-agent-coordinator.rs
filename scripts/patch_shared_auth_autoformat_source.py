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
    "  unset owner_token GH_TOKEN GITHUB_TOKEN WORKFLOW_TOKEN\n",
    "  unset owner_token OWNER_TOKEN GH_TOKEN GITHUB_TOKEN WORKFLOW_TOKEN GIT_ASKPASS GIT_TERMINAL_PROMPT\n",
    "cleanup environment",
)

replace_once(
    '''source_sha="$(sed -n 's/^source-sha=//p' <<<"$marker")"
release_tag="$(sed -n 's/^release-tag=//p' <<<"$marker")"
''',
    '''source_sha="$(sed -n 's/^source-sha=//p' <<<"$marker")"
requested_source_sha="$source_sha"
release_tag="$(sed -n 's/^release-tag=//p' <<<"$marker")"
''',
    "requested source capture",
)

replace_once(
    '''case "$release_channel" in
  rc)
    source_pull="$(owner_api "repos/${TARGET_REPOSITORY}/pulls/${SOURCE_PR}")"
    test "$(jq -er '.state' <<<"$source_pull")" = 'open'
    test "$(jq -er '.head.sha' <<<"$source_pull")" = "$source_sha"
    ;;
  final)
    target_main="$(owner_api "repos/${TARGET_REPOSITORY}/git/ref/heads/main" --jq '.object.sha')"
    test "$target_main" = "$source_sha"
    ;;
esac

stage='download-exact-private-source'
GH_TOKEN="$owner_token" gh api \\
  -H 'Accept: application/vnd.github+json' \\
  "repos/${TARGET_REPOSITORY}/tarball/${source_sha}" >"$work/source.tar.gz"
test -s "$work/source.tar.gz"
tar -xzf "$work/source.tar.gz" --strip-components=1 -C "$work/source"
for required in \\
  Cargo.toml \\
  clients/rust/Cargo.toml \\
  clients/ts/package.json \\
  clients/dart/pubspec.yaml \\
  clients/gleam/gleam.toml; do
  test -f "$work/source/$required"
done

stage='rust-format'
(cd "$work/source" && cargo fmt --all --check) >"$work/logs/rust.log" 2>&1
''',
    '''case "$release_channel" in
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
  *Username*) printf '%s\\n' 'x-access-token' ;;
  *) printf '%s\\n' "$OWNER_TOKEN" ;;
esac
SH
chmod 700 "$askpass"
export OWNER_TOKEN="$owner_token"
export GIT_ASKPASS="$askpass"
export GIT_TERMINAL_PROMPT=0
rm -rf "$work/source"
(
  git clone \\
    --filter=blob:none \\
    --no-tags \\
    --single-branch \\
    --branch "$source_head_ref" \\
    "https://github.com/${source_head_repo}.git" \\
    "$work/source"
) >"$work/logs/clone.log" 2>&1
test "$(git -C "$work/source" rev-parse HEAD)" = "$source_sha"
for required in \\
  Cargo.toml \\
  clients/rust/Cargo.toml \\
  clients/ts/package.json \\
  clients/dart/pubspec.yaml \\
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
  git -C "$work/source" commit -m 'style: apply canonical Rust formatting' \\
    >"$work/logs/rust-format-commit.log" 2>&1
  source_sha="$(git -C "$work/source" rev-parse HEAD)"
  [[ "$source_sha" =~ ^[0-9a-f]{40}$ ]]
  source_epoch="$(git -C "$work/source" show -s --format=%ct HEAD)"

  stage='rust-format-push'
  git -C "$work/source" push origin "HEAD:${source_head_ref}" \\
    >"$work/logs/rust-format-push.log" 2>&1
  test "$(owner_api "repos/${TARGET_REPOSITORY}/pulls/${SOURCE_PR}" --jq '.head.sha')" = "$source_sha"
  GH_TOKEN="$owner_token" gh pr comment "$SOURCE_PR" \\
    --repo "$TARGET_REPOSITORY" \\
    --body "Applied canonical Rust formatting through the reviewed DEN-2422 artifact broker. New exact head: \`${source_sha}\`. Artifact validation continues from this revision." \\
    >/dev/null
fi

stage='rust-format-check'
(cd "$work/source" && cargo fmt --all --check) >>"$work/logs/rust.log" 2>&1
''',
    "source clone and Rust autoformat",
)

replace_once(
    '''CONTROL_RUN_ATTEMPT="$GITHUB_RUN_ATTEMPT" \\
SOURCE_SHA="$source_sha" \\
RELEASE_TAG="$release_tag" \\
''',
    '''CONTROL_RUN_ATTEMPT="$GITHUB_RUN_ATTEMPT" \\
REQUESTED_SOURCE_SHA="$requested_source_sha" \\
SOURCE_SHA="$source_sha" \\
RELEASE_TAG="$release_tag" \\
''',
    "provenance environment",
)

replace_once(
    '''    "source_repository": os.environ["TARGET_REPOSITORY"],
    "source_commit": os.environ["SOURCE_SHA"],
''',
    '''    "source_repository": os.environ["TARGET_REPOSITORY"],
    "requested_source_commit": os.environ["REQUESTED_SOURCE_SHA"],
    "source_commit": os.environ["SOURCE_SHA"],
''',
    "provenance requested source",
)

publisher.write_text(text)

for temporary in [
    Path("scripts/patch_shared_auth_autoformat_source.py"),
    Path(".github/workflows/patch-shared-auth-autoformat-source.yml"),
]:
    if temporary.exists():
        temporary.unlink()
