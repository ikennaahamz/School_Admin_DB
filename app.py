"""
School Administration System — Streamlit application entry point.

CMPE344 Database Management Systems and Programming II
Cloud database: Supabase (PostgreSQL)   Front end: Streamlit

Run locally
    streamlit run app.py

Screens are dispatched from the sidebar. Each screen declares the
roles allowed to open it in its ``ROLES`` tuple, and :func:`_gate`
enforces that before the screen function runs. Role checks happen in
the presentation layer only as a convenience to the user — the real
protection for the data is that nothing destructive is reachable
without an authenticated session.
"""

from __future__ import annotations

import streamlit as st

from src import auth, db, screens

st.set_page_config(
    page_title="School Administration System",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

def init_state() -> None:
    """Seed session_state keys. Runs once per browser session."""
    st.session_state.setdefault("user", None)
    st.session_state.setdefault("flash", None)
    st.session_state.setdefault("db_ok", None)
    st.session_state.setdefault("db_version", "")


def flash(kind: str, message: str) -> None:
    """Queue a message to show once, then clear it.

    Streamlit redraws the whole script on interaction, so a message
    set during a button callback has to be stashed and rendered on
    the next pass rather than shown immediately.
    """
    st.session_state["flash"] = (kind, message)


FLASH_HANDLERS = {
    "success": st.success,
    "error": st.error,
    "warning": st.warning,
    "info": st.info,
}


def show_flash() -> None:
    entry = st.session_state.pop("flash", None)
    if not entry:
        return
    kind, message = entry
    # Look the handler up by name and call it. Indexing a dict of
    # bound methods and handing the result to getattr() would pass a
    # method where an attribute *name* was expected.
    FLASH_HANDLERS.get(kind, st.info)(message)


# ---------------------------------------------------------------------------
# Screen registry
# ---------------------------------------------------------------------------

def _gate(user: auth.User, roles: tuple[str, ...]) -> bool:
    """True when the signed-in user may open a screen.

    An empty ``roles`` tuple means "no restriction", so the dashboard
    is reachable by everyone signed in. Passing an empty tuple to
    :func:`auth.require_role` would instead ask whether the user has
    any of zero roles, which is false for everyone but an admin.
    """
    if user is None:
        return False
    if not roles:
        return True
    return auth.require_role(user, *roles)


def screen_dashboard(user):
    screens.dashboard(user)


def screen_students(user):
    screens.students_crud(user)


def screen_instructors(user):
    screens.instructors_crud(user)


def screen_sections(user):
    screens.sections_crud(user)


def screen_enrollments(user):
    screens.enrollments_crud(user)


def screen_assignments(user):
    screens.assignments_crud(user)


def screen_attendance(user):
    screens.attendance_crud(user)


def screen_reports(user):
    screens.reports_screen(user)


def screen_admin(user):
    screens.admin_users(user)


# key, label, icon, handler, allowed roles
SCREENS = [
    ("dashboard",    "Dashboard",     "📊", screen_dashboard,    ()),
    ("students",     "Students",      "🎓", screen_students,     ("admin", "registrar")),
    ("instructors",  "Instructors",   "👨‍🏫", screen_instructors,  ("admin", "registrar")),
    ("sections",     "Courses",       "📚", screen_sections,     ("admin", "registrar", "instructor")),
    ("enrollments",  "Enrolments",    "📝", screen_enrollments,  ("admin", "registrar", "instructor")),
    ("assignments",  "Assignments",   "📄", screen_assignments,  ("admin", "registrar", "instructor")),
    ("attendance",   "Attendance",    "🗓️", screen_attendance,   ("admin", "registrar", "instructor")),
    ("reports",      "Reports",       "📈", screen_reports,      ("admin", "registrar", "instructor", "student")),
    ("admin",        "User Admin",    "🔐", screen_admin,        ("admin",)),
]


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def render_sidebar() -> str | None:
    with st.sidebar:
        st.markdown("### 🎓 School Administration")
        st.caption("CMPE344 · DBMS and Programming II")

        if st.session_state["user"] is None:
            st.info("Sign in to continue.")
            return None

        user: auth.User = st.session_state["user"]

        st.markdown("---")
        st.markdown(f"**{user.full_name}**")
        st.caption(f"`{user.username}` · level {user.access_level}")
        for role in user.roles:
            st.caption(f"• {role}")

        available = [s for s in SCREENS if _gate(user, s[4])]
        by_key = {s[0]: s for s in available}

        st.markdown("---")
        # Options are the screen keys, and format_func maps a key to its
        # decorated label. Passing labels as the options and indexing
        # them in format_func would not work — format_func receives the
        # option value itself.
        choice = st.radio(
            "Navigate",
            options=list(by_key),
            label_visibility="collapsed",
            format_func=lambda k: f"{by_key[k][2]}  {by_key[k][1]}",
        ) if available else None

        selected_key = choice

        st.markdown("---")
        if st.button("🚪 Sign out", width='stretch'):
            st.session_state["user"] = None
            st.rerun()

        return selected_key


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def render_login() -> None:
    st.markdown("## Sign in")
    st.caption("Demo accounts all use the password `Passw0rd!`")

    tab_login, tab_register = st.tabs(["Sign in", "Register"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("Username", autocomplete="username")
            password = st.text_input("Password", type="password",
                                     autocomplete="current-password")
            submitted = st.form_submit_button("Sign in", type="primary")

        if submitted:
            try:
                user, message = auth.authenticate(username, password)
                if user is None:
                    flash("error", message)
                else:
                    st.session_state["user"] = user
                    flash("success", message)
                    st.rerun()
            except db.DatabaseError as exc:
                flash("error", str(exc))

    with tab_register:
        st.caption("New accounts start as *pending* with the "
                   "*student* role. An administrator activates them.")
        with st.form("register_form"):
            r_username = st.text_input("Username")
            r_email = st.text_input("Email")
            r_first = st.text_input("First name")
            r_last = st.text_input("Last name")
            r_password = st.text_input("Password", type="password")
            r_confirm = st.text_input("Confirm password", type="password")
            registered = st.form_submit_button("Create account")

        if registered:
            try:
                ok, message = auth.register(
                    r_username, r_email, r_first, r_last,
                    r_password, r_confirm,
                )
                flash("success" if ok else "error", message)
            except db.DatabaseError as exc:
                flash("error", str(exc))

    with st.expander("Demo accounts"):
        st.markdown(
            """
| Username     | Role(s)              | Password    |
|--------------|----------------------|-------------|
| `admin`      | admin                | `Passw0rd!` |
| `registrar`  | registrar            | `Passw0rd!` |
| `i.kaya`     | instructor, CS       | `Passw0rd!` |
| `i.koc`      | instructor, technical| `Passw0rd!` |
| `student1`   | student              | `Passw0rd!` |
| `student2`   | student              | `Passw0rd!` |
"""
        )


# ---------------------------------------------------------------------------
# Database status
# ---------------------------------------------------------------------------

def render_db_status() -> None:
    if st.session_state["db_ok"] is None:
        st.session_state["db_ok"], st.session_state["db_version"] = db.ping()
    if not st.session_state["db_ok"]:
        st.error(
            "Not connected to the database. Copy `.env.example` to `.env` "
            "and set `DATABASE_URL` to your Supabase connection string.\n\n"
            f"`{st.session_state['db_version']}`"
        )
        st.stop()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    init_state()

    selected = render_sidebar()
    show_flash()
    render_db_status()

    if st.session_state["user"] is None:
        st.markdown("# 🎓 School Administration System")
        st.caption("Database-backed student records, enrolment, grading "
                   "and management reporting.")
        render_login()
        return

    user = st.session_state["user"]
    handlers = {key: fn for key, _l, _i, fn, _r in SCREENS}

    if selected is None:
        screens.dashboard(user)
        return

    try:
        handlers[selected](user)
    except db.DatabaseError as exc:
        # A constraint or FK violation should read as an explanation,
        # not a stack trace.
        st.error(str(exc))
        st.caption("The database rejected the change. The message above "
                   "names the rule that was enforced.")


if __name__ == "__main__":
    main()