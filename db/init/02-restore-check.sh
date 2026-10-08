#!/bin/bash
# ---------------------------------------------------------------------------
# Tracelet — what the monthly restore check needs from a superuser (ADR-0022).
#
# The check restores the newest backup into a scratch database, tracelet_verify,
# on this server, compares row counts, and drops it. It runs as tracelet_maint,
# which is not a superuser, so two things are prepared here once:
#
#   1. tracelet_maint may create databases (CREATEDB).
#   2. A template database already holding the extensions the dump uses. PostGIS
#      is not a trusted extension, so tracelet_maint could not install it into
#      the scratch database itself.
#
# Idempotent. It runs automatically on an empty volume (after 01-), and on an
# existing volume with:   ./scripts/tl db-setup
# ---------------------------------------------------------------------------
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-'SQL'
	ALTER ROLE tracelet_maint CREATEDB;

	SELECT 'CREATE DATABASE tracelet_verify_template IS_TEMPLATE true'
	 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'tracelet_verify_template')\gexec

	-- Nothing connects to a template; a connection would also block cloning it.
	REVOKE CONNECT ON DATABASE tracelet_verify_template FROM PUBLIC;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname tracelet_verify_template <<-'SQL'
	CREATE EXTENSION IF NOT EXISTS postgis;
	CREATE EXTENSION IF NOT EXISTS citext;
SQL

echo "restore-check setup: tracelet_maint has CREATEDB; tracelet_verify_template is ready"
