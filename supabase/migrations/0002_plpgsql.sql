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
-- Eight blocks: 4 functions, 1 procedure, 3 triggers. The trigger
-- blocks are 5, 6 and 7; blocks 1, 2, 3 and 8 are plain functions.
--
-- Counting each trigger's function as a function of its own, that is
-- 7 functions and 1 procedure. This line previously said "Seven
-- blocks: 2 functions, 1 procedure, 4 triggers", which matched
-- neither the number of blocks nor the breakdown.
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