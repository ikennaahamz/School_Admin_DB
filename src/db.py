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
closes it in a ``finally`` block. Supabase's pooler absorbs the
per-connection cost, and correctness matters more than microseconds
here.

The brief asks for the database connection code to be shown in the
report; this file is that code.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable

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
    """Which database the app talks to: 'supabase' or 'local'."""
    return setting("DB_TARGET", "supabase") or "supabase"


def _connection_string() -> str:
    """Resolve the DSN, failing loudly with an actionable message."""
    if db_target() == "local":
        dsn = setting("LOCAL_DATABASE_URL")
        if not dsn:
            raise DatabaseError(
                "DB_TARGET is 'local' but LOCAL_DATABASE_URL is not set. "
                "Copy .env.example to .env and fill it in."
            )
        return dsn

    dsn = setting("DATABASE_URL")
    if not dsn:
        raise DatabaseError(
            "DATABASE_URL is not set. On Streamlit Cloud, add it under "
            "Deploy -> Settings -> Secrets. Locally, copy .env.example "
            "to .env and paste the connection string from Supabase -> "
            "Project Settings -> Database."
        )
    return dsn


@contextmanager
def get_connection(actor: str | None = None):
    """Yield a connection, then always close it.

    ``actor`` is written to the ``app.user`` session setting. The
    ``trg_audit_grade_change`` trigger reads it via
    ``current_setting('app.user', TRUE)`` so the grade_audit table
    records the person who entered a grade rather than the database
    role that happened to execute the statement.
    """
    try:
        conn = psycopg2.connect(_connection_string(), connect_timeout=10)
    except psycopg2.Error as exc:
        raise DatabaseError(
            f"Could not connect to the database. Check DATABASE_URL in your "
            f".env file.\n\nTechnical detail: {exc}"
        ) from exc

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
    message = str(exc).strip().splitlines()[0]

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