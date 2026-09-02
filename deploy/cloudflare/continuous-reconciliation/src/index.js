const INTERVAL_SECONDS = 180;
const MAX_WORKERS = 3;
const RUN_KEY_PREFIX = "continuous-50-day-reconciliation";

export function parseBoundedInt(value, name, minimum, maximum) {
  const parsed = Number.parseInt(String(value), 10);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${name} must be between ${minimum} and ${maximum}`);
  }
  return parsed;
}

export function bucketStart(now = new Date()) {
  const epochSeconds = Math.floor(now.getTime() / 1000);
  return new Date((epochSeconds - (epochSeconds % INTERVAL_SECONDS)) * 1000);
}

export function bucketKey(now = new Date()) {
  const stamp = bucketStart(now).toISOString().replace(/[-:]/g, "").replace(".000", "");
  return `${RUN_KEY_PREFIX}:scheduled:${stamp}`;
}

export function buildRequest(now, env) {
  const lookbackHours = parseBoundedInt(env.LOOKBACK_HOURS ?? "1200", "LOOKBACK_HOURS", 24, 1200);
  const overlapHours = parseBoundedInt(env.OVERLAP_HOURS ?? "6", "OVERLAP_HOURS", 0, 48);
  const maxWorkers = parseBoundedInt(env.MAX_WORKERS ?? "3", "MAX_WORKERS", 1, MAX_WORKERS);
  if (overlapHours >= lookbackHours) {
    throw new Error("OVERLAP_HOURS must be smaller than LOOKBACK_HOURS");
  }
  const scheduledFor = bucketStart(now);
  const runKey = bucketKey(now);
  const payload = {
    org: "ORESoftware",
    repo: "ai-agent-coordinator.rs",
    task_type: "artifact_recovery",
    priority: 90,
    max_attempts: 3,
    budget_usd: 12.0,
    payload: {
      schema_version: "artifact_recovery_job.v1",
      run: {
        mode: "scheduled",
        run_key: runKey,
        scheduled_run_key: runKey,
        scheduled_for: scheduledFor.toISOString(),
        observed_local_time: now.toISOString(),
        timezone: "UTC",
        local_time: scheduledFor.toISOString().slice(11, 16),
        recovered: false,
        recovery_minutes: 3,
        manual_id: null,
        interval_seconds: INTERVAL_SECONDS,
        bucket_started_at: scheduledFor.toISOString()
      },
      tracking: {
        canonical_issue: "DEN-3179",
        prompt_intake_foundation: "DEN-834",
        repository_creation_issue: "DEN-319",
        credential_rotation_issue: "DEN-1230",
        local_cli_task_id: "019fd526-f34d-7f72-94fa-2da6185f2d74",
        related_issues: ["DEN-2797", "DEN-3180", "DEN-2190", "DEN-3474"],
        policy_repository: "ORESoftware/my-ai",
        policy_path: "AGENTS.md"
      },
      source_contract: {
        scan_all_accessible_authorized_chatgpt_threads: true,
        scan_authorized_claude_session_exports: true,
        scan_accessible_authorized_codex_tasks: true,
        include_newly_changed_sources: true,
        exclude_hidden_reasoning: true,
        exclude_secret_values: true,
        persist_prompt_bodies: false,
        bounded_batch_size: 50,
        resume_from_durable_cursor: true,
        rolling_window_hours: lookbackHours,
        overlap_hours: overlapHours,
        window_selection: "threads_or_tasks_created_or_updated_since_cutoff",
        revisit_unresolved_items_outside_window: true,
        require_full_pagination: true,
        require_fresh_source_coverage_receipt: true,
        worker_concurrency_limit: maxWorkers
      },
      ledger_contract: {
        schema_version: "artifact_recovery_ledger.v1",
        key_fields: ["origin_source", "origin_id", "owner", "repository"],
        verify_remote_before_action: true,
        reuse_existing_repository_branch_and_pull_request: true,
        default_new_repository_visibility: "private",
        retry_transient_blockers_on_later_runs: true,
        current_truth_sources: ["linear_issues", "github_issues", "github_commits", "github_branches", "github_pull_requests"],
        skip_archived_cancelled_duplicate_superseded_or_outmoded: true,
        never_reanimate_closed_superseded_work: true,
        require_current_remote_read_before_every_mutation: true,
        require_optimistic_concurrency_receipt: true
      },
      detection_contract: {
        states: ["repository_missing", "artifact_only", "repository_has_no_remote", "changes_uncommitted", "commits_unpushed", "branch_not_created", "branch_not_published", "branch_without_pull_request", "claimed_repository_unverified", "claimed_commit_unverified", "claimed_branch_unverified", "claimed_pull_request_unverified"],
        tangible_artifact_kinds: ["code", "documentation"],
        ordinary_conversation_creates_no_repository: true
      },
      delivery_contract: {
        github_app_first: true,
        repository_creation_fallback: "emit_cli_recovery_item",
        open_draft_pull_request: true,
        commit_only_intended_paths: true,
        scan_intended_content_for_secrets: true,
        record_repository_branch_commit_and_pr_evidence: true,
        update_linear_project_documentation: true,
        update_github_project: true,
        feature_branch_only: true,
        independent_review_required: true,
        exact_head_checks_required: true,
        merge_and_deploy_require_separate_reviewed_gate: true
      },
      allowed_actions: ["read_authorized_source_metadata", "read_current_github_evidence", "create_or_reuse_feature_branch", "commit_bounded_intended_scope", "push_without_force", "open_or_reuse_draft_pull_request", "amend_canonical_linear_issue", "synchronize_mapped_github_project", "emit_cli_recovery_item"],
      forbidden_actions: ["reuse_chat_pasted_credentials", "store_pat_or_token", "revoke_or_rotate_credentials", "force_push", "broadly_stage_mixed_worktree", "direct_default_branch_write", "bypass_protection", "auto_merge", "claim_delivery_without_remote_evidence", "revive_archived_cancelled_duplicate_or_superseded_work"]
    }
  };
  return { runKey, payload };
}

export async function enqueue(now, env, fetchImpl = fetch) {
  if (env.ACTIVATION_MODE !== "enabled") {
    return { status: "disabled", run_key: bucketKey(now) };
  }
  if (!env.AI_AGENT_COORDINATOR_API_TOKEN) {
    throw new Error("AI_AGENT_COORDINATOR_API_TOKEN is required when enabled");
  }
  const endpoint = new URL(env.COORDINATOR_URL);
  if (endpoint.username || endpoint.password || endpoint.search || endpoint.hash) {
    throw new Error("COORDINATOR_URL must not contain credentials, query, or fragment");
  }
  if (endpoint.protocol !== "https:" && endpoint.hostname !== "127.0.0.1" && endpoint.hostname !== "localhost") {
    throw new Error("COORDINATOR_URL must use HTTPS outside loopback");
  }
  endpoint.pathname = `${endpoint.pathname.replace(/\/$/, "")}/v1/jobs`;
  const { runKey, payload } = buildRequest(now, env);
  const response = await fetchImpl(endpoint, {
    method: "POST",
    redirect: "manual",
    headers: {
      authorization: `Bearer ${env.AI_AGENT_COORDINATOR_API_TOKEN}`,
      "content-type": "application/json",
      accept: "application/json",
      "idempotency-key": runKey
    },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    throw new Error(`coordinator enqueue failed with HTTP ${response.status}`);
  }
  return { status: "enqueued", run_key: runKey, coordinator_status: response.status };
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(enqueue(new Date(controller.scheduledTime), env));
  },
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "GET" || url.pathname !== "/healthz") {
      return new Response("not found\n", { status: 404 });
    }
    return Response.json({
      status: "ok",
      activation_mode: env.ACTIVATION_MODE === "enabled" ? "enabled" : "disabled",
      interval_seconds: INTERVAL_SECONDS,
      maximum_workers: MAX_WORKERS
    });
  }
};
