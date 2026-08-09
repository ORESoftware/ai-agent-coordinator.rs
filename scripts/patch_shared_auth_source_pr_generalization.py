from __future__ import annotations

from pathlib import Path

publisher = Path("scripts/publish_shared_auth_client_artifacts.sh")
workflow = Path(".github/workflows/publish-shared-auth-client-artifacts-v2.yml")
text = publisher.read_text()
workflow_text = workflow.read_text()


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    ': "${SOURCE_PR:?SOURCE_PR is required}"\n',
    '',
    'remove hardcoded source PR requirement',
)

replace_once(
    'repos/${TARGET_REPOSITORY}/pulls/${SOURCE_PR}',
    'repos/${TARGET_REPOSITORY}/pulls/${source_pr}',
    'head convergence source PR',
)
replace_once(
    'gh pr comment "$SOURCE_PR"',
    'gh pr comment "$source_pr"',
    'source PR comment target',
)

replace_once(
    '''test "$target_repository" = "$TARGET_REPOSITORY"
test "$source_pr" = "$SOURCE_PR"
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]]
test "$trusted_main" = "$parent_sha"
test "$protocol" = "$expected_protocol"
case "$release_channel:$release_tag" in
  rc:v0.1.0-rc.1|final:v0.1.0) ;;
  *) exit 1 ;;
esac
''',
    '''test "$target_repository" = "$TARGET_REPOSITORY"
[[ "$source_pr" =~ ^[1-9][0-9]*$ ]]
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]]
test "$trusted_main" = "$parent_sha"
test "$protocol" = "$expected_protocol"
case "$release_channel" in
  rc)
    [[ "$release_tag" =~ ^v0\\.1\\.0-rc\\.[1-9][0-9]*$ ]]
    ;;
  final)
    test "$release_tag" = 'v0.1.0'
    ;;
  *) exit 1 ;;
esac
''',
    'carrier source PR and release tuple validation',
)

replace_once(
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
''',
    '''source_pull="$(owner_api "repos/${TARGET_REPOSITORY}/pulls/${source_pr}")"
case "$release_channel" in
  rc)
    jq -e \\
      --arg owner "$expected_owner" \\
      --arg repository "$TARGET_REPOSITORY" \\
      --arg source_sha "$source_sha" '
      .state == "open" and
      .merged == false and
      .user.login == $owner and
      .base.ref == "main" and
      .head.repo.full_name == $repository and
      .head.sha == $source_sha and
      (.head.ref | startswith("agent/")) and
      (.title | startswith("DEN-2422:"))
    ' <<<"$source_pull" >/dev/null
    source_head_repo="$(jq -er '.head.repo.full_name' <<<"$source_pull")"
    source_head_ref="$(jq -er '.head.ref' <<<"$source_pull")"
    ;;
  final)
    target_main="$(owner_api "repos/${TARGET_REPOSITORY}/git/ref/heads/main" --jq '.object.sha')"
    test "$target_main" = "$source_sha"
    jq -e \\
      --arg owner "$expected_owner" \\
      --arg source_sha "$source_sha" '
      .state == "closed" and
      .merged == true and
      .user.login == $owner and
      .base.ref == "main" and
      .merge_commit_sha == $source_sha and
      (.title | startswith("DEN-2422:"))
    ' <<<"$source_pull" >/dev/null
    source_head_repo="$TARGET_REPOSITORY"
    source_head_ref='main'
    ;;
esac
''',
    'source PR ownership and state validation',
)

replace_once(
    '''CONTROL_RUN_ATTEMPT="$GITHUB_RUN_ATTEMPT" \\
REQUESTED_SOURCE_SHA="$requested_source_sha" \\
SOURCE_SHA="$source_sha" \\
''',
    '''CONTROL_RUN_ATTEMPT="$GITHUB_RUN_ATTEMPT" \\
SOURCE_PULL_REQUEST="$source_pr" \\
REQUESTED_SOURCE_SHA="$requested_source_sha" \\
SOURCE_SHA="$source_sha" \\
''',
    'provenance source PR environment',
)
replace_once(
    '''    "source_repository": os.environ["TARGET_REPOSITORY"],
    "requested_source_commit": os.environ["REQUESTED_SOURCE_SHA"],
''',
    '''    "source_repository": os.environ["TARGET_REPOSITORY"],
    "source_pull_request": int(os.environ["SOURCE_PULL_REQUEST"]),
    "requested_source_commit": os.environ["REQUESTED_SOURCE_SHA"],
''',
    'provenance source PR field',
)

replace_once(
    '''if [[ "$release_channel" == 'rc' ]]; then
  release_heading='Shared Auth clients v0.1.0 release candidate 1'
  release_flag=(--prerelease)
  channel_text='This prerelease validates the exact pull-request head before canonical merge publication.'
else
''',
    '''if [[ "$release_channel" == 'rc' ]]; then
  rc_number="${release_tag##*.}"
  [[ "$rc_number" =~ ^[1-9][0-9]*$ ]]
  release_heading="Shared Auth clients v0.1.0 release candidate ${rc_number}"
  release_flag=(--prerelease)
  channel_text='This prerelease validates the exact pull-request head before canonical merge publication.'
else
''',
    'dynamic release candidate heading',
)
replace_once(
    '- Packaging PR: https://github.com/shared-auth/shared-auth-clients/pull/35\n',
    '- Source PR: https://github.com/${TARGET_REPOSITORY}/pull/${source_pr}\n',
    'dynamic release source PR link',
)

publisher.write_text(text)

workflow_line = '          SOURCE_PR: "35"\n'
if workflow_text.count(workflow_line) != 1:
    raise SystemExit(
        f"workflow source PR environment: expected one match, found {workflow_text.count(workflow_line)}"
    )
workflow.write_text(workflow_text.replace(workflow_line, '', 1))

for temporary in [
    Path("scripts/patch_shared_auth_source_pr_generalization.py"),
    Path(".github/workflows/patch-shared-auth-source-pr-generalization.yml"),
]:
    if temporary.exists():
        temporary.unlink()
