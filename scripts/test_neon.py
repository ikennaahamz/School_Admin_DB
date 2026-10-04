"""
Verify the cloud database connection without printing any secret.

    python scripts/test_neon.py

Reports whether the configured cloud database is reachable, what it is
running, and whether the schema is present. Passwords and other secrets
are never included in the output.

This is the check that matters for the deployment. The Supabase free
tier publishes IPv6-only hostnames and could not be reached from
Streamlit Cloud; a provider with IPv4 resolves and connects normally.
"""

import os
import re
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import psycopg2  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"

PASSED = FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"PASS  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def read_env() -> dict:
    """Parse .env without dotenv, so nothing is echoed."""
    values = {}
    if not ENV.exists():
        return values
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    env = read_env()
    dsn = env.get("DATABASE_URL")

    print("=" * 68)
    print("Cloud database connectivity check")
    print("=" * 68)

    if not dsn:
        print("\nDATABASE_URL is not set in .env.")
        print("Run: neon link --project-id <id> --branch production -y")
        return 1

    # Split the DSN properly. A regex such as r"/([^?/]+)" matches the
    # first slash, which is the one in "postgresql://", and therefore
    # captures "user:password@host:port" instead of the database name.
    # That printed the password. urlparse has no such ambiguity.
    parsed = urlparse(dsn)
    host = parsed.hostname or "?"
    port = parsed.port or 5432
    db_name = (parsed.path or "/").lstrip("/") or "?"
    print(f"\ntarget host : {host}:{port}")
    print(f"target db   : {db_name}")
    print(f"target role : {parsed.username or '?'}")
    print("password    : <redacted>\n")

    print("-- DNS --")
    try:
        infos = socket.getaddrinfo(host, 5432)
        addrs = sorted({i[4][0] for i in infos})
        families = {i[0] for i in infos}
        ipv4 = socket.AF_INET in families
        ipv6 = socket.AF_INET6 in families
        print(f"  resolved  : {addrs}")
        print(f"  families  : {'IPv4' if ipv4 else ''} "
              f"{'IPv6' if ipv6 else ''}".strip())
        check("hostname resolves", True)
        check("an IPv4 address is published", ipv4,
              "only IPv6 is published, which an IPv4-only client "
              "cannot reach. This is what fails with Supabase's free tier.")
    except OSError as exc:
        check("hostname resolves", False, str(exc))
        print("\nCannot continue without a resolvable host.")
        return 1

    print("\n-- connection --")
    try:
        conn = psycopg2.connect(dsn, connect_timeout=25)
    except Exception as exc:  # noqa: BLE001
        check("connects", False, str(exc).strip().splitlines()[0][:200])
        print(f"\n{PASSED} passed, {FAILED} failed")
        return 1

    check("connects", True)

    with conn.cursor() as cur:
        cur.execute("SELECT version(), current_database(), current_user, "
                    "inet_server_addr()")
        version, database, user, server_addr = cur.fetchone()

        print(f"\n  server   : {version.split(' on ')[0]}")
        print(f"  database : {database}")
        print(f"  role     : {user}")
        print(f"  address  : {server_addr}")

        cur.execute("SELECT count(*) FROM pg_type "
                    "WHERE typtype = 'e' AND typnamespace = 'public'::regnamespace")
        enums = cur.fetchone()[0]
        check("ENUM types present", enums > 0, f"found {enums}")

        cur.execute("SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                    "ORDER BY table_name")
        tables = [r[0] for r in cur.fetchall()]
        print(f"\n  tables   : {len(tables)}")
        for name in tables:
            print(f"             {name}")
        check("schema is applied", len(tables) >= 13,
              f"found {len(tables)} tables, expected at least 13. "
              "Run supabase/apply_all.sql in the provider's SQL editor.")

        if "students" in tables:
            cur.execute("SELECT count(*) FROM students")
            students = cur.fetchone()[0]
            check("seed data present", students > 0,
                  f"students table is empty ({students} rows)")
            print(f"\n  students : {students}")

            cur.execute("SELECT letter_grade_for(93)")
            letter = cur.fetchone()[0]
            check("PL/pgSQL functions deployed", letter == "AA",
                  f"letter_grade_for(93) returned {letter!r}, expected 'AA'")

            cur.execute("SELECT count(*) FROM pg_constraint c "
                        "JOIN pg_namespace n ON n.oid = c.connamespace "
                        "WHERE n.nspname = 'public'")
            constraints = cur.fetchone()[0]
            check("constraints present", constraints >= 60,
                  f"found {constraints}")

    conn.close()

    print()
    print("=" * 68)
    print(f"{PASSED} passed, {FAILED} failed")
    print("=" * 68)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())