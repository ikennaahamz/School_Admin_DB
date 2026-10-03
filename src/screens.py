"""
Screen implementations.

One function per screen, each receiving the authenticated
:class:`~src.auth.User`. The screens contain no business rules —
they validate that a form was filled in and let the database decide
whether the result is legal, so a rule can never be bypassed by
editing a request in the browser.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import auth, crud, db, reports


# ---------------------------------------------------------------------------
# Shared widgets
# ---------------------------------------------------------------------------

def _options(df: pd.DataFrame, id_col: str, label_col: str) -> dict[int, str]:
    """Turn an id column into a {id: label} map for a selectbox."""
    return dict(zip(df[id_col].astype(int), df[label_col].astype(str)))


def _pick(df: pd.DataFrame, id_col: str, label_col: str,
          prompt: str = "Select") -> int | None:
    """A selectbox over a lookup table. Returns None when unselected."""
    if df.empty:
        st.warning(f"No rows available to choose from for {prompt.lower()}.")
        return None
    mapping = _options(df, id_col, label_col)
    chosen = st.selectbox(prompt, ["(none)"] + list(mapping),
                          format_func=lambda v: v if v == "(none)"
                          else mapping[v])
    return None if chosen == "(none)" else int(chosen)


def _flash(action: str, message: str) -> None:
    """Queue a success message for the next render pass."""
    st.session_state["flash"] = ("success", f"{action}: {message}")


def _show_table(df: pd.DataFrame, height: int = 320) -> None:
    if df.empty:
        st.info("No records.")
        return
    st.dataframe(df, width='stretch', height=height)


def _departments() -> pd.DataFrame:
    return db.query_df(
        "SELECT department_id, dept_name FROM departments ORDER BY dept_code"
    )


# ---------------------------------------------------------------------------
# 1. Dashboard
# ---------------------------------------------------------------------------

def dashboard(user: auth.User) -> None:
    st.markdown("## 📊 Dashboard")
    st.caption(f"Signed in as **{user.full_name}** "
               f"({'/'.join(user.roles)}, access level {user.access_level})")

    tiles = [
        ("Students", "students"),
        ("Instructors", "instructors"),
        ("Course sections", "course_sections"),
        ("Enrolments", "enrollments"),
        ("Assignments", "assignments"),
        ("Attendance rows", "attendance"),
    ]
    columns = st.columns(6)
    for col, (label, table) in zip(columns, tiles):
        try:
            col.metric(label, f"{db.table_count(table):,}")
        except db.DatabaseError as exc:
            col.metric(label, "—")
            col.caption(str(exc)[:60])

    st.markdown("---")

    left, right = st.columns(2)

    with left:
        st.markdown("### Sections by capacity")
        _show_table(crud.list_sections(term="Fall", academic_year=2025), 300)

    with right:
        st.markdown("### Grade distribution")
        _show_table(reports.run("q5"), 300)

    st.markdown("### Departments")
    _show_table(db.query_df(
        """
        SELECT dept_code, dept_name, established_year,
               annual_budget
          FROM departments ORDER BY dept_code
        """
    ), 260)

    if user.is_admin:
        st.markdown("### Recent grade changes")
        _show_table(db.query_df(
            """
            SELECT ga.changed_at, ga.changed_by,
                   s.student_no,
                   u.first_name || ' ' || u.last_name AS student_name,
                   cs.course_code,
                   ga.old_grade, ga.new_grade, ga.new_letter
              FROM grade_audit ga
              JOIN enrollments e     ON e.enrollment_id = ga.enrollment_id
              JOIN students s        ON s.student_id = ga.student_id
              JOIN users u           ON u.user_id = s.user_id
              JOIN course_sections cs ON cs.section_id = ga.section_id
             ORDER BY ga.audit_id DESC LIMIT 10
            """
        ), 300)


# ---------------------------------------------------------------------------
# 2. Students
# ---------------------------------------------------------------------------

def students_crud(user: auth.User) -> None:
    st.markdown("## 🎓 Students")

    tab_list, tab_add, tab_edit, tab_delete = st.tabs(
        ["View", "Insert", "Update", "Delete"]
    )

    departments = _departments()

    with tab_list:
        dept_map = _options(departments, "department_id", "dept_name")
        chosen_dept = st.selectbox(
            "Department", ["(all)"] + list(dept_map),
            format_func=lambda v: v if v == "(all)" else dept_map[v],
        )
        search = st.text_input("Search name or student number", "")

        frame = crud.list_students(
            department_id=None if chosen_dept == "(all)" else chosen_dept,
            search=search,
        )
        st.caption(f"{len(frame)} students")
        _show_table(frame, 420)

    with tab_add:
        st.caption("A student profile attaches to an existing user "
                   "account. Create the account in **User Admin** first.")
        with st.form("student_add", clear_on_submit=True):
            username = st.text_input("Existing username", max_chars=50)
            student_no = st.text_input("Student number", "S-")
            department = _pick(departments, "department_id",
                               "dept_name", "Department")
            level = st.selectbox("Programme level",
                                 ["undergraduate", "graduate", "phd"])
            year = st.number_input("Enrolment year", 2000, 2100, 2025)
            advisor = _pick(crud.list_students(), "student_id", "student_name",
                            "Advisor (optional)")
            submitted = st.form_submit_button("Insert student",
                                              type="primary")

        if submitted:
            if not username.strip():
                flash("error", "Enter the username of the linked account.")
            elif not student_no.strip():
                flash("error", "Enter a student number.")
            elif department is None:
                flash("error", "Choose a department.")
            else:
                try:
                    user_id = db.query_scalar(
                        "SELECT user_id FROM users WHERE username = %s",
                        (username.strip().lower(),),
                    )
                    if user_id is None:
                        flash("error",
                              f"No user account named '{username}'. Create "
                              "the account in User Admin first.")
                    else:
                        new_id = crud.insert_student(
                            user_id=user_id, department_id=department,
                            student_no=student_no.strip(), program_level=level,
                            enrollment_year=int(year), advisor_id=advisor,
                        )
                        _flash("Inserted", f"student {student_no} "
                                          f"(student_id {new_id})")
                except db.DatabaseError as exc:
                    flash("error", str(exc))

    with tab_edit:
        frame = crud.list_students()
        student_id = _pick(frame, "student_id", "student_name",
                           "Student to update")
        if student_id:
            current = frame[frame.student_id == student_id].iloc[0]
            with st.form("student_edit"):
                department = st.selectbox(
                    "Department", list(dept_map),
                    index=list(dept_map.values()).index(current["dept_name"]),
                    format_func=lambda v: dept_map[v],
                )
                level = st.selectbox(
                    "Programme level",
                    ["undergraduate", "graduate", "phd"],
                    index=["undergraduate", "graduate", "phd"].index(
                        current["program_level"]),
                )
                year = st.number_input("Enrolment year", 2000, 2100,
                                       int(current["enrollment_year"]))
                advisor_frame = frame[frame.student_id != student_id]
                advisor = _pick(advisor_frame, "student_id", "student_name",
                                "Advisor")
                submitted = st.form_submit_button("Save changes",
                                                  type="primary")

            if submitted:
                try:
                    crud.update_student(
                        student_id, department_id=department,
                        program_level=level, enrollment_year=int(year),
                        advisor_id=advisor,
                    )
                    _flash("Updated", f"student {current['student_no']}")
                except db.DatabaseError as exc:
                    flash("error", str(exc))

    with tab_delete:
        st.warning("Deleting a student profile cascades to that student's "
                   "enrolments, submissions and attendance records. The "
                   "login account itself is kept.")
        frame = crud.list_students()
        student_id = _pick(frame, "student_id", "student_name",
                           "Student to delete")
        if student_id:
            current = frame[frame.student_id == student_id].iloc[0]
            st.write(f"**{current['student_name']}** "
                     f"({current['student_no']})")
            if st.button("Delete permanently", type="primary"):
                try:
                    crud.delete_student(student_id)
                    _flash("Deleted", str(current["student_no"]))
                except db.DatabaseError as exc:
                    flash("error", str(exc))


# ---------------------------------------------------------------------------
# 3. Instructors
# ---------------------------------------------------------------------------

def instructors_crud(user: auth.User) -> None:
    st.markdown("## 👨‍🏫 Instructors")
    tab_list, tab_add, tab_edit, tab_delete = st.tabs(
        ["View", "Insert", "Update", "Delete"]
    )
    departments = _departments()

    with tab_list:
        _show_table(crud.list_instructors(), 400)

    with tab_add:
        with st.form("instructor_add", clear_on_submit=True):
            username = st.text_input("Existing username")
            employee_no = st.text_input("Employee number", "E-")
            department = _pick(departments, "department_id",
                               "dept_name", "Department")
            title = st.selectbox("Rank",
                                 ["Lecturer", "Assistant Professor",
                                  "Associate Professor", "Professor"])
            hire_date = st.date_input("Hire date")
            salary = st.number_input("Salary", 0.0, 1_000_000.0, 60_000.0,
                                     step=1_000.0)
            submitted = st.form_submit_button("Insert instructor",
                                              type="primary")

        if submitted:
            user_id = db.query_scalar(
                "SELECT user_id FROM users WHERE username = %s",
                (username.strip().lower(),),
            )
            if user_id is None:
                flash("error", f"No user account named '{username}'.")
            elif department is None:
                flash("error", "Choose a department.")
            else:
                try:
                    new_id = crud.insert_instructor(
                        user_id=user_id, department_id=department,
                        employee_no=employee_no.strip(),
                        hire_date=str(hire_date), rank_title=title,
                        salary=float(salary),
                    )
                    _flash("Inserted", f"instructor_id {new_id}")
                except db.DatabaseError as exc:
                    flash("error", str(exc))

    with tab_edit:
        frame = crud.list_instructors()
        instructor_id = _pick(frame, "instructor_id", "instructor_name",
                              "Instructor to update")
        if instructor_id:
            current = frame[frame.instructor_id == instructor_id].iloc[0]
            dept_map = _options(departments, "department_id", "dept_name")
            with st.form("instructor_edit"):
                department = st.selectbox(
                    "Department", list(dept_map),
                    index=list(dept_map.values()).index(current["dept_name"]),
                    format_func=lambda v: dept_map[v],
                )
                title = st.selectbox(
                    "Rank",
                    ["Lecturer", "Assistant Professor",
                     "Associate Professor", "Professor"],
                    index=["Lecturer", "Assistant Professor",
                           "Associate Professor", "Professor"].index(
                        current["rank_title"]),
                )
                salary = st.number_input(
                    "Salary", 0.0, 1_000_000.0,
                    float(current["salary"] or 0.0), step=1_000.0)
                submitted = st.form_submit_button("Save changes",
                                                  type="primary")

            if submitted:
                try:
                    crud.update_instructor(
                        instructor_id, department_id=department,
                        rank_title=title, salary=float(salary))
                    _flash("Updated", str(current["instructor_name"]))
                except db.DatabaseError as exc:
                    flash("error", str(exc))

    with tab_delete:
        st.info("Course sections keep their enrolment history: deleting an "
                "instructor leaves their sections unassigned rather than "
                "removing them.")
        frame = crud.list_instructors()
        instructor_id = _pick(frame, "instructor_id", "instructor_name",
                              "Instructor to delete")
        if instructor_id and st.button("Delete permanently", type="primary"):
            try:
                crud.delete_instructor(instructor_id)
                _flash("Deleted", str(
                    frame[frame.instructor_id == instructor_id]
                    .iloc[0]["instructor_name"]))
            except db.DatabaseError as exc:
                flash("error", str(exc))


# ---------------------------------------------------------------------------
# 4. Course sections
# ---------------------------------------------------------------------------

def sections_crud(user: auth.User) -> None:
    st.markdown("## 📚 Courses & Sections")
    tab_list, tab_add, tab_edit, tab_delete = st.tabs(
        ["View", "Insert", "Update", "Delete"]
    )

    with tab_list:
        term = st.selectbox("Term", ["(all)", "Fall", "Spring", "Summer"])
        year = st.selectbox("Year", ["(all)", "2025", "2026"])
        frame = crud.list_sections(
            term=None if term == "(all)" else term,
            academic_year=None if year == "(all)" else int(year),
        )
        _show_table(frame, 400)

    with tab_add:
        with st.form("section_add", clear_on_submit=True):
            code = st.text_input("Course code", "CS", max_chars=12)
            name = st.text_input("Course name")
            term = st.selectbox("Term", ["Fall", "Spring", "Summer"])
            year = st.number_input("Academic year", 2020, 2100, 2025)
            instructor = _pick(crud.list_instructors(), "instructor_id",
                               "instructor_name", "Instructor (optional)")
            classroom = _pick(db.query_df(
                "SELECT classroom_id, building || ' ' || room_number AS room "
                "FROM classrooms ORDER BY building, room_number"),
                "classroom_id", "room", "Classroom (optional)")
            credits = st.selectbox("Credits", [1, 2, 3, 4, 5, 6])
            capacity = st.number_input("Capacity", 1, 500, 30)
            submitted = st.form_submit_button("Insert section",
                                              type="primary")

        if submitted:
            if not code.strip() or not name.strip():
                flash("error", "Course code and name are both required.")
            else:
                try:
                    new_id = crud.insert_section(
                        course_code=code, course_name=name, term=term,
                        academic_year=int(year), instructor_id=instructor,
                        classroom_id=classroom, credits=int(credits),
                        capacity=int(capacity),
                    )
                    _flash("Inserted", f"section_id {new_id}")
                except db.DatabaseError as exc:
                    flash("error", str(exc))

    with tab_edit:
        frame = crud.list_sections()
        section_id = _pick(frame, "section_id", "course_name",
                           "Section to update")
        if section_id:
            current = frame[frame.section_id == section_id].iloc[0]
            with st.form("section_edit"):
                instructor = _pick(crud.list_instructors(), "instructor_id",
                                   "instructor_name", "Instructor")
                classroom = _pick(db.query_df(
                    "SELECT classroom_id, building || ' ' || room_number AS room "
                    "FROM classrooms ORDER BY building, room_number"),
                    "classroom_id", "room", "Classroom")
                capacity = st.number_input(
                    "Capacity", 1, 500, int(current["capacity"]))
                st.caption(f"Currently {current['enrolled_count']} enrolled. "
                           "Capacity cannot drop below that.")
                submitted = st.form_submit_button("Save changes",
                                                  type="primary")

            if submitted:
                try:
                    crud.update_section(
                        section_id, instructor_id=instructor,
                        classroom_id=classroom, capacity=int(capacity))
                    _flash("Updated", str(current["course_code"]))
                except db.DatabaseError as exc:
                    flash("error", str(exc))

    with tab_delete:
        st.warning("Deleting a section cascades to its enrolments, "
                   "assignments, submissions and attendance rows.")
        frame = crud.list_sections()
        section_id = _pick(frame, "section_id", "course_name",
                           "Section to delete")
        if section_id and st.button("Delete permanently", type="primary"):
            try:
                crud.delete_section(section_id)
                _flash("Deleted", str(
                    frame[frame.section_id == section_id]
                    .iloc[0]["course_code"]))
            except db.DatabaseError as exc:
                flash("error", str(exc))


# ---------------------------------------------------------------------------
# 5. Enrolments
# ---------------------------------------------------------------------------

def enrollments_crud(user: auth.User) -> None:
    st.markdown("## 📝 Enrolments")
    st.caption("Enrolment goes through the `enroll_student` procedure, so "
               "the capacity rule and duplicate check apply. Grades are "
               "recorded through the audit trigger, which derives the "
               "letter and updates the student's GPA.")

    tab_list, tab_enrol, tab_grade, tab_drop = st.tabs(
        ["View", "Enrol", "Grade", "Withdraw"]
    )

    with tab_list:
        status = st.selectbox("Status",
                              ["(all)", "enrolled", "completed",
                               "failed", "dropped"])
        _show_table(crud.list_enrollments(
            status=None if status == "(all)" else status), 420)

    with tab_enrol:
        student_id = _pick(crud.list_students(), "student_id", "student_name",
                           "Student")
        section_id = _pick(crud.list_sections(), "section_id", "course_name",
                           "Section")
        if student_id and section_id:
            if st.button("Enrol student", type="primary"):
                try:
                    crud.enrol(student_id, section_id,
                               actor=user.username)
                    _flash("Enrolled",
                           f"student {student_id} into section {section_id}")
                except db.DatabaseError as exc:
                    # The procedure's own RAISE EXCEPTION messages arrive
                    # here, which is how the UI explains a refusal.
                    flash("error", str(exc))

    with tab_grade:
        frame = crud.list_enrollments(status="completed")
        target = _pick(
            frame.assign(label=frame["student_name"] + " — "
                         + frame["course_code"]),
            "enrollment_id", "label", "Enrolment",
        )
        if target:
            current = frame[frame.enrollment_id == target].iloc[0]
            st.write(f"Current grade: **{current['final_grade']}** "
                     f"({current['grade_letter']})")
            with st.form("grade_form"):
                grade = st.number_input("New final grade (%)",
                                        0.0, 100.0,
                                        float(current["final_grade"] or 0.0),
                                        step=0.5)
                submitted = st.form_submit_button("Record grade",
                                                  type="primary")

            if submitted:
                try:
                    crud.set_grade(target, float(grade), actor=user.username)
                    _flash("Recorded",
                           f"grade {grade} for {current['course_code']}. "
                           "The trigger derived the letter and updated the "
                           "student's GPA.")
                except db.DatabaseError as exc:
                    flash("error", str(exc))

    with tab_drop:
        st.info("Withdrawing sets the status to 'dropped' and keeps the "
                "row, so the history of who took and dropped what "
                "survives.")
        frame = crud.list_enrollments(status="enrolled")
        target = _pick(
            frame.assign(label=frame["student_name"] + " — "
                         + frame["course_code"]),
            "enrollment_id", "label", "Enrolment to withdraw",
        )
        if target and st.button("Withdraw", type="primary"):
            try:
                crud.unenrol(target, actor=user.username)
                _flash("Withdrawn", str(target))
            except db.DatabaseError as exc:
                flash("error", str(exc))


# ---------------------------------------------------------------------------
# 6. Assignments and submissions
# ---------------------------------------------------------------------------

def assignments_crud(user: auth.User) -> None:
    st.markdown("## 📄 Assignments & Submissions")
    tab_assign, tab_submissions, tab_grade = st.tabs(
        ["Assignments", "Submissions", "Grade a submission"]
    )

    with tab_assign:
        tab_list, tab_add, tab_delete = st.tabs(
            ["List", "Insert", "Delete"]
        )

        with tab_list:
            _show_table(crud.list_assignments(), 400)

        with tab_add:
            with st.form("assignment_add", clear_on_submit=True):
                section_id = _pick(crud.list_sections(), "section_id",
                                   "course_name", "Section")
                title = st.text_input("Title")
                weight = st.number_input("Weight (%)", 0.5, 100.0, 10.0,
                                        step=0.5)
                max_points = st.number_input("Max points", 1.0, 1000.0,
                                             100.0)
                due_date = st.date_input("Due date")
                submitted = st.form_submit_button("Insert assignment",
                                                  type="primary")

            if submitted:
                if section_id is None:
                    flash("error", "Choose a section.")
                elif not title.strip():
                    flash("error", "Enter a title.")
                else:
                    try:
                        new_id = crud.insert_assignment(
                            section_id=section_id, title=title.strip(),
                            weight=float(weight),
                            max_points=float(max_points),
                            due_date=str(due_date))
                        _flash("Inserted", f"assignment {new_id}")
                    except db.DatabaseError as exc:
                        flash("error", str(exc))

        with tab_delete:
            frame = crud.list_assignments()
            target = _pick(frame.assign(
                label=frame["course_code"] + " — " + frame["title"]),
                "assignment_id", "label", "Assignment to delete")
            if target and st.button("Delete permanently", type="primary"):
                try:
                    crud.delete_assignment(target)
                    _flash("Deleted", str(target))
                except db.DatabaseError as exc:
                    flash("error", str(exc))

    with tab_submissions:
        _show_table(db.query_df(
            """
            SELECT sm.submission_id, cs.course_code, a.title,
                   s.student_no,
                   u.first_name || ' ' || u.last_name AS student_name,
                   sm.submitted_at, sm.score, sm.is_late,
                   ROUND(100.0 * sm.score / a.max_points, 1) AS pct
              FROM submissions sm
              JOIN assignments a     ON a.assignment_id = sm.assignment_id
              JOIN course_sections cs ON cs.section_id = a.section_id
              JOIN students s        ON s.student_id = sm.student_id
              JOIN users u           ON u.user_id = s.user_id
             ORDER BY cs.course_code, a.title, s.student_no
             LIMIT 300
            """
        ), 420)

    with tab_grade:
        frame = db.query_df(
            """
            SELECT sm.submission_id, cs.course_code, a.title,
                   s.student_no,
                   u.first_name || ' ' || u.last_name AS student_name,
                   sm.score, a.max_points
              FROM submissions sm
              JOIN assignments a     ON a.assignment_id = sm.assignment_id
              JOIN course_sections cs ON cs.section_id = a.section_id
              JOIN students s        ON s.student_id = sm.student_id
              JOIN users u           ON u.user_id = s.user_id
             WHERE sm.score IS NULL
             ORDER BY cs.course_code, s.student_no
             LIMIT 200
            """
        )
        st.caption(f"{len(frame)} submissions awaiting a mark")
        target = _pick(
            frame.assign(label=frame["student_name"] + " — "
                         + frame["title"]),
            "submission_id", "label", "Submission")
        if target:
            current = frame[frame.submission_id == target].iloc[0]
            with st.form("submission_grade"):
                score = st.number_input(
                    f"Score (out of {current['max_points']})",
                    0.0, float(current["max_points"]), 0.0, step=0.5)
                feedback = st.text_area("Feedback")
                submitted = st.form_submit_button("Save mark",
                                                  type="primary")

            if submitted:
                try:
                    crud.grade_submission(target, float(score), feedback,
                                          actor=user.username)
                    _flash("Saved", f"score {score}")
                except db.DatabaseError as exc:
                    flash("error", str(exc))


# ---------------------------------------------------------------------------
# 7. Attendance
# ---------------------------------------------------------------------------

def attendance_crud(user: auth.User) -> None:
    st.markdown("## 🗓️ Attendance")
    tab_view, tab_mark, tab_delete = st.tabs(["View", "Record", "Delete"])

    with tab_view:
        sections = crud.list_sections()
        section_id = _pick(sections, "section_id", "course_name",
                           "Section (optional)")
        frame = crud.list_attendance(
            section_id=None if section_id is None else int(section_id))
        _show_table(frame, 420)

    with tab_mark:
        st.caption("Saving the same session twice corrects the earlier "
                   "value rather than creating a duplicate row.")
        with st.form("attendance_form"):
            section_id = _pick(crud.list_sections(), "section_id",
                               "course_name", "Section")
            student_id = _pick(crud.list_students(), "student_id",
                               "student_name", "Student")
            session_date = st.date_input("Session date")
            status = st.selectbox("Status",
                                  ["present", "absent", "late", "excused"])
            submitted = st.form_submit_button("Save attendance",
                                              type="primary")

        if submitted:
            if section_id is None or student_id is None:
                flash("error", "Choose both a section and a student.")
            else:
                try:
                    crud.record_attendance(
                        section_id=section_id, student_id=student_id,
                        session_date=str(session_date), status=status)
                    _flash("Saved",
                           f"{student_id} marked '{status}' on {session_date}")
                except db.DatabaseError as exc:
                    flash("error", str(exc))

    with tab_delete:
        frame = crud.list_attendance()
        target = _pick(
            frame.assign(label=frame["student_name"] + " — "
                         + frame["course_code"] + " " + frame["session_date"]),
            "attendance_id", "label", "Attendance record")
        if target and st.button("Delete permanently", type="primary"):
            try:
                crud.delete_attendance(target)
                _flash("Deleted", str(target))
            except db.DatabaseError as exc:
                flash("error", str(exc))


# ---------------------------------------------------------------------------
# 8. Reports
# ---------------------------------------------------------------------------

def reports_screen(user: auth.User) -> None:
    st.markdown("## 📈 Management Reports")
    st.caption("Eight analytical queries run against the live database "
               "through the same connection the rest of the app uses.")

    tab_reports, tab_sql, tab_plpgsql = st.tabs(
        ["Results", "SQL source", "PL/pgSQL output"]
    )

    with tab_reports:
        chosen = st.selectbox(
            "Report", [r["key"] for r in reports.REPORTS],
            format_func=lambda k: next(
                r["title"] for r in reports.REPORTS if r["key"] == k),
        )
        report = next(r for r in reports.REPORTS if r["key"] == chosen)

        st.info(f"**{report['question']}**")
        st.caption(f"SQL features: {report['features']}")

        frame = reports.run(chosen)
        st.caption(f"{len(frame)} rows")
        _show_table(frame, 460)

        if not frame.empty and len(frame.columns) > 1:
            numeric = frame.select_dtypes("number")
            if not numeric.empty:
                st.markdown("**Summary statistics**")
                st.dataframe(numeric.describe().T, width='stretch')

        st.download_button(
            "Download as CSV", frame.to_csv(index=False),
            file_name=f"{chosen}.csv", mime="text/csv",
        )

    with tab_sql:
        chosen = st.selectbox(
            "Report", [r["key"] for r in reports.REPORTS],
            format_func=lambda k: next(
                r["title"] for r in reports.REPORTS if r["key"] == k),
            key="sql_picker",
        )
        st.code(reports.sql_by_key(chosen), language="sql")

    with tab_plpgsql:
        st.markdown("### Procedural blocks, executed live")
        st.caption("Each row below is this application calling a "
                   "PL/pgSQL function or procedure and showing what came "
                   "back. The two refusals are the procedure's own "
                   "RAISE EXCEPTION messages.")
        _show_table(reports.demo_procedures(), 300)

        st.markdown("### Grade audit trail")
        _show_table(db.query_df(
            """
            SELECT ga.changed_at, ga.changed_by, s.student_no,
                   cs.course_code, ga.old_grade, ga.new_grade,
                   ga.old_letter, ga.new_letter
              FROM grade_audit ga
              JOIN students s         ON s.student_id = ga.student_id
              JOIN course_sections cs ON cs.section_id = ga.section_id
             ORDER BY ga.audit_id DESC LIMIT 25
            """
        ), 320)


# ---------------------------------------------------------------------------
# 9. User administration
# ---------------------------------------------------------------------------

def admin_users(user: auth.User) -> None:
    if not auth.require_role(user, "admin"):
        st.error("This screen requires the administrator role.")
        return

    st.markdown("## 🔐 User Administration")
    tab_users, tab_create, tab_audit = st.tabs(
        ["Accounts", "Create", "Grade audit"]
    )

    with tab_users:
        frame = crud.list_users()
        target = _pick(frame.assign(label=frame["full_name"] + " ("
                                   + frame["username"] + ")"),
                        "user_id", "label", "Account")

        if target:
            row = frame[frame.user_id == target].iloc[0]
            st.write(f"**{row['full_name']}** · `{row['username']}` · "
                     f"{row['email']}")
            st.caption(f"Status: **{row['status']}** · Roles: {row['roles']}")

            new_status = st.selectbox(
                "Status",
                ["active", "suspended", "pending"],
                index=["active", "suspended", "pending"].index(row["status"]),
            )
            if st.button("Apply status"):
                try:
                    crud.set_user_status(target, new_status,
                                         actor=user.username)
                    _flash("Updated",
                           f"{row['username']} is now '{new_status}'")
                except db.DatabaseError as exc:
                    flash("error", str(exc))

            st.markdown("---")
            available = [r for r in auth.visible_role_names(user)
                         if r not in str(row["roles"]).split(", ")]
            if available:
                role = st.selectbox("Grant role", available)
                if st.button("Grant"):
                    try:
                        crud.grant_role(target, role, actor=user.username)
                        _flash("Granted", role)
                    except db.DatabaseError as exc:
                        flash("error", str(exc))

            held = [r for r in str(row["roles"]).split(", ")
                    if r and r != "(no roles)"]
            if held:
                role = st.selectbox("Revoke role", held)
                if st.button("Revoke"):
                    try:
                        crud.revoke_role(target, role, actor=user.username)
                        _flash("Revoked", role)
                    except db.DatabaseError as exc:
                        flash("error", str(exc))

        st.markdown("---")
        st.markdown("### All accounts")
        _show_table(frame, 320)

    with tab_create:
        with st.form("user_create", clear_on_submit=True):
            username = st.text_input("Username")
            email = st.text_input("Email")
            first = st.text_input("First name")
            last = st.text_input("Last name")
            password = st.text_input("Password", type="password")
            confirm = st.text_input("Confirm password", type="password")
            role = st.selectbox("Role", auth.visible_role_names(user))
            submitted = st.form_submit_button("Create active account",
                                              type="primary")

        if submitted:
            problems = auth.password_problems(password, confirm)
            if problems:
                flash("error", " ".join(problems))
            else:
                try:
                    new_id = crud.create_user(
                        username=username, email=email, first_name=first,
                        last_name=last, password=password, role_name=role,
                        actor=user.username)
                    _flash("Created",
                           f"{username} (user_id {new_id}) with role {role}")
                except db.DatabaseError as exc:
                    flash("error", str(exc))

    with tab_audit:
        st.caption("Every grade change, who made it, and what it changed. "
                   "The audit rows carry no foreign keys on purpose, so "
                   "they survive deletion of the enrolment they describe.")
        _show_table(db.query_df(
            """
            SELECT ga.audit_id, ga.changed_at, ga.changed_by,
                   s.student_no,
                   u.first_name || ' ' || u.last_name AS student_name,
                   cs.course_code,
                   ga.old_grade, ga.new_grade,
                   ga.old_letter, ga.new_letter
              FROM grade_audit ga
              JOIN students s         ON s.student_id = ga.student_id
              JOIN users u            ON u.user_id = s.user_id
              JOIN course_sections cs ON cs.section_id = ga.section_id
             ORDER BY ga.audit_id DESC LIMIT 100
            """
        ), 420)
