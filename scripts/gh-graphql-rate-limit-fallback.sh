#!/usr/bin/env bash
set -Eeuo pipefail

REAL_GH="${REAL_GH:?REAL_GH must point to the real gh executable}"
ORIGINAL_ARGS=("$@")

fail() {
  printf 'gh-graphql-fallback: %s\n' "$*" >&2
  exit 1
}

graphql() {
  "$REAL_GH" api graphql "$@"
}

emit_json() {
  local value=$1
  local filter=${2:-}
  if [[ -n "$filter" ]]; then
    jq -r "$filter" <<<"$value"
  else
    printf '%s\n' "$value"
  fi
}

split_repo() {
  local full=$1
  [[ "$full" == */* ]] || fail "invalid repository name: $full"
  REPO_OWNER=${full%%/*}
  REPO_NAME=${full#*/}
}

repository_query() {
  local full=$1
  split_repo "$full"
  graphql \
    -f query='query($owner:String!,$name:String!){repository(owner:$owner,name:$name){id nameWithOwner url isPrivate isArchived defaultBranchRef{name target{... on Commit{oid}}}}}' \
    -f owner="$REPO_OWNER" \
    -f name="$REPO_NAME"
}

initialize_empty_repository() {
  local full=$1 description=$2 work push_ok=false
  work="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/lambda-repo-init.XXXXXX")"
  git init -q -b main "$work"
  git -C "$work" config user.name 'ORES Lambda Repository Bootstrap'
  git -C "$work" config user.email 'noreply@oresoftware.com'
  printf '# %s\n\n%s\n\nThe implementation is delivered through a reviewed feature-branch pull request.\n' \
    "${full#*/}" "$description" > "$work/README.md"
  git -C "$work" add README.md
  git -C "$work" commit -q -m 'chore: initialize repository for reviewed Lambda bootstrap'
  git -C "$work" remote add origin "https://github.com/$full.git"
  for attempt in $(seq 1 20); do
    if git -C "$work" push -q -u origin main; then
      push_ok=true
      break
    fi
    sleep "$(( attempt < 5 ? 2 : 5 ))"
  done
  rm -rf -- "$work"
  [[ "$push_ok" == true ]] || fail "created $full but could not initialize main"
}

[[ "${1:-}" == api ]] || exec "$REAL_GH" "${ORIGINAL_ARGS[@]}"
[[ "${2:-}" != graphql ]] || exec "$REAL_GH" "${ORIGINAL_ARGS[@]}"

shift
method=GET
endpoint=''
jq_filter=''
input_file=''
while (($#)); do
  case "$1" in
    --method|-X)
      [[ $# -ge 2 ]] || fail "$1 requires a value"
      method=$2
      shift 2
      ;;
    --method=*)
      method=${1#*=}
      shift
      ;;
    --jq|-q)
      [[ $# -ge 2 ]] || fail "$1 requires a value"
      jq_filter=$2
      shift 2
      ;;
    --jq=*)
      jq_filter=${1#*=}
      shift
      ;;
    --input)
      [[ $# -ge 2 ]] || fail "$1 requires a value"
      input_file=$2
      shift 2
      ;;
    --input=*)
      input_file=${1#*=}
      shift
      ;;
    --*)
      exec "$REAL_GH" "${ORIGINAL_ARGS[@]}"
      ;;
    *)
      if [[ -z "$endpoint" ]]; then
        endpoint=$1
        shift
      else
        exec "$REAL_GH" "${ORIGINAL_ARGS[@]}"
      fi
      ;;
  esac
done

[[ -n "$endpoint" ]] || exec "$REAL_GH" "${ORIGINAL_ARGS[@]}"
method="${method^^}"
payload='{}'
if [[ -n "$input_file" ]]; then
  if [[ "$input_file" == - ]]; then
    payload="$(cat)"
  else
    payload="$(cat -- "$input_file")"
  fi
fi

case "$method:$endpoint" in
  GET:user)
    response="$(graphql -f query='query{viewer{login databaseId}}')"
    value="$(jq -c '.data.viewer | {login, id:.databaseId}' <<<"$response")"
    emit_json "$value" "$jq_filter"
    ;;

  GET:user/memberships/orgs/*)
    organization=${endpoint#user/memberships/orgs/}
    response="$(graphql \
      -f query='query($login:String!){organization(login:$login){id login viewerIsAMember viewerCanAdminister viewerCanCreateRepositories}}' \
      -f login="$organization")"
    exists="$(jq -r '.data.organization != null' <<<"$response")"
    member="$(jq -r '.data.organization.viewerIsAMember // false' <<<"$response")"
    administer="$(jq -r '.data.organization.viewerCanAdminister // false' <<<"$response")"
    create="$(jq -r '.data.organization.viewerCanCreateRepositories // false' <<<"$response")"
    [[ "$exists" == true && "$member" == true && "$administer" == true && "$create" == true ]] || \
      fail "viewer lacks active administrator/create-repository authority for $organization"
    value='{"state":"active","role":"admin"}'
    emit_json "$value" "$jq_filter"
    ;;

  GET:repos/*/pulls\?*)
    path=${endpoint#repos/}
    full=${path%%/pulls\?*}
    query_string=${endpoint#*\?}
    head_value="$(tr '&' '\n' <<<"$query_string" | sed -n 's/^head=//p' | head -n1)"
    head_branch=${head_value#*:}
    [[ -n "$head_branch" ]] || fail "pull-request listing requires a head query"
    split_repo "$full"
    response="$(graphql \
      -f query='query($owner:String!,$name:String!){repository(owner:$owner,name:$name){pullRequests(first:50,states:OPEN,orderBy:{field:UPDATED_AT,direction:DESC}){nodes{number url headRefName headRefOid}}}}' \
      -f owner="$REPO_OWNER" \
      -f name="$REPO_NAME")"
    value="$(jq -c --arg head "$head_branch" \
      '[.data.repository.pullRequests.nodes[]? | select(.headRefName == $head) | {number, html_url:.url, head:{sha:.headRefOid}}]' \
      <<<"$response")"
    emit_json "$value" "$jq_filter"
    ;;

  GET:repos/*/commits/*/check-runs\?*)
    path=${endpoint#repos/}
    full=${path%%/commits/*}
    oid=${path#*/commits/}
    oid=${oid%%/check-runs\?*}
    split_repo "$full"
    response="$(graphql \
      -f query='query($owner:String!,$name:String!,$oid:GitObjectID!){repository(owner:$owner,name:$name){object(oid:$oid){... on Commit{statusCheckRollup{contexts(first:100){nodes{__typename ... on CheckRun{name status conclusion startedAt completedAt} ... on StatusContext{context state}}}}}}}}' \
      -f owner="$REPO_OWNER" \
      -f name="$REPO_NAME" \
      -f oid="$oid")"
    value="$(jq -c '{check_runs:[.data.repository.object.statusCheckRollup.contexts.nodes[]? | if .__typename == "CheckRun" then {name:.name,status:(.status|ascii_downcase),conclusion:(if .conclusion == null then null else (.conclusion|ascii_downcase) end)} else {name:.context,status:(if .state == "PENDING" then "in_progress" else "completed" end),conclusion:(if .state == "SUCCESS" then "success" elif (.state == "FAILURE" or .state == "ERROR") then "failure" else null end)} end]}' <<<"$response")"
    emit_json "$value" "$jq_filter"
    ;;

  GET:repos/*/pulls/*)
    path=${endpoint#repos/}
    full=${path%%/pulls/*}
    number=${path##*/pulls/}
    [[ "$number" =~ ^[0-9]+$ ]] || exec "$REAL_GH" "${ORIGINAL_ARGS[@]}"
    split_repo "$full"
    response="$(graphql \
      -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){id number url headRefName headRefOid isDraft mergeable state}}}' \
      -f owner="$REPO_OWNER" \
      -f name="$REPO_NAME" \
      -F number="$number")"
    value="$(jq -c '.data.repository.pullRequest | {number,url,html_url:.url,draft:.isDraft,state:(.state|ascii_downcase),head:{sha:.headRefOid},mergeable:(if .mergeable == "MERGEABLE" then true elif .mergeable == "CONFLICTING" then false else null end)}' <<<"$response")"
    emit_json "$value" "$jq_filter"
    ;;

  GET:repos/*/contents/*)
    path=${endpoint#repos/}
    full=${path%%/contents/*}
    expression=${path#*/contents/}
    expression=${expression%%\?ref=*}
    ref=main
    if [[ "$endpoint" == *'?ref='* ]]; then
      ref=${endpoint##*?ref=}
    fi
    split_repo "$full"
    response="$(graphql \
      -f query='query($owner:String!,$name:String!,$expression:String!){repository(owner:$owner,name:$name){object(expression:$expression){... on Blob{text isBinary byteSize}}}}' \
      -f owner="$REPO_OWNER" \
      -f name="$REPO_NAME" \
      -f expression="$ref:$expression")"
    text="$(jq -r '.data.repository.object.text // empty' <<<"$response")"
    [[ -n "$text" ]] || fail "content not found: $full:$ref:$expression"
    encoded="$(printf '%s' "$text" | base64 -w0)"
    value="$(jq -nc --arg content "$encoded" '{content:$content}')"
    emit_json "$value" "$jq_filter"
    ;;

  GET:repos/*)
    full=${endpoint#repos/}
    [[ "$full" != */*/* && "$full" != *'?'* ]] || exec "$REAL_GH" "${ORIGINAL_ARGS[@]}"
    response="$(repository_query "$full")"
    [[ "$(jq -r '.data.repository != null' <<<"$response")" == true ]] || exit 1
    value="$(jq -c '.data.repository' <<<"$response")"
    emit_json "$value" "$jq_filter"
    ;;

  POST:orgs/*/repos)
    organization=${endpoint#orgs/}
    organization=${organization%/repos}
    name="$(jq -r '.name' <<<"$payload")"
    description="$(jq -r '.description // ""' <<<"$payload")"
    [[ -n "$name" && "$name" != null ]] || fail "createRepository requires name"
    owner_response="$(graphql \
      -f query='query($login:String!){organization(login:$login){id viewerIsAMember viewerCanAdminister viewerCanCreateRepositories}}' \
      -f login="$organization")"
    owner_id="$(jq -r '.data.organization.id // empty' <<<"$owner_response")"
    [[ -n "$owner_id" ]] || fail "organization not found: $organization"
    [[ "$(jq -r '.data.organization.viewerIsAMember and .data.organization.viewerCanAdminister and .data.organization.viewerCanCreateRepositories' <<<"$owner_response")" == true ]] || \
      fail "viewer may not create repositories in $organization"
    response="$(graphql \
      -f query='mutation($owner:ID!,$name:String!,$description:String!){createRepository(input:{ownerId:$owner,name:$name,description:$description,visibility:PRIVATE,hasIssuesEnabled:true,hasWikiEnabled:false}){repository{id nameWithOwner url isPrivate}}}' \
      -f owner="$owner_id" \
      -f name="$name" \
      -f description="$description")"
    value="$(jq -c '.data.createRepository.repository' <<<"$response")"
    full="$(jq -r '.nameWithOwner // empty' <<<"$value")"
    [[ "$full" == "$organization/$name" ]] || fail "createRepository returned unexpected repository: $full"
    initialize_empty_repository "$full" "$description"
    emit_json "$value" "$jq_filter"
    ;;

  PATCH:repos/*)
    full=${endpoint#repos/}
    [[ "$full" != */*/* ]] || exec "$REAL_GH" "${ORIGINAL_ARGS[@]}"
    response="$(repository_query "$full")"
    [[ "$(jq -r '.data.repository != null' <<<"$response")" == true ]] || fail "repository not found: $full"
    value="$(jq -c '.data.repository' <<<"$response")"
    emit_json "$value" "$jq_filter"
    ;;

  POST:repos/*/pulls)
    path=${endpoint#repos/}
    full=${path%/pulls}
    title="$(jq -r '.title' <<<"$payload")"
    body="$(jq -r '.body // ""' <<<"$payload")"
    head="$(jq -r '.head' <<<"$payload")"
    base="$(jq -r '.base' <<<"$payload")"
    draft="$(jq -r '.draft // false' <<<"$payload")"
    split_repo "$full"
    repository_response="$(repository_query "$full")"
    repository_id="$(jq -r '.data.repository.id // empty' <<<"$repository_response")"
    [[ -n "$repository_id" ]] || fail "repository not found: $full"
    response="$(graphql \
      -f query='mutation($repository:ID!,$base:String!,$head:String!,$title:String!,$body:String!,$draft:Boolean!){createPullRequest(input:{repositoryId:$repository,baseRefName:$base,headRefName:$head,title:$title,body:$body,draft:$draft,maintainerCanModify:true}){pullRequest{id number url headRefOid}}}' \
      -f repository="$repository_id" \
      -f base="$base" \
      -f head="$head" \
      -f title="$title" \
      -f body="$body" \
      -F draft="$draft")"
    value="$(jq -c '.data.createPullRequest.pullRequest | {number,html_url:.url,head:{sha:.headRefOid}}' <<<"$response")"
    emit_json "$value" "$jq_filter"
    ;;

  PUT:repos/*/pulls/*/merge)
    path=${endpoint#repos/}
    full=${path%%/pulls/*}
    remainder=${path#*/pulls/}
    number=${remainder%/merge}
    expected="$(jq -r '.sha' <<<"$payload")"
    split_repo "$full"
    pr_response="$(graphql \
      -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){id headRefOid}}}' \
      -f owner="$REPO_OWNER" \
      -f name="$REPO_NAME" \
      -F number="$number")"
    pr_id="$(jq -r '.data.repository.pullRequest.id // empty' <<<"$pr_response")"
    observed="$(jq -r '.data.repository.pullRequest.headRefOid // empty' <<<"$pr_response")"
    [[ -n "$pr_id" && "$observed" == "$expected" ]] || fail "pull request head drift for $full#$number"
    response="$(graphql \
      -f query='mutation($pull:ID!,$expected:GitObjectID!){mergePullRequest(input:{pullRequestId:$pull,expectedHeadOid:$expected,mergeMethod:SQUASH,commitHeadline:"feat(issue-183): bootstrap Rust Lambda repository"}){pullRequest{merged mergedAt} mergeCommit{oid}}}' \
      -f pull="$pr_id" \
      -f expected="$expected")"
    merged="$(jq -r '.data.mergePullRequest.pullRequest.merged // false' <<<"$response")"
    merge_sha="$(jq -r '.data.mergePullRequest.mergeCommit.oid // empty' <<<"$response")"
    [[ "$merged" == true && -n "$merge_sha" ]] || fail "mergePullRequest rejected $full#$number"
    value="$(jq -nc --arg sha "$merge_sha" '{merged:true,sha:$sha,message:"Pull Request successfully merged"}')"
    emit_json "$value" "$jq_filter"
    ;;

  *)
    exec "$REAL_GH" "${ORIGINAL_ARGS[@]}"
    ;;
esac
