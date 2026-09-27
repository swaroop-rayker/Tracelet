#!/bin/bash
# ---------------------------------------------------------------------------
# Tracelet — first-boot database initialisation.
#
# Runs ONCE, as the superuser, when the db volume is empty. Two jobs Alembic
# cannot do:
#
#   1. CREATE EXTENSION requires superuser.
#   2. Roles are cluster-level objects, and the grant model must exist before
#      any table does.
#
# A shell script rather than plain .sql because the Postgres entrypoint runs
# .sql files through psql with no access to the environment, and the role
# passwords arrive as environment variables.
#
# This does NOT re-run against an existing volume. If you change it, apply the
# change by hand and record it in docs/ERRORS.md.
#
# Reference: docs/DATA_MODEL.md §12 (database roles).
# ---------------------------------------------------------------------------
set -euo pipefail

: "${TRACELET_DB_APP_PASSWORD:?required}"
: "${TRACELET_DB_MAINT_PASSWORD:?required}"
: "${TRACELET_DB_MIGRATE_PASSWORD:?required}"

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v dbname="$POSTGRES_DB" \
     -v app_pw="$TRACELET_DB_APP_PASSWORD" \
     -v maint_pw="$TRACELET_DB_MAINT_PASSWORD" \
     -v migrate_pw="$TRACELET_DB_MIGRATE_PASSWORD" <<-'SQL'

	-- === Extensions ====================================================

	-- PostGIS: geography(Polygon,4326) + ST_Covers + GiST for geofencing.
	-- geography rather than geometry, so point-in-polygon is geodesically
	-- correct at any latitude and across the antimeridian (ADR-0002, F6.AC2).
	CREATE EXTENSION IF NOT EXISTS postgis;

	-- citext: case-insensitive admin email and link slug.
	CREATE EXTENSION IF NOT EXISTS citext;

	-- pg_stat_statements: the only query-performance telemetry this
	-- deployment has, since there is no Prometheus (ADR-0013).
	CREATE EXTENSION IF NOT EXISTS pg_stat_statements;


	-- === Roles =========================================================
	-- Three roles with deliberately different powers. The separation is what
	-- makes the audit log append-only at the engine level rather than by
	-- convention: a SQL-injection foothold in the application path cannot
	-- erase the record of itself, because tracelet_app will have no DELETE on
	-- audit_log (NFR5.AC5). Plain CREATE ROLE is safe here because this runs
	-- only against an empty cluster.

	-- The API. Ordinary data access, no DDL.
	CREATE ROLE tracelet_app LOGIN PASSWORD :'app_pw';

	-- Retention purges, backups, restore verification. The only role that
	-- will be permitted to delete audit rows.
	CREATE ROLE tracelet_maint LOGIN PASSWORD :'maint_pw';

	-- DDL only. Used by Alembic, never by the running application.
	CREATE ROLE tracelet_migrate LOGIN PASSWORD :'migrate_pw';


	-- === Schema ownership and grants ===================================

	GRANT CONNECT ON DATABASE :"dbname"
	    TO tracelet_app, tracelet_maint, tracelet_migrate;

	-- tracelet_migrate owns public, so Alembic can create and alter.
	ALTER SCHEMA public OWNER TO tracelet_migrate;
	GRANT USAGE ON SCHEMA public TO tracelet_app, tracelet_maint;

	-- Default privileges, so every table Alembic creates is reachable without
	-- remembering to grant it. Tables needing a narrower grant — audit_log
	-- above all — revoke explicitly in their own migration.
	ALTER DEFAULT PRIVILEGES FOR ROLE tracelet_migrate IN SCHEMA public
	    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tracelet_app;

	ALTER DEFAULT PRIVILEGES FOR ROLE tracelet_migrate IN SCHEMA public
	    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tracelet_maint;

	ALTER DEFAULT PRIVILEGES FOR ROLE tracelet_migrate IN SCHEMA public
	    GRANT USAGE, SELECT ON SEQUENCES TO tracelet_app, tracelet_maint;

	-- PostGIS metadata must be readable by the application.
	GRANT SELECT ON TABLE public.spatial_ref_sys TO tracelet_app, tracelet_maint;

	-- pg_stat_statements, for the System Health page (F10.AC1).
	GRANT pg_read_all_stats TO tracelet_app;

SQL

echo "tracelet: extensions installed and roles created"
