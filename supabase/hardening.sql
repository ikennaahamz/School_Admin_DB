-- =============================================================
--  hardening.sql  —  OPTIONAL, not part of the schema
--
--  Closes the one real exposure in this project.
--
--  This application does NOT use Supabase Auth, the Data API
--  (PostgREST), or the anon key. It connects straight to Postgres as
--  the table owner and does its own bcrypt authentication in
--  src/auth.py. That is why Row Level Security is deliberately NOT
--  enabled: RLS does not apply to the table owner, so a policy here
--  would be silently bypassed by every statement the application
--  runs. It would look like protection while providing none.
--
--  What IS worth fixing is this: Supabase exposes a Data API at
--  https://<ref>.supabase.co/rest/v1/, and on a default project the
--  `anon` and `authenticated` roles can read the `public` schema
--  through it. Anyone holding the project's anon key could therefore
--  read every user row, every bcrypt hash and every grade.
--
--  Revoking those grants is the correct fix when the Data API is not
--  used, and it is one statement per role. Enabling RLS is only
--  necessary if you intend to go on serving the Data API.
--
--  Safe to run: the application connects as `postgres`, which owns the
--  tables and keeps its own grants, so nothing here affects it.
--
--  Optional either way. Run it if the project is real rather than a
--  coursework submission; skip it and say so in the report.
-- =============================================================

-- The roles only exist on Supabase. On a plain PostgreSQL instance the
-- DO block skips them instead of failing, which keeps this file
-- runnable in local development.
DO $$
DECLARE
    r TEXT;
BEGIN
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA public FROM %I', r);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', r);
            EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', r);
            EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM %I', r);
            RAISE NOTICE 'revoked Data API access from role %', r;
        ELSE
            RAISE NOTICE 'role % does not exist here, skipping', r;
        END IF;
    END LOOP;
END;
$$;

-- Optional belt and braces: turn the Data API off entirely.
-- Supabase -> Project Settings -> API -> Data API -> Disable.
--
-- Once disabled, /rest/v1/ returns 404 for every role including
-- anon, and this project's exposure is closed regardless of grants.
--
-- Confirm with:
--   curl -i https://<project-ref>.supabase.co/rest/v1/students
--
-- Expected after disabling: HTTP 404.