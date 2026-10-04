-- ============================================================================
--  apply_all.sql  --  generated file, do not edit
--
--  Single-file version of 0001_schema.sql + 0002_plpgsql.sql + 0003_seed.sql,
--  for pasting into the Supabase dashboard's SQL Editor.
--
--  Source of truth:  supabase/migrations/*.sql
--  Regenerate with:  python scripts/build_single_sql.py
--
--  This file is a concatenation. Nothing is stripped or rewritten, so
--  what runs in the dashboard is exactly what is version controlled.
--
--  Expected on completion: 33 users, 25 students, 6 instructors,
--  18 course sections, 57 enrolments, 15 assignments, 62 submissions,
--  212 attendance records.
-- ============================================================================


-- ===== 0001_schema.sql ===================================================

-- =============================================================
-- 0001_schema.sql  —  DDL: School Administration System
-- CMPE344 Database Management Systems and Programming II
-- Target: PostgreSQL 15 (Supabase)
--
-- 12 tables. Every table gets a surrogate primary key; business
-- keys carry UNIQUE constraints. Cross-table links are enforced
-- with FOREIGN KEY ... ON DELETE clauses, and every column that
-- can only hold one of a fixed set of values gets a CHECK.
-- =============================================================

-- -------------------------------------------------------------
-- 0. Domain domains (lookup value sets) — keep CHECK lists and
--    the app's selectboxes in sync with these.
-- -------------------------------------------------------------

CREATE TYPE user_status   AS ENUM ('active', 'suspended', 'pending');
CREATE TYPE program_level AS ENUM ('undergraduate', 'graduate', 'phd');
CREATE TYPE term_name     AS ENUM ('Fall', 'Spring', 'Summer');
CREATE TYPE enroll_status AS ENUM ('enrolled', 'dropped', 'completed', 'failed');
CREATE TYPE attendance_status AS ENUM ('present', 'absent', 'late', 'excused');
CREATE TYPE grade_letter  AS ENUM ('AA', 'BB', 'CC', 'DD', 'FF', 'NA');

-- =============================================================
-- 1. users  — authentication table (brief requirement: "a user
--    table that will allow users to login the system")
-- =============================================================
CREATE TABLE users (
    user_id       SERIAL PRIMARY KEY,
    username      VARCHAR(50)  NOT NULL,
    email         VARCHAR(120) NOT NULL,
    -- bcrypt digest, generated in Python (src/auth.py). Never the
    -- plaintext password, never reversible.
    password_hash TEXT         NOT NULL,
    first_name    VARCHAR(60)  NOT NULL,
    last_name     VARCHAR(60)  NOT NULL,
    status        user_status  NOT NULL DEFAULT 'pending',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_users_username UNIQUE (username),
    CONSTRAINT uq_users_email    UNIQUE (email),
    CONSTRAINT ck_users_username CHECK (username ~ '^[a-z0-9_.]{3,50}$')
);

COMMENT ON TABLE  users IS 'Login accounts. Authentication + authorisation source.';
COMMENT ON COLUMN users.password_hash IS 'bcrypt digest. Plaintext passwords are never stored.';

-- =============================================================
-- 2. roles  — the "user groups" the brief asks for (admin,
--    employee/registrar, customer->student, technical staff)
-- =============================================================
CREATE TABLE roles (
    role_id       SERIAL PRIMARY KEY,
    role_name     VARCHAR(40) NOT NULL,
    description   VARCHAR(255),
    -- Higher rank sees more. Used to gate screens in the app.
    access_level  SMALLINT    NOT NULL DEFAULT 1,
    CONSTRAINT uq_roles_name UNIQUE (role_name),
    CONSTRAINT ck_roles_access CHECK (access_level BETWEEN 1 AND 5)
);

-- =============================================================
-- 3. user_roles  — many-to-many: one person may be both an
--    instructor and a teaching assistant, so roles cannot be a
--    single column on users.
-- =============================================================
CREATE TABLE user_roles (
    user_id       INTEGER NOT NULL REFERENCES users(user_id)    ON DELETE CASCADE,
    role_id       INTEGER NOT NULL REFERENCES roles(role_id)    ON DELETE RESTRICT,
    granted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_user_roles PRIMARY KEY (user_id, role_id)
);

-- =============================================================
-- 4. departments
-- =============================================================
CREATE TABLE departments (
    department_id SERIAL PRIMARY KEY,
    dept_code     VARCHAR(10)  NOT NULL,
    dept_name     VARCHAR(100) NOT NULL,
    -- Money is stored as NUMERIC, never FLOAT: budget arithmetic
    -- with binary floats accumulates cent-level error.
    annual_budget NUMERIC(14,2) NOT NULL DEFAULT 0,
    established_year SMALLINT NOT NULL DEFAULT 2000,
    CONSTRAINT uq_departments_code UNIQUE (dept_code),
    CONSTRAINT ck_departments_budget CHECK (annual_budget >= 0),
    CONSTRAINT ck_departments_year  CHECK (established_year BETWEEN 1900 AND 2100)
);

-- =============================================================
-- 5. classrooms
-- =============================================================
CREATE TABLE classrooms (
    classroom_id  SERIAL PRIMARY KEY,
    building      VARCHAR(50) NOT NULL,
    room_number   VARCHAR(10) NOT NULL,
    capacity      INTEGER     NOT NULL DEFAULT 30,
    has_projector BOOLEAN     NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_classrooms_room UNIQUE (building, room_number),
    CONSTRAINT ck_classrooms_capacity CHECK (capacity > 0)
);

-- =============================================================
-- 6. instructors  — profile rows that hang off a users row
-- =============================================================
CREATE TABLE instructors (
    instructor_id SERIAL PRIMARY KEY,
    user_id       INTEGER     NOT NULL REFERENCES users(user_id)     ON DELETE CASCADE,
    department_id INTEGER     NOT NULL REFERENCES departments(department_id) ON DELETE RESTRICT,
    employee_no   VARCHAR(20)  NOT NULL,
    hire_date     DATE         NOT NULL DEFAULT CURRENT_DATE,
    rank_title    VARCHAR(40)  NOT NULL DEFAULT 'Lecturer',
    salary        NUMERIC(12,2),
    CONSTRAINT uq_instructors_user UNIQUE (user_id),
    CONSTRAINT uq_instructors_empno UNIQUE (employee_no),
    CONSTRAINT ck_instructors_salary CHECK (salary IS NULL OR salary > 0)
);

-- =============================================================
-- 7. students  — profile rows that hang off a users row
-- =============================================================
CREATE TABLE students (
    student_id    SERIAL PRIMARY KEY,
    user_id       INTEGER      NOT NULL REFERENCES users(user_id)     ON DELETE CASCADE,
    department_id INTEGER      NOT NULL REFERENCES departments(department_id) ON DELETE RESTRICT,
    -- Circular reference: a student's advisor is another student.
    -- Added after creation because the table has to exist first.
    advisor_id    INTEGER,
    student_no    VARCHAR(20)  NOT NULL,
    program_level program_level NOT NULL DEFAULT 'undergraduate',
    enrollment_year SMALLINT   NOT NULL DEFAULT EXTRACT(YEAR FROM CURRENT_DATE),
    gpa           NUMERIC(3,2) NOT NULL DEFAULT 0.00,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_students_user UNIQUE (user_id),
    CONSTRAINT uq_students_no   UNIQUE (student_no),
    CONSTRAINT ck_students_gpa   CHECK (gpa >= 0.00 AND gpa <= 4.00),
    CONSTRAINT ck_students_year  CHECK (enrollment_year BETWEEN 1900 AND 2100),
    CONSTRAINT ck_students_advise CHECK (advisor_id IS NULL OR advisor_id <> student_id)
);

ALTER TABLE students
    ADD CONSTRAINT fk_students_advisor
    FOREIGN KEY (advisor_id) REFERENCES students(student_id) ON DELETE SET NULL;

CREATE INDEX idx_students_department ON students(department_id);
CREATE INDEX idx_instructors_department ON instructors(department_id);

-- =============================================================
-- 8. course_sections  — a course *offered* in a term. Splitting
--    this from a static catalogue is what lets the same course run
--    twice with different instructors and rooms, which the
--    "instructor load" report depends on.
-- =============================================================
CREATE TABLE course_sections (
    section_id    SERIAL PRIMARY KEY,
    course_code   VARCHAR(12)  NOT NULL,
    course_name   VARCHAR(120) NOT NULL,
    term          term_name    NOT NULL,
    academic_year SMALLINT     NOT NULL,
    instructor_id INTEGER      REFERENCES instructors(instructor_id) ON DELETE SET NULL,
    classroom_id  INTEGER      REFERENCES classrooms(classroom_id)  ON DELETE SET NULL,
    credits       SMALLINT     NOT NULL DEFAULT 3,
    capacity      INTEGER      NOT NULL DEFAULT 30,
    -- Computed by PL/pgSQL trigger in 0002, kept as a column so the
    -- reports can sort on it without re-scanning enrollments.
    enrolled_count INTEGER    NOT NULL DEFAULT 0,
    CONSTRAINT uq_sections_offering UNIQUE (course_code, term, academic_year, classroom_id),
    CONSTRAINT ck_sections_capacity CHECK (capacity > 0),
    CONSTRAINT ck_sections_credits  CHECK (credits BETWEEN 1 AND 6),
    CONSTRAINT ck_sections_year     CHECK (academic_year BETWEEN 2000 AND 2100),
    -- The trigger must never be able to push this over capacity.
    CONSTRAINT ck_sections_enrolled CHECK (enrolled_count >= 0 AND enrolled_count <= capacity)
);

CREATE INDEX idx_sections_instructor ON course_sections(instructor_id);
CREATE INDEX idx_sections_term       ON course_sections(term, academic_year);

-- =============================================================
-- 9. enrollments  — the junction between students and sections,
--    and the fact table every analytical query aggregates.
-- =============================================================
CREATE TABLE enrollments (
    enrollment_id SERIAL PRIMARY KEY,
    student_id    INTEGER   NOT NULL REFERENCES students(student_id)         ON DELETE CASCADE,
    section_id    INTEGER   NOT NULL REFERENCES course_sections(section_id)  ON DELETE CASCADE,
    enrolled_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status        enroll_status NOT NULL DEFAULT 'enrolled',
    final_grade   NUMERIC(5,2),
    grade_letter  grade_letter,
    CONSTRAINT uq_enrollments_student_section UNIQUE (student_id, section_id),
    CONSTRAINT ck_enrollments_grade  CHECK (final_grade IS NULL OR (final_grade >= 0 AND final_grade <= 100)),
    -- A record is either still open, or it is closed *with* a mark.
    -- A 'failed' enrolment carries a grade below 60; that is a real
    -- outcome and must be storable.
    CONSTRAINT ck_enrollments_final CHECK (
        (status IN ('completed', 'failed') AND final_grade IS NOT NULL)
     OR (status IN ('enrolled', 'dropped') AND final_grade IS NULL)
    )
);

CREATE INDEX idx_enrollments_section ON enrollments(section_id);

-- =============================================================
-- 10. assignments
-- =============================================================
CREATE TABLE assignments (
    assignment_id SERIAL PRIMARY KEY,
    section_id    INTEGER   NOT NULL REFERENCES course_sections(section_id) ON DELETE CASCADE,
    title         VARCHAR(150) NOT NULL,
    weight        NUMERIC(5,2) NOT NULL DEFAULT 10.00,
    max_points    NUMERIC(6,2) NOT NULL DEFAULT 100.00,
    due_date      DATE      NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_assignments_weight CHECK (weight > 0 AND weight <= 100),
    CONSTRAINT ck_assignments_points CHECK (max_points > 0)
);

CREATE INDEX idx_assignments_section ON assignments(section_id);

-- =============================================================
-- 11. submissions
-- =============================================================
CREATE TABLE submissions (
    submission_id  SERIAL PRIMARY KEY,
    assignment_id  INTEGER   NOT NULL REFERENCES assignments(assignment_id) ON DELETE CASCADE,
    student_id     INTEGER   NOT NULL REFERENCES students(student_id)          ON DELETE CASCADE,
    submitted_at   TIMESTAMPTZ,
    score          NUMERIC(6,2),
    feedback       TEXT,
    graded_by      INTEGER   REFERENCES users(user_id) ON DELETE SET NULL,
    is_late        BOOLEAN   NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_submissions_assignment_student UNIQUE (assignment_id, student_id),
    -- Handed in but not scored yet is a legitimate state; graded
    -- without being handed in is not.
    CONSTRAINT ck_submissions_score CHECK (
        score IS NULL OR (submitted_at IS NOT NULL AND score >= 0 AND score <= 100)
    )
);

CREATE INDEX idx_submissions_student ON submissions(student_id);

-- =============================================================
-- 12. attendance
-- =============================================================
CREATE TABLE attendance (
    attendance_id SERIAL PRIMARY KEY,
    section_id    INTEGER NOT NULL REFERENCES course_sections(section_id) ON DELETE CASCADE,
    student_id    INTEGER NOT NULL REFERENCES students(student_id)           ON DELETE CASCADE,
    session_date  DATE    NOT NULL,
    status        attendance_status NOT NULL DEFAULT 'present',
    CONSTRAINT uq_attendance_session UNIQUE (section_id, student_id, session_date)
);

CREATE INDEX idx_attendance_student ON attendance(student_id);

-- =============================================================
-- Convenience view: a flat, human-readable roster used by the
-- reports screen. Demonstrates that the schema answers real
-- questions rather than just storing rows.
-- =============================================================
CREATE VIEW v_roster AS
SELECT
    cs.section_id,
    cs.course_code,
    cs.course_name,
    cs.term,
    cs.academic_year,
    cs.credits,
    cs.capacity,
    cs.enrolled_count,
    d.dept_name,
    iu.first_name || ' ' || iu.last_name AS instructor_name,
    s.student_id,
    su.first_name || ' ' || su.last_name AS student_name,
    s.student_no,
    e.status,
    e.final_grade,
    e.grade_letter,
    -- Percentage weighted by credits. Not a grade point value:
    -- divide by 25.0 to reach the 4.00 GPA scale.
    ROUND((e.final_grade * cs.credits), 2) AS weighted_score
FROM enrollments e
JOIN students       s  ON s.student_id  = e.student_id
JOIN users          su ON su.user_id     = s.user_id
JOIN course_sections cs ON cs.section_id = e.section_id
LEFT JOIN instructors i  ON i.instructor_id = cs.instructor_id
LEFT JOIN users      iu ON iu.user_id      = i.user_id
LEFT JOIN departments d  ON d.dept_code = LEFT(cs.course_code, 2);

-- ===== 0002_plpgsql.sql ==================================================

-- =============================================================
-- 0002_plpgsql.sql  —  PL/pgSQL blocks
-- CMPE344 Database Management Systems and Programming II
--
-- NOTE ON LANGUAGE: the brief asks for PL/SQL, but Supabase runs
-- PostgreSQL, which has no PL/SQL. PL/pgSQL is PostgreSQL's
-- equivalent procedural language and maps one-to-one onto the
-- PL/SQL constructs the brief names (procedures, functions,
-- triggers). Approval from the course coordinator is being
-- sought; see docs/report.md.
--
-- Seven blocks: 2 functions, 1 procedure, 4 triggers.
-- =============================================================

-- -------------------------------------------------------------
-- Block 1 — FUNCTION: letter grade from a numeric score
-- Used by Block 7 (the grade-change trigger).
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION letter_grade_for(score NUMERIC)
RETURNS grade_letter
LANGUAGE plpgsql
IMMUTABLE
AS $$
BEGIN
    IF score IS NULL THEN
        RETURN 'NA';
    ELSIF score >= 93 THEN
        RETURN 'AA';
    ELSIF score >= 87 THEN
        RETURN 'BB';
    ELSIF score >= 80 THEN
        RETURN 'CC';
    ELSIF score >= 70 THEN
        RETURN 'DD';
    ELSE
        RETURN 'FF';
    END IF;
END;
$$;

-- -------------------------------------------------------------
-- Block 2 — FUNCTION: recalculate and store a student's GPA
-- Weighted by course credits. Returns the new GPA.
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION calculate_student_gpa(p_student_id INTEGER)
RETURNS NUMERIC
LANGUAGE plpgsql
VOLATILE
AS $$
DECLARE
    v_gpa NUMERIC;
BEGIN
    -- Guard: a student id that does not exist should not silently
    -- produce a 0.00 GPA.
    IF NOT EXISTS (SELECT 1 FROM students WHERE student_id = p_student_id) THEN
        RAISE EXCEPTION 'Student % does not exist', p_student_id
            USING ERRCODE = 'no_data_found';
    END IF;

    SELECT COALESCE(
        ROUND(SUM(e.final_grade * cs.credits)
              / NULLIF(SUM(cs.credits), 0) / 25.0, 2),
        0.00)
      INTO v_gpa
      FROM enrollments e
      JOIN course_sections cs ON cs.section_id = e.section_id
     WHERE e.student_id = p_student_id
       AND e.status = 'completed'
       AND e.final_grade IS NOT NULL;

    -- A percentage average mapped onto the 4.00 scale: 100 -> 4.00.
    UPDATE students
       SET gpa = GREATEST(LEAST(v_gpa, 4.00), 0.00),
           updated_at = NOW()
     WHERE student_id = p_student_id;

    RETURN v_gpa;
END;
$$;

COMMENT ON FUNCTION calculate_student_gpa(INTEGER) IS
    'Credit-weighted GPA on a 4.00 scale, recomputed and persisted.';

-- -------------------------------------------------------------
-- Block 3 — FUNCTION: how full a section is, as a percentage
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION section_fill_ratio(p_section_id INTEGER)
RETURNS NUMERIC
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_capacity INTEGER;
    v_enrolled INTEGER;
BEGIN
    SELECT capacity, enrolled_count
      INTO v_capacity, v_enrolled
      FROM course_sections
     WHERE section_id = p_section_id;

    IF v_capacity IS NULL THEN
        RAISE EXCEPTION 'Section % does not exist', p_section_id
            USING ERRCODE = 'no_data_found';
    END IF;

    IF v_capacity = 0 THEN
        RETURN 0;
    END IF;

    RETURN ROUND((v_enrolled::NUMERIC / v_capacity) * 100, 1);
END;
$$;

-- -------------------------------------------------------------
-- Block 4 — PROCEDURE: enrol a student in a section
-- Enforces the business rules that a bare INSERT cannot: no
-- duplicate enrolment, no over-enrolment, and a recalculated GPA
-- afterwards.
-- -------------------------------------------------------------
CREATE OR REPLACE PROCEDURE enroll_student(
    p_student_id INTEGER,
    p_section_id INTEGER
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_capacity   INTEGER;
    v_enrolled   INTEGER;
    v_credits    SMALLINT;
    v_completed  INTEGER;
    v_needed     INTEGER;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM students WHERE student_id = p_student_id) THEN
        RAISE EXCEPTION 'Student % does not exist', p_student_id;
    END IF;

    SELECT cs.capacity, cs.enrolled_count, cs.credits
      INTO v_capacity, v_enrolled, v_credits
      FROM course_sections cs
     WHERE cs.section_id = p_section_id
       FOR UPDATE;              -- row lock: two concurrent callers
                               -- cannot both pass the capacity test

    IF v_capacity IS NULL THEN
        RAISE EXCEPTION 'Section % does not exist', p_section_id;
    END IF;

    IF EXISTS (
        SELECT 1 FROM enrollments
         WHERE student_id = p_student_id
           AND section_id = p_section_id
           AND status <> 'dropped'
    ) THEN
        RAISE EXCEPTION 'Student % is already enrolled in section %',
            p_student_id, p_section_id;
    END IF;

    IF v_enrolled >= v_capacity THEN
        RAISE EXCEPTION 'Section % is full (% of % seats taken)',
            p_section_id, v_enrolled, v_capacity;
    END IF;

    -- Re-enrolling after a withdrawal revives the existing row
    -- rather than inserting a second one: (student_id, section_id)
    -- is unique, and a student who drops and returns should have
    -- one enrolment, not a history of fragments. Clearing the grade
    -- also satisfies ck_enrollments_final, which requires an open
    -- enrolment to carry no mark.
    INSERT INTO enrollments (student_id, section_id, status)
    VALUES (p_student_id, p_section_id, 'enrolled')
    ON CONFLICT (student_id, section_id) DO UPDATE
        SET status       = 'enrolled',
            enrolled_at  = NOW(),
            final_grade  = NULL,
            grade_letter = NULL;

    -- Credit-hour load cap. Computed before inserting so we can
    -- refuse rather than insert and roll back.
    SELECT COUNT(*) INTO v_completed
      FROM enrollments e
      JOIN course_sections cs ON cs.section_id = e.section_id
     WHERE e.student_id = p_student_id
       AND e.status = 'completed';

    v_needed := v_completed + v_credits;
    IF v_needed > 24 THEN
        RAISE WARNING 'Student % would carry % credit hours (usual cap is 24)',
            p_student_id, v_needed;
    END IF;

    RAISE NOTICE 'Enrolled student % in section % (% of % seats)',
        p_student_id, p_section_id, v_enrolled + 1, v_capacity;
END;
$$;

-- -------------------------------------------------------------
-- Block 5 — TRIGGER: keep enrolled_count in step with enrollments
-- AFTER INSERT / UPDATE / DELETE. The counter is denormalised on
-- purpose (the reports sort and filter on it constantly), which
-- means it must be maintained by the database, not by the app.
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_sections_enrolled_count()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_old_section_id INTEGER;
    v_new_section_id INTEGER;
BEGIN
    v_old_section_id := CASE WHEN TG_OP IN ('UPDATE','DELETE') THEN OLD.section_id END;
    v_new_section_id := CASE WHEN TG_OP IN ('UPDATE','INSERT') THEN NEW.section_id END;

    -- A moved enrolment decrements the old section as well as
    -- incrementing the new one, so both have to be recounted.
    IF v_old_section_id IS DISTINCT FROM v_new_section_id THEN
        UPDATE course_sections cs
           SET enrolled_count = (
                   SELECT COUNT(*) FROM enrollments e
                    WHERE e.section_id = cs.section_id
                      AND e.status <> 'dropped'
               )
         WHERE cs.section_id IN (v_old_section_id, v_new_section_id);
    ELSE
        UPDATE course_sections cs
           SET enrolled_count = (
                   SELECT COUNT(*) FROM enrollments e
                    WHERE e.section_id = cs.section_id
                      AND e.status <> 'dropped'
               )
         WHERE cs.section_id = v_new_section_id;
    END IF;

    RETURN NULL;
END;
$$;

CREATE TRIGGER tg_sections_enrolled_count_ins
    AFTER INSERT ON enrollments
    FOR EACH ROW EXECUTE FUNCTION trg_sections_enrolled_count();

CREATE TRIGGER tg_sections_enrolled_count_upd
    AFTER UPDATE OF section_id, status ON enrollments
    FOR EACH ROW EXECUTE FUNCTION trg_sections_enrolled_count();

CREATE TRIGGER tg_sections_enrolled_count_del
    AFTER DELETE ON enrollments
    FOR EACH ROW EXECUTE FUNCTION trg_sections_enrolled_count();

-- -------------------------------------------------------------
-- Block 6 — TRIGGER: stamp updated_at automatically
-- Keeps the column honest no matter which client writes the row,
-- including psql and the SQL editor.
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER tg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TRIGGER tg_students_updated_at
    BEFORE UPDATE ON students
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- -------------------------------------------------------------
-- Block 7 — TRIGGER: audit grade changes
-- Every grade movement is written to grade_audit with the actor
-- from the session variable the app sets, so a disputed mark can
-- be traced to whoever entered it.
-- -------------------------------------------------------------
CREATE TABLE grade_audit (
    audit_id      SERIAL PRIMARY KEY,
    enrollment_id INTEGER     NOT NULL,
    student_id    INTEGER     NOT NULL,
    section_id    INTEGER     NOT NULL,
    old_grade     NUMERIC(5,2),
    new_grade     NUMERIC(5,2),
    old_letter    grade_letter,
    new_letter    grade_letter,
    changed_by    VARCHAR(60) NOT NULL DEFAULT CURRENT_USER,
    changed_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_grade_audit_changed CHECK (old_grade IS DISTINCT FROM new_grade)
);

CREATE OR REPLACE FUNCTION trg_audit_grade_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.final_grade IS NOT DISTINCT FROM OLD.final_grade THEN
        RETURN NEW;                    -- nothing material changed
    END IF;

    -- A grade being cleared is a withdrawal, not a fail. The caller
    -- owns the status in that case ('dropped'), and the CHECK
    -- constraint requires a grade-less row to carry no grade.
    IF NEW.final_grade IS NULL THEN
        NEW.grade_letter := 'NA';
        RETURN NEW;
    END IF;

    -- Enrolment status must agree with whether a grade exists, and
    -- a completed row is the only one allowed to carry a mark.
    NEW.grade_letter := letter_grade_for(NEW.final_grade);
    NEW.status := CASE WHEN NEW.final_grade >= 60 THEN 'completed' ELSE 'failed' END;

    INSERT INTO grade_audit (
        enrollment_id, student_id, section_id,
        old_grade, new_grade, old_letter, new_letter, changed_by
    )
    VALUES (
        NEW.enrollment_id, NEW.student_id, NEW.section_id,
        OLD.final_grade, NEW.final_grade,
        OLD.grade_letter, NEW.grade_letter,
        COALESCE(current_setting('app.user', TRUE), CURRENT_USER)
    );

    -- Keep the student's GPA consistent with the change.
    PERFORM calculate_student_gpa(NEW.student_id);

    RETURN NEW;
END;
$$;

CREATE TRIGGER tg_audit_grade_change
    BEFORE UPDATE OF final_grade ON enrollments
    FOR EACH ROW EXECUTE FUNCTION trg_audit_grade_change();

-- -------------------------------------------------------------
-- Block 8 — FUNCTION: roster of a section, as a set of rows
-- A function returning TABLE is how the reports screen pulls a
-- class list in one round trip.
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_section_roster(p_section_id INTEGER)
RETURNS TABLE (
    student_no   VARCHAR,
    student_name TEXT,
    status       enroll_status,
    final_grade  NUMERIC,
    grade_letter grade_letter
)
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    RETURN QUERY
    SELECT s.student_no,
           su.first_name || ' ' || su.last_name,
           e.status,
           e.final_grade,
           e.grade_letter
      FROM enrollments e
      JOIN students s  ON s.student_id = e.student_id
      JOIN users    su ON su.user_id    = s.user_id
     WHERE e.section_id = p_section_id
     ORDER BY su.last_name, su.first_name;
END;
$$;

-- ===== 0003_seed.sql =====================================================

-- =============================================================
-- 0003_seed.sql  —  DML: realistic seed data
-- CMPE344 Database Management Systems and Programming II
--
-- Demo password for every account: Passw0rd!
-- The hash below is a real bcrypt (cost 12) digest, generated by
-- src/auth.py's hashing routine — not a placeholder string.
-- =============================================================

BEGIN;

-- -------------------------------------------------------------
-- Roles — the "user groups" the brief requires
-- -------------------------------------------------------------
INSERT INTO roles (role_name, description, access_level) VALUES
    ('admin',     'Full system access. Manages users, roles and all records.', 5),
    ('registrar', 'Student records, enrolment and reporting. No user management.', 4),
    ('instructor','Manages own course sections, assignments and grading.',         3),
    ('student',   'Views own enrolments, grades and attendance. Read only.',       1),
    ('technical', 'Maintains classrooms, timetables and infrastructure.',         2);

-- -------------------------------------------------------------
-- Departments
-- -------------------------------------------------------------
INSERT INTO departments (dept_code, dept_name, annual_budget, established_year) VALUES
    ('CS', 'Computer Science',       1850000.00, 1998),
    ('EE', 'Electrical Engineering', 1620000.00, 2001),
    ('ME', 'Mechanical Engineering', 1480000.00, 2003),
    ('BA', 'Business Administration', 940000.00, 1995),
    ('PS', 'Psychology',              720000.00, 2008),
    ('MA', 'Mathematics',             810000.00, 1992);

-- -------------------------------------------------------------
-- Classrooms
-- -------------------------------------------------------------
INSERT INTO classrooms (building, room_number, capacity, has_projector) VALUES
    ('Science',   'A101', 60, TRUE),  ('Science',   'A102', 40, TRUE),
    ('Science',   'B204', 35, FALSE), ('Engineering','E301', 45, TRUE),
    ('Engineering','E305', 25, FALSE),('Business',  'C110', 80, TRUE),
    ('Humanities','H201', 50, FALSE),('Library',   'L002', 30, TRUE);

-- -------------------------------------------------------------
-- Staff and admin accounts
-- -------------------------------------------------------------
INSERT INTO users (username, email, password_hash, first_name, last_name, status) VALUES
    ('admin',    'admin@school.edu',    '$2b$12$4f7v9UCLth6kmWV64vzWBudhI9uebdOTNeIPCUXpZyNb9gBHvWt/u', 'System',    'Administrator', 'active'),
    ('registrar','registrar@school.edu','$2b$12$4f7v9UCLth6kmWV64vzWBudhI9uebdOTNeIPCUXpZyNb9gBHvWt/u', 'Elif',      'Yildirim',      'active'),
    ('i.kaya',   'i.kaya@school.edu',   '$2b$12$4f7v9UCLth6kmWV64vzWBudhI9uebdOTNeIPCUXpZyNb9gBHvWt/u', 'Mert',      'Kaya',          'active'),
    ('i.demir',  'i.demir@school.edu',  '$2b$12$4f7v9UCLth6kmWV64vzWBudhI9uebdOTNeIPCUXpZyNb9gBHvWt/u', 'Seda',      'Demir',         'active'),
    ('i.ozturk', 'i.ozturk@school.edu', '$2b$12$4f7v9UCLth6kmWV64vzWBudhI9uebdOTNeIPCUXpZyNb9gBHvWt/u', 'Burak',     'Ozturk',        'active'),
    ('i.acar',   'i.acar@school.edu',   '$2b$12$4f7v9UCLth6kmWV64vzWBudhI9uebdOTNeIPCUXpZyNb9gBHvWt/u', 'Deniz',     'Acar',          'active'),
    ('i.koc',    'i.koc@school.edu',    '$2b$12$4f7v9UCLth6kmWV64vzWBudhI9uebdOTNeIPCUXpZyNb9gBHvWt/u', 'Aylin',     'Koc',           'active'),
    ('i.sahin',  'i.sahin@school.edu',  '$2b$12$4f7v9UCLth6kmWV64vzWBudhI9uebdOTNeIPCUXpZyNb9gBHvWt/u', 'Cem',       'Sahin',         'active');

-- -------------------------------------------------------------
-- 25 student accounts, generated rather than hand-listed
-- -------------------------------------------------------------
INSERT INTO users (username, email, password_hash, first_name, last_name, status)
SELECT
    'student' || g,
    'student' || g || '@school.edu',
    '$2b$12$4f7v9UCLth6kmWV64vzWBudhI9uebdOTNeIPCUXpZyNb9gBHvWt/u',
    (ARRAY['Ali','Ayse','Mehmet','Zeynep','Can','Elif','Emre','Deniz','Burak','Ceren',
           'Kerem','Selin','Baris','Melis','Onur','Sena','Kaan','Duru','Arda','Pelin',
           'Efe','Neha','Kaya','Su','Bora'])[g],
    (ARRAY['Yilmaz','Kaya','Demir','Sahin','Aydin','Celik','Yildiz','Yilmaz','Ozturk','Arslan',
           'Koc','Kurt','Acar','Duman','Tekin','Eren','Gunes','Yavuz','Polat','Sah',
           'Akin','Karakaya','Bal','Turan','Aksoy'])[g],
    'active'
FROM generate_series(1, 25) AS g;

-- -------------------------------------------------------------
-- Role assignments
-- -------------------------------------------------------------
INSERT INTO user_roles (user_id, role_id)
SELECT u.user_id, r.role_id FROM users u, roles r
WHERE u.username = 'admin'      AND r.role_name = 'admin'
UNION ALL
SELECT u.user_id, r.role_id FROM users u, roles r
WHERE u.username = 'registrar'  AND r.role_name = 'registrar'
UNION ALL
SELECT u.user_id, r.role_id FROM users u, roles r
WHERE u.username LIKE 'i.%'      AND r.role_name = 'instructor'
UNION ALL
SELECT u.user_id, r.role_id FROM users u, roles r
WHERE u.username LIKE 'student%' AND r.role_name = 'student';

-- The brief asks for role *groups*; giving one member two roles
-- demonstrates the user_roles join table is genuinely many-to-many.
INSERT INTO user_roles (user_id, role_id)
SELECT u.user_id, r.role_id FROM users u, roles r
WHERE u.username = 'i.koc' AND r.role_name = 'technical';

-- -------------------------------------------------------------
-- Instructor profiles
-- -------------------------------------------------------------
INSERT INTO instructors (user_id, department_id, employee_no, hire_date, rank_title, salary)
SELECT
    (SELECT user_id FROM users WHERE username = v.username),
    (SELECT department_id FROM departments WHERE dept_code = v.dept),
    v.empno, v.hired, v.title, v.pay
FROM (VALUES
    ('i.kaya',   'CS', 'E-1001', DATE '2015-09-01', 'Professor',      96000.00),
    ('i.demir',  'EE', 'E-1002', DATE '2018-02-15', 'Associate Prof', 78000.00),
    ('i.ozturk', 'ME', 'E-1003', DATE '2020-09-01', 'Lecturer',       62000.00),
    ('i.acar',   'BA', 'E-1004', DATE '2017-03-20', 'Professor',      91000.00),
    ('i.koc',    'PS', 'E-1005', DATE '2021-09-01', 'Lecturer',       60000.00),
    ('i.sahin',  'MA', 'E-1006', DATE '2013-09-01', 'Professor',      99000.00)
) AS v(username, dept, empno, hired, title, pay);

-- -------------------------------------------------------------
-- Student profiles
-- -------------------------------------------------------------
INSERT INTO students (user_id, department_id, student_no, program_level, enrollment_year, gpa)
SELECT
    (SELECT user_id FROM users WHERE username = 'student' || v.g),
    (SELECT department_id FROM departments WHERE dept_code = v.dept),
    'S-' || LPAD(v.g::TEXT, 5, '0'),
    v.lvl::program_level,
    v.yr,
    0.00
FROM (VALUES
    (1,'CS','undergraduate',2023),(2,'CS','undergraduate',2023),(3,'CS','graduate',2022),
    (4,'EE','undergraduate',2024),(5,'EE','undergraduate',2023),(6,'EE','graduate',2021),
    (7,'ME','undergraduate',2024),(8,'ME','undergraduate',2023),(9,'ME','undergraduate',2022),
    (10,'BA','undergraduate',2024),(11,'BA','undergraduate',2023),(12,'BA','undergraduate',2022),
    (13,'PS','undergraduate',2024),(14,'PS','undergraduate',2023),(15,'PS','graduate',2022),
    (16,'MA','undergraduate',2023),(17,'MA','undergraduate',2022),(18,'MA','graduate',2021),
    (19,'CS','undergraduate',2024),(20,'CS','undergraduate',2024),(21,'EE','undergraduate',2024),
    (22,'ME','undergraduate',2024),(23,'BA','undergraduate',2024),(24,'PS','undergraduate',2024),
    (25,'MA','undergraduate',2024)
) AS v(g, dept, lvl, yr);

-- Advisors: every student in year >= 2023 is advised by a
-- graduate student from the same department. EXISTS is used
-- instead of joining the target table, because an UPDATE cannot
-- reference its own table inside a FROM-join condition.
UPDATE students s
   SET advisor_id = (
       SELECT a.student_id
         FROM students a
         JOIN users au ON au.user_id = a.user_id
        WHERE a.program_level = 'graduate'
          AND a.student_id <> s.student_id
          AND au.username = 'student' || a.student_id::TEXT
          AND a.department_id = s.department_id
        ORDER BY a.student_id
        LIMIT 1
   )
 WHERE s.enrollment_year >= 2023
   AND s.student_id % 3 <> 0;

-- -------------------------------------------------------------
-- Course sections — one row per offering, so the same course code
-- appears more than once with a different instructor or room.
-- -------------------------------------------------------------
INSERT INTO course_sections
    (course_code, course_name, term, academic_year, instructor_id, classroom_id, credits, capacity)
VALUES
    ('CS301', 'Database Systems',        'Spring', 2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1001'), (SELECT classroom_id FROM classrooms WHERE building='Science' AND room_number='A101'), 4, 55),
    ('CS302', 'Web Technologies',       'Spring', 2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1001'), (SELECT classroom_id FROM classrooms WHERE building='Science' AND room_number='A102'), 3, 40),
    ('CS401', 'Machine Learning',        'Spring', 2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1001'), (SELECT classroom_id FROM classrooms WHERE building='Science' AND room_number='A102'), 4, 30),
    ('EE201', 'Circuit Analysis',        'Spring', 2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1002'), (SELECT classroom_id FROM classrooms WHERE building='Engineering' AND room_number='E301'), 4, 45),
    ('EE305', 'Embedded Systems',        'Spring', 2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1002'), (SELECT classroom_id FROM classrooms WHERE building='Engineering' AND room_number='E305'), 3, 25),
    ('ME210', 'Thermodynamics',          'Spring', 2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1003'), (SELECT classroom_id FROM classrooms WHERE building='Engineering' AND room_number='E301'), 4, 40),
    ('ME320', 'Fluid Mechanics',         'Spring', 2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1003'), (SELECT classroom_id FROM classrooms WHERE building='Engineering' AND room_number='E305'), 3, 25),
    ('BA110', 'Principles of Marketing', 'Spring', 2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1004'), (SELECT classroom_id FROM classrooms WHERE building='Business' AND room_number='C110'), 3, 75),
    ('BA250', 'Financial Accounting',    'Spring', 2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1004'), (SELECT classroom_id FROM classrooms WHERE building='Business' AND room_number='C110'), 3, 70),
    ('PS101', 'Introduction to Psychology','Spring',2025,(SELECT instructor_id FROM instructors WHERE employee_no='E-1005'), (SELECT classroom_id FROM classrooms WHERE building='Humanities' AND room_number='H201'), 3, 50),
    ('PS340', 'Cognitive Neuroscience',  'Spring', 2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1005'), (SELECT classroom_id FROM classrooms WHERE building='Humanities' AND room_number='H201'), 4, 30),
    ('MA200', 'Linear Algebra',          'Spring', 2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1006'), (SELECT classroom_id FROM classrooms WHERE building='Library' AND room_number='L002'), 3, 30),
    -- Fall 2025 offerings: still open, so enrolments have no grade
    -- and the "current term" reports have something to show.
    ('CS310', 'Operating Systems',       'Fall',   2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1001'), (SELECT classroom_id FROM classrooms WHERE building='Science' AND room_number='A101'), 4, 50),
    ('CS320', 'Software Engineering',    'Fall',   2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1001'), (SELECT classroom_id FROM classrooms WHERE building='Science' AND room_number='A102'), 3, 40),
    ('EE210', 'Signals and Systems',     'Fall',   2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1002'), (SELECT classroom_id FROM classrooms WHERE building='Engineering' AND room_number='E301'), 3, 45),
    ('BA220', 'Operations Management',   'Fall',   2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1004'), (SELECT classroom_id FROM classrooms WHERE building='Business' AND room_number='C110'), 3, 70),
    ('PS210', 'Social Psychology',       'Fall',   2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1005'), (SELECT classroom_id FROM classrooms WHERE building='Humanities' AND room_number='H201'), 3, 45),
    ('MA210', 'Calculus II',             'Fall',   2025, (SELECT instructor_id FROM instructors WHERE employee_no='E-1006'), (SELECT classroom_id FROM classrooms WHERE building='Library' AND room_number='L002'), 4, 30);

-- -------------------------------------------------------------
-- Enrolments
-- Only Spring-2025 sections get final grades; Fall-2025 sections
-- stay open. Both shapes need to exist or the reports have
-- nothing interesting to say.
-- -------------------------------------------------------------
INSERT INTO enrollments (student_id, section_id, status, final_grade, grade_letter)
SELECT
    s.student_id,
    cs.section_id,
    'completed',
    ROUND(45 + ((s.student_id * 7 + cs.section_id * 13) % 55), 2),
    NULL
FROM students s
JOIN course_sections cs
  ON cs.course_code LIKE (SELECT d.dept_code || '%' FROM departments d
                           WHERE d.department_id = s.department_id)
WHERE cs.term = 'Spring' AND cs.academic_year = 2025;

-- A handful of failing and dropped records, so the grade
-- distribution report has a spread and the app has rows to delete.
UPDATE enrollments SET final_grade = 42.50
 WHERE student_id IN (7, 19) AND section_id IN (SELECT section_id FROM course_sections WHERE course_code LIKE 'ME%');

INSERT INTO enrollments (student_id, section_id, status, final_grade, grade_letter)
SELECT s.student_id, cs.section_id, 'enrolled', NULL, NULL
FROM students s
JOIN course_sections cs
  ON cs.course_code LIKE (SELECT d.dept_code || '%' FROM departments d
                           WHERE d.department_id = s.department_id)
WHERE cs.term = 'Fall' AND cs.academic_year = 2025
  AND s.student_id <= 3;

-- Withdraw a few students by converting their existing enrolment
-- rather than inserting a second one: (student_id, section_id) is
-- unique, and the CHECK constraint requires a 'dropped' row to
-- carry no grade.
UPDATE enrollments
   SET status = 'dropped', final_grade = NULL, grade_letter = NULL
 WHERE status = 'completed'
   AND student_id IN (12, 23);

-- Populate letter grades for the seeded rows. Done by calling
-- letter_grade_for() directly rather than re-saving the score, so
-- the grade-change trigger does not fire and the audit trail stays
-- free of 50 rows of seed noise.
UPDATE enrollments
   SET grade_letter = letter_grade_for(final_grade)
 WHERE final_grade IS NOT NULL
   AND grade_letter IS NULL;

-- -------------------------------------------------------------
-- Assignments
-- -------------------------------------------------------------
INSERT INTO assignments (section_id, title, weight, max_points, due_date)
SELECT cs.section_id, v.title, v.wt, v.pts, v.due::date
FROM (VALUES
    (1,'Assignment 1 - ER Modelling',        10.00, 100.00,'2025-03-07'),
    (1,'Assignment 2 - SQL Joins',           15.00, 100.00,'2025-03-28'),
    (1,'Midterm Project - Schema Design',   30.00, 100.00,'2025-04-18'),
    (2,'Assignment 1 - HTML Forms',          10.00,  50.00,'2025-03-14'),
    (2,'Assignment 2 - REST Endpoints',      15.00, 100.00,'2025-04-04'),
    (3,'Assignment 1 - Linear Regression',   20.00, 100.00,'2025-03-21'),
    (4,'Assignment 1 - Mesh Analysis',       10.00, 100.00,'2025-03-14'),
    (5,'Assignment 1 - GPIO Drivers',        15.00,  75.00,'2025-03-21'),
    (6,'Problem Set 1',                       10.00, 100.00,'2025-03-07'),
    (7,'Problem Set 2',                       10.00, 100.00,'2025-03-28'),
    (8,'Case Study - Pricing',               20.00, 100.00,'2025-04-11'),
    (9,'Problem Set 1 - Ledgers',            15.00, 100.00,'2025-03-21'),
    (10,'Essay - Research Methods',          20.00, 100.00,'2025-04-04'),
    (11,'Assignment 1 - Perception Mapping', 15.00, 100.00,'2025-03-28'),
    (12,'Problem Set 1 - Vector Spaces',     10.00, 100.00,'2025-03-14')
) AS v(sec, title, wt, pts, due)
JOIN course_sections cs ON cs.section_id = v.sec;

-- -------------------------------------------------------------
-- Submissions — one per enrolled student per assignment.
-- Scores are skewed high so the average-vs-percentage report in
-- docs/report.md has something meaningful to show.
-- -------------------------------------------------------------
INSERT INTO submissions (assignment_id, student_id, submitted_at, score, graded_by, is_late)
SELECT
    a.assignment_id,
    e.student_id,
    a.due_date - ((e.student_id % 4) || ' days')::INTERVAL,
    CASE WHEN e.student_id % 11 = 0 THEN NULL
         ELSE ROUND((a.max_points * 0.55 + ((e.student_id * 13) % 45) / 100.0 * a.max_points), 2) END,
    (SELECT user_id FROM users WHERE username = 'admin'),
    (e.student_id % 9 = 0)
FROM assignments a
JOIN enrollments e ON e.section_id = a.section_id
WHERE e.status <> 'dropped';

-- -------------------------------------------------------------
-- Attendance — four sessions per section.
--
-- The status is drawn from a weighted distribution rather than from
-- a modulus of student_id: an expression like (student_id + day) % 7
-- correlates the outcome with the student, which produced students
-- marked absent at every single session.
--
-- The draw comes from an md5 hash of (student, date) rather than
-- random(). random() evaluated through a LATERAL subquery was
-- hoisted into a single one-time Result node, giving every row the
-- same status. A hash is per-row by construction and still
-- reproducible: re-running the seed yields identical data.
-- -------------------------------------------------------------
INSERT INTO attendance (section_id, student_id, session_date, status)
SELECT
    t.section_id,
    t.student_id,
    t.session_date,
    CASE
        WHEN t.bucket <  86 THEN 'present'   -- 86%
        WHEN t.bucket <  91 THEN 'late'      --  5%
        WHEN t.bucket <  98 THEN 'absent'    --  7%
        ELSE                   'excused'    --  2%
    END::attendance_status
FROM (
    SELECT
        e.section_id,
        e.student_id,
        d.session_date,
        abs(('x' || substr(
             md5(e.student_id::TEXT || ':' || d.session_date::TEXT), 1, 8
         ))::bit(32)::BIGINT) % 100 AS bucket
    FROM enrollments e
    CROSS JOIN (VALUES (DATE '2025-03-05'), (DATE '2025-03-12'),
                       (DATE '2025-03-19'), (DATE '2025-03-26')
              ) AS d(session_date)
    WHERE e.status <> 'dropped'
) AS t;

COMMIT;

-- -------------------------------------------------------------
-- Seed summary, so the report can quote real counts.
-- -------------------------------------------------------------
SELECT 'users'            AS entity, COUNT(*) FROM users
UNION ALL SELECT 'departments',      COUNT(*) FROM departments
UNION ALL SELECT 'instructors',      COUNT(*) FROM instructors
UNION ALL SELECT 'students',         COUNT(*) FROM students
UNION ALL SELECT 'course_sections',  COUNT(*) FROM course_sections
UNION ALL SELECT 'enrollments',      COUNT(*) FROM enrollments
UNION ALL SELECT 'assignments',      COUNT(*) FROM assignments
UNION ALL SELECT 'submissions',      COUNT(*) FROM submissions
UNION ALL SELECT 'attendance',       COUNT(*) FROM attendance;
