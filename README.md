# School Administration System

**CMPE344 — Database Management Systems and Programming II**

A PostgreSQL database and Streamlit web application for university
student records: enrolment, grading, attendance, role-based
authentication, and management reporting.

| Layer | Technology |
|---|---|
| Database | PostgreSQL 15 on **Supabase** |
| Procedural | **PL/pgSQL** — 8 functions, procedures and triggers |
| Front end | **Streamlit** 1.65 (Python) |
| Deployment | Supabase + Streamlit Community Cloud |
| Reports | 8 analytical queries (CTEs, window functions, subqueries) |

> **On PL/pgSQL vs PL/SQL.** The brief asks for PL/SQL blocks but also
> names Supabase as an allowed platform. Supabase runs PostgreSQL,
> which has no PL/SQL. PL/pgSQL is PostgreSQL's direct equivalent and
> covers every construct the brief names — procedures, functions,
> triggers. See [docs/report.md](docs/report.md#note-on-plsql-vs-plpgsql)
> for the full argument and the approval being sought from the course
> coordinator.

---

## Quick start

```bash
python -m venv .venv
.\.venv\Scripts\activate            # Windows
# source .venv/bin/activate         # macOS / Linux

pip install -r requirements.txt

cp .env.example .env                # then paste your Supabase DSN
streamlit run app.py
```

The app opens on a sign-in screen. Demo accounts all use
`Passw0rd!`:

| Username    | Role(s)               | Sees |
|-------------|-----------------------|------|
| `admin`     | admin                 | all 9 screens |
| `registrar` | registrar             | 8 screens |
| `i.kaya`    | instructor (CS)       | 6 screens |
| `i.koc`     | instructor + technical| 6 screens |
| `student1`  | student               | 2 screens |

## Setting up the database

Create a project at [supabase.com](https://supabase.com), then find
**Project Settings → Database → Connection string** and paste it into
`.env` as `DATABASE_URL`.

Use the **session pooler (port 5432)** or the direct connection — not
the transaction pooler (port 6543), which does not support the
prepared statements psycopg2 uses.

Apply the migrations in order:

```bash
psql "$DATABASE_URL" -f supabase/migrations/0001_schema.sql
psql "$DATABASE_URL" -f supabase/migrations/0002_plpgsql.sql
psql "$DATABASE_URL" -f supabase/migrations/0003_seed.sql
```

Then confirm everything works:

```bash
psql "$DATABASE_URL" -f supabase/migrations/verify_plpgsql.sql
```

### Applying the schema with no IPv4 (Supabase free tier)

The Supabase **free tier publishes IPv6-only hostnames**.
`db.<ref>.supabase.co` has an AAAA record and no A record, so on an
IPv4-only network `psql` cannot resolve it at all. Upgrading to Pro is
one way to get IPv4; it is not necessary.

**Preferred — the dashboard SQL editor.** Paste
[`supabase/apply_all.sql`](supabase/apply_all.sql), the three
migrations concatenated, into **SQL Editor → Run**. It executes inside
Supabase's own network, so no IPv4 is required. Regenerate it whenever a
migration changes:

```bash
python scripts/build_single_sql.py
```

**Alternative — the pooler.** `aws-0-<region>.pooler.supabase.com`
publishes IPv4 records. Its username is `postgres.<project_ref>` rather
than `postgres`. `scripts/probe_supabase.py` identifies the correct
region by trying them and reporting which one authenticates.

Only *local* connections are affected. Streamlit Community Cloud reaches
the database over IPv6 from AWS, so the free tier is sufficient for the
running application.

## Repository layout

```
app.py                          Streamlit entry point, routing and auth gate
src/
  db.py                         connections, error translation, actor context
  auth.py                       bcrypt login, registration, role checks
  screens.py                    the ten screen implementations
  crud/__init__.py              insert/update/delete, one section per entity
  reports.py                    the eight analytical queries
supabase/migrations/
  0001_schema.sql               DDL — 13 tables, keys, CHECK constraints
  0002_plpgsql.sql              8 procedural blocks
  0003_seed.sql                 seed data (33 users, 212 attendance rows)
  0004_queries.sql              the 8 analytical queries
  verify_plpgsql.sql            14 executable checks
scripts/
  smoke_test.py                 34 checks over the data layer
  app_test.py                   59 checks driving the real Streamlit app
docs/
  erd.md                        ER diagram, rendered by GitHub
  report.md                     the project report
```

## Deploying to Streamlit Community Cloud

Push the repository, then use **Deploy an app**:

| Field | Value |
|---|---|
| Repository | `ikennaahamz/School_Admin_DB` |
| Branch | `main` |
| Main file path | `app.py` |
| App URL | optional |

Then open **Advanced Settings → Secrets** and add:

```toml
DATABASE_URL = "postgresql://postgres:PASSWORD@db.PROJECT_REF.supabase.co:5432/postgres?sslmode=require"
```

Redeploy after saving the secret.

**Secrets must be read from `st.secrets`.** Streamlit Cloud injects
deployment secrets there, *not* into the process environment, so a
module that reads only `os.getenv` will report "DATABASE_URL is not
set" on a deployed build while the variable is plainly configured.
`src/db.py` reads `st.secrets` first, then the environment, then `.env`,
in that order — `scripts/verify_config.py` asserts all four cases.

The order matters: secrets before environment. A stale
`DATABASE_URL` exported in the shell should not shadow the value
configured in the deployment.

### Configuration lookup, in one function

```python
def setting(key: str, default: str | None = None) -> str | None:
    try:
        import streamlit as st
        secrets = st.secrets
        value = secrets.get(key) if hasattr(secrets, "get") else None
        if value is None:
            value = getattr(secrets, key, None)
        if value:
            return str(value)
    except Exception:  # noqa: BLE001 - absent secrets must not be fatal
        pass
    return os.getenv(key, default)
```

`st.secrets` is imported lazily and guarded because this module is also
used by the test scripts, which run with no Streamlit runtime and no
secrets file at all.

**Note on the Supabase free tier — this bit us.** Its database
hostnames publish **IPv6 only**: `db.<ref>.supabase.co` has an AAAA
record and no A record, confirmed against Cloudflare, Google and
Quad9. `psql`, `psycopg2` and the Streamlit Cloud container all resolve
addresses with `getaddrinfo`, which returns nothing usable when the
only record is AAAA and the client has no IPv6. The result is
`could not translate host name ... to address: No address associated
with hostname` — which reads like a typo in the connection string and is
not one.

The IPv4 connection pooler is a paid-plan feature, so there is no IPv4
route to a free-tier Supabase database. IPv4 requires either the Pro
plan or a different provider:

| Option | Cost | Notes |
|---|---|---|
| **Neon** | free, no card | Standard `postgres://` DSN. Recommended. |
| Railway | trial credit, then card | Fine if you already have an account |
| Render | free instance | The free Postgres instance expires — do not use for a graded deadline |
| Supabase Pro | paid | Works, if you would rather stay with Supabase |

Switching provider changes nothing in the code. `src/db.py` speaks plain
`psycopg2` and reads the DSN from `DATABASE_URL`, and the schema is
standard PostgreSQL 15+ with no Supabase-specific dependency. Only the
secret's value changes; re-run `apply_all.sql` on the new database.

See ["Applying the schema with no IPv4"](#applying-the-schema-with-no-ipv4-supabase-free-tier)
for the dashboard route, which still works for applying the schema on
Supabase itself.

## Screens

| # | Screen | Purpose | Roles |
|---|--------|---------|-------|
| 1 | Login / Register | authenticate, self-register | — |
| 2 | Dashboard | counts, current sections, grade spread, audit trail | all |
| 3 | Students | view, insert, update, delete | admin, registrar |
| 4 | Instructors | view, insert, update, delete | admin, registrar |
| 5 | Courses & Sections | view, insert, update, delete | + instructor |
| 6 | Enrolments | enrol, grade, withdraw | + instructor |
| 7 | Assignments | assignments, submissions, marking | + instructor |
| 8 | Attendance | view, record, delete | + instructor |
| 9 | Reports | 8 queries, SQL source, live PL/pgSQL output | all |
| 10 | User Administration | accounts, status, role grants, audit | admin |

## Design notes

Three decisions carry most of the weight, and each is argued in the
report:

**Business rules live in the database, not in Python.** Capacity,
credit limits, grade derivation and audit are PL/pgSQL, so they apply
no matter which client writes the row. The screens validate that a
form was filled in; the database decides what is legal.

**`enrolled_count` is denormalised and maintained by a trigger.** The
reports filter and sort on it constantly. A counter the application
had to keep in step would drift; a counter the database maintains
cannot.

**The capacity check takes a `FOR UPDATE` row lock.** Without it, two
simultaneous enrolments both read the same `enrolled_count`, both pass
the capacity test, and the section ends up over-subscribed.

## Testing

```bash
python scripts/smoke_test.py     # 34 checks — data layer
python scripts/app_test.py       # 59 checks — the live Streamlit app
psql "$DATABASE_URL" -f supabase/migrations/verify_plpgsql.sql
```

`app_test.py` uses `streamlit.testing.v1.AppTest` to run the real
application script headlessly: it signs in as each role, asserts which
screens appear, renders all nine, and executes all eight reports. It
is not a syntax check — it catches render failures.

## Security

- Passwords are bcrypt cost-12 digests. No plaintext password exists
  anywhere in the repository, including the seed data.
- Login failures return one message for both an unknown user and a
  wrong password, and spend comparable time in each case, so the form
  cannot be used to enumerate valid usernames.
- Self-registration creates a `pending` account with only the
  `student` role. Only an administrator can activate an account or
  grant a role.
- Every SQL statement takes parameters. The three places an identifier
  cannot be parameterised — table names, the procedure name — use an
  explicit allowlist.
- `.env` is git-ignored. Only `.env.example` belongs in the repository.

### Why Row Level Security is not enabled

A fair question on a Supabase project, and the answer is specific to
this design.

This application does **not** use Supabase Auth or the Data API. It
connects straight to Postgres as the table owner and authenticates in
`src/auth.py` with its own bcrypt hashing. There is no `anon` key in
`.env` and no reference to PostgREST anywhere in the code.

RLS does not apply to a table's owner. Because every statement here
runs as the owner, any policy written would be silently bypassed — the
application would behave identically, while the presence of policies
would imply an authorisation model that is not actually enforcing
anything. That is worse than no RLS, because it invites the reader to
trust a control that is inert.

Enabling RLS *properly* would mean `FORCE ROW LEVEL SECURITY`, a
dedicated non-owner application role, and per-row policies keyed to a
session user — which needs Supabase Auth or a `SET LOCAL app.user_id`
on every connection. That is a redesign, not a checkbox.

**What is genuinely worth closing:** Supabase exposes a Data API at
`https://<ref>.supabase.co/rest/v1/`, and on a default project the
`anon` role can read the `public` schema through it, so anyone holding
the project's anon key could read every user row, password hash and
grade. Since this application does not use that API, the fix is one
statement per role, in [`supabase/hardening.sql`](supabase/hardening.sql):

```sql
REVOKE ALL ON SCHEMA public FROM anon;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
-- and the same for the authenticated role
```

Optionally also disable the Data API under **Project Settings → API**.
`scripts/verify_hardening.py` proves this does not disturb the
application (9 checks, all passing).