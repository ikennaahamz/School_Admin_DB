"""
Print the fingerprint of the local DATABASE_URL, to compare against the
one the deployed app prints when it cannot connect.

    .\\.venv\\Scripts\\python.exe scripts\\dsn_fingerprint.py

Why
---
The deployed build fails with::

    password authentication failed for user 'neondb_owner'

That error is identical whether the password is wrong, truncated, stale
after a rotation, or belongs to a different branch, so it cannot
distinguish between the possibilities on its own. Both sides can,
though: this prints a fingerprint of the credentials that are known to
work, and the application prints one of the credentials it is actually
holding. Same fingerprint means the same secret; any differing field
names the defect.

A fingerprint shows the connection target in full -- host, port,
database, user, query parameters -- and reduces the password to its
length and an eight-character SHA-256 prefix. That is enough to prove
two passwords identical and useless for reconstructing one, so this is
safe to paste into a bug report. It reads ``src/db.py`` rather than
reimplementing the format, so the two cannot drift apart.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

from src import db  # noqa: E402

load_dotenv(ROOT / ".env", override=True)


def main() -> int:
    print("=" * 70)
    print("Fingerprints of the credentials configured on this machine")
    print("=" * 70)
    print(f"DB_TARGET = {db.db_target()!r}  -> the app will use "
          f"{'LOCAL_DATABASE_URL' if db.db_target() == 'local' else 'DATABASE_URL'}")
    print()

    # Read both explicitly. Which one the app selects depends on
    # DB_TARGET, but a mismatch between the two is worth seeing even when
    # only one of them is in use -- that mismatch is a common cause of a
    # deployment that "works locally" and fails in the cloud, because
    # local runs silently pick the other value.
    targets = [
        ("DATABASE_URL", "the cloud database (Neon)"),
        ("LOCAL_DATABASE_URL", "the local PostgreSQL instance"),
    ]
    failures = 0
    for key, description in targets:
        dsn = db.setting(key)
        print(f"--- {key}  ({description}) ---")
        if not dsn:
            print("  not set")
            print()
            continue
        try:
            print(f"  {db.dsn_fingerprint(db._normalise_dsn(dsn))}")
        except Exception as exc:  # noqa: BLE001 - report, never crash
            failures += 1
            print(f"  could not be described: {exc}")
        print()

    # The value the app would actually open right now.
    try:
        active = db._connection_string()
        print("--- the value the application will connect with now ---")
        print(f"  {db.dsn_fingerprint(active)}")
        print()
        print("If the deployed app prints a different fingerprint, that")
        print("difference is the fault. host/port/db/user identify a wrong")
        print("endpoint or branch; passlen and sha256 identify a wrong or")
        print("mangled password.")
    except db.DatabaseError as exc:
        failures += 1
        print("--- the application cannot resolve a DSN ---")
        print(f"  {exc}")
    print()
    print("=" * 70)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
