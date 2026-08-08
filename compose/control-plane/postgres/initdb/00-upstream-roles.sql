-- Upstream e2b-infra assumes a Supabase-shaped database in which the owning
-- role is named "postgres". Two migration steps grant to it by name:
--
--   packages/db/scripts/migrator.go        GRANT EXECUTE ON FUNCTION auth.uid() TO postgres
--   migrations/20231220094836_*.sql        GRANT trigger_user TO postgres
--
-- This deployment runs the database as "kitdev" (compose.yaml POSTGRES_USER),
-- and the stock postgres image creates a "postgres" role only when the user
-- keeps its default name. On a freshly initialised cluster both grants
-- therefore fail with: role "postgres" does not exist (SQLSTATE 42704), and
-- the postgres-migrator exits 1 before any schema is created.
--
-- Creating the role here is smaller and less invasive than patching two
-- upstream call sites and rebuilding the migrator image. It is NOLOGIN and
-- holds no privileges of its own: nothing connects as it, and the application
-- connects as kitdev. It exists solely so the upstream grants resolve.
--
-- This runs from /docker-entrypoint-initdb.d, so it applies only when the data
-- directory is being initialised. Clusters created before this file already
-- carry the role and are unaffected.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'postgres') THEN
    CREATE ROLE postgres NOLOGIN;
  END IF;
END
$$;
