-- Least-privilege application role for Row-Level Security.
--
-- The API, the worker, and Alembic-on-boot all connect as this
-- NON-superuser role. This is load-bearing for RLS: PostgreSQL
-- superusers bypass RLS unconditionally, and the table owner bypasses
-- it too unless the table is set FORCE ROW LEVEL SECURITY. By running
-- everything (including migrations) as `splitsmith_app`, every table is
-- owned by it, and the RLS migration's `FORCE ROW LEVEL SECURITY` makes
-- the owner-applies case explicit. This mirrors Neon production, where
-- the default role is a non-superuser owner.
--
-- The POSTGRES_USER superuser (`splitsmith`) stays available for
-- bootstrap, seeding, and debugging. Tests seed cross-tenant rows
-- through it precisely because a superuser bypasses RLS.
--
-- This script runs once, on first DB init (empty data dir). After
-- changing it, recreate the volume: `docker compose down -v`.
CREATE ROLE splitsmith_app LOGIN PASSWORD 'splitsmith_app' NOSUPERUSER NOBYPASSRLS;

-- CREATE lets the role create its own tables (Alembic runs as it, so it
-- owns the app + procrastinate schema); USAGE lets it reference objects
-- in the schema. On PG15+ the public schema no longer grants CREATE to
-- PUBLIC, so this grant is required.
GRANT CREATE, USAGE ON SCHEMA public TO splitsmith_app;
