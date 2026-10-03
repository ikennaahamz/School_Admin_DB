"""
Authentication and role-based authorisation.

The brief requires a user table that allows users to log in, plus
support for user groups (admin, employee, customer, technical
staff) each with a specific role in the system. This module is that
mechanism:

* passwords are stored as bcrypt digests and never in plaintext;
* login fetches the digest for the given username and compares the
  candidate against it with bcrypt's constant-time comparison, so a
  failed attempt reveals nothing about how close the guess was;
* roles come from the ``user_roles`` join table, so a person may
  hold more than one;
* :func:`require_role` is what each screen calls to gate itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import bcrypt

from src import db

# Cost 12 is bcrypt's default and takes roughly a quarter second per
# hash on typical hardware. Raising it later only requires rehashing
# on next successful login, so it is safe to leave here.
BCRYPT_ROUNDS = 12

MIN_PASSWORD_LENGTH = 8


@dataclass
class User:
    """The authenticated principal, as the app understands it."""

    user_id: int
    username: str
    email: str
    first_name: str
    last_name: str
    roles: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def access_level(self) -> int:
        """Highest access level across the user's roles.

        Mirrors ``roles.access_level``. Read from the database rather
        than hardcoded, so a level set in the admin screen takes
        effect on the next login.
        """
        if not self.roles:
            return 0
        levels = {
            row["access_level"]
            for row in db.query_df(
                """
                SELECT access_level FROM roles
                WHERE role_name = ANY(%s)
                """,
                (self.roles,),
            ).to_dict("records")
        }
        return max(levels) if levels else 0

    def has_role(self, *names: str) -> bool:
        return any(name in self.roles for name in names)


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def hash_password(plaintext: str) -> str:
    """Return a bcrypt digest for ``plaintext``."""
    return bcrypt.hashpw(
        plaintext.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("utf-8")


def verify_password(plaintext: str, digest: str) -> bool:
    """Constant-time comparison of a candidate password against a digest."""
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), digest.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed digest in the row: fail closed rather than crash.
        return False


def password_problems(password: str, confirmation: str) -> list[str]:
    """Validate a new password. Returns a list of problems; empty is good."""
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Must be at least {MIN_PASSWORD_LENGTH} characters.")
    if password != confirmation:
        problems.append("The two passwords do not match.")
    if not any(c.isalpha() for c in password):
        problems.append("Must contain at least one letter.")
    if not any(c.isdigit() for c in password):
        problems.append("Must contain at least one digit.")
    return problems


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def authenticate(username: str, password: str) -> tuple[User | None, str]:
    """Attempt a login.

    Returns ``(user, message)``. On failure ``user`` is ``None`` and
    the message is deliberately identical for "no such user" and
    "wrong password", so the form cannot be used to enumerate valid
    usernames.
    """
    if not username or not password:
        return None, "Enter both a username and a password."

    rows = db.query_df(
        """
        SELECT user_id, username, email, password_hash,
               first_name, last_name, status
          FROM users
         WHERE username = %s
        """,
        (username.strip().lower(),),
    ).to_dict("records")

    if not rows:
        # Spend roughly the same time as a real verification so that
        # response timing does not reveal whether the user exists.
        verify_password(password, hash_password("decoy"))
        return None, "Incorrect username or password."

    row = rows[0]

    if not verify_password(password, row["password_hash"]):
        return None, "Incorrect username or password."

    if row["status"] != "active":
        return None, (
            f"This account is '{row['status']}'. Ask an administrator to "
            "activate it."
        )

    roles = db.query_df(
        """
        SELECT r.role_name
          FROM user_roles ur
          JOIN roles r ON r.role_id = ur.role_id
         WHERE ur.user_id = %s
         ORDER BY r.role_name
        """,
        (row["user_id"],),
    )["role_name"].tolist()

    if not roles:
        return None, "This account has no roles assigned."

    user = User(
        user_id=row["user_id"],
        username=row["username"],
        email=row["email"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        roles=roles,
    )
    return user, f"Signed in as {user.full_name} ({', '.join(roles)})."


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(username: str, email: str, first_name: str, last_name: str,
             password: str, confirmation: str,
             role_name: str = "student") -> tuple[bool, str]:
    """Create a user and its first role.

    New accounts default to the ``student`` role and to
    ``status = 'pending'``, so self-registration cannot mint an
    administrator. An admin promotes the account from the admin
    screen.
    """
    problems = password_problems(password, confirmation)
    if problems:
        return False, " ".join(problems)

    username = username.strip().lower()
    email = email.strip().lower()

    if not username or len(username) < 3:
        return False, "Username must be at least 3 characters."
    if "@" not in email or "." not in email.split("@")[-1]:
        return False, "Enter a valid email address."
    if not first_name.strip() or not last_name.strip():
        return False, "Both first and last name are required."

    existing = db.query_scalar(
        "SELECT COUNT(*) FROM users WHERE username = %s OR email = %s",
        (username, email),
    )
    if existing:
        return False, "That username or email is already registered."

    row = db.execute(
        """
        INSERT INTO users (username, email, password_hash,
                           first_name, last_name, status)
        VALUES (%s, %s, %s, %s, %s, 'pending')
        RETURNING user_id
        """,
        (username, email, hash_password(password),
         first_name.strip(), last_name.strip()),
        returning=True,
    )
    user_id = row[0]

    # The join table is what makes roles many-to-many. Assigning here
    # keeps registration to a single place.
    db.execute(
        """
        INSERT INTO user_roles (user_id, role_id)
        SELECT %s, role_id FROM roles WHERE role_name = %s
        """,
        (user_id, role_name),
    )

    return True, (
        f"Account '{username}' created with the '{role_name}' role. "
        "An administrator must activate it before you can sign in."
    )


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------

def require_role(user: User | None, *allowed: str) -> bool:
    """True if the user holds at least one of ``allowed``.

    Administrators pass every gate by design: they manage the system.
    """
    if user is None:
        return False
    if user.is_admin:
        return True
    return user.has_role(*allowed)


def visible_role_names(user: User) -> list[str]:
    """Every role name in the database, for populating role pickers."""
    return db.query_df(
        "SELECT role_name FROM roles ORDER BY access_level DESC, role_name"
    )["role_name"].tolist()


def describe(user: User | None) -> str:
    """One-line identity summary for the sidebar."""
    if user is None:
        return "Not signed in"
    return f"{user.full_name} — {', '.join(user.roles)}"


def lookup_user(user_id: int) -> dict[str, Any] | None:
    """Fetch a user row by id, for the admin screens."""
    rows = db.query_df(
        """
        SELECT user_id, username, email, first_name, last_name,
               status, created_at
          FROM users WHERE user_id = %s
        """,
        (user_id,),
    ).to_dict("records")
    return rows[0] if rows else None