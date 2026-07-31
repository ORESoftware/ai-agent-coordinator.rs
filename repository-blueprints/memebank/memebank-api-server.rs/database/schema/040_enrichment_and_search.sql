BEGIN;

CREATE TABLE memebank.enrichment_observations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    library_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    variant_id uuid NOT NULL,
    kind memebank.observation_kind NOT NULL,
    status memebank.observation_status NOT NULL DEFAULT 'generated',
    provider text NOT NULL CHECK (char_length(provider) BETWEEN 1 AND 100),
    runtime text NOT NULL CHECK (char_length(runtime) BETWEEN 1 AND 100),
    model_name text NOT NULL CHECK (char_length(model_name) BETWEEN 1 AND 160),
    model_revision text NOT NULL CHECK (char_length(model_revision) BETWEEN 1 AND 200),
    processor_version text NOT NULL CHECK (char_length(processor_version) BETWEEN 1 AND 160),
    recipe text NOT NULL CHECK (char_length(recipe) BETWEEN 1 AND 160),
    locale text CHECK (locale IS NULL OR locale ~ '^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$'),
    confidence real CHECK (confidence BETWEEN 0 AND 1),
    bounds jsonb CHECK (bounds IS NULL OR jsonb_typeof(bounds) = 'array'),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    supersedes_observation_id uuid REFERENCES memebank.enrichment_observations(id) ON DELETE SET NULL,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (library_id, id),
    FOREIGN KEY (library_id, asset_id)
        REFERENCES memebank.assets(library_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (library_id, variant_id)
        REFERENCES memebank.asset_variants(library_id, id)
        ON DELETE CASCADE,
    CHECK (payload ? 'payload_kind')
);

CREATE TABLE memebank.ocr_regions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    library_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    observation_id uuid NOT NULL,
    reading_order integer NOT NULL CHECK (reading_order >= 0),
    text_content text NOT NULL CHECK (char_length(text_content) BETWEEN 1 AND 20000),
    normalized_text text NOT NULL CHECK (char_length(normalized_text) BETWEEN 1 AND 20000),
    locale text CHECK (locale IS NULL OR locale ~ '^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$'),
    confidence real CHECK (confidence BETWEEN 0 AND 1),
    bounds jsonb NOT NULL CHECK (jsonb_typeof(bounds) = 'array'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (observation_id, reading_order),
    FOREIGN KEY (library_id, asset_id)
        REFERENCES memebank.assets(library_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (library_id, observation_id)
        REFERENCES memebank.enrichment_observations(library_id, id)
        ON DELETE CASCADE
);

CREATE TABLE memebank.tags (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    library_id uuid NOT NULL REFERENCES memebank.libraries(id) ON DELETE CASCADE,
    normalized_tag text NOT NULL CHECK (normalized_tag ~ '^[a-z0-9]+([._-][a-z0-9]+)*$'),
    display_name text NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 120),
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (library_id, normalized_tag),
    UNIQUE (library_id, id)
);

CREATE TABLE memebank.asset_tag_decisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    library_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    tag_id uuid NOT NULL,
    source memebank.tag_source NOT NULL,
    decision memebank.tag_decision NOT NULL,
    decided_by_user_id uuid,
    source_observation_id uuid,
    confidence real CHECK (confidence BETWEEN 0 AND 1),
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (library_id, asset_id)
        REFERENCES memebank.assets(library_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (library_id, tag_id)
        REFERENCES memebank.tags(library_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (library_id, source_observation_id)
        REFERENCES memebank.enrichment_observations(library_id, id)
        ON DELETE SET NULL,
    CHECK (
        (source = 'user' AND decided_by_user_id IS NOT NULL AND source_observation_id IS NULL)
        OR (source = 'generated' AND source_observation_id IS NOT NULL)
        OR (source = 'imported')
    )
);

CREATE TABLE memebank.embedding_models (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_key text NOT NULL UNIQUE CHECK (model_key ~ '^[a-z0-9]+([._:/-][a-z0-9]+)*$'),
    space memebank.embedding_space NOT NULL,
    model_name text NOT NULL CHECK (char_length(model_name) BETWEEN 1 AND 160),
    model_revision text NOT NULL CHECK (char_length(model_revision) BETWEEN 1 AND 200),
    processor_version text NOT NULL CHECK (char_length(processor_version) BETWEEN 1 AND 160),
    dimension integer NOT NULL CHECK (dimension IN (384, 768, 1024)),
    metric memebank.embedding_metric NOT NULL,
    provenance_kind text NOT NULL CHECK (provenance_kind IN ('local_artifact', 'remote_api')),
    source_uri text NOT NULL CHECK (char_length(source_uri) BETWEEN 1 AND 1000),
    artifact_sha256 text CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'),
    license text NOT NULL CHECK (char_length(license) BETWEEN 1 AND 160),
    redistribution_terms text NOT NULL CHECK (char_length(redistribution_terms) BETWEEN 1 AND 1000),
    status memebank.model_status NOT NULL DEFAULT 'staging',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    activated_at timestamptz,
    retired_at timestamptz,
    UNIQUE (space, model_name, model_revision, processor_version, dimension, metric),
    CHECK (
        (provenance_kind = 'local_artifact' AND artifact_sha256 IS NOT NULL)
        OR (provenance_kind = 'remote_api')
    ),
    CHECK (status <> 'active' OR activated_at IS NOT NULL),
    CHECK (status <> 'retired' OR retired_at IS NOT NULL)
);

CREATE TABLE memebank.embedding_search_routes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    space memebank.embedding_space NOT NULL,
    primary_model_id uuid NOT NULL REFERENCES memebank.embedding_models(id) ON DELETE RESTRICT,
    shadow_model_id uuid REFERENCES memebank.embedding_models(id) ON DELETE RESTRICT,
    cutover_state text NOT NULL DEFAULT 'stable' CHECK (cutover_state IN ('stable', 'backfilling', 'shadowing', 'cutting_over', 'rollback')),
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (space),
    CHECK (shadow_model_id IS NULL OR shadow_model_id <> primary_model_id)
);

CREATE TABLE memebank.asset_search_documents (
    asset_id uuid PRIMARY KEY,
    library_id uuid NOT NULL,
    source_revision bigint NOT NULL CHECK (source_revision >= 0),
    title_text text NOT NULL DEFAULT '',
    note_text text NOT NULL DEFAULT '',
    confirmed_tags_text text NOT NULL DEFAULT '',
    ocr_text text NOT NULL DEFAULT '',
    selected_caption_text text NOT NULL DEFAULT '',
    search_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('simple'::regconfig, coalesce(title_text, '')), 'A')
        || setweight(to_tsvector('simple'::regconfig, coalesce(confirmed_tags_text, '')), 'A')
        || setweight(to_tsvector('simple'::regconfig, coalesce(note_text, '')), 'B')
        || setweight(to_tsvector('simple'::regconfig, coalesce(ocr_text, '')), 'B')
        || setweight(to_tsvector('simple'::regconfig, coalesce(selected_caption_text, '')), 'C')
    ) STORED,
    refreshed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (library_id, asset_id)
        REFERENCES memebank.assets(library_id, id)
        ON DELETE CASCADE
);

CREATE TABLE memebank.asset_embeddings_384 (
    library_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    variant_id uuid NOT NULL,
    model_id uuid NOT NULL REFERENCES memebank.embedding_models(id) ON DELETE RESTRICT,
    source_observation_id uuid REFERENCES memebank.enrichment_observations(id) ON DELETE SET NULL,
    space memebank.embedding_space NOT NULL,
    metric memebank.embedding_metric NOT NULL,
    model_revision text NOT NULL,
    source_text_digest_hex text CHECK (source_text_digest_hex IS NULL OR source_text_digest_hex ~ '^[0-9a-f]{64}$'),
    embedding vector(384) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (model_id, asset_id),
    FOREIGN KEY (library_id, asset_id)
        REFERENCES memebank.assets(library_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (library_id, variant_id)
        REFERENCES memebank.asset_variants(library_id, id)
        ON DELETE CASCADE,
    CHECK ((space = 'native_visual') = (source_text_digest_hex IS NULL))
);

CREATE TABLE memebank.asset_embeddings_768 (
    library_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    variant_id uuid NOT NULL,
    model_id uuid NOT NULL REFERENCES memebank.embedding_models(id) ON DELETE RESTRICT,
    source_observation_id uuid REFERENCES memebank.enrichment_observations(id) ON DELETE SET NULL,
    space memebank.embedding_space NOT NULL,
    metric memebank.embedding_metric NOT NULL,
    model_revision text NOT NULL,
    source_text_digest_hex text CHECK (source_text_digest_hex IS NULL OR source_text_digest_hex ~ '^[0-9a-f]{64}$'),
    embedding vector(768) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (model_id, asset_id),
    FOREIGN KEY (library_id, asset_id)
        REFERENCES memebank.assets(library_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (library_id, variant_id)
        REFERENCES memebank.asset_variants(library_id, id)
        ON DELETE CASCADE,
    CHECK ((space = 'native_visual') = (source_text_digest_hex IS NULL))
);

CREATE TABLE memebank.asset_embeddings_1024 (
    library_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    variant_id uuid NOT NULL,
    model_id uuid NOT NULL REFERENCES memebank.embedding_models(id) ON DELETE RESTRICT,
    source_observation_id uuid REFERENCES memebank.enrichment_observations(id) ON DELETE SET NULL,
    space memebank.embedding_space NOT NULL,
    metric memebank.embedding_metric NOT NULL,
    model_revision text NOT NULL,
    source_text_digest_hex text CHECK (source_text_digest_hex IS NULL OR source_text_digest_hex ~ '^[0-9a-f]{64}$'),
    embedding vector(1024) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (model_id, asset_id),
    FOREIGN KEY (library_id, asset_id)
        REFERENCES memebank.assets(library_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (library_id, variant_id)
        REFERENCES memebank.asset_variants(library_id, id)
        ON DELETE CASCADE,
    CHECK ((space = 'native_visual') = (source_text_digest_hex IS NULL))
);

CREATE UNIQUE INDEX asset_tag_decisions_identity_idx
    ON memebank.asset_tag_decisions (
        asset_id,
        tag_id,
        source,
        COALESCE(decided_by_user_id, '00000000-0000-0000-0000-000000000000'::uuid),
        COALESCE(source_observation_id, '00000000-0000-0000-0000-000000000000'::uuid)
    );

CREATE INDEX enrichment_asset_kind_status_idx
    ON memebank.enrichment_observations (library_id, asset_id, kind, status, created_at DESC);

CREATE INDEX ocr_regions_asset_order_idx
    ON memebank.ocr_regions (library_id, asset_id, reading_order);

CREATE INDEX tags_name_trgm_idx
    ON memebank.tags USING gin (display_name gin_trgm_ops);

CREATE INDEX asset_search_documents_tsv_idx
    ON memebank.asset_search_documents USING gin (search_vector);

COMMIT;
