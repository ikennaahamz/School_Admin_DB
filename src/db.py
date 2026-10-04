"""
Database connection layer for the School Administration System.

Every SQL statement in the application goes through this module, so
connection handling, error translation and the actor context live in
exactly one place.

Connection model
----------------
psycopg2 connections are NOT thread-safe and Streamlit reruns the
whole script on every interaction. A connection cached in
``st.session_state`` would therefore be shared across threads. To avoid
that, this module opens a short-lived connection per operation and
closes it in a ``finally`` block. The provider's connection pooler
absorbs the per-connection cost, and correctness matters more than
microseconds here.

Which database is used is chosen by the ``DB_TARGET`` setting.
``local`` selects LOCAL_DATABASE_URL for development; anything else,
including the default, means the cloud database named by
DATABASE_URL. The project was developed against Supabase and moved to
Neon because Supabase's free tier publishes IPv6-only hostnames that
an IPv4-only client cannot reach.

The brief asks for the database connection code to be shown in the
report; this file is that code.
"""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlparse

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()


class DatabaseError(RuntimeError):
    """Raised for failures the UI should show the user as a message."""


def setting(key: str, default: str | None = None) -> str | None:
    """Read a configuration value from wherever Streamlit put it.

    Three sources, in priority order:

    1. ``st.secrets`` — Streamlit Community Cloud injects deployment
       secrets here, and a local ``.streamlit/secrets.toml`` also lands
       here. This is the source that matters in production.
    2. A normal environment variable.
    3. A ``.env`` file, loaded by ``load_dotenv()`` above.

    Reading only ``os.getenv`` was a real deployment bug: the value set
    under Streamlit Cloud's Secrets panel never reached the app, so a
    deployed build reported "DATABASE_URL is not set" while the variable
    was plainly configured. Both must be consulted.

    ``st.secrets`` is imported lazily and guarded, because this module
    is also used by scripts/ and smoke tests that run with no Streamlit
    runtime and no secrets file.
    """
    try:
        import streamlit as st

        secrets = st.secrets
        # .get() raises if secrets.toml is absent on local disk, and
        # attribute access raises for an unknown key on Cloud.
        value = secrets.get(key) if hasattr(secrets, "get") else None
        if value is None:
            value = getattr(secrets, key, None)
        if value:
            return str(value)
    except Exception:  # noqa: BLE001 - absent secrets must not be fatal
        pass

    return os.getenv(key, default)


def db_target() -> str:
    """Which database the app talks to.

    ``local`` selects LOCAL_DATABASE_URL for development. Any other
    value, including the default, means the cloud database. The value
    is compared case-insensitively because the provider changed: the
    default used to be the literal string ``supabase``, which is
    retained as an accepted alias but is no longer meaningful.
    """
    return (setting("DB_TARGET", "cloud") or "cloud").strip().lower()


def _normalise_dsn(dsn: str) -> str:
    """Repair the paste defects that survive a trip through a secrets panel.

    A connection string that a human copies by hand picks up debris that
    no amount of care at the source prevents: surrounding whitespace, the
    ``DATABASE_URL=`` key when the whole ``.env`` line is pasted into the
    value, quotes carried over from either file format, or the tail of a
    ``[section]`` header. None of these are so much the user's mistake as
    an artefact of the tooling, and each produces a baffling error -- a
    stray bracket becomes "password authentication failed", which points
    straight at the credential and away from the real cause.

    The repair is one rule: a PostgreSQL DSN always begins with its
    scheme, so everything before the first occurrence of a scheme is
    debris and is discarded. Surrounding quotes, brackets and whitespace
    are then trimmed. A well-formed DSN passes through byte-for-byte,
    which is what makes this safe to apply unconditionally.

    Verified by ``scripts/verify_config.py``.

    This deliberately does not attempt to repair a wrong password. No
    amount of string tidying recovers a character that was never copied,
    and guessing at one would be dishonest. ``dsn_fingerprint()`` exists
    to make that case obvious instead of mysterious.
    """
    value = dsn.strip()

    # Quotes inherited from .env or .toml syntax, when they wrap the
    # whole value. Done before the scheme is located, so that
    # DATABASE_URL="postgres://..." loses both the key and the quotes.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()

    start = -1
    for scheme in ("postgresql://", "postgres://"):
        found = value.find(scheme)
        if found > start:
            start = found
    if start > 0:
        value = value[start:]

    # Trailing debris left by the slice above, or pasted on its own.
    return value.strip().rstrip("\"' \t\r\n]").strip()


def dsn_fingerprint(dsn: str) -> str:
    """Describe a DSN precisely enough to compare, and safely enough to print.

    This exists because "password authentication failed" is the least
    informative error PostgreSQL emits: it is identical whether the
    password is wrong, truncated, stale after a rotation, or belongs to a
    different branch, and it says nothing about which. When a deployment
    fails from a machine nobody can inspect, the only way to settle it is
    to compare what the deployed process actually holds against what is
    known to work.

    So this prints the connection target in full -- host, port, database,
    user, query parameters -- plus the length and an eight-character
    SHA-256 prefix of the password. That is enough to prove two strings
    are identical or to name the difference between them, and it does not
    disclose the secret: recovering a 16-character credential from a
    truncated digest of it is not feasible.

    Never print the DSN itself. libpq does exactly that when it rejects
    a malformed connection string, so driver error text is redacted
    before it reaches a log or the screen.
    """
    try:
        parsed = urlparse(_normalise_dsn(dsn))
    except ValueError as exc:  # pragma: no cover - urlparse rarely raises
        return f"unparseable ({exc})"

    password = parsed.password or ""
    digest = ""
    if password:
        digest = hashlib.sha256(password.encode("utf-8")).hexdigest()[:8]
    try:
        port = parsed.port or 5432
    except ValueError:
        port = "invalid"

    parts = [
        f"host={parsed.hostname}",
        f"port={port}",
        f"db={parsed.path.lstrip('/') or '(default)'}",
        f"user={parsed.username or '(none)'}",
        f"passlen={len(password)}",
        f"sha256={digest or '(empty)'}",
    ]
    if parsed.query:
        params = "&".join(f"{k}={v}" for k, v in parse_qsl(parsed.query))
        parts.append(f"query={params}")
    if password:
        parts.append("credentials=redacted")
    return " ".join(parts)


def _connection_string() -> str:
    """Resolve the DSN, failing loudly with an actionable message."""
    if db_target() == "local":
        dsn = setting("LOCAL_DATABASE_URL")
        if not dsn:
            raise DatabaseError(
                "DB_TARGET is 'local' but LOCAL_DATABASE_URL is not set. "
                "Copy .env.example to .env and fill it in."
            )
        return _normalise_dsn(dsn)

    dsn = setting("DATABASE_URL")
    if not dsn:
        raise DatabaseError(
            "DATABASE_URL is not set. On Streamlit Cloud, add it under "
            "Deploy -> Settings -> Secrets. Locally, copy .env.example "
            "to .env and paste the connection string from your "
            "provider's dashboard, or run 'neon link' for Neon."
        )
    return _normalise_dsn(dsn)


def _redact(message: str) -> str:
    """Strip credentials out of driver error text.

    libpq quotes the offending password back inside its own error message
    when it refuses a malformed connection string::

        invalid dsn: unexpected spaces found in "<the password>"

    so a database error is not automatically safe to log or display. Two
    passes: credentials sitting in a URI, then any bare ``npg_`` token
    that escaped unquoted.
    """
    message = re.sub(r"(://[^:@\s/]*:)([^@\s]*)@", r"\1<redacted>@", message)
    return re.sub(r"\bnpg_[A-Za-z0-9]+", "npg_<redacted>", message)


@contextmanager
def get_connection(actor: str | None = None):
    """Yield a connection, then always close it.

    ``actor`` is written to the ``app.user`` session setting. The
    ``trg_audit_grade_change`` trigger reads it via
    ``current_setting('app.user', TRUE)`` so the grade_audit table
    records the person who entered a grade rather than the database
    role that happened to execute the statement.
    """
    dsn = _connection_string()
    try:
        conn = psycopg2.connect(dsn, connect_timeout=10)
    except psycopg2.Error as exc:
        detail = _redact(str(exc).strip())
        message = (
            f"Could not connect to the database.\n\n"
            f"Connection target: {dsn_fingerprint(dsn)}\n\n"
            f"Technical detail: {detail}\n\n"
            f"If the deployed app reports this while the same value works "
            f"locally, the secret in the deployment differs from the one "
            f"that works. Compare the fingerprint above with "
            f"'python scripts/dsn_fingerprint.py'; every field, including "
            f"the sha256, must match."
        )
        raise DatabaseError(message) from exc

    try:
        with conn.cursor() as cur:
            if actor:
                # set_config() takes a plain string literal, so the value
                # is passed as a parameter rather than interpolated —
                # an actor name is user-controlled input.
                cur.execute(
                    "SELECT set_config('app.user', %s, false)", (str(actor),)
                )
        yield conn
        conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        raise DatabaseError(_friendly_error(exc)) from exc
    finally:
        conn.close()


def _friendly_error(exc: psycopg2.Error) -> str:
    """Translate a Postgres error into something worth showing a user.

    The raw ``pgcode`` is kept because it is the evidence an
    instructor wants to see next to the rule that was enforced.
    """
    pgcode = getattr(exc, "pgcode", None)
    message = _redact(str(exc).strip().splitlines()[0])

    if pgcode == "23505":
        return f"Duplicate value — {message} (SQLSTATE 23505: unique_violation)"
    if pgcode == "23503":
        return (
            f"Related record missing or still in use — {message} "
            "(SQLSTATE 23503: foreign_key_violation)"
        )
    if pgcode == "23514":
        return (
            f"Value rejected by a CHECK constraint — {message} "
            "(SQLSTATE 23514: check_violation)"
        )
    if pgcode == "P0001":
        # RAISE EXCEPTION from our own PL/pgSQL code.
        return message
    return f"Database error — {message} (SQLSTATE {pgcode})"


def _coerce(value: Any) -> Any:
    """Make a psycopg2 value safe for Streamlit's dataframe renderer.

    psycopg2 returns NUMERIC as ``decimal.Decimal`` and dates as
    ``datetime.date``. Neither survives the Arrow conversion behind
    ``st.dataframe`` — ArrowTypeError: "Expected bytes, got a
    'decimal.Decimal' object" — so every query result would render as
    a broken table.

    Decimal becomes float so the column keeps a numeric dtype, which
    keeps sorting, ``describe()`` and CSV export working. The exact
    decimal value still lives in the database; this only affects
    display.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def query_df(sql: str, params: Iterable[Any] | None = None,
             actor: str | None = None) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame with Arrow-safe column types."""
    with get_connection(actor) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return pd.DataFrame()
            coerced = [{k: _coerce(v) for k, v in row.items()} for row in rows]
            return pd.DataFrame(coerced)


def query_scalar(sql: str, params: Iterable[Any] | None = None,
                 actor: str | None = None) -> Any:
    """Run a SELECT expected to yield exactly one value."""
    with get_connection(actor) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else None


def execute(sql: str, params: Iterable[Any] | None = None,
            actor: str | None = None, returning: str | None = None) -> Any:
    """Run an INSERT/UPDATE/DELETE and return the affected row count.

    ``returning`` yields the inserted row when the caller needs the
    generated primary key — for example after inserting a student so
    the app can immediately create their user account.
    """
    with get_connection(actor) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if returning:
                return cur.fetchone()
            return cur.rowcount


def call_procedure(name: str, args: list[Any] | None = None,
                   actor: str | None = None) -> None:
    """CALL a PL/pgSQL procedure by name with positional arguments.

    The procedure name cannot be parameterised in CALL, so it is
    checked against an allowlist rather than interpolated blindly.
    """
    allowed = {"enroll_student"}
    if name not in allowed:
        raise ValueError(f"Procedure {name!r} is not on the allowlist.")

    placeholders = ", ".join(["%s"] * len(args or []))
    sql = f"CALL {name}({placeholders})"
    with get_connection(actor) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, args or [])


def table_count(table: str) -> int:
    """Row count for a dashboard tile.

    ``table`` is checked against a fixed list because it cannot be
    parameterised — identifiers cannot be bind values in SQL.
    """
    allowed = {
        "users", "departments", "instructors", "students",
        "course_sections", "enrollments", "assignments",
        "submissions", "attendance", "classrooms", "roles",
    }
    if table not in allowed:
        raise ValueError(f"Table {table!r} is not on the allowlist.")
    return int(query_scalar(f"SELECT COUNT(*) FROM {table}") or 0)


def ping() -> tuple[bool, str]:
    """Connectivity check for the sidebar status indicator."""
    try:
        version = query_scalar("SELECT version()")
        return True, str(version).split(" on ")[0]
    except DatabaseError as exc:
        return False, str(exc)