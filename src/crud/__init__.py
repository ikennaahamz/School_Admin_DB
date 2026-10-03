"""
CRUD helpers, one module per entity group.

Each function is a thin, honest wrapper around one SQL statement. The
deliberate constraint is that these functions contain no business
rules: capacity, credit limits, grade derivation and audit all live
in PL/pgSQL in the database, where they apply no matter which client
writes the row. Python checks that a form was filled in; the database
decides what is legal.
"""

from src import db

# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------

def list_students(department_id: int | None = None,
                  search: str = "") -> "pd.DataFrame":
    params: list = []
    where = ["TRUE"]

    if department_id:
        where.append("s.department_id = %s")
        params.append(department_id)
    if search:
        where.append(
            "(u.first_name ILIKE %s OR u.last_name ILIKE %s "
            "OR s.student_no ILIKE %s)"
        )
        params.extend([f"%{search}%"] * 3)

    return db.query_df(
        f"""
        SELECT s.student_id, s.student_no,
               u.first_name || ' ' || u.last_name AS student_name,
               u.email, d.dept_code, d.dept_name,
               s.program_level, s.enrollment_year,
               s.gpa,
               COALESCE(a.first_name || ' ' || a.last_name,
                        '(unassigned)') AS advisor,
               COUNT(e.enrollment_id) FILTER (WHERE e.status <> 'dropped')
                   AS courses_taken
          FROM students s
          JOIN users u        ON u.user_id = s.user_id
          JOIN departments d  ON d.department_id = s.department_id
          LEFT JOIN students adv ON adv.student_id = s.advisor_id
          LEFT JOIN users a    ON a.user_id = adv.user_id
          LEFT JOIN enrollments e ON e.student_id = s.student_id
         WHERE {' AND '.join(where)}
         GROUP BY s.student_id, s.student_no, u.first_name, u.last_name,
                  u.email, d.dept_code, d.dept_name, s.program_level,
                  s.enrollment_year, s.gpa, a.first_name, a.last_name
         ORDER BY s.student_no
        """,
        params,
    )


def insert_student(*, user_id: int, department_id: int, student_no: str,
                   program_level: str, enrollment_year: int,
                   advisor_id: int | None = None) -> int:
    """Create a student profile attached to an existing user account."""
    row = db.execute(
        """
        INSERT INTO students (user_id, department_id, student_no,
                              program_level, enrollment_year, advisor_id)
        VALUES (%s, %s, %s, %s::program_level, %s, %s)
        RETURNING student_id
        """,
        (user_id, department_id, student_no, program_level,
         enrollment_year, advisor_id),
        returning=True,
    )
    return row[0]


def update_student(student_id: int, *, department_id: int,
                   program_level: str, enrollment_year: int,
                   advisor_id: int | None) -> None:
    db.execute(
        """
        UPDATE students
           SET department_id = %s,
               program_level = %s::program_level,
               enrollment_year = %s,
               advisor_id = %s
         WHERE student_id = %s
        """,
        (department_id, program_level, enrollment_year, advisor_id, student_id),
    )


def delete_student(student_id: int) -> int:
    """Remove a student profile.

    The FK on students.user_id is ON DELETE CASCADE and the FK on
    enrollments.student_id is too, so deleting a profile cascades to
    the enrolment, submission and attendance rows that depend on it.
    The user account itself survives, so the login is not orphaned.
    """
    return db.execute(
        "DELETE FROM students WHERE student_id = %s", (student_id,)
    )


# ---------------------------------------------------------------------------
# Instructors
# ---------------------------------------------------------------------------

def list_instructors() -> "pd.DataFrame":
    return db.query_df(
        """
        SELECT i.instructor_id, i.employee_no,
               u.first_name || ' ' || u.last_name AS instructor_name,
               u.email, u.status, d.dept_name,
               i.rank_title, i.hire_date, i.salary,
               COUNT(cs.section_id) AS sections_taught
          FROM instructors i
          JOIN users u       ON u.user_id = i.user_id
          JOIN departments d ON d.department_id = i.department_id
          LEFT JOIN course_sections cs ON cs.instructor_id = i.instructor_id
         GROUP BY i.instructor_id, i.employee_no, u.first_name, u.last_name,
                  u.email, u.status, d.dept_name, i.rank_title,
                  i.hire_date, i.salary
         ORDER BY i.employee_no
        """
    )


def insert_instructor(*, user_id: int, department_id: int, employee_no: str,
                      hire_date: str, rank_title: str,
                      salary: float | None) -> int:
    row = db.execute(
        """
        INSERT INTO instructors (user_id, department_id, employee_no,
                                 hire_date, rank_title, salary)
        VALUES (%s, %s, %s, %s::date, %s, %s)
        RETURNING instructor_id
        """,
        (user_id, department_id, employee_no, hire_date, rank_title, salary),
        returning=True,
    )
    return row[0]


def update_instructor(instructor_id: int, *, department_id: int,
                      rank_title: str, salary: float | None) -> None:
    db.execute(
        """
        UPDATE instructors
           SET department_id = %s, rank_title = %s, salary = %s
         WHERE instructor_id = %s
        """,
        (department_id, rank_title, salary, instructor_id),
    )


def delete_instructor(instructor_id: int) -> int:
    """Delete a profile.

    course_sections.instructor_id is ON DELETE SET NULL, so the
    course sections survive and simply become unassigned. A hard
    delete would orphan real teaching records.
    """
    return db.execute(
        "DELETE FROM instructors WHERE instructor_id = %s", (instructor_id,)
    )


# ---------------------------------------------------------------------------
# Course sections
# ---------------------------------------------------------------------------

def list_sections(term: str | None = None,
                  academic_year: int | None = None) -> "pd.DataFrame":
    params: list = []
    where = ["TRUE"]
    if term:
        where.append("cs.term = %s::term_name")
        params.append(term)
    if academic_year:
        where.append("cs.academic_year = %s")
        params.append(academic_year)

    return db.query_df(
        f"""
        SELECT cs.section_id, cs.course_code, cs.course_name,
               cs.term, cs.academic_year, cs.credits,
               COALESCE(u.first_name || ' ' || u.last_name,
                        '(unassigned)') AS instructor,
               COALESCE(c.building || ' ' || c.room_number,
                        '(no room)')    AS room,
               cs.capacity, cs.enrolled_count,
               section_fill_ratio(cs.section_id) AS fill_pct
          FROM course_sections cs
          LEFT JOIN instructors i ON i.instructor_id = cs.instructor_id
          LEFT JOIN users u       ON u.user_id = i.user_id
          LEFT JOIN classrooms c   ON c.classroom_id = cs.classroom_id
         WHERE {' AND '.join(where)}
         ORDER BY cs.academic_year DESC, cs.term, cs.course_code
        """,
        params,
    )


def insert_section(*, course_code: str, course_name: str, term: str,
                   academic_year: int, instructor_id: int | None,
                   classroom_id: int | None, credits: int,
                   capacity: int) -> int:
    row = db.execute(
        """
        INSERT INTO course_sections (course_code, course_name, term,
                                     academic_year, instructor_id,
                                     classroom_id, credits, capacity)
        VALUES (%s, %s, %s::term_name, %s, %s, %s, %s, %s)
        RETURNING section_id
        """,
        (course_code.strip().upper(), course_name.strip(), term,
         academic_year, instructor_id, classroom_id, credits, capacity),
        returning=True,
    )
    return row[0]


def update_section(section_id: int, *, instructor_id: int | None,
                   classroom_id: int | None, capacity: int) -> None:
    db.execute(
        """
        UPDATE course_sections
           SET instructor_id = %s, classroom_id = %s, capacity = %s
         WHERE section_id = %s
        """,
        (instructor_id, classroom_id, capacity, section_id),
    )


def delete_section(section_id: int) -> int:
    return db.execute(
        "DELETE FROM course_sections WHERE section_id = %s", (section_id,)
    )


# ---------------------------------------------------------------------------
# Enrolments
# ---------------------------------------------------------------------------

def list_enrollments(status: str | None = None) -> "pd.DataFrame":
    params: list = []
    where = ["TRUE"]
    if status:
        where.append("e.status = %s::enroll_status")
        params.append(status)

    return db.query_df(
        f"""
        SELECT e.enrollment_id, s.student_no,
               su.first_name || ' ' || su.last_name AS student_name,
               cs.course_code, cs.course_name, cs.term, cs.academic_year,
               e.status, e.final_grade, e.grade_letter, e.enrolled_at
          FROM enrollments e
          JOIN students s        ON s.student_id = e.student_id
          JOIN users su          ON su.user_id = s.user_id
          JOIN course_sections cs ON cs.section_id = e.section_id
         WHERE {' AND '.join(where)}
         ORDER BY s.student_no, cs.course_code
        """,
        params,
    )


def enrol(student_id: int, section_id: int, actor: str) -> None:
    """Enrol via the PL/pgSQL procedure, not a bare INSERT.

    Calling ``enroll_student`` means the capacity rule, the duplicate
    check and the credit-load warning all apply. An INSERT here would
    bypass all three.
    """
    db.call_procedure("enroll_student", [student_id, section_id], actor=actor)


def unenrol(enrollment_id: int, actor: str) -> int:
    """Withdraw an enrolment by marking it dropped.

    Deliberately not a DELETE: the audit trail and the history of who
    took and dropped what are worth keeping. A status change keeps
    the row and lets the enrolled_count trigger decrement.
    """
    return db.execute(
        "UPDATE enrollments SET status = 'dropped', final_grade = NULL, "
        "grade_letter = NULL WHERE enrollment_id = %s",
        (enrollment_id,),
        actor=actor,
    )


def set_grade(enrollment_id: int, grade: float | None, actor: str) -> None:
    """Record a final grade.

    The BEFORE UPDATE trigger derives the letter, sets the status to
    completed or failed, writes the audit row and recomputes the
    student's GPA. Passing None withdraws the grade.
    """
    db.execute(
        "UPDATE enrollments SET final_grade = %s WHERE enrollment_id = %s",
        (grade, enrollment_id),
        actor=actor,
    )


# ---------------------------------------------------------------------------
# Assignments and submissions
# ---------------------------------------------------------------------------

def list_assignments(section_id: int | None = None) -> "pd.DataFrame":
    params: list = []
    where = ["TRUE"]
    if section_id:
        where.append("a.section_id = %s")
        params.append(section_id)

    return db.query_df(
        f"""
        SELECT a.assignment_id, cs.course_code, a.title, a.weight,
               a.max_points, a.due_date,
               COUNT(sm.submission_id) AS submitted,
               COUNT(e.student_id)     AS enrolled,
               ROUND(AVG(sm.score), 1) AS avg_score
          FROM assignments a
          JOIN course_sections cs ON cs.section_id = a.section_id
          LEFT JOIN enrollments e  ON e.section_id = a.section_id
                                 AND e.status <> 'dropped'
          LEFT JOIN submissions sm ON sm.assignment_id = a.assignment_id
                                 AND sm.student_id = e.student_id
         WHERE {' AND '.join(where)}
         GROUP BY a.assignment_id, cs.course_code, a.title, a.weight,
                  a.max_points, a.due_date
         ORDER BY cs.course_code, a.due_date
        """,
        params,
    )


def insert_assignment(*, section_id: int, title: str, weight: float,
                      max_points: float, due_date: str) -> int:
    row = db.execute(
        """
        INSERT INTO assignments (section_id, title, weight, max_points,
                                 due_date)
        VALUES (%s, %s, %s, %s, %s::date)
        RETURNING assignment_id
        """,
        (section_id, title, weight, max_points, due_date),
        returning=True,
    )
    return row[0]


def delete_assignment(assignment_id: int) -> int:
    return db.execute(
        "DELETE FROM assignments WHERE assignment_id = %s", (assignment_id,)
    )


def grade_submission(submission_id: int, score: float | None,
                     feedback: str, actor: str) -> None:
    db.execute(
        """
        UPDATE submissions
           SET score = %s, feedback = %s, graded_by = %s
         WHERE submission_id = %s
        """,
        (score, feedback, _grader_id(actor), submission_id),
        actor=actor,
    )


def _grader_id(username: str) -> int | None:
    """Map an actor name back to a users.user_id for graded_by."""
    return db.query_scalar(
        "SELECT user_id FROM users WHERE username = %s", (username,)
    )


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------

def list_attendance(section_id: int | None = None,
                    student_id: int | None = None) -> "pd.DataFrame":
    params: list = []
    where = ["TRUE"]
    if section_id:
        where.append("a.section_id = %s")
        params.append(section_id)
    if student_id:
        where.append("a.student_id = %s")
        params.append(student_id)

    return db.query_df(
        f"""
        SELECT a.attendance_id, cs.course_code,
               s.student_no,
               su.first_name || ' ' || su.last_name AS student_name,
               a.session_date, a.status
          FROM attendance a
          JOIN course_sections cs ON cs.section_id = a.section_id
          JOIN students s   ON s.student_id = a.student_id
          JOIN users su     ON su.user_id = s.user_id
         WHERE {' AND '.join(where)}
         ORDER BY cs.course_code, a.session_date, s.student_no
        """,
        params,
    )


def record_attendance(*, section_id: int, student_id: int,
                      session_date: str, status: str) -> None:
    """Upsert one attendance mark.

    ON CONFLICT against (section_id, student_id, session_date) makes
    the form idempotent: marking the same session twice corrects the
    earlier value instead of failing on the unique constraint.
    """
    db.execute(
        """
        INSERT INTO attendance (section_id, student_id, session_date, status)
        VALUES (%s, %s, %s::date, %s::attendance_status)
        ON CONFLICT (section_id, student_id, session_date)
        DO UPDATE SET status = EXCLUDED.status
        """,
        (section_id, student_id, session_date, status),
    )


def delete_attendance(attendance_id: int) -> int:
    return db.execute(
        "DELETE FROM attendance WHERE attendance_id = %s", (attendance_id,)
    )


# ---------------------------------------------------------------------------
# Admin: users and roles
# ---------------------------------------------------------------------------

def list_users() -> "pd.DataFrame":
    return db.query_df(
        """
        SELECT u.user_id, u.username, u.email,
               u.first_name || ' ' || u.last_name AS full_name,
               u.status, u.created_at,
               COALESCE(STRING_AGG(r.role_name, ', ' ORDER BY r.role_name),
                        '(no roles)') AS roles,
               COUNT(s.student_id)    AS is_student,
               COUNT(i.instructor_id) AS is_instructor
          FROM users u
          LEFT JOIN user_roles ur ON ur.user_id = u.user_id
          LEFT JOIN roles r       ON r.role_id = ur.role_id
          LEFT JOIN students s    ON s.user_id = u.user_id
          LEFT JOIN instructors i ON i.user_id = u.user_id
         GROUP BY u.user_id, u.username, u.email, u.first_name,
                  u.last_name, u.status, u.created_at
         ORDER BY u.username
        """
    )


def set_user_status(user_id: int, status: str, actor: str) -> None:
    """Activate, suspend or pend an account.

    Deleting a user is intentionally not offered: users are referenced
    by instructors, students, submissions and the grade audit trail.
    Suspension preserves the records.
    """
    db.execute(
        "UPDATE users SET status = %s::user_status WHERE user_id = %s",
        (status, user_id),
        actor=actor,
    )


def grant_role(user_id: int, role_name: str, actor: str) -> None:
    db.execute(
        """
        INSERT INTO user_roles (user_id, role_id)
        SELECT %s, role_id FROM roles WHERE role_name = %s
        ON CONFLICT (user_id, role_id) DO NOTHING
        """,
        (user_id, role_name),
        actor=actor,
    )


def revoke_role(user_id: int, role_name: str, actor: str) -> None:
    db.execute(
        """
        DELETE FROM user_roles
         WHERE user_id = %s
           AND role_id = (SELECT role_id FROM roles WHERE role_name = %s)
        """,
        (user_id, role_name),
        actor=actor,
    )


def create_user(*, username: str, email: str, first_name: str,
                last_name: str, password: str, role_name: str,
                actor: str) -> int:
    """Create an active user with one role.

    Used by the admin screen. Unlike :func:`auth.register` the account
    is active immediately, because an administrator has vouched for it.
    """
    from src.auth import hash_password

    row = db.execute(
        """
        INSERT INTO users (username, email, password_hash,
                           first_name, last_name, status)
        VALUES (%s, %s, %s, %s, %s, 'active')
        RETURNING user_id
        """,
        (username.strip().lower(), email.strip().lower(),
         hash_password(password), first_name.strip(), last_name.strip()),
        actor=actor,
        returning=True,
    )
    user_id = row[0]
    db.execute(
        """
        INSERT INTO user_roles (user_id, role_id)
        SELECT %s, role_id FROM roles WHERE role_name = %s
        """,
        (user_id, role_name),
        actor=actor,
    )
    return user_id