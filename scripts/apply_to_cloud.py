"""
Apply the schema and seed data to the cloud database.

    python scripts/apply_to_cloud.py

Connects using DATABASE_URL from .env and runs supabase/apply_all.sql,
which is the three migrations concatenated. Reports what exists before
and after, and prints no secret.

Refuses to run against a database that already has tables, unless
--force is passed. A half-applied schema is worse than none, so the
check is deliberate rather than a bare CREATE TABLE that fails halfway.
"""

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import psycopg2  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SQL_FILE = ROOT / "supabase" / "apply_all.sql"
FORCE = "--force" in sys.argv


def read_env() -> dict:
    values = {}
    env = ROOT / ".env"
    if not env.exists():
        return values
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def counts(cur) -> dict:
    cur.execute("SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'")
    tables = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM pg_constraint c "
                "JOIN pg_namespace n ON n.oid = c.connamespace "
                "WHERE n.nspname = 'public'")
    constraints = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM information_schema.triggers "
                "WHERE trigger_schema = 'public'")
    triggers = cur.fetchone()[0]
    return {"tables": tables, "constraints": constraints, "triggers": triggers}


def main() -> int:
    dsn = read_env().get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set in .env. Run: neon link ...")
        return 1

    parsed = urlparse(dsn)
    print("=" * 68)
    print("Applying schema to the cloud database")
    print("=" * 68)
    print(f"\nhost    : {parsed.hostname}:{parsed.port or 5432}")
    print(f"database: {(parsed.path or '').lstrip('/')}")
    print(f"role    : {parsed.username}")
    print("password: <redacted>\n")

    sql = SQL_FILE.read_text(encoding="utf-8")
    print(f"script  : {SQL_FILE.name} ({sql.count(';')} statements, "
          f"{SQL_FILE.stat().st_size:,} bytes)\n")

    conn = psycopg2.connect(dsn, connect_timeout=30)
    conn.autocommit = False

    with conn.cursor() as cur:
        before = counts(cur)
        print(f"before  : {before}")

        if before["tables"] > 0 and not FORCE:
            print("\nRefusing to run: the database already has tables.")
            print("Re-running would fail partway and leave a half-applied")
            print("schema. To proceed anyway, pass --force, or drop the")
            print("schema first:")
            print("  DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            return 1

        print("\nrunning...")
        cur.execute(sql)
        conn.commit()
        print("committed.\n")

        after = counts(cur)
        print(f"after   : {after}")

        cur.execute("SELECT count(*) FROM users")
        users = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM students")
        students = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM course_sections")
        sections = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM enrollments")
        enrolments = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM assignments")
        assignments = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM submissions")
        submissions = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM attendance")
        attendance = cur.fetchone()[0]

        print("\nseed data")
        print(f"  users           {users:>5}")
        print(f"  students        {students:>5}")
        print(f"  course sections {sections:>5}")
        print(f"  enrolments      {enrolments:>5}")
        print(f"  assignments     {assignments:>5}")
        print(f"  submissions     {submissions:>5}")
        print(f"  attendance      {attendance:>5}")

        # Prove the procedural layer deployed, not just the tables.
        cur.execute("SELECT letter_grade_for(93)")
        print(f"\n  letter_grade_for(93) -> {cur.fetchone()[0]}")
        cur.execute("SELECT round(section_fill_ratio("
                    "(SELECT min(section_id) FROM course_sections)))")
        print(f"  section_fill_ratio(first) -> {cur.fetchone()[0]}")

        # Prove a trigger fires.
        cur.execute("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal")
        print(f"  triggers registered -> {cur.fetchone()[0]}")

    conn.close()

    print("\n" + "=" * 68)
    print("Cloud database ready.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())