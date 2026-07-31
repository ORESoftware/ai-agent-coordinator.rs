\set ON_ERROR_STOP on

DO $bootstrap$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mb_migrator') THEN
        CREATE ROLE mb_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mb_app') THEN
        CREATE ROLE mb_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mb_worker') THEN
        CREATE ROLE mb_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mb_policy_owner') THEN
        CREATE ROLE mb_policy_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS;
    END IF;
END
$bootstrap$;

ALTER ROLE mb_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
ALTER ROLE mb_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
ALTER ROLE mb_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
ALTER ROLE mb_policy_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS;

COMMENT ON ROLE mb_migrator IS 'No-login ownership role for reviewed schema changes; never granted to application workloads';
COMMENT ON ROLE mb_app IS 'No-login group role for authenticated MemeBank API transactions under forced RLS';
COMMENT ON ROLE mb_worker IS 'No-login group role for library-scoped background workers under forced RLS';
COMMENT ON ROLE mb_policy_owner IS 'No-login minimal owner for security-definer policy helpers; never granted to application or worker logins';
