-- Captures real output from every analytical query and every
-- PL/pgSQL block, for embedding in docs/report.md. Nothing in the
-- report's output sections is hand-transcribed.
--
--   psql -d school_validation -f scripts/capture_output.sql

-- Plain ASCII borders: the Unicode line style renders as mojibake
-- when psql's output is redirected on a cp1252 Windows console.
\pset border 2
\pset linestyle ascii

\echo '### QUERY 1 - Enrolment pressure per department'
SELECT d.dept_name,
       COUNT(DISTINCT s.student_id) AS students,
       COUNT(e.enrollment_id)       AS enrolments,
       ROUND(AVG(cs.capacity), 1)   AS avg_capacity,
       ROUND(100.0 * SUM(cs.enrolled_count) / SUM(cs.capacity), 1)
                                   AS seat_utilisation_pct
  FROM departments d
  JOIN students s         ON s.department_id = d.department_id
  JOIN enrollments e      ON e.student_id = s.student_id
                         AND e.status <> 'dropped'
  JOIN course_sections cs ON cs.section_id = e.section_id
                         AND cs.course_code LIKE d.dept_code || '%'
 GROUP BY d.dept_code, d.dept_name
HAVING COUNT(e.enrollment_id) > 0
 ORDER BY seat_utilisation_pct DESC;

\echo '### QUERY 2 - Course difficulty'
\o
SELECT cs.course_code, COUNT(e.final_grade) AS graded,
       ROUND(AVG(e.final_grade), 2)         AS avg_mark,
       ROUND(STDDEV_SAMP(e.final_grade), 2) AS spread,
       ROUND(AVG(e.final_grade)
             - (SELECT AVG(final_grade) FROM enrollments
                 WHERE final_grade IS NOT NULL), 2) AS vs_school_avg
  FROM course_sections cs
  JOIN enrollments e ON e.section_id = cs.section_id
 WHERE e.final_grade IS NOT NULL
 GROUP BY cs.course_code, cs.course_name
 ORDER BY avg_mark ASC LIMIT 8;

\echo '### QUERY 3 - Student standing, top 5 by GPA'
\o
WITH student_results AS (
    SELECT s.student_id, s.student_no, d.dept_name,
           COUNT(e.enrollment_id) AS courses_taken,
           SUM(cs.credits)       AS credit_hours,
           ROUND(AVG(e.final_grade), 2) AS avg_mark,
           ROUND(SUM(e.final_grade * cs.credits)
                 / NULLIF(SUM(cs.credits), 0) / 25.0, 2) AS computed_gpa
      FROM students s
      JOIN departments d     ON d.department_id = s.department_id
      JOIN enrollments e    ON e.student_id = s.student_id
                           AND e.status IN ('completed', 'failed')
      JOIN course_sections cs ON cs.section_id = e.section_id
     WHERE e.final_grade IS NOT NULL
     GROUP BY s.student_id, s.student_no, d.dept_name),
ranked AS (
    SELECT sr.*,
           RANK() OVER (ORDER BY sr.computed_gpa DESC) AS position,
           RANK() OVER (PARTITION BY sr.dept_name
                        ORDER BY sr.computed_gpa DESC) AS dept_position
      FROM student_results sr)
SELECT r.position, r.dept_position, r.student_no, r.dept_name,
       r.courses_taken, r.credit_hours, r.avg_mark, r.computed_gpa
  FROM ranked r
 WHERE r.position <= 5
 ORDER BY r.position;

\echo '### QUERY 4 - Teaching load per instructor'
\o
SELECT u.first_name || ' ' || u.last_name AS instructor, d.dept_name,
       COUNT(cs.section_id)               AS sections,
       COALESCE(SUM(cs.enrolled_count), 0) AS students_taught,
       ROUND(100.0 * COALESCE(SUM(cs.enrolled_count), 0)
             / NULLIF(SUM(cs.capacity), 0), 1) AS utilisation_pct
  FROM instructors i
  JOIN users u       ON u.user_id = i.user_id
  JOIN departments d ON d.department_id = i.department_id
  LEFT JOIN course_sections cs ON cs.instructor_id = i.instructor_id
 GROUP BY u.user_id, u.first_name, u.last_name, d.dept_name, i.rank_title
 ORDER BY students_taught DESC, instructor;

\echo '### QUERY 5 - Grade distribution'
\o
WITH bucketed AS (
    SELECT CASE
             WHEN e.final_grade >= 93 THEN 'A  (93-100) excellent'
             WHEN e.final_grade >= 87 THEN 'B  (87-92)  good'
             WHEN e.final_grade >= 80 THEN 'C  (80-86)  satisfactory'
             WHEN e.final_grade >= 70 THEN 'D  (70-79)  pass'
             WHEN e.final_grade >= 50 THEN 'E  (50-69)  weak'
             ELSE                      'F  (0-49)   fail'
           END AS band, e.final_grade
      FROM enrollments e WHERE e.final_grade IS NOT NULL)
SELECT band, COUNT(*) AS records,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_cohort
  FROM bucketed GROUP BY band ORDER BY band;

\echo '### QUERY 6 - Departments beating the school average'
\o
SELECT d.dept_name, COUNT(DISTINCT s.student_id) AS students,
       ROUND(AVG(e.final_grade), 2) AS dept_avg,
       (SELECT ROUND(AVG(e2.final_grade), 2) FROM enrollments e2
         WHERE e2.final_grade IS NOT NULL) AS school_avg
  FROM departments d
  JOIN students s    ON s.department_id = d.department_id
  JOIN enrollments e ON e.student_id = s.student_id
                    AND e.final_grade IS NOT NULL
 GROUP BY d.dept_code, d.dept_name
HAVING AVG(e.final_grade) > (SELECT AVG(final_grade) FROM enrollments
                               WHERE final_grade IS NOT NULL)
 ORDER BY dept_avg DESC;

\echo '### QUERY 7 - Assignment submission rates'
\o
SELECT cs.course_code, a.title,
       COUNT(sm.submission_id) AS submitted,
       COUNT(e.student_id)     AS enrolled,
       ROUND(100.0 * COUNT(sm.submission_id)
             / NULLIF(COUNT(e.student_id), 0), 1) AS submission_rate_pct,
       COUNT(*) FILTER (WHERE sm.is_late) AS late
  FROM assignments a
  JOIN course_sections cs ON cs.section_id = a.section_id
  LEFT JOIN enrollments e  ON e.section_id = a.section_id
                          AND e.status <> 'dropped'
  LEFT JOIN submissions sm ON sm.assignment_id = a.assignment_id
                          AND sm.student_id = e.student_id
 GROUP BY cs.course_code, a.assignment_id, a.title
 ORDER BY submission_rate_pct ASC LIMIT 6;

\echo '### QUERY 8 - Attendance risk'
\o
SELECT s.student_no,
       u.first_name || ' ' || u.last_name AS student_name,
       d.dept_name,
       COUNT(*) FILTER (WHERE a.status = 'absent') AS absences,
       COUNT(a.attendance_id) AS sessions,
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
 ORDER BY absence_rate_pct DESC LIMIT 5;

\o
\echo '### PL/pgSQL BLOCK 1 - letter_grade_for, boundary values'
SELECT v AS score, letter_grade_for(v) AS letter
  FROM (VALUES (100::numeric),(93),(87),(80),(70),(69.99),(0))
       AS t(v) ORDER BY v DESC;

\echo '### PL/pgSQL BLOCK 2 - calculate_student_gpa'
SELECT s.student_id, s.gpa AS before_gpa,
       calculate_student_gpa(s.student_id) AS returned
  FROM students s WHERE s.student_id IN (1,2,6);

\echo '### PL/pgSQL BLOCK 3 - section_fill_ratio'
SELECT cs.section_id, cs.course_code, cs.enrolled_count, cs.capacity,
       section_fill_ratio(cs.section_id) AS fill_pct
  FROM course_sections cs ORDER BY cs.section_id LIMIT 5;

\echo '### PL/pgSQL BLOCK 4 - enroll_student, refusal paths'
CALL enroll_student(1, 1);
CALL enroll_student(999999, 1);

\echo '### PL/pgSQL BLOCK 4 - enroll_student, success + counter trigger'
CALL enroll_student(4, 3);
SELECT section_id, enrolled_count FROM course_sections WHERE section_id = 3;

\echo '### PL/pgSQL BLOCK 6/7 - grade change: audit, letter, status, GPA'
BEGIN;
  SET LOCAL "app.user" = 'i.kaya';
  UPDATE enrollments SET final_grade = 95.50 WHERE enrollment_id = 1;
  SELECT final_grade, grade_letter, status FROM enrollments WHERE enrollment_id = 1;
  SELECT old_grade, new_grade, old_letter, new_letter, changed_by
    FROM grade_audit ORDER BY audit_id DESC LIMIT 1;
  SELECT student_id, gpa FROM students WHERE student_id = 1;
ROLLBACK;

\echo '### PL/pgSQL BLOCK 5 - enrolled_count agrees with reality'
SELECT cs.section_id, cs.enrolled_count AS stored,
       COUNT(e.enrollment_id) AS actual
  FROM course_sections cs
  LEFT JOIN enrollments e ON e.section_id = cs.section_id
                        AND e.status <> 'dropped'
 GROUP BY cs.section_id, cs.enrolled_count
HAVING cs.enrolled_count <> COUNT(e.enrollment_id);
-- zero rows above: every counter matches

\echo '### Constraint enforcement (each of these must be refused)'
UPDATE students SET gpa = 9.99 WHERE student_id = 1;
INSERT INTO assignments (section_id, title, weight, max_points, due_date)
     VALUES (1, 'Bad', 150, 100, CURRENT_DATE);
INSERT INTO users (username, email, password_hash, first_name, last_name)
     VALUES ('admin', 'x@school.edu', 'x', 'X', 'Y');
UPDATE course_sections SET enrolled_count = capacity + 1 WHERE section_id = 1;

\echo '### capture complete'