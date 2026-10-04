"""
Prove the configuration lookup works from every source Streamlit uses.

    python scripts/verify_config.py

The bug this guards against: Streamlit Community Cloud injects secrets
through st.secrets, not the process environment. Reading only os.getenv
meant a deployed build reported "DATABASE_URL is not set" while the
variable was plainly configured under the Secrets panel.

Three cases are checked:

  1. st.secrets only        — the Cloud deployment case
  2. environment variable   — the usual local case
  3. nothing set            — must raise a clear, actionable error

Case 1 is the one that failed in production, and it cannot be exercised
by setting an environment variable, so st.secrets is faked.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402
from src import db  # noqa: E402

PASSED = FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"PASS  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}" + (f"  — {detail}" if detail else ""))


class FakeSecrets(dict):
    """Minimal stand-in for st.secrets.

    Streamlit's own object raises AttributeError for an unknown key;
    dict.get() returns None. Both are exercised by db.setting().
    """

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


def main() -> int:
    dsn = ("postgresql://postgres:pw@db.example.supabase.co:5432/"
           "postgres?sslmode=require")

    print("=" * 66)
    print("Verifying configuration lookup across all sources")
    print("=" * 66)

    for key in ("DATABASE_URL", "LOCAL_DATABASE_URL", "DB_TARGET"):
        os.environ.pop(key, None)

    # ---- case 3: nothing configured ----------------------------
    print("\n-- case 3: nothing configured --")
    check("setting() returns None when unset",
          db.setting("DATABASE_URL") is None)
    try:
        db._connection_string()
        check("missing DATABASE_URL raises", False, "no error raised")
    except db.DatabaseError as exc:
        message = str(exc)
        check("missing DATABASE_URL raises DatabaseError", True)
        check("message mentions Streamlit Secrets",
              "Secrets" in message, message[:80])
        check("message mentions .env", ".env" in message, message[:80])

    # ---- case 1: st.secrets only (the Cloud deployment case) ----
    print("\n-- case 1: st.secrets only, environment empty --")
    import streamlit as st

    st.secrets = FakeSecrets({"DATABASE_URL": dsn})
    check("setting() reads DATABASE_URL from st.secrets",
          db.setting("DATABASE_URL") == dsn,
          f"got {db.setting('DATABASE_URL')!r}")
    check("_connection_string() resolves from st.secrets",
          db._connection_string() == dsn)
    check("unknown secret key returns None",
          db.setting("NOT_A_REAL_KEY") is None)

    # A stale environment variable must not beat the secret.
    os.environ["DATABASE_URL"] = "postgresql://wrong/from-env"
    check("st.secrets takes priority over the environment",
          db.setting("DATABASE_URL") == dsn,
          f"got {db.setting('DATABASE_URL')!r}")
    os.environ.pop("DATABASE_URL", None)

    # DB_TARGET as a secret too, since _connection_string() needs it.
    check("db_target() reads DB_TARGET from st.secrets",
          db.db_target() == "supabase", f"got {db.db_target()!r}")

    # ---- case 2: environment variable (local development) ------
    print("\n-- case 2: environment variable, secrets absent --")
    st.secrets = FakeSecrets({})
    os.environ["DATABASE_URL"] = dsn
    check("setting() falls back to the environment",
          db.setting("DATABASE_URL") == dsn)
    check("_connection_string() resolves from the environment",
          db._connection_string() == dsn)

    os.environ["DB_TARGET"] = "local"
    os.environ["LOCAL_DATABASE_URL"] = dsn
    check("DB_TARGET=local selects LOCAL_DATABASE_URL",
          db.db_target() == "local")
    check("_connection_string() follows DB_TARGET",
          db._connection_string() == dsn)

    # ---- an object with no .get(), to cover the getattr path ----
    print("\n-- case 4: secrets object without a .get() method --")

    class AttrOnly:
        DATABASE_URL = dsn

    st.secrets = AttrOnly()
    check("setting() falls back to getattr on the secrets object",
          db.setting("DATABASE_URL") == dsn,
          f"got {db.setting('DATABASE_URL')!r}")

    # ---- restore the real local configuration --------------------
    # os.environ.pop() above also removed the values load_dotenv()
    # merged in at import time, so re-load rather than assume.
    st.secrets = FakeSecrets({})
    for key in ("DATABASE_URL", "LOCAL_DATABASE_URL", "DB_TARGET"):
        os.environ.pop(key, None)
    load_dotenv(ROOT / ".env", override=True)

    print("\n-- restored: .env path still works --")
    try:
        resolved = db._connection_string()
        check("local .env resolves again",
              resolved.startswith("postgresql://"), f"got {resolved[:40]!r}")
        check("db_target() restored from .env",
              db.db_target() in {"local", "supabase"},
              f"got {db.db_target()!r}")
    except db.DatabaseError as exc:
        check("local .env resolves again", False, str(exc)[:90])

    print()
    print("=" * 66)
    print(f"{PASSED} passed, {FAILED} failed")
    print("=" * 66)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())