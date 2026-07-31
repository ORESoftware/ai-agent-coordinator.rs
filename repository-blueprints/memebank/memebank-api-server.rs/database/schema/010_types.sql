BEGIN;

CREATE TYPE memebank.library_visibility AS ENUM ('private', 'shared');
CREATE TYPE memebank.membership_role AS ENUM ('viewer', 'editor', 'owner');
CREATE TYPE memebank.membership_status AS ENUM ('invited', 'active', 'revoked');
CREATE TYPE memebank.asset_state AS ENUM ('importing', 'ready', 'failed', 'deleting', 'deleted');
CREATE TYPE memebank.variant_kind AS ENUM ('original', 'normalized', 'thumbnail', 'preview', 'animated_frame');
CREATE TYPE memebank.digest_algorithm AS ENUM ('sha256', 'blake3');
CREATE TYPE memebank.provider_kind AS ENUM ('aws_s3', 'cloudflare_r2', 'google_drive', 'onedrive', 'apple_document_provider', 'filesystem');
CREATE TYPE memebank.storage_ownership AS ENUM ('service_managed', 'user_owned', 'device_local');
CREATE TYPE memebank.storage_location_state AS ENUM ('pending', 'present', 'deleting', 'missing', 'orphaned', 'deleted');
CREATE TYPE memebank.observation_kind AS ENUM ('ocr_span', 'object', 'label', 'scene', 'caption', 'moderation', 'embedding');
CREATE TYPE memebank.observation_status AS ENUM ('generated', 'confirmed', 'rejected', 'superseded');
CREATE TYPE memebank.embedding_space AS ENUM ('native_visual', 'ocr_text', 'caption_text', 'tag_text');
CREATE TYPE memebank.embedding_metric AS ENUM ('cosine', 'inner_product', 'l2');
CREATE TYPE memebank.model_status AS ENUM ('staging', 'active', 'retiring', 'retired');
CREATE TYPE memebank.tag_source AS ENUM ('user', 'generated', 'imported');
CREATE TYPE memebank.tag_decision AS ENUM ('pending', 'confirmed', 'rejected');
CREATE TYPE memebank.job_state AS ENUM ('queued', 'leased', 'succeeded', 'failed', 'canceled');
CREATE TYPE memebank.lifecycle_request_state AS ENUM ('requested', 'planning', 'running', 'blocked', 'succeeded', 'failed', 'canceled');
CREATE TYPE memebank.reconciliation_state AS ENUM ('queued', 'scanning', 'repairing', 'succeeded', 'failed', 'canceled');
CREATE TYPE memebank.actor_kind AS ENUM ('user', 'worker', 'system', 'administrator');
CREATE TYPE memebank.outbox_state AS ENUM ('pending', 'publishing', 'published', 'failed');

COMMIT;
