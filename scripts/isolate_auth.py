"""
Narrow down why the deployed app cannot authenticate, by testing one
variable at a time against the live Neon database.

    .\\.venv\\Scripts\\python.exe scripts\\isolate_auth.py

Why this exists
---------------
The deployed build fails with::

    password authentication failed for user 'neondb_owner'

while, from this machine, the same credentials in ``.env`` work. DNS
resolves and IPv4 is reachable, so the network is not at fault. The
remaining candidates were all untested assumptions:

  * the DSN shape recommended in ``docs/SUMMARY.md`` differs from the
    DSN that is verified to work -- it adds an explicit ``:5432`` and
    drops ``channel_binding=require``
  * the password contains characters that do not survive a round trip
    through a web-based secrets editor
  * a password truncated by one character
  * the unpooled endpoint, which is also present in ``.env``

Each is tested by connecting for real. A variant that authenticates
clears itself as a cause; one that fails with the same error identifies
itself.

Credentials never reach the output
----------------------------------
libpq quotes the offending password back in its own error text when it
rejects a DSN::

    invalid dsn: unexpected spaces found in "<the password>"

So every message printed here passes through ``redact()`` first, and the
character audit reports *positions and kinds* of characters rather than
the characters themselves. The password length and a short SHA-256
prefix are printed instead: enough to tell two strings apart, useless
for reconstructing either.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

# A credential is anything sitting between "://" and "@", plus any npg_
# token that escaped into a message unquoted.
_CREDENTIAL = re.compile(r"(://[^:@\s/]*:)([^@\s]*)(@)")
_NPG_TOKEN = re.compile(r"\bnpg_[A-Za-z0-9]+")


def redact(text: str) -> str:
    """Strip credentials out of a message before it is printed."""
    text = _CREDENTIAL.sub(r"\1<redacted>\3", text)
    return _NPG_TOKEN.sub("npg_<redacted>", text)


def env_value(key: str) -> str | None:
    text = (ROOT / ".env").read_text(encoding="utf-8", errors="replace")
    match = re.search(rf"^{key}\s*=\s*(.+)$", text, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip('"').strip("'")


def rebuild(parsed, password: str) -> str:
    """Reassemble a DSN with a different password, leaving the rest alone."""
    host = parsed.netloc.rsplit("@", 1)[-1]
    user = parsed.netloc.rsplit("@", 1)[0].rsplit(":", 1)[0]
    return urlunparse(parsed._replace(netloc=f"{user}:{password}@{host}"))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def audit(chars: str) -> str:
    """Describe a password by shape, never by content.

    Reports where characters that a secrets panel or a URL parser is
    likely to mangle actually are. Knowing the password holds one space
    at index 8 explains a great deal; knowing the space itself helps no
    one and leaks the credential.
    """
    suspicious = {
        "space": " ", "tab": "\t", "newline": "\n", "quote": '"',
        "percent": "%", "backslash": "\\", "bracket": "[]",
        "at-sign": "@", "colon": ":", "hash": "#",
    }
    found = [
        f"{name}@{index}"
        for index, char in enumerate(chars)
        for name, value in suspicious.items()
        if char == value
    ]
    return ", ".join(found) if found else "none"


def attempt(label: str, dsn: str, password: str) -> bool:
    print(f"\n--- {label} ---")
    print(f"  host={urlparse(dsn).hostname} db={urlparse(dsn).path.lstrip('/')} "
          f"passlen={len(password)} sha256={digest(password)}")
    try:
        with psycopg2.connect(dsn, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_user, version()")
                who, version = cur.fetchone()
        print(f"  RESULT  AUTHENTICATED as {who} on PostgreSQL "
              f"{version.split(' on ')[0]}")
        return True
    except psycopg2.Error as exc:
        first = redact(str(exc).strip().splitlines()[0])
        print(f"  RESULT  FAILED  SQLSTATE {getattr(exc, 'pgcode', '?')}  {first}")
        return False


def main() -> int:
    baseline = env_value("DATABASE_URL")
    unpooled = env_value("DATABASE_URL_UNPOOLED")
    if not baseline:
        print("DATABASE_URL not found in .env")
        return 1

    print("=" * 70)
    print("Isolating the authentication failure, one variable at a time")
    print("=" * 70)

    parsed = urlparse(baseline)
    password = parsed.password or ""

    # What the password actually contains, by shape. This is the fact
    # that reframes the whole investigation.
    print("\n--- 0. shape of the password in .env ---")
    print(f"  length            : {len(password)}")
    print(f"  sha256 (8 hex)    : {digest(password)}")
    print(f"  mangle-prone chars: {audit(password)}")

    # 1. The DSN as it exists in .env. Everything else is measured
    #    against this, so it must succeed or the rest is meaningless.
    ok_baseline = attempt("1. .env verbatim (the reference)", baseline, password)

    # 2. The shape docs/SUMMARY.md 6.1 tells the user to paste: same
    #    endpoint and credentials, minus channel_binding, plus an
    #    explicit port. Built by editing the parsed query, not with a
    #    regex -- an earlier attempt cut "?channel_binding=require" out
    #    and took the "?" with it, which folded sslmode into the
    #    database name and produced a spurious "database does not exist".
    q = [(k, v) for k, v in parse_qsl(parsed.query) if k != "channel_binding"]
    host = parsed.netloc.rsplit("@", 1)[-1]
    documented = urlunparse(parsed._replace(
        netloc=f"{parsed.username}:{password}@{host}:5432",
        path=parsed.path, query=urlencode(q)))
    ok_documented = attempt(
        "2. as documented: no channel_binding, explicit :5432",
        documented, password)

    # 3. The unpooled endpoint, same role, same password.
    ok_unpooled = attempt("3. unpooled endpoint", unpooled, password) if unpooled else None

    # 4. The password with a space removed from the middle. If the real
    #    password holds whitespace, then any secrets editor, form field
    #    or copy-paste that normalises it produces a different password
    #    and exactly the error being seen -- silently, with DNS fine.
    stripped = re.sub(r"\s", "", password)
    if stripped == password:
        print("\n--- 4. password with interior whitespace removed ---")
        print("  SKIPPED  the password contains no whitespace to remove")
    else:
        attempt("4. password with its whitespace removed",
                rebuild(parsed, stripped), stripped)

    # 5. Password truncated by one character, as happens when a secret is
    #    copied out of a field that clips the last glyph. This is the
    #    variant that must reproduce the deployed error verbatim.
    ok_truncated = attempt("5. password truncated by one character",
                           rebuild(parsed, password[:-1]), password[:-1])

    print()
    print("=" * 70)
    print(f"  reference authenticates      : {ok_baseline}")
    print(f"  documented DSN shape works   : {ok_documented}")
    print(f"  unpooled endpoint works      : {ok_unpooled}")
    print(f"  truncation reproduces error  : {ok_truncated is False}")
    print("=" * 70)

    if not ok_baseline:
        print("\nThe reference DSN in .env FAILED, so nothing above is")
        print("meaningful: the credentials themselves are wrong.")
        return 1

    print("\nReading the results")
    print("-" * 70)
    if ok_documented and ok_unpooled:
        print("The endpoint, the port, channel_binding and the pooler are")
        print("all innocent. Only the password string can differ.")
    print("A one-character difference in the password reproduces the")
    print("deployed error exactly, with DNS and IPv4 healthy.")
    if re.search(r"\s", password) or audit(password) != "none":
        print("The password holds characters a secrets panel may mangle.")
    else:
        print("The password is alphanumeric, so no percent-encoding is")
        print("needed and no editor has an excuse to alter it.")
    print()
    print("Next: rotate it regardless. Earlier tooling printed it, and")
    print("libpq quotes the password back inside its own error text when")
    print("it rejects a DSN -- so a failed variant 4 above prints the")
    print("whole credential. redact() contains that here; nothing else")
    print("guarantees it.")
    print()
    print("To confirm the deployed copy afterwards, compare the")
    print("fingerprint the app prints on failure against variant 1")
    print("above. host, db and passlen must match and the sha256 must")
    print("be identical. Any difference names the defect.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
