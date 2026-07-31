BEGIN;

CREATE TABLE memebank.storage_connections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    library_id uuid NOT NULL REFERENCES memebank.libraries(id) ON DELETE CASCADE,
    owner_user_id uuid,
    provider_kind memebank.provider_kind NOT NULL,
    ownership memebank.storage_ownership NOT NULL,
    display_name text NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 160),
    secret_ref text CHECK (
        secret_ref IS NULL
        OR (
            char_length(secret_ref) BETWEEN 1 AND 512
            AND secret_ref !~* '(token|password|secret)='
            AND secret_ref !~* '^https?://'
        )
    ),
    capability_snapshot jsonb NOT NULL CHECK (jsonb_typeof(capability_snapshot) = 'object'),
    state text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'revoked', 'error', 'disabled')),
    last_verified_at timestamptz,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (library_id, id),
    CHECK (
        (ownership = 'user_owned' AND owner_user_id IS NOT NULL)
        OR (ownership <> 'user_owned')
    )
);

CREATE TABLE memebank.storage_locations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    library_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    blob_id uuid NOT NULL REFERENCES memebank_private.blobs(id) ON DELETE RESTRICT,
    provider_namespace text CHECK (provider_namespace IS NULL OR char_length(provider_namespace) <= 512),
    provider_file_id text CHECK (provider_file_id IS NULL OR char_length(provider_file_id) <= 1024),
    object_key text CHECK (object_key IS NULL OR char_length(object_key) <= 2048),
    opaque_revision text CHECK (opaque_revision IS NULL OR char_length(opaque_revision) <= 512),
    etag text CHECK (etag IS NULL OR char_length(etag) <= 512),
    version_id text CHECK (version_id IS NULL OR char_length(version_id) <= 1024),
    state memebank.storage_location_state NOT NULL DEFAULT 'pending',
    integrity_algorithm memebank.digest_algorithm,
    integrity_hex text CHECK (integrity_hex IS NULL OR integrity_hex ~ '^[0-9a-f]{64}$'),
    last_verified_at timestamptz,
    missing_since timestamptz,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (library_id, id),
    FOREIGN KEY (library_id, connection_id)
        REFERENCES memebank.storage_connections(library_id, id)
        ON DELETE CASCADE,
    CHECK (num_nonnulls(provider_file_id, object_key) = 1),
    CHECK ((integrity_algorithm IS NULL) = (integrity_hex IS NULL)),
    CHECK (state <> 'missing' OR missing_since IS NOT NULL)
);

CREATE TABLE memebank.storage_location_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    library_id uuid NOT NULL,
    location_id uuid NOT NULL,
    from_state memebank.storage_location_state,
    to_state memebank.storage_location_state NOT NULL,
    actor_kind memebank.actor_kind NOT NULL,
    actor_user_id uuid,
    reason_code text NOT NULL CHECK (reason_code ~ '^[a-z][a-z0-9_]{2,63}$'),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (library_id, location_id)
        REFERENCES memebank.storage_locations(library_id, id)
        ON DELETE CASCADE,
    CHECK (
        (actor_kind = 'user' AND actor_user_id IS NOT NULL)
        OR (actor_kind <> 'user')
    )
);

CREATE UNIQUE INDEX storage_locations_external_file_idx
    ON memebank.storage_locations (connection_id, provider_namespace, provider_file_id)
    WHERE provider_file_id IS NOT NULL AND state <> 'deleted';

CREATE UNIQUE INDEX storage_locations_object_key_idx
    ON memebank.storage_locations (connection_id, provider_namespace, object_key, COALESCE(version_id, ''))
    WHERE object_key IS NOT NULL AND state <> 'deleted';

CREATE INDEX storage_locations_blob_state_idx
    ON memebank.storage_locations (library_id, blob_id, state);

CREATE INDEX storage_location_events_location_idx
    ON memebank.storage_location_events (library_id, location_id, occurred_at DESC, id DESC);

COMMIT;
