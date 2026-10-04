"""Prove hardening.sql is safe: it must not break the application.

The application connects as `postgres`, the table owner. Revoking
grants from `anon` and `authenticated` must therefore leave every
query working.

Runs against a throwaway database built from apply_all.sql:

  1. confirm the app's queries work before hardening
  2. apply hardening.sql
  3. confirm they still work after

On plain PostgreSQL the `anon` and `authenticated` roles do not exist,
so the DO block reports 'does not exist here, skipping'. That is the
expected path locally; on Supabase it performs the real revokes. Either
way the application must be unaffected, which is what this asserts.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = "school_hardening_test"
PSQL = r"C:\Program Files\PostgreSQL\17\bin\psql.exe"

PASSED = FAILED = 0


def _env() -> dict:
    """Inherit the real environment and add PGPASSWORD.

    A minimal env was tried first and broke two things at once: psql
    was no longer on PATH, and localhost stopped resolving because the
    inherited resolver configuration was gone.
    """
    env = dict(os.environ)
    env["PGPASSWORD"] = "Irechukwu7."
    return env


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"PASS  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}" + (f"  — {detail}" if detail else ""))


def run_file(sql_file: Path, database: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PSQL, "-U", "postgres", "-h", "localhost", "-p", "5432",
         "-d", database, "-v", "ON_ERROR_STOP=1", "-q", "-f", str(sql_file)],
        capture_output=True, text=True, env=_env(),
    )


def psql(sql: str, database: str = "postgres") -> str:
    """Run SQL through psql, returning combined output.

    Uses the local instance rather than the application's connection so
    that a database outage here does not look like a code failure.
    """
    result = subprocess.run(
        [PSQL, "-U", "postgres", "-h", "localhost", "-p", "5432",
         "-d", database, "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", sql],
        capture_output=True, text=True, env=_env(),
    )
    return (result.stdout + result.stderr).strip()


def main() -> int:
    print("=" * 66)
    print("Verifying hardening.sql does not break the application")
    print("=" * 66)

    # Rebuild a clean database from the single-file schema.
    psql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
         f"WHERE datname='{DB}' AND pid<>pg_backend_pid()")
    psql(f"DROP DATABASE IF EXISTS {DB}")
    psql(f"CREATE DATABASE {DB}")

    apply_all = ROOT / "supabase" / "apply_all.sql"
    result = run_file(apply_all, DB)
    check("apply_all.sql builds a fresh database", result.returncode == 0,
          result.stderr[-160:])
    if result.returncode != 0:
        return 1

    probe = "SELECT COUNT(*) FROM students"
    before = psql(probe, DB)
    check("students queryable before hardening", before.isdigit(),
          f"got {before!r}")

    hard = run_file(ROOT / "supabase" / "hardening.sql", DB)
    check("hardening.sql applies cleanly", hard.returncode == 0,
          hard.stderr[-160:])

    after = psql(probe, DB)
    check("students queryable after hardening", after == before,
          f"before={before!r} after={after!r}")

    # The application depends on these, so all must still resolve.
    for table in ["users", "enrollments", "course_sections", "grade_audit"]:
        got = psql(f"SELECT COUNT(*) FROM {table}", DB)
        check(f"{table} still queryable as the owner", got.isdigit(),
              f"got {got!r}")

    got = psql("SELECT letter_grade_for(93)", DB)
    check("PL/pgSQL function still callable", got == "AA", f"got {got!r}")

    psql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
         f"WHERE datname='{DB}' AND pid<>pg_backend_pid()")
    psql(f"DROP DATABASE IF EXISTS {DB}")

    print()
    print("=" * 66)
    print(f"{PASSED} passed, {FAILED} failed")
    print("=" * 66)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())