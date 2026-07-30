#!/usr/bin/env bash
# Declarative PostgreSQL migrations for ai-agent-coordinator, via dpm:
# https://github.com/declarative-migrations/declarative-postgres-migrate.rs
#
# The schema authority lives in k8s-libs-and-shared-defs:
#   pg-defs/schema/databases/ai_agent_coordinator/schema.sql
# Runtime SeaORM entities are adapters only. The service never migrates at boot.
#
# Usage:
#   scripts/dpm.sh diff
#   scripts/dpm.sh verify
#   scripts/dpm.sh review
#   scripts/dpm.sh apply
#   scripts/dpm.sh bootstrap
#
# Environment:
#   K8S_LIBS_AND_SHARED_DEFS_DIR  checkout containing pg-defs/ (defaults to
#                                 ../k8s-libs-and-shared-defs beside this repo)
#   TARGET_DATABASE_URL           target, falling back to
#                                 AI_AGENT_COORDINATOR_DATABASE_URL then
#                                 DATABASE_URL
#   SHADOW_DATABASE_URL           server where dpm may create/drop temporary DBs
set -euo pipefail

cmd="${1:-diff}"
[ "$#" -gt 0 ] && shift

service_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
shared_defs_dir="${K8S_LIBS_AND_SHARED_DEFS_DIR:-$service_dir/../k8s-libs-and-shared-defs}"
schema_sql="$shared_defs_dir/pg-defs/schema/databases/ai_agent_coordinator/schema.sql"

if [ ! -f "$schema_sql" ]; then
  echo "error: shared schema contract not found at $schema_sql" >&2
  echo "set K8S_LIBS_AND_SHARED_DEFS_DIR to the shared-defs checkout" >&2
  exit 1
fi

if ! command -v dpm >/dev/null 2>&1; then
  echo "error: dpm not found on PATH" >&2
  echo "install: brew install declarative-migrations/tap/dpm" >&2
  exit 1
fi

if [ -z "${SHADOW_DATABASE_URL:-}" ]; then
  echo "error: SHADOW_DATABASE_URL is required" >&2
  echo "use a non-production Postgres server where dpm may create/drop databases" >&2
  exit 1
fi

target="${TARGET_DATABASE_URL:-${AI_AGENT_COORDINATOR_DATABASE_URL:-${DATABASE_URL:-}}}"

# dpm's flags-2-env loader checks the current directory for a contract. Run
# outside the service root so the coordinator's own .cli-flags.toml cannot be
# mistaken for dpm's CLI contract.
cd "$service_dir/scripts"

case "$cmd" in
  bootstrap)
    exec dpm bootstrap \
      --source "$schema_sql" \
      --schemas ai_agent_coordinator \
      "$@"
    ;;
  diff | verify | review | apply)
    if [ -z "$target" ]; then
      echo "error: no target database URL; set TARGET_DATABASE_URL," >&2
      echo "AI_AGENT_COORDINATOR_DATABASE_URL, or DATABASE_URL" >&2
      exit 1
    fi
    # Keep credentials out of argv; dpm reads TARGET_DATABASE_URL directly.
    export TARGET_DATABASE_URL="$target"
    exec dpm "$cmd" \
      --source "$schema_sql" \
      --schemas ai_agent_coordinator \
      "$@"
    ;;
  *)
    exec dpm "$cmd" "$@"
    ;;
esac
