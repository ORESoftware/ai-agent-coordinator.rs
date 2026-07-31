BEGIN;

CREATE FUNCTION memebank_private.current_user_id()
RETURNS uuid
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
    SELECT nullif(current_setting('memebank.user_id', true), '')::uuid
$$;

CREATE FUNCTION memebank_private.current_worker_library_id()
RETURNS uuid
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
    SELECT nullif(current_setting('memebank.library_id', true), '')::uuid
$$;

CREATE FUNCTION memebank_private.has_library_access(
    target_library_id uuid,
    write_required boolean DEFAULT false,
    owner_required boolean DEFAULT false
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memebank, memebank_private
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM memebank.library_memberships AS membership
        WHERE membership.library_id = target_library_id
          AND membership.user_id = memebank_private.current_user_id()
          AND membership.status = 'active'
          AND (
              NOT write_required
              OR membership.role IN ('editor', 'owner')
          )
          AND (
              NOT owner_required
              OR membership.role = 'owner'
          )
    )
$$;

ALTER FUNCTION memebank_private.has_library_access(uuid, boolean, boolean)
    OWNER TO mb_policy_owner;

CREATE FUNCTION memebank_private.worker_has_library_access(target_library_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
    SELECT memebank_private.current_worker_library_id() = target_library_id
$$;

CREATE FUNCTION memebank_private.touch_revision()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := clock_timestamp();
    NEW.revision := OLD.revision + 1;
    RETURN NEW;
END
$$;

CREATE FUNCTION memebank_private.forbid_append_only_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
        USING ERRCODE = '55000';
END
$$;

CREATE FUNCTION memebank_private.validate_observation_payload()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    payload_kind text;
BEGIN
    payload_kind := NEW.payload ->> 'payload_kind';
    IF payload_kind IS NULL THEN
        RAISE EXCEPTION 'observation payload_kind is required'
            USING ERRCODE = '23514';
    END IF;

    IF (NEW.kind = 'ocr_span' AND payload_kind <> 'ocr')
       OR (NEW.kind IN ('object', 'label', 'scene') AND payload_kind <> 'label')
       OR (NEW.kind = 'caption' AND payload_kind <> 'caption')
       OR (NEW.kind = 'moderation' AND payload_kind <> 'moderation')
       OR (NEW.kind = 'embedding' AND payload_kind <> 'embedding') THEN
        RAISE EXCEPTION 'payload_kind % is incompatible with observation kind %', payload_kind, NEW.kind
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END
$$;

CREATE FUNCTION memebank_private.validate_embedding_model()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, memebank, memebank_private
AS $$
DECLARE
    expected_dimension integer := TG_ARGV[0]::integer;
    model_record memebank.embedding_models%ROWTYPE;
BEGIN
    SELECT * INTO STRICT model_record
    FROM memebank.embedding_models
    WHERE id = NEW.model_id;

    IF model_record.dimension <> expected_dimension THEN
        RAISE EXCEPTION 'model dimension % does not match table dimension %', model_record.dimension, expected_dimension
            USING ERRCODE = '23514';
    END IF;

    IF model_record.space <> NEW.space
       OR model_record.metric <> NEW.metric
       OR model_record.model_revision <> NEW.model_revision THEN
        RAISE EXCEPTION 'embedding row provenance does not match model registry'
            USING ERRCODE = '23514';
    END IF;

    IF model_record.status NOT IN ('staging', 'active', 'retiring') THEN
        RAISE EXCEPTION 'retired model % cannot accept embeddings', model_record.model_key
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END
$$;

ALTER FUNCTION memebank_private.validate_embedding_model()
    OWNER TO mb_policy_owner;

CREATE FUNCTION memebank_private.refresh_asset_search_document(target_asset_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, memebank, memebank_private
AS $$
DECLARE
    target_library_id uuid;
BEGIN
    SELECT asset.library_id INTO STRICT target_library_id
    FROM memebank.assets AS asset
    WHERE asset.id = target_asset_id;

    IF NOT (
        memebank_private.worker_has_library_access(target_library_id)
        OR memebank_private.has_library_access(target_library_id, true, false)
    ) THEN
        RAISE EXCEPTION 'search document refresh denied for library %', target_library_id
            USING ERRCODE = '42501';
    END IF;

    INSERT INTO memebank.asset_search_documents (
        asset_id,
        library_id,
        source_revision,
        title_text,
        note_text,
        confirmed_tags_text,
        ocr_text,
        selected_caption_text,
        refreshed_at
    )
    SELECT
        asset.id,
        asset.library_id,
        asset.revision,
        coalesce(asset.title, ''),
        coalesce(asset.note, ''),
        coalesce((
            SELECT string_agg(tag.display_name, ' ' ORDER BY tag.display_name)
            FROM memebank.asset_tag_decisions AS decision
            JOIN memebank.tags AS tag
              ON tag.library_id = decision.library_id
             AND tag.id = decision.tag_id
            WHERE decision.library_id = asset.library_id
              AND decision.asset_id = asset.id
              AND decision.decision = 'confirmed'
        ), ''),
        coalesce((
            SELECT string_agg(region.text_content, ' ' ORDER BY region.reading_order, region.id)
            FROM memebank.ocr_regions AS region
            JOIN memebank.enrichment_observations AS observation
              ON observation.library_id = region.library_id
             AND observation.id = region.observation_id
            WHERE region.library_id = asset.library_id
              AND region.asset_id = asset.id
              AND observation.status IN ('generated', 'confirmed')
        ), ''),
        coalesce((
            SELECT string_agg(observation.payload ->> 'text', ' ' ORDER BY observation.created_at, observation.id)
            FROM memebank.enrichment_observations AS observation
            WHERE observation.library_id = asset.library_id
              AND observation.asset_id = asset.id
              AND observation.kind = 'caption'
              AND observation.status = 'confirmed'
              AND jsonb_typeof(observation.payload -> 'text') = 'string'
        ), ''),
        clock_timestamp()
    FROM memebank.assets AS asset
    WHERE asset.id = target_asset_id
    ON CONFLICT (asset_id) DO UPDATE
    SET library_id = EXCLUDED.library_id,
        source_revision = EXCLUDED.source_revision,
        title_text = EXCLUDED.title_text,
        note_text = EXCLUDED.note_text,
        confirmed_tags_text = EXCLUDED.confirmed_tags_text,
        ocr_text = EXCLUDED.ocr_text,
        selected_caption_text = EXCLUDED.selected_caption_text,
        refreshed_at = EXCLUDED.refreshed_at;
END
$$;

ALTER FUNCTION memebank_private.refresh_asset_search_document(uuid)
    OWNER TO mb_policy_owner;

CREATE FUNCTION memebank.claim_jobs(
    worker_id text,
    claim_limit integer,
    lease_seconds integer
)
RETURNS SETOF memebank.jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, memebank, memebank_private
AS $$
DECLARE
    scoped_library_id uuid := memebank_private.current_worker_library_id();
BEGIN
    IF scoped_library_id IS NULL THEN
        RAISE EXCEPTION 'memebank.library_id worker scope is required'
            USING ERRCODE = '42501';
    END IF;

    IF char_length(worker_id) NOT BETWEEN 1 AND 200
       OR claim_limit NOT BETWEEN 1 AND 100
       OR lease_seconds NOT BETWEEN 5 AND 3600 THEN
        RAISE EXCEPTION 'invalid claim arguments'
            USING ERRCODE = '22023';
    END IF;

    RETURN QUERY
    WITH candidates AS (
        SELECT job.id
        FROM memebank.jobs AS job
        WHERE job.library_id = scoped_library_id
          AND job.state = 'queued'
          AND job.available_at <= clock_timestamp()
          AND job.attempt_count < job.max_attempts
        ORDER BY job.priority DESC, job.available_at, job.created_at, job.id
        FOR UPDATE SKIP LOCKED
        LIMIT claim_limit
    )
    UPDATE memebank.jobs AS job
    SET state = 'leased',
        lease_owner = worker_id,
        lease_epoch = job.lease_epoch + 1,
        lease_expires_at = clock_timestamp() + make_interval(secs => lease_seconds),
        attempt_count = job.attempt_count + 1,
        updated_at = clock_timestamp()
    FROM candidates
    WHERE job.id = candidates.id
    RETURNING job.*;
END
$$;

ALTER FUNCTION memebank.claim_jobs(text, integer, integer)
    OWNER TO mb_policy_owner;

CREATE FUNCTION memebank.renew_job_lease(
    target_job_id uuid,
    worker_id text,
    expected_lease_epoch bigint,
    lease_seconds integer
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, memebank, memebank_private
AS $$
DECLARE
    scoped_library_id uuid := memebank_private.current_worker_library_id();
    updated_count integer;
BEGIN
    IF scoped_library_id IS NULL OR lease_seconds NOT BETWEEN 5 AND 3600 THEN
        RETURN false;
    END IF;

    UPDATE memebank.jobs AS job
    SET lease_expires_at = clock_timestamp() + make_interval(secs => lease_seconds),
        updated_at = clock_timestamp()
    WHERE job.id = target_job_id
      AND job.library_id = scoped_library_id
      AND job.state = 'leased'
      AND job.lease_owner = worker_id
      AND job.lease_epoch = expected_lease_epoch
      AND job.lease_expires_at > clock_timestamp();

    GET DIAGNOSTICS updated_count = ROW_COUNT;
    RETURN updated_count = 1;
END
$$;

ALTER FUNCTION memebank.renew_job_lease(uuid, text, bigint, integer)
    OWNER TO mb_policy_owner;

CREATE TRIGGER libraries_touch_revision
BEFORE UPDATE ON memebank.libraries
FOR EACH ROW EXECUTE FUNCTION memebank_private.touch_revision();

CREATE TRIGGER memberships_touch_revision
BEFORE UPDATE ON memebank.library_memberships
FOR EACH ROW EXECUTE FUNCTION memebank_private.touch_revision();

CREATE TRIGGER assets_touch_revision
BEFORE UPDATE ON memebank.assets
FOR EACH ROW EXECUTE FUNCTION memebank_private.touch_revision();

CREATE TRIGGER variants_touch_revision
BEFORE UPDATE ON memebank.asset_variants
FOR EACH ROW EXECUTE FUNCTION memebank_private.touch_revision();

CREATE TRIGGER storage_connections_touch_revision
BEFORE UPDATE ON memebank.storage_connections
FOR EACH ROW EXECUTE FUNCTION memebank_private.touch_revision();

CREATE TRIGGER storage_locations_touch_revision
BEFORE UPDATE ON memebank.storage_locations
FOR EACH ROW EXECUTE FUNCTION memebank_private.touch_revision();

CREATE TRIGGER observations_touch_revision
BEFORE UPDATE ON memebank.enrichment_observations
FOR EACH ROW EXECUTE FUNCTION memebank_private.touch_revision();

CREATE TRIGGER tags_touch_revision
BEFORE UPDATE ON memebank.tags
FOR EACH ROW EXECUTE FUNCTION memebank_private.touch_revision();

CREATE TRIGGER tag_decisions_touch_revision
BEFORE UPDATE ON memebank.asset_tag_decisions
FOR EACH ROW EXECUTE FUNCTION memebank_private.touch_revision();

CREATE TRIGGER export_requests_touch_revision
BEFORE UPDATE ON memebank.export_requests
FOR EACH ROW EXECUTE FUNCTION memebank_private.touch_revision();

CREATE TRIGGER deletion_requests_touch_revision
BEFORE UPDATE ON memebank.deletion_requests
FOR EACH ROW EXECUTE FUNCTION memebank_private.touch_revision();

CREATE TRIGGER observation_payload_guard
BEFORE INSERT OR UPDATE OF kind, payload ON memebank.enrichment_observations
FOR EACH ROW EXECUTE FUNCTION memebank_private.validate_observation_payload();

CREATE TRIGGER embedding_384_model_guard
BEFORE INSERT OR UPDATE ON memebank.asset_embeddings_384
FOR EACH ROW EXECUTE FUNCTION memebank_private.validate_embedding_model('384');

CREATE TRIGGER embedding_768_model_guard
BEFORE INSERT OR UPDATE ON memebank.asset_embeddings_768
FOR EACH ROW EXECUTE FUNCTION memebank_private.validate_embedding_model('768');

CREATE TRIGGER embedding_1024_model_guard
BEFORE INSERT OR UPDATE ON memebank.asset_embeddings_1024
FOR EACH ROW EXECUTE FUNCTION memebank_private.validate_embedding_model('1024');

CREATE TRIGGER storage_location_events_append_only
BEFORE UPDATE OR DELETE ON memebank.storage_location_events
FOR EACH ROW EXECUTE FUNCTION memebank_private.forbid_append_only_mutation();

CREATE TRIGGER job_events_append_only
BEFORE UPDATE OR DELETE ON memebank.job_events
FOR EACH ROW EXECUTE FUNCTION memebank_private.forbid_append_only_mutation();

CREATE TRIGGER audit_events_append_only
BEFORE UPDATE OR DELETE ON memebank.audit_events
FOR EACH ROW EXECUTE FUNCTION memebank_private.forbid_append_only_mutation();

CREATE INDEX embedding_384_lookup_idx
    ON memebank.asset_embeddings_384 (library_id, model_id, asset_id);
CREATE INDEX embedding_768_lookup_idx
    ON memebank.asset_embeddings_768 (library_id, model_id, asset_id);
CREATE INDEX embedding_1024_lookup_idx
    ON memebank.asset_embeddings_1024 (library_id, model_id, asset_id);

CREATE INDEX embedding_384_cosine_hnsw_idx
    ON memebank.asset_embeddings_384 USING hnsw (embedding vector_cosine_ops)
    WHERE metric = 'cosine';
CREATE INDEX embedding_384_ip_hnsw_idx
    ON memebank.asset_embeddings_384 USING hnsw (embedding vector_ip_ops)
    WHERE metric = 'inner_product';
CREATE INDEX embedding_384_l2_hnsw_idx
    ON memebank.asset_embeddings_384 USING hnsw (embedding vector_l2_ops)
    WHERE metric = 'l2';

CREATE INDEX embedding_768_cosine_hnsw_idx
    ON memebank.asset_embeddings_768 USING hnsw (embedding vector_cosine_ops)
    WHERE metric = 'cosine';
CREATE INDEX embedding_768_ip_hnsw_idx
    ON memebank.asset_embeddings_768 USING hnsw (embedding vector_ip_ops)
    WHERE metric = 'inner_product';
CREATE INDEX embedding_768_l2_hnsw_idx
    ON memebank.asset_embeddings_768 USING hnsw (embedding vector_l2_ops)
    WHERE metric = 'l2';

CREATE INDEX embedding_1024_cosine_hnsw_idx
    ON memebank.asset_embeddings_1024 USING hnsw (embedding vector_cosine_ops)
    WHERE metric = 'cosine';
CREATE INDEX embedding_1024_ip_hnsw_idx
    ON memebank.asset_embeddings_1024 USING hnsw (embedding vector_ip_ops)
    WHERE metric = 'inner_product';
CREATE INDEX embedding_1024_l2_hnsw_idx
    ON memebank.asset_embeddings_1024 USING hnsw (embedding vector_l2_ops)
    WHERE metric = 'l2';

COMMIT;
