-- Declarative Postgres schema contract for ai-agent-coordinator.rs.
--
-- NAMESPACE: every owned object lives in the `ai_agent_coordinator` Postgres
-- schema. The coordinator may share a database with other applications without
-- putting its tables in `public` or relying on search_path.
--
-- Migrations are declarative via dpm (declarative-postgres-migrate):
--   ai-agent-coordinator.rs/scripts/dpm.sh {diff|verify|review|apply}
-- with this file as --source and --schemas ai_agent_coordinator. Never apply
-- this file directly to a live database and never migrate at application boot.
--
-- Rust consumers use hand-written SeaORM entities in ai-agent-coordinator.rs.
-- These entities are runtime adapters only; this file is the schema authority.

create schema if not exists ai_agent_coordinator;

-- Durable leased queue for agent work.
create table if not exists ai_agent_coordinator.jobs (
  id text primary key,
  org text not null,
  repo text not null,
  task_type text not null,
  payload jsonb not null,
  priority bigint not null default 0,
  status text not null default 'queued',
  idempotency_key text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  available_at timestamptz not null default now(),
  claimed_by text,
  lease_expires_at timestamptz,
  attempts bigint not null default 0,
  max_attempts bigint not null default 3,
  result jsonb,
  last_error text,
  budget_usd double precision,
  constraint jobs_idempotency_key_unique unique (idempotency_key),
  constraint jobs_status_chk
    check (status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  constraint jobs_priority_chk check (priority between -1000 and 1000),
  constraint jobs_attempts_chk check (attempts >= 0),
  constraint jobs_max_attempts_chk check (max_attempts between 1 and 100),
  constraint jobs_budget_usd_chk check (budget_usd is null or budget_usd > 0),
  constraint jobs_running_lease_chk check (
    status <> 'running'
    or (claimed_by is not null and lease_expires_at is not null)
  )
);

create index if not exists jobs_claim_idx
  on ai_agent_coordinator.jobs
  (status, available_at, priority desc, created_at asc);

create index if not exists jobs_repo_idx
  on ai_agent_coordinator.jobs
  (org, repo, status, created_at desc);

create index if not exists jobs_running_org_idx
  on ai_agent_coordinator.jobs (org)
  where status = 'running';

create index if not exists jobs_running_repo_idx
  on ai_agent_coordinator.jobs (org, repo)
  where status = 'running';

-- Per-request model token and cost ledger used for daily budget enforcement.
create table if not exists ai_agent_coordinator.model_usage (
  id bigint generated always as identity primary key,
  request_id text not null,
  created_at timestamptz not null default now(),
  org text not null,
  repo text not null,
  provider text not null,
  model text not null,
  prompt_tokens bigint not null,
  completion_tokens bigint not null,
  cost_usd double precision not null,
  constraint model_usage_prompt_tokens_chk check (prompt_tokens >= 0),
  constraint model_usage_completion_tokens_chk check (completion_tokens >= 0),
  constraint model_usage_cost_usd_chk check (cost_usd >= 0)
);

create index if not exists model_usage_org_time_idx
  on ai_agent_coordinator.model_usage (org, created_at);

create index if not exists model_usage_repo_time_idx
  on ai_agent_coordinator.model_usage (org, repo, created_at);

-- Idempotency ledger for externally visible Linear mutations.
create table if not exists ai_agent_coordinator.linear_mutations (
  mutation_key text primary key,
  job_id text not null references ai_agent_coordinator.jobs (id) on delete cascade,
  organization text not null,
  repository text not null,
  issue_identifier text not null,
  commit_id text not null,
  keyword text not null,
  action text not null,
  status text not null default 'pending',
  attempts bigint not null default 0,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint linear_mutations_action_chk
    check (action in ('reference', 'reference_and_transition')),
  constraint linear_mutations_status_chk
    check (status in ('pending', 'succeeded', 'failed')),
  constraint linear_mutations_attempts_chk check (attempts >= 0)
);

create index if not exists linear_mutations_status_idx
  on ai_agent_coordinator.linear_mutations (status, updated_at);

create index if not exists linear_mutations_job_id_idx
  on ai_agent_coordinator.linear_mutations (job_id);

-- Durable daily portfolio briefing delivery state.
create sequence if not exists ai_agent_coordinator.daily_portfolio_delivery_fence_seq
  as bigint minvalue 1 no cycle;

create table if not exists ai_agent_coordinator.daily_portfolio_delivery_runs (
  run_key text primary key,
  scheduled_run_key text not null,
  mode text not null,
  source_digest text not null,
  plan_digest text not null,
  delivery_digest text not null,
  destination text not null,
  idempotency_key text not null,
  status text not null default 'planned',
  generation bigint not null default 0,
  attempts bigint not null default 0,
  last_error text,
  lease_owner text,
  lease_fence bigint,
  lease_expires_at timestamptz,
  receipt_id text,
  receipt_destination text,
  receipt_body_digest text,
  delivered_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint daily_portfolio_delivery_runs_idempotency_unique
    unique (idempotency_key),
  constraint daily_portfolio_delivery_runs_identifier_chk check (
    char_length(run_key) between 1 and 256
    and char_length(scheduled_run_key) between 1 and 256
    and char_length(destination) between 1 and 256
    and char_length(idempotency_key) between 1 and 256
    and run_key !~ '[[:cntrl:]]'
    and scheduled_run_key !~ '[[:cntrl:]]'
    and destination !~ '[[:cntrl:]]'
    and idempotency_key !~ '[[:cntrl:]]'
    and lower(run_key) !~ '(^gh[pousr]_|^github_pat_|^sk-|token=|password=|secret=)'
    and lower(destination) !~ '(^gh[pousr]_|^github_pat_|^sk-|token=|password=|secret=)'
  ),
  constraint daily_portfolio_delivery_runs_digest_chk check (
    source_digest ~ '^[0-9a-f]{64}$'
    and plan_digest ~ '^[0-9a-f]{64}$'
    and delivery_digest ~ '^[0-9a-f]{64}$'
  ),
  constraint daily_portfolio_delivery_runs_mode_chk
    check (mode in ('scheduled', 'recovery', 'manual')),
  constraint daily_portfolio_delivery_runs_status_chk
    check (status in ('planned', 'delivering', 'ambiguous', 'failed', 'delivered')),
  constraint daily_portfolio_delivery_runs_identity_chk check (
    scheduled_run_key ~ '^daily-portfolio:scheduled:[0-9]{4}-[0-9]{2}-[0-9]{2}$'
    and idempotency_key = run_key
    and (
      (mode = 'scheduled' and run_key = scheduled_run_key)
      or (
        mode = 'recovery'
        and run_key ~ '^daily-portfolio:recovery:[A-Za-z0-9._:/-]{1,220}$'
        and run_key <> scheduled_run_key
      )
      or (
        mode = 'manual'
        and run_key ~ '^daily-portfolio:manual:[A-Za-z0-9._:/-]{1,222}$'
        and run_key <> scheduled_run_key
      )
    )
  ),
  constraint daily_portfolio_delivery_runs_counter_chk
    check (generation >= 0 and attempts >= 0),
  constraint daily_portfolio_delivery_runs_error_chk check (
    last_error is null
    or (
      char_length(last_error) between 1 and 512
      and last_error !~ '[[:cntrl:]]'
    )
  ),
  constraint daily_portfolio_delivery_runs_lease_chk check (
    (
      lease_owner is null
      and lease_fence is null
      and lease_expires_at is null
    )
    or (
      lease_owner is not null
      and char_length(lease_owner) between 1 and 256
      and lease_owner !~ '[[:cntrl:]]'
      and lower(lease_owner) !~ '(^gh[pousr]_|^github_pat_|^sk-|token=|password=|secret=)'
      and lease_fence > 0
      and lease_expires_at is not null
    )
  ),
  constraint daily_portfolio_delivery_runs_state_chk check (
    (status = 'delivering') = (lease_owner is not null)
    or status in ('planned', 'failed', 'ambiguous')
  ),
  constraint daily_portfolio_delivery_runs_error_state_chk check (
    (status in ('failed', 'ambiguous')) = (last_error is not null)
  ),
  constraint daily_portfolio_delivery_runs_receipt_chk check (
    (
      receipt_id is null
      and receipt_destination is null
      and receipt_body_digest is null
      and delivered_at is null
    )
    or (
      receipt_id is not null
      and char_length(receipt_id) between 1 and 256
      and receipt_id !~ '[[:cntrl:]]'
      and receipt_destination = destination
      and receipt_body_digest = delivery_digest
      and delivered_at is not null
    )
  ),
  constraint daily_portfolio_delivery_runs_terminal_chk check (
    (status = 'delivered') = (receipt_id is not null)
    and (status <> 'delivered' or lease_owner is null)
    and (status <> 'delivered' or last_error is null)
  ),
  constraint daily_portfolio_delivery_runs_time_chk
    check (updated_at >= created_at)
);

create index if not exists daily_portfolio_delivery_runs_status_idx
  on ai_agent_coordinator.daily_portfolio_delivery_runs
  (status, updated_at, created_at);

create index if not exists daily_portfolio_delivery_runs_lease_expiry_idx
  on ai_agent_coordinator.daily_portfolio_delivery_runs (lease_expires_at)
  where lease_expires_at is not null;

create table if not exists ai_agent_coordinator.daily_portfolio_delivery_baseline (
  singleton_key text primary key default 'scheduled',
  source_run_key text not null references ai_agent_coordinator.daily_portfolio_delivery_runs (run_key) on delete restrict,
  scheduled_run_key text not null,
  plan_digest text not null,
  delivery_digest text not null,
  receipt_id text not null,
  delivered_at timestamptz not null,
  updated_at timestamptz not null default now(),
  constraint daily_portfolio_delivery_baseline_singleton_chk
    check (singleton_key = 'scheduled'),
  constraint daily_portfolio_delivery_baseline_scheduled_key_chk
    check (scheduled_run_key ~ '^daily-portfolio:scheduled:[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
  constraint daily_portfolio_delivery_baseline_digest_chk check (
    plan_digest ~ '^[0-9a-f]{64}$'
    and delivery_digest ~ '^[0-9a-f]{64}$'
  ),
  constraint daily_portfolio_delivery_baseline_receipt_chk check (
    char_length(receipt_id) between 1 and 256
    and receipt_id !~ '[[:cntrl:]]'
  ),
  constraint daily_portfolio_delivery_baseline_time_chk
    check (updated_at >= delivered_at)
);
