BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA memebank;
CREATE SCHEMA memebank_private;

COMMENT ON SCHEMA memebank IS 'Tenant-visible MemeBank catalog and workflow objects';
COMMENT ON SCHEMA memebank_private IS 'Internal blob, security-helper, and maintenance objects';

COMMIT;
