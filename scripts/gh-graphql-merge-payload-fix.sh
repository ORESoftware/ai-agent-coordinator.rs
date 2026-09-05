#!/usr/bin/env bash
set -Eeuo pipefail

REAL_GH="${REAL_GH:?REAL_GH must point to the real gh executable}"
BASE_GRAPHQL_FALLBACK="${BASE_GRAPHQL_FALLBACK:?BASE_GRAPHQL_FALLBACK must point to the reviewed GraphQL fallback}"
ORIGINAL_ARGS=("$@")

fail() {
  printf 'gh-graphql-merge-fix: %s\n' "$*" >&2
  exit 1
}

delegate() {
  exec env REAL_GH="$REAL_GH" bash "$BASE_GRAPHQL_FALLBACK" "${ORIGINAL_ARGS[@]}"
}

graphql() {
  "$REAL_GH" api graphql "$@"
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
      delegate
      ;;
    *)
      if [[ -z "$endpoint" ]]; then
        endpoint=$1
        shift
      else
        delegate
      fi
      ;;
  esac
done

method="${method^^}"
if [[ "$method:$endpoint" != PUT:repos/*/pulls/*/merge ]]; then
  delegate
fi

[[ -n "$input_file" ]] || fail 'merge request requires --input'
if [[ "$input_file" == - ]]; then
  payload="$(cat)"
else
  payload="$(cat -- "$input_file")"
fi

path=${endpoint#repos/}
full=${path%%/pulls/*}
remainder=${path#*/pulls/}
number=${remainder%/merge}
[[ "$full" == */* && "$number" =~ ^[0-9]+$ ]] || fail "invalid merge endpoint: $endpoint"
owner=${full%%/*}
name=${full#*/}
expected="$(jq -r '.sha // empty' <<<"$payload")"
[[ "$expected" =~ ^[0-9a-f]{40}$ ]] || fail 'merge request requires an exact 40-character head SHA'

pr_response="$(graphql \
  -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){id headRefOid isDraft mergeable state}}}' \
  -f owner="$owner" \
  -f name="$name" \
  -F number="$number")"
pr_id="$(jq -r '.data.repository.pullRequest.id // empty' <<<"$pr_response")"
observed="$(jq -r '.data.repository.pullRequest.headRefOid // empty' <<<"$pr_response")"
draft="$(jq -r 'if .data.repository.pullRequest == null then true else .data.repository.pullRequest.isDraft end' <<<"$pr_response")"
mergeable="$(jq -r '.data.repository.pullRequest.mergeable // "UNKNOWN"' <<<"$pr_response")"
state="$(jq -r '.data.repository.pullRequest.state // "UNKNOWN"' <<<"$pr_response")"

[[ -n "$pr_id" ]] || fail "pull request not found: $full#$number"
[[ "$observed" == "$expected" ]] || fail "pull request head drift for $full#$number"
[[ "$draft" == false ]] || fail "pull request is draft: $full#$number"
[[ "$state" == OPEN ]] || fail "pull request is not open: $full#$number"
[[ "$mergeable" == MERGEABLE ]] || fail "pull request is not mergeable: $full#$number ($mergeable)"

response="$(graphql \
  -f query='mutation($pull:ID!,$expected:GitObjectID!){mergePullRequest(input:{pullRequestId:$pull,expectedHeadOid:$expected,mergeMethod:SQUASH,commitHeadline:"feat(issue-183): bootstrap Rust Lambda repository"}){pullRequest{merged mergedAt mergeCommit{oid}}}}' \
  -f pull="$pr_id" \
  -f expected="$expected")"
merged="$(jq -r '.data.mergePullRequest.pullRequest.merged // false' <<<"$response")"
merge_sha="$(jq -r '.data.mergePullRequest.pullRequest.mergeCommit.oid // empty' <<<"$response")"
[[ "$merged" == true && "$merge_sha" =~ ^[0-9a-f]{40}$ ]] || \
  fail "mergePullRequest rejected $full#$number"

value="$(jq -nc --arg sha "$merge_sha" '{merged:true,sha:$sha,message:"Pull Request successfully merged"}')"
if [[ -n "$jq_filter" ]]; then
  jq -r "$jq_filter" <<<"$value"
else
  printf '%s\n' "$value"
fi
