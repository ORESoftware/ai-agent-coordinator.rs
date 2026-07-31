BEGIN;

CREATE TABLE memebank.jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    library_id uuid NOT NULL REFERENCES memebank.libraries(id) ON DELETE CASCADE,
    asset_id uuid,
    kind text NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]{2,63}$'),
    idempotency_key text NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9._:-]{16,200}$'),
    state memebank.job_state NOT NULL DEFAULT 'queued',
    priority smallint NOT NULL DEFAULT 0 CHECK (priority BETWEEN -100 AND 100),
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_owner text CHECK (lease_owner IS NULL OR char_length(lease_owner) BETWEEN 1 AND 200),
    lease_epoch bigint NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    lease_expires_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts integer NOT NULL DEFAULT 8 CHECK (max_attempts BETWEEN 1 AND 100),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    result jsonb CHECK (result IS NULL OR jsonb_typeof(result) = 'object'),
    last_error jsonb CHECK (last_error IS NULL OR jsonb_typeof(last_error) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    UNIQUE (library_id, id),
    UNIQUE (library_id, kind, idempotency_key),
    FOREIGN KEY (library_id, asset_id)
        REFERENCES memebank.assets(library_id, id)
        ON DELETE CASCADE,
    CHECK (
        (state = 'leased' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (state <> 'leased' AND lease_owner IS NULL AND lease_expires_at IS NULL)
    ),
    CHECK (
        (state IN ('succeeded', 'failed', 'canceled') AND completed_at IS NOT NULL)
        OR (state NOT IN ('succeeded', 'failed', 'canceled') AND completed_at IS NULL)
    ),
    CHECK (attempt_count <= max_attempts)
);

CREATE TABLE memebank.job_attempts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    library_id uuid NOT NULL,
    job_id uuid NOT NULL,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    lease_epoch bigint NOT NULL CHECK (lease_epoch > 0),
    worker_id text NOT NULL CHECK (char_length(worker_id) BETWEEN 1 AND 200),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    outcome text CHECK (outcome IS NULL OR outcome IN ('succeeded', 'retryable_failure', 'terminal_failure', 'lease_lost', 'canceled')),
    error jsonb CHECK (error IS NULL OR jsonb_typeof(error) = 'object'),
    UNIQUE (job_id, attempt_number),
    FOREIGN KEY (library_id, job_id)
        REFERENCES memebank.jobs(library_id, id)
        ON DELETE CASCADE,
    CHECK ((finished_at IS NULL) = (outcome IS NULL))
);

CREATE TABLE memebank.job_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    library_id uuid NOT NULL,
    job_id uuid NOT NULL,
    lease_epoch bigint NOT NULL CHECK (lease_epoch >= 0),
    event_kind text NOT NULL CHECK (event_kind ~ '^[a-z][a-z0-9_]{2,63}$'),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(payload) = 'object'),
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (library_id, job_id)
        REFERENCES memebank.jobs(library_id, id)
        ON DELETE CASCADE
);

CREATE TABLE memebank.outbox_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    library_id uuid NOT NULL REFERENCES memebank.libraries(id) ON DELETE CASCADE,
    aggregate_kind text NOT NULL CHECK (aggregate_kind ~ '^[a-z][a-z0-9_]{2,63}$'),
    aggregate_id uuid NOT NULL,
    topic text NOT NULL CHECK (topic ~ '^[a-z][a-z0-9._-]{2,159}$'),
    event_key text NOT NULL CHECK (char_length(event_key) BETWEEN 1 AND 512),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    state memebank.outbox_state NOT NULL DEFAULT 'pending',
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_owner text,
    lease_epoch bigint NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    lease_expires_at timestamptz,
    published_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error jsonb CHECK (last_error IS NULL OR jsonb_typeof(last_error) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (topic, event_key),
    CHECK (
        (state = 'publishing' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (state <> 'publishing' AND lease_owner IS NULL AND lease_expires_at IS NULL)
    ),
    CHECK (state <> 'published' OR published_at IS NOT NULL)
);

CREATE TABLE memebank.export_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    library_id uuid NOT NULL REFERENCES memebank.libraries(id) ON DELETE CASCADE,
    requested_by_user_id uuid NOT NULL,
    state memebank.lifecycle_request_state NOT NULL DEFAULT 'requested',
    format text NOT NULL CHECK (format IN ('jsonl_zip', 'portable_archive')),
    include_originals boolean NOT NULL DEFAULT true,
    manifest_blob_id uuid REFERENCES memebank_private.blobs(id) ON DELETE RESTRICT,
    failure jsonb CHECK (failure IS NULL OR jsonb_typeof(failure) = 'object'),
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    CHECK (state <> 'succeeded' OR manifest_blob_id IS NOT NULL),
    CHECK (
        (state IN ('succeeded', 'failed', 'canceled') AND completed_at IS NOT NULL)
        OR (state NOT IN ('succeeded', 'failed', 'canceled') AND completed_at IS NULL)
    )
);

CREATE TABLE memebank.deletion_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    library_id uuid NOT NULL REFERENCES memebank.libraries(id) ON DELETE CASCADE,
    asset_id uuid,
    requested_by_user_id uuid NOT NULL,
    state memebank.lifecycle_request_state NOT NULL DEFAULT 'requested',
    delete_provider_objects boolean NOT NULL DEFAULT false,
    plan jsonb CHECK (plan IS NULL OR jsonb_typeof(plan) = 'object'),
    blockers jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(blockers) = 'array'),
    failure jsonb CHECK (failure IS NULL OR jsonb_typeof(failure) = 'object'),
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    FOREIGN KEY (library_id, asset_id)
        REFERENCES memebank.assets(library_id, id)
        ON DELETE SET NULL,
    CHECK (
        (state IN ('succeeded', 'failed', 'canceled') AND completed_at IS NOT NULL)
        OR (state NOT IN ('succeeded', 'failed', 'canceled') AND completed_at IS NULL)
    )
);

CREATE TABLE memebank.reconciliation_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    library_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    state memebank.reconciliation_state NOT NULL DEFAULT 'queued',
    scan_cursor text CHECK (scan_cursor IS NULL OR char_length(scan_cursor) <= 2048),
    scanned_count bigint NOT NULL DEFAULT 0 CHECK (scanned_count >= 0),
    missing_count bigint NOT NULL DEFAULT 0 CHECK (missing_count >= 0),
    orphan_count bigint NOT NULL DEFAULT 0 CHECK (orphan_count >= 0),
    repaired_count bigint NOT NULL DEFAULT 0 CHECK (repaired_count >= 0),
    failure jsonb CHECK (failure IS NULL OR jsonb_typeof(failure) = 'object'),
    started_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    FOREIGN KEY (library_id, connection_id)
        REFERENCES memebank.storage_connections(library_id, id)
        ON DELETE CASCADE,
    CHECK (
        (state IN ('succeeded', 'failed', 'canceled') AND completed_at IS NOT NULL)
        OR (state NOT IN ('succeeded', 'failed', 'canceled') AND completed_at IS NULL)
    )
);

CREATE TABLE memebank.audit_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    library_id uuid NOT NULL REFERENCES memebank.libraries(id) ON DELETE CASCADE,
    actor_kind memebank.actor_kind NOT NULL,
    actor_user_id uuid,
    request_id uuid NOT NULL,
    action text NOT NULL CHECK (action ~ '^[a-z][a-z0-9._-]{2,159}$'),
    entity_kind text NOT NULL CHECK (entity_kind ~ '^[a-z][a-z0-9_]{2,63}$'),
    entity_id uuid,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        (actor_kind = 'user' AND actor_user_id IS NOT NULL)
        OR (actor_kind <> 'user')
    )
);

CREATE INDEX jobs_claim_idx
    ON memebank.jobs (library_id, priority DESC, available_at, created_at, id)
    WHERE state = 'queued';

CREATE INDEX jobs_expired_lease_idx
    ON memebank.jobs (lease_expires_at, library_id, id)
    WHERE state = 'leased';

CREATE INDEX job_events_job_idx
    ON memebank.job_events (library_id, job_id, occurred_at, id);

CREATE INDEX outbox_claim_idx
    ON memebank.outbox_events (available_at, created_at, id)
    WHERE state IN ('pending', 'failed');

CREATE INDEX lifecycle_export_state_idx
    ON memebank.export_requests (library_id, state, created_at, id);

CREATE INDEX lifecycle_deletion_state_idx
    ON memebank.deletion_requests (library_id, state, created_at, id);

CREATE INDEX reconciliation_state_idx
    ON memebank.reconciliation_runs (library_id, connection_id, state, updated_at, id);

CREATE INDEX audit_library_time_idx
    ON memebank.audit_events (library_id, occurred_at DESC, id DESC);

COMMIT;
