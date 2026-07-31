BEGIN;

REVOKE ALL ON SCHEMA memebank FROM PUBLIC;
REVOKE ALL ON SCHEMA memebank_private FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA memebank FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA memebank_private FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA memebank FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA memebank_private FROM PUBLIC;

GRANT USAGE ON SCHEMA memebank TO mb_app, mb_worker, mb_policy_owner;
GRANT USAGE ON SCHEMA memebank_private TO mb_worker, mb_policy_owner;

ALTER TABLE memebank.libraries ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.libraries FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.library_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.library_memberships FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.assets FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.asset_variants ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.asset_variants FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.perceptual_hashes ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.perceptual_hashes FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.storage_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.storage_connections FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.storage_locations ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.storage_locations FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.storage_location_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.storage_location_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.enrichment_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.enrichment_observations FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.ocr_regions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.ocr_regions FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.tags FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.asset_tag_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.asset_tag_decisions FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.embedding_models ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.embedding_models FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.embedding_search_routes ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.embedding_search_routes FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.asset_search_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.asset_search_documents FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.asset_embeddings_384 ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.asset_embeddings_384 FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.asset_embeddings_768 ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.asset_embeddings_768 FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.asset_embeddings_1024 ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.asset_embeddings_1024 FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.jobs FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.job_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.job_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.job_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.job_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.outbox_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.outbox_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.export_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.export_requests FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.deletion_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.deletion_requests FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.reconciliation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.reconciliation_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank.audit_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memebank_private.blobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE memebank_private.blobs FORCE ROW LEVEL SECURITY;

CREATE POLICY libraries_app_select ON memebank.libraries
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(id, false, false));
CREATE POLICY libraries_app_insert ON memebank.libraries
    FOR INSERT TO mb_app
    WITH CHECK (owner_user_id = memebank_private.current_user_id());
CREATE POLICY libraries_app_update ON memebank.libraries
    FOR UPDATE TO mb_app
    USING (memebank_private.has_library_access(id, true, true))
    WITH CHECK (owner_user_id = memebank_private.current_user_id());
CREATE POLICY libraries_worker_select ON memebank.libraries
    FOR SELECT TO mb_worker
    USING (memebank_private.worker_has_library_access(id));

CREATE POLICY memberships_app_select ON memebank.library_memberships
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY memberships_app_manage ON memebank.library_memberships
    FOR ALL TO mb_app
    USING (memebank_private.has_library_access(library_id, true, true))
    WITH CHECK (memebank_private.has_library_access(library_id, true, true));
CREATE POLICY memberships_worker_select ON memebank.library_memberships
    FOR SELECT TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id));

CREATE POLICY assets_app_select ON memebank.assets
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY assets_app_write ON memebank.assets
    FOR ALL TO mb_app
    USING (memebank_private.has_library_access(library_id, true, false))
    WITH CHECK (memebank_private.has_library_access(library_id, true, false));
CREATE POLICY assets_worker_all ON memebank.assets
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY variants_app_select ON memebank.asset_variants
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY variants_worker_all ON memebank.asset_variants
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY perceptual_app_select ON memebank.perceptual_hashes
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY perceptual_worker_all ON memebank.perceptual_hashes
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY storage_connections_app_select ON memebank.storage_connections
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY storage_connections_app_write ON memebank.storage_connections
    FOR ALL TO mb_app
    USING (memebank_private.has_library_access(library_id, true, false))
    WITH CHECK (
        memebank_private.has_library_access(library_id, true, false)
        AND (owner_user_id IS NULL OR owner_user_id = memebank_private.current_user_id())
    );
CREATE POLICY storage_connections_worker_all ON memebank.storage_connections
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY storage_locations_app_select ON memebank.storage_locations
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY storage_locations_worker_all ON memebank.storage_locations
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY storage_location_events_app_select ON memebank.storage_location_events
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY storage_location_events_worker_insert ON memebank.storage_location_events
    FOR INSERT TO mb_worker
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY observations_app_select ON memebank.enrichment_observations
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY observations_app_decide ON memebank.enrichment_observations
    FOR UPDATE TO mb_app
    USING (memebank_private.has_library_access(library_id, true, false))
    WITH CHECK (memebank_private.has_library_access(library_id, true, false));
CREATE POLICY observations_worker_all ON memebank.enrichment_observations
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY ocr_regions_app_select ON memebank.ocr_regions
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY ocr_regions_worker_all ON memebank.ocr_regions
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY tags_app_select ON memebank.tags
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY tags_app_write ON memebank.tags
    FOR ALL TO mb_app
    USING (memebank_private.has_library_access(library_id, true, false))
    WITH CHECK (memebank_private.has_library_access(library_id, true, false));
CREATE POLICY tags_worker_all ON memebank.tags
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY tag_decisions_app_all ON memebank.asset_tag_decisions
    FOR ALL TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false))
    WITH CHECK (
        memebank_private.has_library_access(library_id, true, false)
        AND (decided_by_user_id IS NULL OR decided_by_user_id = memebank_private.current_user_id())
    );
CREATE POLICY tag_decisions_worker_all ON memebank.asset_tag_decisions
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY embedding_models_app_select ON memebank.embedding_models
    FOR SELECT TO mb_app
    USING (status IN ('active', 'retiring'));
CREATE POLICY embedding_models_worker_select ON memebank.embedding_models
    FOR SELECT TO mb_worker
    USING (true);
CREATE POLICY embedding_routes_app_select ON memebank.embedding_search_routes
    FOR SELECT TO mb_app
    USING (true);
CREATE POLICY embedding_routes_worker_select ON memebank.embedding_search_routes
    FOR SELECT TO mb_worker
    USING (true);

CREATE POLICY search_documents_app_select ON memebank.asset_search_documents
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY search_documents_worker_all ON memebank.asset_search_documents
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY embeddings_384_app_select ON memebank.asset_embeddings_384
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY embeddings_384_worker_all ON memebank.asset_embeddings_384
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));
CREATE POLICY embeddings_768_app_select ON memebank.asset_embeddings_768
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY embeddings_768_worker_all ON memebank.asset_embeddings_768
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));
CREATE POLICY embeddings_1024_app_select ON memebank.asset_embeddings_1024
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY embeddings_1024_worker_all ON memebank.asset_embeddings_1024
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY jobs_app_select ON memebank.jobs
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY jobs_worker_all ON memebank.jobs
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));
CREATE POLICY job_attempts_app_select ON memebank.job_attempts
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY job_attempts_worker_all ON memebank.job_attempts
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));
CREATE POLICY job_events_app_select ON memebank.job_events
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false));
CREATE POLICY job_events_worker_insert ON memebank.job_events
    FOR INSERT TO mb_worker
    WITH CHECK (memebank_private.worker_has_library_access(library_id));
CREATE POLICY outbox_worker_all ON memebank.outbox_events
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY exports_app_all ON memebank.export_requests
    FOR ALL TO mb_app
    USING (memebank_private.has_library_access(library_id, false, false))
    WITH CHECK (
        memebank_private.has_library_access(library_id, false, false)
        AND requested_by_user_id = memebank_private.current_user_id()
    );
CREATE POLICY exports_worker_all ON memebank.export_requests
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));
CREATE POLICY deletions_app_all ON memebank.deletion_requests
    FOR ALL TO mb_app
    USING (memebank_private.has_library_access(library_id, false, true))
    WITH CHECK (
        memebank_private.has_library_access(library_id, false, true)
        AND requested_by_user_id = memebank_private.current_user_id()
    );
CREATE POLICY deletions_worker_all ON memebank.deletion_requests
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));
CREATE POLICY reconciliation_app_select ON memebank.reconciliation_runs
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, true));
CREATE POLICY reconciliation_worker_all ON memebank.reconciliation_runs
    FOR ALL TO mb_worker
    USING (memebank_private.worker_has_library_access(library_id))
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY audit_app_select ON memebank.audit_events
    FOR SELECT TO mb_app
    USING (memebank_private.has_library_access(library_id, false, true));
CREATE POLICY audit_worker_insert ON memebank.audit_events
    FOR INSERT TO mb_worker
    WITH CHECK (memebank_private.worker_has_library_access(library_id));

CREATE POLICY blobs_worker_all ON memebank_private.blobs
    FOR ALL TO mb_worker
    USING (true)
    WITH CHECK (true);
CREATE POLICY blobs_policy_owner_all ON memebank_private.blobs
    FOR ALL TO mb_policy_owner
    USING (true)
    WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE ON memebank.libraries TO mb_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON memebank.library_memberships TO mb_app;
GRANT SELECT, INSERT, UPDATE ON memebank.assets TO mb_app;
GRANT SELECT ON memebank.asset_variants, memebank.perceptual_hashes TO mb_app;
GRANT SELECT, INSERT, UPDATE ON memebank.storage_connections TO mb_app;
GRANT SELECT ON memebank.storage_locations, memebank.storage_location_events TO mb_app;
GRANT SELECT, UPDATE ON memebank.enrichment_observations TO mb_app;
GRANT SELECT ON memebank.ocr_regions TO mb_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON memebank.tags, memebank.asset_tag_decisions TO mb_app;
GRANT SELECT ON memebank.embedding_models, memebank.embedding_search_routes TO mb_app;
GRANT SELECT ON memebank.asset_search_documents, memebank.asset_embeddings_384, memebank.asset_embeddings_768, memebank.asset_embeddings_1024 TO mb_app;
GRANT SELECT ON memebank.jobs, memebank.job_attempts, memebank.job_events TO mb_app;
GRANT SELECT, INSERT, UPDATE ON memebank.export_requests, memebank.deletion_requests TO mb_app;
GRANT SELECT ON memebank.reconciliation_runs, memebank.audit_events TO mb_app;

GRANT SELECT ON memebank.libraries, memebank.library_memberships TO mb_worker;
GRANT SELECT, INSERT, UPDATE ON memebank.assets, memebank.asset_variants, memebank.perceptual_hashes TO mb_worker;
GRANT SELECT, INSERT, UPDATE ON memebank.storage_connections, memebank.storage_locations TO mb_worker;
GRANT SELECT, INSERT ON memebank.storage_location_events TO mb_worker;
GRANT SELECT, INSERT, UPDATE ON memebank.enrichment_observations, memebank.ocr_regions, memebank.tags, memebank.asset_tag_decisions TO mb_worker;
GRANT SELECT ON memebank.embedding_models, memebank.embedding_search_routes TO mb_worker;
GRANT SELECT, INSERT, UPDATE ON memebank.asset_search_documents, memebank.asset_embeddings_384, memebank.asset_embeddings_768, memebank.asset_embeddings_1024 TO mb_worker;
GRANT SELECT, INSERT, UPDATE ON memebank.jobs, memebank.job_attempts TO mb_worker;
GRANT SELECT, INSERT ON memebank.job_events TO mb_worker;
GRANT SELECT, INSERT, UPDATE ON memebank.outbox_events, memebank.export_requests, memebank.deletion_requests, memebank.reconciliation_runs TO mb_worker;
GRANT SELECT, INSERT ON memebank.audit_events TO mb_worker;
GRANT SELECT, INSERT, UPDATE ON memebank_private.blobs TO mb_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA memebank TO mb_worker;

GRANT SELECT ON memebank.library_memberships, memebank.assets, memebank.tags, memebank.asset_tag_decisions, memebank.enrichment_observations, memebank.ocr_regions, memebank.embedding_models TO mb_policy_owner;
GRANT SELECT, INSERT, UPDATE ON memebank.asset_search_documents TO mb_policy_owner;
GRANT SELECT, UPDATE ON memebank.jobs TO mb_policy_owner;

GRANT EXECUTE ON FUNCTION memebank_private.current_user_id() TO mb_app, mb_worker;
GRANT EXECUTE ON FUNCTION memebank_private.current_worker_library_id() TO mb_worker;
GRANT EXECUTE ON FUNCTION memebank_private.has_library_access(uuid, boolean, boolean) TO mb_app, mb_worker;
GRANT EXECUTE ON FUNCTION memebank_private.worker_has_library_access(uuid) TO mb_worker;
GRANT EXECUTE ON FUNCTION memebank_private.refresh_asset_search_document(uuid) TO mb_app, mb_worker;
GRANT EXECUTE ON FUNCTION memebank.claim_jobs(text, integer, integer) TO mb_worker;
GRANT EXECUTE ON FUNCTION memebank.renew_job_lease(uuid, text, bigint, integer) TO mb_worker;

COMMIT;
