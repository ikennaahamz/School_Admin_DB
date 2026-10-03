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