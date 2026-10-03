"""
Headless test of the Streamlit app.

    python scripts/app_test.py

Uses streamlit.testing.v1.AppTest, which runs app.py the same way
Streamlit does — re-executing the script on every interaction — but
without a browser. That makes it possible to assert that each screen
renders, that the login gate holds, and that role gating hides the
admin screen from a non-admin.

This is the difference between "the file compiles" and "the screen
works".
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The console is cp1252 by default on Windows, and the screen labels
# contain emoji. Without this, printing a failure detail raises
# UnicodeEncodeError and hides the real assertion result.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from streamlit.testing.v1 import AppTest  # noqa: E402

PASSED = 0
FAILED = 0


def check(label, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"PASS  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}" + (f"  — {detail}" if detail else ""))


def fresh() -> AppTest:
    """Build an AppTest and execute the script once.

    from_file() only constructs the test; without the initial run()
    there are no elements to interact with, so every accessor would
    return an empty list and every check would silently pass.
    """
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()


def sign_in(at: AppTest, username="admin", password="Passw0rd!") -> AppTest:
    """Drive the login form and return the app in a signed-in state.

    The values are staged and the button clicked before a single run().
    Calling .run() between them consumes the staged value and resets
    the widget, so the form would submit empty.
    """
    at.text_input[0].set_value(username)
    at.text_input[1].set_value(password)
    at.button[0].click()          # at.button[0] is "Sign in"
    return at.run()


def main() -> int:
    print("=" * 74)
    print("CMPE344 — Streamlit application test")
    print("=" * 74)

    # ---------------------------------------------------------------- login
    print("\n-- login screen --")
    at = fresh()
    check("app renders without exception", not at.exception,
          str(at.exception) if at.exception else "")
    check("shows the sign-in heading",
          any("Sign in" in h.value for h in at.markdown))
    check("has username and password fields", len(at.text_input) >= 2)
    # AppTest does not expose whether a field is masked in this
    # Streamlit version, so the source is asserted instead.
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    check("password fields declared type='password'",
          source.count('type="password"') >= 2,
          f"found {source.count(chr(39))} password declarations")

    print("\n-- rejected credentials --")
    at = fresh()
    at.text_input[0].set_value("admin")
    at.text_input[1].set_value("wrong-password")
    at.button[0].click()
    at = at.run().run()
    check("wrong password does not sign in", at.session_state["user"] is None)
    check("wrong password shows an error", at.error,
          "no error surfaced")

    print("\n-- successful login --")
    at = sign_in(fresh())
    user = at.session_state["user"]
    check("admin session established", user is not None)
    check("admin role attached", user and "admin" in user.roles,
          f"roles={user.roles if user else None}")
    check("access level is 5", user and user.access_level == 5)
    check("navigation radio appears", len(at.radio) >= 1)
    check("sign-out button present",
          any("Sign out" in b.label for b in at.button))

    # ------------------------------------------------------------ dashboard
    print("\n-- dashboard --")
    options = at.radio[0].options
    check("admin sees all 9 screens", len(options) == 9,
          f"saw {len(options)}: {options}")
    check("dashboard is default", at.radio[0].value == "dashboard")
    check("metric tiles render", len(at.metric) >= 6,
          f"{len(at.metric)} tiles")
    check("dashboard renders no exception", not at.exception,
          str(at.exception) if at.exception else "")
    check("dataframe shown", len(at.dataframe) >= 1)

    # --------------------------------------------------- role-based gating
    print("\n-- role gating --")
    # Expected screen counts, derived from SCREENS in app.py:
    #   dashboard + reports ......... everyone
    #   students, instructors ...... admin, registrar
    #   courses, enrolments,
    #   assignments, attendance .... admin, registrar, instructor
    #   user admin ................. admin only
    at = sign_in(fresh(), "student1")
    student = at.session_state["user"]
    check("student session established", student is not None)
    check("student has the student role", student and "student" in student.roles)
    student_screens = len(at.radio[0].options)
    check("student sees exactly 2 screens", student_screens == 2,
          f"saw {student_screens}: {at.radio[0].options}")
    check("student cannot see User Admin", "User Admin" not in str(
        at.radio[0].options))
    check("student cannot see Students CRUD", "Students" not in str(
        at.radio[0].options))
    check("student can still see Reports", "Reports" in str(
        at.radio[0].options))

    at = sign_in(fresh(), "i.kaya")
    instructor = at.session_state["user"]
    check("instructor session established", instructor is not None)
    instructor_screens = len(at.radio[0].options)
    check("instructor sees exactly 6 screens", instructor_screens == 6,
          f"saw {instructor_screens}: {at.radio[0].options}")
    check("instructor cannot manage users",
          "User Admin" not in str(at.radio[0].options))
    check("instructor cannot edit students",
          "Students" not in str(at.radio[0].options))

    at = sign_in(fresh(), "registrar")
    check("registrar sees exactly 8 screens",
          len(at.radio[0].options) == 8,
          f"saw {len(at.radio[0].options)}")

    # -------------------------------------------- every screen renders
    print("\n-- every admin screen renders --")
    for screen in ["dashboard", "students", "instructors", "sections",
                   "enrollments", "assignments", "attendance", "reports",
                   "admin"]:
        at = sign_in(fresh())
        at.radio[0].set_value(screen).run()
        ok = not at.exception
        check(f"'{screen}' renders", ok,
              str(at.exception[0].message) if at.exception else "")
        if ok and screen != "dashboard":
            check(f"'{screen}' produced output",
                  bool(at.dataframe or at.metric or at.markdown))

    # ------------------------------------------- reports actually query
    print("\n-- reports screen runs real queries --")
    at = sign_in(fresh())
    at.radio[0].set_value("reports").run()
    check("reports screen has no error", not at.exception,
          str(at.exception[0].message) if at.exception else "")
    check("report picker lists 8 reports",
          len(at.selectbox[0].options) == 8,
          f"{len(at.selectbox[0].options)} options")
    check("results table rendered", len(at.dataframe) >= 1)
    check("CSV download offered", len(at.download_button) >= 1)

    print("\n-- every report executes --")
    for index in range(8):
        at = sign_in(fresh())
        at.radio[0].set_value("reports").run()
        at.selectbox[0].set_value(at.selectbox[0].options[index]).run()
        check(f"report {index + 1} executes", not at.exception,
              str(at.exception[0].message) if at.exception else "")

    # ---------------------------------------------- PL/pgSQL tab output
    print("\n-- PL/pgSQL demonstration tab --")
    at = sign_in(fresh())
    at.radio[0].set_value("reports").run()
    check("procedural block output rendered", len(at.dataframe) >= 2,
          f"{len(at.dataframe)} tables")

    # ------------------------------------------------------ CRUD insert
    print("\n-- CRUD insert path --")
    at = sign_in(fresh())
    at.radio[0].set_value("sections").run()
    check("sections screen renders forms", len(at.number_input) >= 1)
    check("sections screen has no error", not at.exception,
          str(at.exception[0].message) if at.exception else "")

    print("\n" + "=" * 74)
    print(f"{PASSED} passed, {FAILED} failed")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())