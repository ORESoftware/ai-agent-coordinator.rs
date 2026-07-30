#!/usr/bin/env bash
set -euo pipefail

: "${COORDINATOR_API_TOKEN:?COORDINATOR_API_TOKEN is required}"

image_ref="$(ruby -ryaml -e "puts YAML.safe_load(File.read('deploy/k8s/deployment.yaml')).dig('spec', 'template', 'spec', 'containers', 0, 'image')")"
readonly image_ref

docker pull "$image_ref"

container=ai-agent-coordinator-linear-pilot-contract
cleanup() {
  docker logs "$container" || true
  docker rm --force "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run --detach \
  --name "$container" \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /app/data:rw,noexec,nosuid,size=64m,uid=10001,gid=10001,mode=0700 \
  --publish 127.0.0.1:18080:8080 \
  --env COORDINATOR_API_TOKEN \
  --env GITHUB_WEBHOOK_SECRET_SONUS_AURIS=ci-sonus-fixture \
  --env GITHUB_WEBHOOK_SECRET_DAEDALUS_FAB=ci-daedalus-fixture \
  --env GITHUB_WEBHOOK_ORG_SECRET_ENVS=sonus-auris=GITHUB_WEBHOOK_SECRET_SONUS_AURIS,daedalus-fab=GITHUB_WEBHOOK_SECRET_DAEDALUS_FAB \
  --env GITHUB_AUTO_ENQUEUE_PUSHES=true \
  --env GITHUB_PUSH_ALLOWED_REPOSITORIES=sonus-auris/sonus-auris-site.web,daedalus-fab/daedalus-clients \
  --env GITHUB_PUSH_DEFAULT_BRANCHES=sonus-auris/sonus-auris-site.web=main,daedalus-fab/daedalus-clients=main \
  --env LINEAR_DELIVERY_ENABLED=true \
  --env LINEAR_DELIVERY_DRY_RUN=true \
  --env LINEAR_API_URL=https://api.linear.app/graphql \
  --env LINEAR_API_AUTH_SCHEME=api_key \
  --env LINEAR_TEAM_KEY=DEN \
  --env LINEAR_PROJECT_NAMES=sonus-auris=github.com/sonus-auris,daedalus-fab=github.com/daedalus-fab \
  "$image_ref"

for attempt in $(seq 1 60); do
  if curl --fail --silent --show-error http://127.0.0.1:18080/readyz >/dev/null; then
    break
  fi
  if [[ "$attempt" == 60 ]]; then
    echo 'pilot-configured coordinator did not become ready' >&2
    exit 1
  fi
  sleep 1
done

cat > job.json <<'JSON'
{
  "org": "sonus-auris",
  "repo": "sonus-auris-site.web",
  "task_type": "github_push",
  "priority": 10,
  "max_attempts": 3,
  "payload": {
    "ref": "refs/heads/main",
    "repository": {
      "full_name": "sonus-auris/sonus-auris-site.web"
    },
    "coordinator": {
      "default_branch": "main",
      "linear_directives": [
        {
          "commit_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "issue_identifier": "DEN-455",
          "keyword": "refs",
          "closes_issue": false
        }
      ]
    }
  }
}
JSON

created="$(curl --fail --silent --show-error \
  --request POST \
  --header "Authorization: Bearer ${COORDINATOR_API_TOKEN}" \
  --header 'Content-Type: application/json' \
  --header 'Idempotency-Key: ci-linear-pilot-plan' \
  --data-binary @job.json \
  http://127.0.0.1:18080/v1/jobs)"

job_id="$(CREATED="$created" python3 - <<'PY'
import json
import os
print(json.loads(os.environ['CREATED'])['job']['id'])
PY
)"
readonly job_id

plan="$(curl --fail --silent --show-error \
  --request POST \
  --header "Authorization: Bearer ${COORDINATOR_API_TOKEN}" \
  "http://127.0.0.1:18080/v1/linear/plan/${job_id}")"

PLAN="$plan" JOB_ID="$job_id" python3 - <<'PY'
import json
import os
payload = json.loads(os.environ['PLAN'])
delivery = payload['delivery']
assert delivery['job_id'] == os.environ['JOB_ID']
assert delivery['organization'] == 'sonus-auris'
assert delivery['repository'] == 'sonus-auris/sonus-auris-site.web'
assert delivery['default_branch'] == 'main'
assert delivery['dry_run'] is True
assert delivery['directives'] == [{
    'issue_identifier': 'DEN-455',
    'commit_id': 'a' * 40,
    'action': 'reference',
    'status': 'planned',
    'dry_run': True,
}]
PY

live_status="$(curl --silent --show-error \
  --output blocked-live-delivery.json \
  --write-out '%{http_code}' \
  --request POST \
  --header "Authorization: Bearer ${COORDINATOR_API_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data '{"worker_id":"ci-live-must-be-blocked","orgs":["sonus-auris"],"repositories":["sonus-auris/sonus-auris-site.web"],"lease_seconds":60}' \
  http://127.0.0.1:18080/v1/linear/deliver-next)"
test "$live_status" = 400
grep -Fq 'dry-run is enabled' blocked-live-delivery.json

queued="$(curl --fail --silent --show-error \
  --header "Authorization: Bearer ${COORDINATOR_API_TOKEN}" \
  "http://127.0.0.1:18080/v1/jobs/${job_id}")"

QUEUED="$queued" python3 - <<'PY'
import json
import os
job = json.loads(os.environ['QUEUED'])['job']
assert job['status'] == 'queued'
assert job['attempts'] == 0
PY

if env | grep -q '^LINEAR_API_TOKEN='; then
  echo 'the dry-run smoke must not receive a Linear token' >&2
  exit 1
fi

echo 'Protected Linear pilot dry-run smoke passed.'
