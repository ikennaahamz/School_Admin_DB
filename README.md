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