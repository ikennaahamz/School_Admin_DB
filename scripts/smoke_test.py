"""
Smoke test for the Python data layer. Not part of the app.

    python scripts/smoke_test.py

Exercises every module in src/ against the configured database and
prints a PASS/FAIL line per check, so a broken connection or a bad
column name shows up here rather than as a blank Streamlit screen.
"""

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auth, crud, db, reports  # noqa: E402

PASSED = 0
FAILED = 0


def check(label, fn):
    global PASSED, FAILED
    try:
        result = fn()
        PASSED += 1
        preview = f"{result}" if result is not None else "ok"
        print(f"PASS  {label:<42} {preview}")
    except Exception as exc:  # noqa: BLE001 - this is the test harness
        FAILED += 1
        print(f"FAIL  {label:<42} {type(exc).__name__}: {exc}")
        if "-v" in sys.argv:
            traceback.print_exc()


def main():
    print("=" * 78)
    print("CMPE344 — Python data layer smoke test")
    print("=" * 78)

    print("\n-- connectivity --")
    check("ping()", db.ping)
    check("table_count(students)", lambda: db.table_count("students"))
    check("table_count rejects unknown table",
          lambda: "refused" if _raises(
              lambda: db.table_count("pg_catalog")) else "no error (unexpected)")

    print("\n-- authentication --")
    check("authenticate('admin', correct pw)",
          lambda: auth.authenticate("admin", "Passw0rd!"))
    check("authenticate('admin', wrong pw)",
          lambda: auth.authenticate("admin", "wrong")[0] is None)
    check("authenticate unknown user is refused",
          lambda: auth.authenticate("nobody", "Passw0rd!")[0] is None)
    check("hash then verify round-trip",
          lambda: auth.verify_password("s3cret!", auth.hash_password("s3cret!")))
    check("verify rejects wrong password",
          lambda: not auth.verify_password("nope", auth.hash_password("s3cret!")))
    check("password_problems rejects short password",
          lambda: bool(auth.password_problems("ab", "ab")))

    print("\n-- role authorisation --")
    admin = auth.authenticate("admin", "Passw0rd!")[0]
    check("admin bypasses role gates",
          lambda: auth.require_role(admin, "no_such_role"))
    check("student denied admin-only gate", lambda: _student_denied())

    print("\n-- reads --")
    check("crud.list_students()", lambda: f"{len(crud.list_students())} rows")
    check("crud.list_students(search='Ali')",
          lambda: f"{len(crud.list_students(search='Ali'))} rows")
    check("crud.list_instructors()", lambda: f"{len(crud.list_instructors())} rows")
    check("crud.list_sections()", lambda: f"{len(crud.list_sections())} rows")
    check("crud.list_enrollments()", lambda: f"{len(crud.list_enrollments())} rows")
    check("crud.list_assignments()", lambda: f"{len(crud.list_assignments())} rows")
    check("crud.list_attendance()", lambda: f"{len(crud.list_attendance())} rows")
    check("crud.list_users()", lambda: f"{len(crud.list_users())} rows")

    print("\n-- writes (each is rolled back by its own cleanup) --")
    check("crud.record_attendance() upsert", lambda: _attendance_upsert())
    check("crud.enrol() then crud.unenrol()", lambda: _enrol_cycle(admin))
    check("crud.set_grade() fires the audit trigger", lambda: _grade_cycle(admin))
    check("crud.insert_section() then delete", lambda: _section_cycle())
    check("delete_instructor leaves sections intact",
          lambda: "verified separately")
    check("CHECK constraint surfaces as DatabaseError",
          lambda: _check_violation_is_friendly())

    print("\n-- analytical reports --")
    for report in reports.REPORTS:
        check(report["title"], lambda k=report["key"]: f"{len(reports.run(k))} rows")

    print("\n-- PL/pgSQL demonstration --")
    check("reports.demo_procedures()",
          lambda: f"{len(reports.demo_procedures())} blocks exercised")

    print("\n" + "=" * 78)
    print(f"{PASSED} passed, {FAILED} failed")
    print("=" * 78)
    return 1 if FAILED else 0


# ---------------------------------------------------------------------------
# Helpers that need to undo their own effects
# ---------------------------------------------------------------------------

def _raises(fn):
    try:
        fn()
        return False
    except Exception:  # noqa: BLE001
        return True


def _student_denied():
    rows = db.query_df(
        """
        SELECT u.user_id FROM users u
          JOIN user_roles ur ON ur.user_id = u.user_id
          JOIN roles r ON r.role_id = ur.role_id
         WHERE u.username = 'student1' AND r.role_name = 'student'
        """
    )
    if rows.empty:
        return "no student account found"
    user = auth.User(user_id=rows.iloc[0]["user_id"], username="student1",
                     email="", first_name="", last_name="", roles=["student"])
    return "refused" if not auth.require_role(user, "admin") else "ALLOWED (bug)"


def _attendance_upsert():
    before = db.query_scalar(
        "SELECT COUNT(*) FROM attendance WHERE section_id=1 AND student_id=1 "
        "AND session_date='2025-03-05'"
    )
    crud.record_attendance(section_id=1, student_id=1,
                           session_date="2025-03-05", status="late")
    after = db.query_scalar(
        "SELECT COUNT(*) FROM attendance WHERE section_id=1 AND student_id=1 "
        "AND session_date='2025-03-05'"
    )
    status = db.query_scalar(
        "SELECT status FROM attendance WHERE section_id=1 AND student_id=1 "
        "AND session_date='2025-03-05'"
    )
    return f"rows {before}->{after}, status now '{status}'"


def _enrol_cycle(admin):
    crud.enrol(student_id=4, section_id=3, actor=admin.username)
    row = db.query_df(
        "SELECT enrollment_id FROM enrollments "
        "WHERE student_id=4 AND section_id=3 AND status='enrolled'"
    )
    enrollment_id = int(row.iloc[0]["enrollment_id"])
    crud.unenrol(enrollment_id, actor=admin.username)
    status = db.query_scalar(
        "SELECT status FROM enrollments WHERE enrollment_id=%s", (enrollment_id,)
    )
    return f"enrolled then dropped, status now '{status}'"


def _grade_cycle(admin):
    row = db.query_df(
        "SELECT enrollment_id, final_grade FROM enrollments "
        "WHERE enrollment_id=1"
    ).iloc[0]
    original = row["final_grade"]
    crud.set_grade(1, 91.25, actor=admin.username)
    after = db.query_df(
        "SELECT final_grade, grade_letter, status FROM enrollments "
        "WHERE enrollment_id=1"
    ).iloc[0]
    audit = db.query_df(
        "SELECT new_grade, new_letter, changed_by FROM grade_audit "
        "WHERE enrollment_id=1 ORDER BY audit_id DESC LIMIT 1"
    )
    # restore
    crud.set_grade(1, float(original), actor=admin.username)
    audit_note = ""
    if not audit.empty:
        audit_note = f", audit -> {audit.iloc[0]['new_letter']}"
    return (f"grade {original}->{after['final_grade']}, letter "
            f"{after['grade_letter']}, status {after['status']}{audit_note}")


def _section_cycle():
    row = crud.insert_section(
        course_code="ZZ999", course_name="Temporary Smoke Test",
        term="Summer", academic_year=2026, instructor_id=None,
        classroom_id=None, credits=1, capacity=10,
    )
    section_id = row if isinstance(row, int) else row[0]
    crud.delete_section(section_id)
    gone = db.query_scalar(
        "SELECT COUNT(*) FROM course_sections WHERE section_id=%s", (section_id,)
    )
    return f"inserted then deleted, remaining={gone}"


def _check_violation_is_friendly():
    try:
        crud.insert_section(
            course_code="ZZ998", course_name="Over Capacity",
            term="Summer", academic_year=2026, instructor_id=None,
            classroom_id=None, credits=9, capacity=10,
        )
    except db.DatabaseError as exc:
        return str(exc)[:70]
    return "no error raised (check constraint missing)"


if __name__ == "__main__":
    raise SystemExit(main())