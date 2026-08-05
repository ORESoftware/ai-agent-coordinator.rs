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
    '''stage='typescript-package-locate'
packed="$(find "$work/artifacts" -maxdepth 1 -type f -name '*.tgz' -print -quit)"
test -n "$packed"
mv "$packed" "$work/artifacts/shared-auth-client-typescript-0.1.0.tgz"

stage='dart-dependencies'
''',
    '''stage='typescript-package-locate'
packed="$(find "$work/artifacts" -maxdepth 1 -type f -name '*.tgz' -print -quit)"
test -n "$packed"
mv "$packed" "$work/artifacts/shared-auth-client-typescript-0.1.0.tgz"

stage='typescript-lock-scope'
mapfile -t typescript_lock_paths < <(git -C "$work/source" diff --name-only -- clients/ts)
if ((${#typescript_lock_paths[@]} > 0)); then
  test "$release_channel" = 'rc'
  test "${#typescript_lock_paths[@]}" -eq 1
  test "${typescript_lock_paths[0]}" = 'clients/ts/package-lock.json'
  git -C "$work/source" diff --check -- clients/ts/package-lock.json

  stage='typescript-lock-commit'
  git -C "$work/source" config user.name 'ORESoftware artifact automation'
  git -C "$work/source" config user.email 'bot@oresoftware.dev'
  git -C "$work/source" add -u -- clients/ts/package-lock.json
  git -C "$work/source" diff --quiet -- clients/ts
  ! git -C "$work/source" diff --cached --quiet
  git -C "$work/source" commit -m 'chore: refresh TypeScript package lock' \\
    >"$work/logs/typescript-lock-commit.log" 2>&1
  source_sha="$(git -C "$work/source" rev-parse HEAD)"
  [[ "$source_sha" =~ ^[0-9a-f]{40}$ ]]
  source_epoch="$(git -C "$work/source" show -s --format=%ct HEAD)"

  stage='typescript-lock-push'
  git -C "$work/source" push origin "HEAD:${source_head_ref}" \\
    >"$work/logs/typescript-lock-push.log" 2>&1
  wait_for_source_pr_head "$source_sha"
  comment_source_pr_best_effort \\
    "Refreshed the TypeScript package lock through the reviewed DEN-2422 artifact broker. New exact head: \\`${source_sha}\\`. Artifact validation continues from this revision."
fi

stage='dart-dependencies'
''',
    "TypeScript lock reconciliation",
)

replace_once(
    '''stage='gleam-format-scope'
mapfile -t gleam_formatted_paths < <(git -C "$work/source" diff --name-only)
''',
    '''stage='gleam-format-scope'
mapfile -t gleam_formatted_paths < <(git -C "$work/source" diff --name-only -- clients/gleam)
''',
    "Gleam path-scoped diff collection",
)

replace_once(
    '''  git -C "$work/source" diff --check

  stage='gleam-format-commit'
''',
    '''  git -C "$work/source" diff --check -- clients/gleam

  stage='gleam-format-commit'
''',
    "Gleam path-scoped diff check",
)

replace_once(
    '''  git -C "$work/source" add -u -- ':(glob)**/*.gleam'
  git -C "$work/source" diff --quiet
''',
    '''  git -C "$work/source" add -u -- ':(glob)clients/gleam/**/*.gleam'
  git -C "$work/source" diff --quiet -- clients/gleam
''',
    "Gleam path-scoped staging",
)

replace_once(
    '''stage='gleam-dependencies'
(cd "$work/source/clients/gleam" && gleam deps download) >>"$work/logs/gleam.log" 2>&1

stage='gleam-check'
''',
    '''stage='gleam-dependencies'
(cd "$work/source/clients/gleam" && gleam deps download) >>"$work/logs/gleam.log" 2>&1

stage='gleam-lock-scope'
mapfile -t gleam_lock_paths < <(git -C "$work/source" diff --name-only -- clients/gleam)
if ((${#gleam_lock_paths[@]} > 0)); then
  test "$release_channel" = 'rc'
  test "${#gleam_lock_paths[@]}" -eq 1
  test "${gleam_lock_paths[0]}" = 'clients/gleam/manifest.toml'
  git -C "$work/source" diff --check -- clients/gleam/manifest.toml

  stage='gleam-lock-commit'
  git -C "$work/source" config user.name 'ORESoftware artifact automation'
  git -C "$work/source" config user.email 'bot@oresoftware.dev'
  git -C "$work/source" add -u -- clients/gleam/manifest.toml
  git -C "$work/source" diff --quiet -- clients/gleam
  ! git -C "$work/source" diff --cached --quiet
  git -C "$work/source" commit -m 'chore: refresh Gleam dependency manifest' \\
    >"$work/logs/gleam-lock-commit.log" 2>&1
  source_sha="$(git -C "$work/source" rev-parse HEAD)"
  [[ "$source_sha" =~ ^[0-9a-f]{40}$ ]]
  source_epoch="$(git -C "$work/source" show -s --format=%ct HEAD)"

  stage='gleam-lock-push'
  git -C "$work/source" push origin "HEAD:${source_head_ref}" \\
    >"$work/logs/gleam-lock-push.log" 2>&1
  wait_for_source_pr_head "$source_sha"
  comment_source_pr_best_effort \\
    "Refreshed the Gleam dependency manifest through the reviewed DEN-2422 artifact broker. New exact head: \\`${source_sha}\\`. Artifact validation continues from this revision."
fi

stage='gleam-check'
''',
    "Gleam manifest reconciliation",
)

replace_once(
    '''stage='generate-private-provenance'
CONTROL_SHA="$main_sha" \\
''',
    '''stage='source-tree-clean'
git -C "$work/source" diff --quiet
git -C "$work/source" diff --cached --quiet

stage='generate-private-provenance'
CONTROL_SHA="$main_sha" \\
''',
    "clean tracked source requirement",
)

publisher.write_text(text)

for temporary in [
    Path("scripts/patch_shared_auth_lock_reconciliation.py"),
    Path(".github/workflows/patch-shared-auth-lock-reconciliation.yml"),
]:
    if temporary.exists():
        temporary.unlink()
