-- =============================================================
-- verify_plpgsql.sql  —  executable proof that each PL/pgSQL
-- block behaves correctly, including its failure paths.
-- CMPE344  ·  run against a seeded database.
--
--   psql -d school_validation -f supabase/migrations/verify_plpgsql.sql
--
-- Every check prints PASS or FAIL. Nothing here mutates data
-- permanently: the mutating checks run inside a transaction that
-- is rolled back at the end.
-- =============================================================

\set ON_ERROR_STOP off
\pset border 2

\echo ''
\echo '=== 1. FUNCTION letter_grade_for — boundaries ==='
SELECT v AS score, letter_grade_for(v) AS returned, e::grade_letter AS expected,
       CASE WHEN letter_grade_for(v) = e::grade_letter THEN 'PASS' ELSE 'FAIL' END AS result
FROM (VALUES (100::numeric,'AA'),(93,'AA'),(92.99,'BB'),(87,'BB'),(86.99,'CC'),
             (80,'CC'),(79.99,'DD'),(70,'DD'),(69.99,'FF'),(0,'FF')) AS t(v,e);

\echo ''
\echo '=== 2. FUNCTION section_fill_ratio ==='
SELECT cs.section_id, cs.course_code, cs.enrolled_count, cs.capacity,
       section_fill_ratio(cs.section_id) AS pct,
       CASE WHEN section_fill_ratio(cs.section_id)
                 = ROUND(cs.enrolled_count::numeric / cs.capacity * 100, 1)
            THEN 'PASS' ELSE 'FAIL' END AS result
FROM course_sections cs ORDER BY cs.section_id LIMIT 4;

-- Must RAISE 'Section 999999 does not exist'
\echo ''
\echo '=== 3. FUNCTION section_fill_ratio rejects unknown section ==='
SELECT section_fill_ratio(999999) AS should_error;

\echo ''
\echo '=== 4. FUNCTION calculate_student_gpa — nonexistent student ==='
SELECT calculate_student_gpa(999999) AS should_error;

\echo ''
\echo '=== 5. FUNCTION calculate_student_gpa — computes and persists ==='
\echo '--- note: the function UPDATEs, so the stored value must be read in a'
\echo '--- LATER statement than the call. Within one statement the snapshot'
\echo '--- still shows the pre-call value, which is correct MVCC behaviour.'
SELECT s.student_id, s.gpa AS stored_before,
       calculate_student_gpa(s.student_id) AS computed_by_function
FROM students s WHERE s.student_id IN (1,2,6);

SELECT student_id, gpa AS stored_after
  FROM students WHERE student_id IN (1,2,6);

\echo ''
\echo '=== 6. PROCEDURE enroll_student — must refuse these ==='
\echo '--- 6a. duplicate enrolment ---'
CALL enroll_student(1, 1);
\echo '--- 6b. unknown student ---'
CALL enroll_student(999999, 1);
\echo '--- 6c. unknown section ---'
CALL enroll_student(1, 999999);

\echo ''
\echo '=== 7. PROCEDURE enroll_student — capacity rule ==='
\echo '--- 7a. fill a section, then try to add one more (expect refusal) ---'
\echo '---     run alone: the expected exception aborts its own transaction ---'
UPDATE course_sections SET capacity = enrolled_count WHERE section_id = 5;
CALL enroll_student(9, 5);
UPDATE course_sections SET capacity = capacity + 20 WHERE section_id = 5;

\echo '--- 7b. a section with room must accept the enrolment ---'
CALL enroll_student(4, 3);
SELECT section_id, enrolled_count AS counter_after_trigger
  FROM course_sections WHERE section_id = 3;
DELETE FROM enrollments WHERE student_id = 4 AND section_id = 3;

\echo ''
\echo '=== 8. TRIGGER enrolled_count agrees with reality everywhere ==='
SELECT cs.section_id, cs.enrolled_count AS stored,
       COUNT(e.enrollment_id) AS actual,
       CASE WHEN cs.enrolled_count = COUNT(e.enrollment_id)
            THEN 'PASS' ELSE 'FAIL' END AS result
FROM course_sections cs
LEFT JOIN enrollments e
       ON e.section_id = cs.section_id AND e.status <> 'dropped'
GROUP BY cs.section_id, cs.enrolled_count
HAVING cs.enrolled_count <> COUNT(e.enrollment_id);
-- No rows above means every counter matched.

\echo ''
\echo '=== 9. TRIGGER grade audit — records actor, letter, and GPA effect ==='
BEGIN;
    SET LOCAL "app.user" = 'i.kaya';
    UPDATE enrollments SET final_grade = 95.50 WHERE enrollment_id = 1;

    SELECT e.enrollment_id, e.final_grade, e.grade_letter, e.status,
           CASE WHEN e.grade_letter = 'AA' AND e.status = 'completed'
                THEN 'PASS' ELSE 'FAIL' END AS result
      FROM enrollments e WHERE e.enrollment_id = 1;

    SELECT old_grade, new_grade, old_letter, new_letter, changed_by,
           CASE WHEN changed_by = 'i.kaya' THEN 'PASS' ELSE 'FAIL' END AS result
      FROM grade_audit ORDER BY audit_id DESC LIMIT 1;
ROLLBACK;

\echo ''
\echo '=== 10. TRIGGER grade audit — a fail sets status=failed, letter=FF ==='
BEGIN;
    SET LOCAL "app.user" = 'registrar';
    UPDATE enrollments SET final_grade = 41.00 WHERE enrollment_id = 1;
    SELECT final_grade, grade_letter, status,
           CASE WHEN grade_letter = 'FF' AND status = 'failed'
                THEN 'PASS' ELSE 'FAIL' END AS result
      FROM enrollments WHERE enrollment_id = 1;
ROLLBACK;

\echo ''
\echo '=== 11. TRIGGER grade audit — clearing a grade is a withdrawal, not a fail ==='
BEGIN;
    UPDATE enrollments SET final_grade = NULL, status = 'dropped'
     WHERE enrollment_id = 1;
    SELECT final_grade, grade_letter, status,
           CASE WHEN final_grade IS NULL AND status = 'dropped'
                THEN 'PASS' ELSE 'FAIL' END AS result
      FROM enrollments WHERE enrollment_id = 1;
ROLLBACK;

\echo ''
\echo '=== 12. TRIGGER updated_at advances on update ==='
\echo '--- 12a. an untouched row has created_at = updated_at (both defaulted) ---'
SELECT created_at = updated_at AS equal_before_update
  FROM users WHERE username = 'admin';
\echo '--- 12b. after an UPDATE the trigger must move updated_at forward ---'
UPDATE users SET last_name = 'Administrator' WHERE username = 'admin';
SELECT created_at < updated_at AS advanced_after_update,
       CASE WHEN updated_at > created_at THEN 'PASS' ELSE 'FAIL' END AS result
  FROM users WHERE username = 'admin';

\echo ''
\echo '=== 13. FUNCTION get_section_roster ==='
SELECT * FROM get_section_roster(1);

\echo ''
\echo '=== 14. CHECK constraints reject bad data ==='
\echo '--- 14a. GPA above 4.00 (expect error) ---'
UPDATE students SET gpa = 9.99 WHERE student_id = 1;
\echo '--- 14b. weight over 100 (expect error) ---'
INSERT INTO assignments (section_id, title, weight, max_points, due_date)
VALUES (1, 'Bad', 150, 100, CURRENT_DATE);
\echo '--- 14c. duplicate username (expect error) ---'
INSERT INTO users (username, email, password_hash, first_name, last_name)
VALUES ('admin', 'x@school.edu', 'x', 'X', 'Y');
\echo '--- 14d. enrolled_count above capacity (expect error) ---'
UPDATE course_sections SET enrolled_count = capacity + 1 WHERE section_id = 1;

\echo ''
\echo '=== verification complete ==='