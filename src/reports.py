"""
The eight analytical queries.

The SQL below is character-for-character the SQL in
``supabase/migrations/0004_queries.sql``. Keeping one copy means the
output in the report is the output the running application produces,
rather than a hand-copied sample that has drifted from the code.
"""

from __future__ import annotations

import pandas as pd

from src import db

# ---------------------------------------------------------------------------
# Q1 — Enrolment pressure per department
# ---------------------------------------------------------------------------
Q1_ENROLMENT_PRESSURE = """
SELECT
    d.dept_name,
    COUNT(DISTINCT s.student_id)                AS students,
    COUNT(e.enrollment_id)                      AS enrolments,
    ROUND(AVG(cs.capacity), 1)                  AS avg_capacity,
    ROUND(100.0 * SUM(cs.enrolled_count)
          / SUM(cs.capacity), 1)                AS seat_utilisation_pct
FROM departments d
JOIN students s          ON s.department_id = d.department_id
JOIN enrollments e       ON e.student_id     = s.student_id
                        AND e.status <> 'dropped'
JOIN course_sections cs  ON cs.section_id   = e.section_id
                        AND cs.course_code LIKE d.dept_code || '%'
GROUP BY d.dept_code, d.dept_name
HAVING COUNT(e.enrollment_id) > 0
ORDER BY seat_utilisation_pct DESC
"""

# ---------------------------------------------------------------------------
# Q2 — Course difficulty
# ---------------------------------------------------------------------------
Q2_COURSE_DIFFICULTY = """
SELECT
    cs.course_code,
    cs.course_name,
    COUNT(e.final_grade)                          AS graded,
    ROUND(AVG(e.final_grade), 2)                  AS avg_mark,
    ROUND(MIN(e.final_grade), 2)                  AS min_mark,
    ROUND(MAX(e.final_grade), 2)                  AS max_mark,
    ROUND(STDDEV_SAMP(e.final_grade), 2)          AS spread,
    ROUND(AVG(e.final_grade)
          - (SELECT AVG(final_grade) FROM enrollments
              WHERE final_grade IS NOT NULL), 2)  AS vs_school_avg
FROM course_sections cs
JOIN enrollments e ON e.section_id = cs.section_id
WHERE e.final_grade IS NOT NULL
GROUP BY cs.course_code, cs.course_name
ORDER BY avg_mark ASC
"""

# ---------------------------------------------------------------------------
# Q3 — Standing: top students by GPA
# ---------------------------------------------------------------------------
Q3_TOP_STUDENTS = """
WITH student_results AS (
    SELECT
        s.student_id,
        s.student_no,
        d.dept_name,
        COUNT(e.enrollment_id)                          AS courses_taken,
        SUM(cs.credits)                                AS credit_hours,
        ROUND(AVG(e.final_grade), 2)                   AS avg_mark,
        ROUND(SUM(e.final_grade * cs.credits)
              / NULLIF(SUM(cs.credits), 0) / 25.0, 2)  AS computed_gpa
    FROM students s
    JOIN departments d     ON d.department_id = s.department_id
    JOIN enrollments e    ON e.student_id     = s.student_id
                         AND e.status IN ('completed', 'failed')
    JOIN course_sections cs ON cs.section_id = e.section_id
    WHERE e.final_grade IS NOT NULL
    GROUP BY s.student_id, s.student_no, d.dept_name
),
ranked AS (
    SELECT sr.*,
           RANK() OVER (ORDER BY sr.computed_gpa DESC)  AS position,
           RANK() OVER (PARTITION BY sr.dept_name
                        ORDER BY sr.computed_gpa DESC)  AS dept_position
    FROM student_results sr
)
SELECT
    r.position, r.dept_position, r.student_no, r.dept_name,
    r.courses_taken, r.credit_hours, r.avg_mark, r.computed_gpa,
    s.gpa AS stored_gpa,
    (SELECT u.first_name || ' ' || u.last_name
       FROM users u WHERE u.user_id = s.user_id) AS student_name
FROM ranked r
JOIN students s ON s.student_id = r.student_id
WHERE r.position <= 10
ORDER BY r.position
"""

# ---------------------------------------------------------------------------
# Q4 — Teaching load per instructor
# ---------------------------------------------------------------------------
Q4_TEACHING_LOAD = """
SELECT
    u.first_name || ' ' || u.last_name    AS instructor,
    d.dept_name,
    i.rank_title,
    COUNT(cs.section_id)                          AS sections,
    COALESCE(SUM(cs.enrolled_count), 0)           AS students_taught,
    ROUND(COALESCE(AVG(cs.enrolled_count), 0), 1) AS avg_class_size,
    ROUND(100.0 * COALESCE(SUM(cs.enrolled_count), 0)
          / NULLIF(SUM(cs.capacity), 0), 1)       AS utilisation_pct
FROM instructors i
JOIN users u       ON u.user_id = i.user_id
JOIN departments d ON d.department_id = i.department_id
LEFT JOIN course_sections cs ON cs.instructor_id = i.instructor_id
GROUP BY u.user_id, u.first_name, u.last_name, d.dept_name, i.rank_title
ORDER BY students_taught DESC, instructor
"""

# ---------------------------------------------------------------------------
# Q5 — Grade distribution
# ---------------------------------------------------------------------------
Q5_GRADE_DISTRIBUTION = """
WITH bucketed AS (
    SELECT
        CASE
            WHEN e.final_grade >= 93 THEN 'A  (93-100) excellent'
            WHEN e.final_grade >= 87 THEN 'B  (87-92)  good'
            WHEN e.final_grade >= 80 THEN 'C  (80-86)  satisfactory'
            WHEN e.final_grade >= 70 THEN 'D  (70-79)  pass'
            WHEN e.final_grade >= 50 THEN 'E  (50-69)  weak'
            ELSE                      'F  (0-49)   fail'
        END AS band,
        e.final_grade
    FROM enrollments e
    WHERE e.final_grade IS NOT NULL
)
SELECT
    band,
    COUNT(*)                                            AS records,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)  AS pct_of_cohort,
    ROUND(AVG(final_grade), 1)                          AS avg_in_band
FROM bucketed
GROUP BY band
ORDER BY band
"""

# ---------------------------------------------------------------------------
# Q6 — Departments above the school average
# ---------------------------------------------------------------------------
Q6_ABOVE_AVERAGE = """
SELECT
    d.dept_name,
    COUNT(DISTINCT s.student_id)        AS students,
    ROUND(AVG(e.final_grade), 2)        AS dept_avg,
    (SELECT ROUND(AVG(e2.final_grade), 2)
       FROM enrollments e2
      WHERE e2.final_grade IS NOT NULL) AS school_avg,
    ROUND(AVG(e.final_grade)
          - (SELECT AVG(e3.final_grade)
               FROM enrollments e3
              WHERE e3.final_grade IS NOT NULL), 2) AS variance
FROM departments d
JOIN students s    ON s.department_id = d.department_id
JOIN enrollments e ON e.student_id     = s.student_id
                   AND e.final_grade IS NOT NULL
GROUP BY d.dept_code, d.dept_name
HAVING AVG(e.final_grade) > (SELECT AVG(final_grade)
                               FROM enrollments
                              WHERE final_grade IS NOT NULL)
ORDER BY dept_avg DESC
"""

# ---------------------------------------------------------------------------
# Q7 — Assignment submission rates
# ---------------------------------------------------------------------------
Q7_SUBMISSION_RATES = """
SELECT
    cs.course_code,
    a.title                              AS assignment,
    a.due_date,
    a.max_points,
    COUNT(sm.submission_id)                        AS submitted,
    COUNT(e.student_id)                            AS enrolled,
    ROUND(100.0 * COUNT(sm.submission_id)
          / NULLIF(COUNT(e.student_id), 0), 1)     AS submission_rate_pct,
    COUNT(*) FILTER (WHERE sm.is_late)             AS late,
    ROUND(AVG(sm.score), 1)                        AS avg_score,
    ROUND(100.0 * AVG(sm.score) / a.max_points, 1) AS avg_score_pct
FROM assignments a
JOIN course_sections cs ON cs.section_id = a.section_id
LEFT JOIN enrollments e  ON e.section_id = a.section_id
                        AND e.status <> 'dropped'
LEFT JOIN submissions sm ON sm.assignment_id = a.assignment_id
                        AND sm.student_id = e.student_id
GROUP BY cs.course_code, a.assignment_id, a.title, a.due_date, a.max_points
ORDER BY submission_rate_pct ASC, a.due_date
"""

# ---------------------------------------------------------------------------
# Q8 — Attendance risk
# ---------------------------------------------------------------------------
Q8_ATTENDANCE_RISK = """
SELECT
    s.student_no,
    u.first_name || ' ' || u.last_name AS student_name,
    d.dept_name,
    COUNT(a.attendance_id)                        AS sessions,
    COUNT(*) FILTER (WHERE a.status = 'absent')   AS absences,
    COUNT(*) FILTER (WHERE a.status = 'late')     AS late,
    ROUND(100.0 * COUNT(*) FILTER (WHERE a.status = 'absent')
          / NULLIF(COUNT(a.attendance_id), 0), 1) AS absence_rate_pct
FROM students s
JOIN users u       ON u.user_id = s.user_id
JOIN departments d ON d.department_id = s.department_id
JOIN enrollments e ON e.student_id = s.student_id
JOIN attendance a  ON a.student_id = e.student_id
                 AND a.section_id = e.section_id
GROUP BY s.student_id, s.student_no, u.first_name, u.last_name, d.dept_name
HAVING COUNT(*) FILTER (WHERE a.status = 'absent') >= 2
ORDER BY absence_rate_pct DESC, s.student_no
"""


# Each entry is (key, title, SQL, what the management question is,
# which SQL features it demonstrates).
REPORTS: list[dict] = [
    {
        "key": "q1",
        "title": "1. Enrolment pressure per department",
        "sql": Q1_ENROLMENT_PRESSURE,
        "question": "Which departments are filling their classrooms up, "
                    "and which have spare capacity?",
        "features": "4-table JOIN, GROUP BY, HAVING, correlated subquery, "
                    "percentage arithmetic",
    },
    {
        "key": "q2",
        "title": "2. Course difficulty",
        "sql": Q2_COURSE_DIFFICULTY,
        "question": "Do particular courses have intrinsically low marks, "
                    "or is one cohort simply weaker?",
        "features": "JOIN, GROUP BY, AVG, MIN, MAX, COUNT, STDDEV_SAMP, "
                    "uncorrelated subquery",
    },
    {
        "key": "q3",
        "title": "3. Student standing — top 10 by GPA",
        "sql": Q3_TOP_STUDENTS,
        "question": "Who is performing best, both overall and within "
                    "their own department?",
        "features": "CTE, window function RANK with PARTITION BY, "
                    "derived table, correlated subquery",
    },
    {
        "key": "q4",
        "title": "4. Teaching load per instructor",
        "sql": Q4_TEACHING_LOAD,
        "question": "How many sections and students is each member of "
                    "staff carrying?",
        "features": "LEFT JOIN, GROUP BY, COUNT, SUM, AVG, "
                    "COALESCE, NULLIF guard",
    },
    {
        "key": "q5",
        "title": "5. Grade distribution",
        "sql": Q5_GRADE_DISTRIBUTION,
        "question": "How is the cohort spread across grade bands?",
        "features": "CASE expression, CTE, window function SUM() OVER, "
                    "GROUP BY on an expression",
    },
    {
        "key": "q6",
        "title": "6. Departments beating the school average",
        "sql": Q6_ABOVE_AVERAGE,
        "question": "Which departments outperform the institution as a "
                    "whole?",
        "features": "subquery inside HAVING, GROUP BY, aggregate "
                    "comparison, derived variance",
    },
    {
        "key": "q7",
        "title": "7. Assignment submission rates",
        "sql": Q7_SUBMISSION_RATES,
        "question": "Which assessments are being handed in, and how "
                    "much of it is late?",
        "features": "LEFT JOIN, FILTER clause, COUNT vs COUNT(DISTINCT), "
                    "NULLIF division guard",
    },
    {
        "key": "q8",
        "title": "8. Attendance risk",
        "sql": Q8_ATTENDANCE_RISK,
        "question": "Which students have missed enough classes to risk "
                    "failing on attendance?",
        "features": "5-table JOIN, HAVING on an aggregate, FILTER, "
                    "grouped result filtered to individuals",
    },
]


def run(key: str) -> pd.DataFrame:
    """Execute one report by its key and return the result."""
    for report in REPORTS:
        if report["key"] == key:
            return db.query_df(report["sql"])
    raise KeyError(f"Unknown report {key!r}")


def sql_by_key(key: str) -> str:
    for report in REPORTS:
        if report["key"] == key:
            return report["sql"]
    raise KeyError(f"Unknown report {key!r}")


# ---------------------------------------------------------------------------
# PL/pgSQL demonstration calls, run from the reports screen so the
# procedural blocks' output is visible in the GUI as well as in psql.
# ---------------------------------------------------------------------------

def demo_procedures() -> pd.DataFrame:
    """Exercise each PL/pgSQL block and tabulate what came back.

    Read-only apart from the GPA recomputation, which writes the same
    value it reads.
    """
    rows: list[dict] = []

    rows.append({
        "block": "letter_grade_for(93)",
        "type": "FUNCTION",
        "returns": db.query_scalar("SELECT letter_grade_for(93)"),
        "note": "93 is the AA threshold",
    })
    rows.append({
        "block": "letter_grade_for(69.99)",
        "type": "FUNCTION",
        "returns": db.query_scalar("SELECT letter_grade_for(69.99)"),
        "note": "just below the DD cut-off, so FF",
    })
    rows.append({
        "block": "section_fill_ratio(1)",
        "type": "FUNCTION",
        "returns": db.query_scalar("SELECT section_fill_ratio(1)"),
        "note": "seats taken as a percentage of capacity",
    })
    rows.append({
        "block": "calculate_student_gpa(1)",
        "type": "FUNCTION",
        "returns": db.query_scalar("SELECT calculate_student_gpa(1)"),
        "note": "credit-weighted, persisted to students.gpa",
    })
    rows.append({
        "block": "get_section_roster(1)",
        "type": "FUNCTION",
        "returns": (
            len(db.query_df("SELECT * FROM get_section_roster(1)"))
        ),
        "note": "row count returned by the roster function",
    })
    rows.append({
        "block": "enroll_student(999999, 1)",
        "type": "PROCEDURE",
        "returns": _capture_error("CALL enroll_student(999999, 1)"),
        "note": "expected to refuse an unknown student",
    })
    rows.append({
        "block": "enroll_student(1, 1)",
        "type": "PROCEDURE",
        "returns": _capture_error("CALL enroll_student(1, 1)"),
        "note": "expected to refuse a duplicate enrolment",
    })

    return pd.DataFrame(rows)


def _capture_error(sql: str) -> str:
    """Run a statement expected to fail and return its message.

    The failure is the point, so the error text is the result rather
    than an exception.
    """
    try:
        db.execute(sql)
        return "no error raised (unexpected)"
    except db.DatabaseError as exc:
        return str(exc).split(" (SQLSTATE")[0]