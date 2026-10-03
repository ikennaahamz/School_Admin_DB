-- =============================================================
-- 0004_queries.sql  —  analytical queries for management
-- CMPE344 Database Management Systems and Programming II
--
-- The brief asks for at least 5–7 queries that retrieve and
-- display statistical information using joins, subqueries,
-- GROUP BY, ORDER BY, SUM, AVG and COUNT. Each one below is
-- also wired into the Streamlit reports screen, so the same SQL
-- that is graded on paper is the SQL the app actually runs.
--
-- Run with:  psql -d school_validation -f supabase/migrations/0004_queries.sql
-- =============================================================

\pset border 2

-- =============================================================
-- QUERY 1 — Enrolment pressure per department
-- Which departments are filling up, and which are not?
-- Demonstrates: 4-table JOIN, GROUP BY, HAVING, ORDER BY,
--                a correlated subquery, percentage arithmetic.
-- =============================================================
SELECT
    d.dept_name,
    COUNT(DISTINCT s.student_id)                     AS students,
    COUNT(e.enrollment_id)                           AS enrolments,
    ROUND(AVG(cs.capacity), 1)                       AS avg_capacity,
    ROUND(100.0 * SUM(cs.enrolled_count) / SUM(cs.capacity), 1)
                                                     AS seat_utilisation_pct
FROM departments d
JOIN students s        ON s.department_id = d.department_id
JOIN enrollments e     ON e.student_id     = s.student_id
                     AND e.status <> 'dropped'
JOIN course_sections cs ON cs.section_id   = e.section_id
                     AND cs.course_code LIKE d.dept_code || '%'
GROUP BY d.dept_code, d.dept_name
HAVING COUNT(e.enrollment_id) > 0
ORDER BY seat_utilisation_pct DESC;

-- =============================================================
-- QUERY 2 — Course difficulty
-- Does a course have intrinsically low marks, or is one
-- instructor's cohort simply weaker? Reports the average, the
-- spread, and the count of graded records.
-- Demonstrates: JOIN, GROUP BY, AVG, MIN, MAX, COUNT,
--                STDDEV, and a subquery for the overall average.
-- =============================================================
SELECT
    cs.course_code,
    cs.course_name,
    COUNT(e.final_grade)                        AS graded,
    ROUND(AVG(e.final_grade), 2)                AS avg_mark,
    ROUND(MIN(e.final_grade), 2)                AS min_mark,
    ROUND(MAX(e.final_grade), 2)                AS max_mark,
    ROUND(STDDEV_SAMP(e.final_grade), 2)        AS spread,
    ROUND(AVG(e.final_grade)
          - (SELECT AVG(final_grade) FROM enrollments
              WHERE final_grade IS NOT NULL), 2) AS vs_school_avg
FROM course_sections cs
JOIN enrollments e ON e.section_id = cs.section_id
WHERE e.final_grade IS NOT NULL
GROUP BY cs.course_code, cs.course_name
ORDER BY avg_mark ASC;

-- =============================================================
-- QUERY 3 — Standing: top students by GPA
-- A CTE computes the weighted GPA, a window function ranks it,
-- and a subquery keeps only the top performers.
-- Demonstrates: CTE, window function RANK, derived table,
--                ROUND, correlated subquery, LIMIT.
-- =============================================================
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
    JOIN course_sections cs ON cs.section_id  = e.section_id
    WHERE e.final_grade IS NOT NULL
    GROUP BY s.student_id, s.student_no, d.dept_name
),
ranked AS (
    SELECT sr.*,
           RANK() OVER (ORDER BY sr.computed_gpa DESC)       AS position,
           RANK() OVER (PARTITION BY sr.dept_name
                        ORDER BY sr.computed_gpa DESC)       AS dept_position
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
ORDER BY r.position;

-- =============================================================
-- QUERY 4 — Teaching load per instructor
-- Who is carrying what, and how full are their sections?
-- Demonstrates: LEFT JOIN (so unassigned sections still appear),
--                GROUP BY, COUNT, SUM, AVG, ROUND.
-- =============================================================
SELECT
    u.first_name || ' ' || u.last_name  AS instructor,
    d.dept_name,
    i.rank_title,
    COUNT(cs.section_id)                        AS sections,
    COALESCE(SUM(cs.enrolled_count), 0)         AS students_taught,
    ROUND(COALESCE(AVG(cs.enrolled_count), 0), 1) AS avg_class_size,
    ROUND(100.0 * COALESCE(SUM(cs.enrolled_count), 0)
          / NULLIF(SUM(cs.capacity), 0), 1)     AS utilisation_pct
FROM instructors i
JOIN users u        ON u.user_id = i.user_id
JOIN departments d  ON d.department_id = i.department_id
LEFT JOIN course_sections cs ON cs.instructor_id = i.instructor_id
GROUP BY u.user_id, u.first_name, u.last_name, d.dept_name, i.rank_title
ORDER BY students_taught DESC, instructor;

-- =============================================================
-- QUERY 5 — Grade distribution, and how it compares to policy
-- A CASE expression buckets marks into letter bands, compared
-- against the school's own thresholds.
-- Demonstrates: CASE expression, GROUP BY on the expression,
--                a CTE, and a window function for percentages.
-- =============================================================
WITH bucketed AS (
    SELECT
        CASE
            WHEN e.final_grade >= 93 THEN 'A  (90-100) excellent'
            WHEN e.final_grade >= 87 THEN 'B  (80-89)  good'
            WHEN e.final_grade >= 80 THEN 'C  (70-79)  satisfactory'
            WHEN e.final_grade >= 70 THEN 'D  (60-69)  weak'
            WHEN e.final_grade >= 50 THEN 'E  (50-59)  fail'
            ELSE                      'F  (0-49)   fail'
        END AS band,
        e.final_grade
    FROM enrollments e
    WHERE e.final_grade IS NOT NULL
),
totalled AS (
    SELECT band, final_grade FROM bucketed
)
SELECT
    band,
    COUNT(*)                                          AS students,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_cohort,
    ROUND(AVG(final_grade), 1)                        AS avg_in_band
FROM totalled
GROUP BY band
ORDER BY band;

-- =============================================================
-- QUERY 6 — Departments above the average
-- A self-contained subquery computes the school-wide average,
-- then HAVING keeps only the departments that beat it.
-- Demonstrates: uncorrelated subquery in HAVING, GROUP BY,
--                HAVING, comparison against a derived value.
-- =============================================================
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
JOIN students s     ON s.department_id = d.department_id
JOIN enrollments e  ON e.student_id     = s.student_id
                    AND e.final_grade IS NOT NULL
GROUP BY d.dept_code, d.dept_name
HAVING AVG(e.final_grade) > (SELECT AVG(final_grade)
                               FROM enrollments
                              WHERE final_grade IS NOT NULL)
ORDER BY dept_avg DESC;

-- =============================================================
-- QUERY 7 — Assignment submission rates
-- Which assessments are being turned in, and how much late?
-- A LEFT JOIN keeps assignments nobody submitted anything for,
-- which an INNER JOIN would silently hide.
-- Demonstrates: LEFT JOIN, conditional aggregation,
--                ROUND, and a division guarded with NULLIF.
-- =============================================================
SELECT
    cs.course_code,
    a.title                                  AS assignment,
    a.due_date,
    a.max_points,
    COUNT(sm.submission_id)                            AS submitted,
    COUNT(e.student_id)                                AS enrolled,
    ROUND(100.0 * COUNT(sm.submission_id)
          / NULLIF(COUNT(e.student_id), 0), 1)         AS submission_rate_pct,
    COUNT(*) FILTER (WHERE sm.is_late)                AS late,
    ROUND(AVG(sm.score), 1)                           AS avg_score,
    ROUND(100.0 * AVG(sm.score) / a.max_points, 1)    AS avg_score_pct
FROM assignments a
JOIN course_sections cs ON cs.section_id = a.section_id
LEFT JOIN enrollments e  ON e.section_id = a.section_id
                        AND e.status <> 'dropped'
LEFT JOIN submissions sm ON sm.assignment_id = a.assignment_id
                        AND sm.student_id = e.student_id
GROUP BY cs.course_code, a.assignment_id, a.title, a.due_date, a.max_points
ORDER BY submission_rate_pct ASC, a.due_date;

-- =============================================================
-- BONUS 8 — Attendance risk
-- Students whose attendance has fallen far enough that
-- repeating or exclusion should be considered.
-- Demonstrates: LEFT JOIN + HAVING on an aggregate, and
--                filtering a grouped result back to individuals.
-- =============================================================
SELECT
    s.student_no,
    u.first_name || ' ' || u.last_name AS student_name,
    d.dept_name,
    COUNT(a.attendance_id)                             AS sessions,
    COUNT(*) FILTER (WHERE a.status = 'absent')        AS absences,
    COUNT(*) FILTER (WHERE a.status = 'late')          AS late,
    ROUND(100.0 * COUNT(*) FILTER (WHERE a.status = 'absent')
          / NULLIF(COUNT(a.attendance_id), 0), 1)     AS absence_rate_pct
FROM students s
JOIN users u       ON u.user_id = s.user_id
JOIN departments d ON d.department_id = s.department_id
JOIN enrollments e ON e.student_id = s.student_id
JOIN attendance a  ON a.student_id = e.student_id
                 AND a.section_id = e.section_id
GROUP BY s.student_id, s.student_no, u.first_name, u.last_name, d.dept_name
HAVING COUNT(*) FILTER (WHERE a.status = 'absent') >= 2
ORDER BY absence_rate_pct DESC, s.student_no;

\echo ''
\echo '--- 8 analytical queries completed ---'