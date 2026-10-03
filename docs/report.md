# School Administration System — Project Report

**Course:** CMPE344 Database Management Systems and Programming II
**Institution:** Cyprus International University
**Instructor:** Prof. Dr. Melike Şah Direkoğlu
**Weight:** 15%

**Stack:** PostgreSQL 15 on Supabase · PL/pgSQL · Streamlit (Python)
**Repository:** `<REPOSITORY_URL>`

---

## 1. Introduction

### 1.1 Purpose

The system stores and manages the records of a university faculty:
who the students are, which courses are offered, who teaches them, who
enrolled and what they were graded. It replaces the spreadsheet-plus-
paper-trail arrangement most faculties still use, and it produces the
statistics a course coordinator or head of department actually needs
before deciding anything.

### 1.2 Scope

In scope: student, instructor and course records; enrolment with
capacity rules; grading with a derived letter and an audit trail;
assignments, submissions and attendance; role-based authentication;
and eight management reports.

Out of scope: fee payment, timetabling optimisation, accommodation,
and library lending. Each would be a natural extension but none is
needed to demonstrate the requirements.

### 1.3 Assumptions

These shaped the design, and each is a decision that could reasonably
have gone another way.

1. **A person has one login but at most one role-specific profile.**
   Someone who is both a lecturer and a research student has one
   account in `users` and two profile rows. Splitting the profiles out
   of `users` avoids a `users` table that is mostly NULL columns.

2. **Roles are many-to-many, not one-per-user.** The brief says "user
   groups", plural. One member of staff is seeded with two roles
   (`instructor` + `technical`) to exercise this. A single `role`
   column on `users` could not represent it.

3. **A course section is one offering, not one course.** The same
   course code runs in Spring and in Fall with different staff and
   rooms, so `course_sections` carries the term, the year, the room
   and the instructor. A static `courses` table could not answer
   "who taught what last term".

4. **The pass mark is 70/100.** This drives the letter-grade bands
   (AA ≥ 93, BB ≥ 87, CC ≥ 80, DD ≥ 70, FF below). A different
   convention would change only `letter_grade_for`.

5. **GPA is on a 4.00 scale**, computed as the credit-weighted mean of
   final marks divided by 25. A student averaging 100% earns 4.00.

6. **Deleting a user is not an operation.** Users are referenced by
   profiles, submissions and the audit trail. Accounts are suspended
   instead, which preserves the records. Students and instructors can
   be deleted, and their dependent rows cascade deliberately.

7. **Business rules live in the database.** Capacity, credit load,
   grade derivation and audit are PL/pgSQL, not Python, so they hold
   regardless of which client writes the row.

### 1.4 Project members

| Student number | Name | Contribution |
|---|---|---|
| `<NUMBER>` | `<NAME>` | `<ROLE>` |
| `<NUMBER>` | `<NAME>` | `<ROLE>` |
| `<NUMBER>` | `<NAME>` | `<ROLE>` |

> Replace this table before submission.

---

## 2. Database

### 2.1 Entity–Relationship diagram

The full diagram, with every column, key and constraint, is in
[`docs/erd.md`](erd.md). It is a Mermaid source block, so GitHub
renders it and it cannot go stale relative to the schema.

The relationship summary:

| From | To | Cardinality |
|---|---|---|
| `users` | `user_roles` | 1 : M |
| `roles` | `user_roles` | 1 : M |
| `users` | `students` / `instructors` | 1 : 0..1 |
| `departments` | `students` / `instructors` | 1 : M |
| `students` | `students` (advisor) | 1 : 0..1 |
| `students` | `enrollments` | 1 : M |
| `course_sections` | `enrollments` | 1 : M |
| `instructors` | `course_sections` | 1 : M |
| `classrooms` | `course_sections` | 1 : M |
| `course_sections` | `assignments` / `attendance` | 1 : M |
| `assignments` | `submissions` | 1 : M |
| `students` | `submissions` | 1 : M |
| `enrollments` | `grade_audit` | 1 : M (no FK) |

### 2.2 Relational model

Thirteen tables. The brief requires six; the extra seven exist so the
analytical queries have real structure to aggregate over.

| Table | Purpose | Key columns |
|---|---|---|
| `users` | login and authentication | `username` UK, `password_hash`, `status` |
| `roles` | the user groups | `role_name` UK, `access_level` 1–5 |
| `user_roles` | user ↔ role, many-to-many | PK (`user_id`, `role_id`) |
| `departments` | faculties | `dept_code` UK, `annual_budget` |
| `classrooms` | rooms and their capacity | UK (`building`, `room_number`) |
| `instructors` | staff profile | `user_id` UK, `employee_no` UK |
| `students` | student profile | `user_id` UK, `student_no` UK, self-FK `advisor_id` |
| `course_sections` | one offering of a course | UK (`course_code`, `term`, `academic_year`, `classroom_id`) |
| `enrollments` | the student ↔ section fact table | UK (`student_id`, `section_id`) |
| `assignments` | work set per section | `weight`, `max_points`, `due_date` |
| `submissions` | a student's work | UK (`assignment_id`, `student_id`) |
| `attendance` | one mark per session | UK (`section_id`, `student_id`, `session_date`) |
| `grade_audit` | append-only grade history | no FKs, deliberately |

### 2.3 Data Definition Language (DDL)

Full source: [`supabase/migrations/0001_schema.sql`](../supabase/migrations/0001_schema.sql)

#### Bounded value sets

Rather than `VARCHAR` plus `CHECK`, PostgreSQL `ENUM` types make an
illegal value impossible to store, and they read cleanly in `psql`.

```sql
CREATE TYPE user_status       AS ENUM ('active', 'suspended', 'pending');
CREATE TYPE program_level     AS ENUM ('undergraduate', 'graduate', 'phd');
CREATE TYPE term_name         AS ENUM ('Fall', 'Spring', 'Summer');
CREATE TYPE enroll_status     AS ENUM ('enrolled', 'dropped', 'completed', 'failed');
CREATE TYPE attendance_status AS ENUM ('present', 'absent', 'late', 'excused');
CREATE TYPE grade_letter      AS ENUM ('AA', 'BB', 'CC', 'DD', 'FF', 'NA');
```

#### Money is NUMERIC, never FLOAT

```sql
annual_budget NUMERIC(14,2) NOT NULL DEFAULT 0
```

Binary floating point cannot represent 0.1 exactly, so summing
budgets in `FLOAT` accumulates cent-level error. `NUMERIC` is exact
decimal, which is what an accounting figure requires.

#### Constraints that encode rules, not just presence

`NOT NULL` says a value is required. These say something about the
world:

```sql
-- A closed enrolment must carry a grade; an open one must not.
CONSTRAINT ck_enrollments_final CHECK (
    (status IN ('completed', 'failed') AND final_grade IS NOT NULL)
 OR (status IN ('enrolled', 'dropped')  AND final_grade IS NULL)
)

-- The trigger must never be able to overfill a section.
CONSTRAINT ck_sections_enrolled CHECK (
    enrolled_count >= 0 AND enrolled_count <= capacity
)

CONSTRAINT ck_students_gpa       CHECK (gpa >= 0.00 AND gpa <= 4.00),
CONSTRAINT ck_assignments_weight CHECK (weight > 0 AND weight <= 100),
CONSTRAINT ck_users_username     CHECK (username ~ '^[a-z0-9_.]{3,50}$'),
CONSTRAINT ck_students_advise    CHECK (advisor_id IS NULL OR advisor_id <> student_id)
```

#### Referential integrity

`ON DELETE` behaviour is chosen per relationship, not defaulted. The
distinction that matters:

```sql
-- A student profile is meaningless without its login:
FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE

-- But deleting a staff member must not delete real teaching history:
FOREIGN KEY (instructor_id) REFERENCES instructors(instructor_id)
    REFERENCES ... ON DELETE SET NULL

-- And a role still held by somebody cannot be deleted underneath them:
FOREIGN KEY (role_id) REFERENCES roles(role_id) ON DELETE RESTRICT
```

A self-referencing foreign key for the student advisor, added after the
table exists:

```sql
ALTER TABLE students
    ADD CONSTRAINT fk_students_advisor
    FOREIGN KEY (advisor_id) REFERENCES students(student_id) ON DELETE SET NULL;
```

### 2.4 Data Manipulation Language (DML)

Full source: [`supabase/migrations/0003_seed.sql`](../supabase/migrations/0003_seed.sql)

The seed loads 33 users, 25 students, 6 instructors, 18 course
sections, 57 enrolments, 15 assignments, 62 submissions and 212
attendance records — enough for the reports to show a distribution
rather than a handful of rows.

Representative statements:

```sql
-- Insert a batch, generated rather than hand-listed
INSERT INTO users (username, email, password_hash, first_name, last_name, status)
SELECT 'student' || g, 'student' || g || '@school.edu', %s,
       (ARRAY['Ali','Ayse', ...])[g],
       (ARRAY['Yilmaz','Kaya', ...])[g], 'active'
FROM generate_series(1, 25) AS g;

-- Delete, showing what the cascades will remove
DELETE FROM course_sections WHERE section_id = ?;   -- -> enrolments,
                                                    --    assignments,
                                                    --    submissions,
                                                    --    attendance

-- Update, guarded by a constraint
UPDATE enrollments
   SET status = 'dropped', final_grade = NULL, grade_letter = NULL
 WHERE student_id IN (12, 23);

-- An upsert, so marking the same session twice corrects rather than fails
INSERT INTO attendance (section_id, student_id, session_date, status)
VALUES (%s, %s, %s::date, %s::attendance_status)
ON CONFLICT (section_id, student_id, session_date)
DO UPDATE SET status = EXCLUDED.status;
```

**Passwords.** Every account's `password_hash` is a real bcrypt cost-12
digest. No plaintext password exists anywhere in the repository,
including the seed file.

### 2.5 Note on PL/SQL vs PL/pgSQL

The brief asks for PL/SQL blocks and, separately, lists Supabase as an
allowed platform. These cannot both hold: Supabase runs PostgreSQL,
which has no PL/SQL, an Oracle language.

PL/pgSQL is PostgreSQL's direct equivalent. It covers every construct
the brief names — procedures, functions, triggers — with the same
semantics for the features used here:

| Brief asks for | Provided in PL/pgSQL |
|---|---|
| procedure | `CREATE PROCEDURE ... LANGUAGE plpgsql` |
| function returning a value | `CREATE FUNCTION ... RETURNS ... ` |
| function returning a table | `RETURNS TABLE (...)` with `RETURN QUERY` |
| `BEFORE`/`AFTER` row trigger | `CREATE TRIGGER ... FOR EACH ROW` |
| explicit exception | `RAISE EXCEPTION` / `EXCEPTION WHEN` |
| `%ROWTYPE`-style variables | `RECORD` |
| cursor loops | `FOR ... IN SELECT`, and `RETURN QUERY` |

The blocks below are written so the substitution is visible: same
structure, same control flow, PL/pgSQL keywords. Approval for this
substitution is being sought from the course coordinator.

> If PL/SQL is mandatory, the alternative is to keep Supabase as the
> live application database and add a second Oracle instance purely to
> satisfy the literal wording. That doubles the deployment and the
> DDL maintenance for a naming requirement, which is why it is not the
> default here.

### 2.6 Procedural blocks

Eight blocks are provided against the required minimum of five.
Full source: [`supabase/migrations/0002_plpgsql.sql`](../supabase/migrations/0002_plpgsql.sql)

| # | Block | Type | Purpose |
|---|---|---|---|
| 1 | `letter_grade_for(score)` | function | numeric mark → letter |
| 2 | `calculate_student_gpa(id)` | function | credit-weighted GPA, recomputed **and stored** |
| 3 | `section_fill_ratio(id)` | function | occupancy as a percentage |
| 4 | `enroll_student(student, section)` | procedure | capacity, duplicate and credit-load rules |
| 5 | `trg_sections_enrolled_count` | 3 triggers | keep `enrolled_count` truthful |
| 6 | `trg_set_updated_at` | 2 triggers | stamp `updated_at` on any write |
| 7 | `trg_audit_grade_change` | trigger | derive letter, set status, audit, recompute GPA |
| 8 | `get_section_roster(id)` | function | class list as a set of rows |

#### Block 2 — `calculate_student_gpa`

```sql
CREATE OR REPLACE FUNCTION calculate_student_gpa(p_student_id INTEGER)
RETURNS NUMERIC
LANGUAGE plpgsql
VOLATILE
AS $$
DECLARE v_gpa NUMERIC;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM students WHERE student_id = p_student_id) THEN
        RAISE EXCEPTION 'Student % does not exist', p_student_id
            USING ERRCODE = 'no_data_found';
    END IF;

    SELECT COALESCE(
        ROUND(SUM(e.final_grade * cs.credits)
              / NULLIF(SUM(cs.credits), 0) / 25.0, 2), 0.00)
      INTO v_gpa
      FROM enrollments e
      JOIN course_sections cs ON cs.section_id = e.section_id
     WHERE e.student_id = p_student_id
       AND e.status = 'completed'
       AND e.final_grade IS NOT NULL;

    UPDATE students
       SET gpa = GREATEST(LEAST(v_gpa, 4.00), 0.00), updated_at = NOW()
     WHERE student_id = p_student_id;

    RETURN v_gpa;
END;
$$;
```

Three details worth naming. The existence check stops an unknown id
silently producing a 0.00 GPA. `NULLIF(SUM(credits), 0)` prevents a
division by zero for a student with no graded courses. And
`GREATEST(LEAST(...))` clamps the result, so a malformed grade cannot
push GPA outside the 0.00–4.00 range the `CHECK` constraint allows.

**Output** — recomputed and persisted:

```
+------------+------------+----------+
| student_id | before_gpa | returned |
+------------+------------+----------+
|          1 |       0.00 |     3.12 |
|          2 |       0.00 |     3.40 |
|          6 |       0.00 |     3.58 |
+------------+------------+----------+
```

#### Block 4 — `enroll_student`

```sql
CREATE OR REPLACE PROCEDURE enroll_student(p_student_id INTEGER, p_section_id INTEGER)
LANGUAGE plpgsql
AS $$
DECLARE
    v_capacity INTEGER; v_enrolled INTEGER; v_credits SMALLINT;
    v_completed INTEGER; v_needed    INTEGER;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM students WHERE student_id = p_student_id) THEN
        RAISE EXCEPTION 'Student % does not exist', p_student_id;
    END IF;

    SELECT cs.capacity, cs.enrolled_count, cs.credits
      INTO v_capacity, v_enrolled, v_credits
      FROM course_sections cs
     WHERE cs.section_id = p_section_id
       FOR UPDATE;              -- row lock, see below
    ...
    IF EXISTS (SELECT 1 FROM enrollments
                WHERE student_id = p_student_id AND section_id = p_section_id
                  AND status <> 'dropped') THEN
        RAISE EXCEPTION 'Student % is already enrolled in section %',
            p_student_id, p_section_id;
    END IF;

    IF v_enrolled >= v_capacity THEN
        RAISE EXCEPTION 'Section % is full (% of % seats taken)',
            p_section_id, v_enrolled, v_capacity;
    END IF;

    -- Re-enrolling after a withdrawal revives the existing row rather
    -- than inserting a second one: (student_id, section_id) is unique.
    INSERT INTO enrollments (student_id, section_id, status)
    VALUES (p_student_id, p_section_id, 'enrolled')
    ON CONFLICT (student_id, section_id) DO UPDATE
        SET status = 'enrolled', enrolled_at = NOW(),
            final_grade = NULL, grade_letter = NULL;
    ...
END;
$$;
```

**Why `FOR UPDATE` matters.** Without the row lock, two simultaneous
enrolments both read `enrolled_count = 29` on a 30-seat section, both
pass the capacity test, and both insert. The section ends up with 31
students in 30 seats — and `ck_sections_enrolled` then *rejects* the
counter update, leaving `enrolled_count` permanently wrong. The lock
is what makes the capacity rule safe under concurrency rather than only
in a single-user test.

**Output** — the refusal paths, then success:

```
### PL/pgSQL BLOCK 4 - enroll_student, refusal paths
  ERROR:  Student 1 is already enrolled in section 1
  CONTEXT:  PL/pgSQL function enroll_student(integer,integer) line 30 at RAISE
  ERROR:  Student 999999 does not exist
  CONTEXT:  PL/pgSQL function enroll_student(integer,integer) line 10 at RAISE

### PL/pgSQL BLOCK 4 - enroll_student, success + counter trigger
  NOTICE:  Enrolled student 4 in section 3 (6 of 30 seats)
+------------+----------------+
| section_id | enrolled_count |
+------------+----------------+
|          3 |              6 |
+------------+----------------+
```

The counter moved from 5 to 6 with no statement in the application
touching it — the trigger did it.

#### Block 7 — `trg_audit_grade_change`

```sql
CREATE OR REPLACE FUNCTION trg_audit_grade_change()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.final_grade IS NOT DISTINCT FROM OLD.final_grade THEN
        RETURN NEW;                              -- nothing material changed
    END IF;

    -- Clearing a grade is a withdrawal, not a fail.
    IF NEW.final_grade IS NULL THEN
        NEW.grade_letter := 'NA';
        RETURN NEW;
    END IF;

    NEW.grade_letter := letter_grade_for(NEW.final_grade);
    NEW.status := CASE WHEN NEW.final_grade >= 60 THEN 'completed'
                       ELSE 'failed' END;

    INSERT INTO grade_audit (enrollment_id, student_id, section_id,
                             old_grade, new_grade, old_letter, new_letter,
                             changed_by)
    VALUES (NEW.enrollment_id, NEW.student_id, NEW.section_id,
            OLD.final_grade, NEW.final_grade,
            OLD.grade_letter, NEW.grade_letter,
            COALESCE(current_setting('app.user', TRUE), CURRENT_USER));

    PERFORM calculate_student_gpa(NEW.student_id);
    RETURN NEW;
END;
$$;
```

**Why `grade_audit` has no foreign keys.** If it did, deleting a
student would cascade away the evidence that their grade was ever
changed. An audit trail that can be deleted along with its subject is
not an audit trail. The table stores bare IDs and is append-only.

**Why the `app.user` setting.** `changed_by` reads a session variable
the application sets per connection, so the audit records the person
who entered the grade, not merely the database role that executed the
statement:

```sql
BEGIN;
  SET LOCAL "app.user" = 'i.kaya';
  UPDATE enrollments SET final_grade = 95.50 WHERE enrollment_id = 1;
COMMIT;
```

**Output** — one statement produced four effects:

```
+-------------+--------------+-----------+
| final_grade | grade_letter |  status   |
+-------------+--------------+-----------+
|       95.50 | AA           | completed |
+-------------+--------------+-----------+

+-----------+-----------+------------+------------+------------+----------+
| old_grade | new_grade | old_letter | new_letter | changed_by | position |
+-----------+-----------+------------+------------+------------+----------+
|     79.00 |     95.50 | DD         | AA         | i.kaya     |        1 |
+-----------+-----------+------------+------------+------------+----------+
```

The letter was derived, the status was set, the audit row recorded the
actor, and the student's GPA was recomputed.

#### Block 5 — keeping `enrolled_count` truthful

```sql
CREATE OR REPLACE FUNCTION trg_sections_enrolled_count()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE v_old INTEGER; v_new INTEGER;
BEGIN
    v_old := CASE WHEN TG_OP IN ('UPDATE','DELETE') THEN OLD.section_id END;
    v_new := CASE WHEN TG_OP IN ('UPDATE','INSERT') THEN NEW.section_id END;

    -- A moved enrolment must decrement the old section too.
    IF v_old IS DISTINCT FROM v_new THEN
        UPDATE course_sections cs
           SET enrolled_count = (SELECT COUNT(*) FROM enrollments e
                                 WHERE e.section_id = cs.section_id
                                   AND e.status <> 'dropped')
         WHERE cs.section_id IN (v_old, v_new);
    ELSE
        ...
    END IF;
    RETURN NULL;
END;
$$;
```

`enrolled_count` is denormalised on purpose: the reports filter and
sort on it constantly, and computing it on demand means a correlated
subquery per row. A denormalised counter is only safe if something
guarantees it, and that something is the database, not the application.

**Verification** — stored counter against ground truth, all sections:

```
### PL/pgSQL BLOCK 5 - enrolled_count agrees with reality
+------------+--------+--------+
| section_id | stored | actual |
+------------+--------+--------+
(0 rows)
```

Zero rows means every stored counter equals the actual count.

#### Block 8 — `get_section_roster`

```sql
CREATE OR REPLACE FUNCTION get_section_roster(p_section_id INTEGER)
RETURNS TABLE (student_no VARCHAR, student_name TEXT, status enroll_status,
               final_grade NUMERIC, grade_letter grade_letter)
LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN QUERY
    SELECT s.student_no, u.first_name || ' ' || u.last_name,
           e.status, e.final_grade, e.grade_letter
      FROM enrollments e
      JOIN students s ON s.student_id = e.student_id
      JOIN users u    ON u.user_id    = s.user_id
     WHERE e.section_id = p_section_id
     ORDER BY u.last_name, u.first_name;
END;
$$;
```

`RETURNS TABLE` is how one round trip returns a whole result set. The
reports screen calls this directly.

#### Verification of all blocks

[`supabase/migrations/verify_plpgsql.sql`](../supabase/migrations/verify_plpgsql.sql)
contains 14 checks covering both the success and the refusal path of
every block, plus the boundary values of `letter_grade_for`:

```
score 100 -> AA     score 93 -> AA     score 87 -> BB
score  80 -> CC     score 70 -> DD     score 69.99 -> FF
```

### 2.7 Constraints refusing bad data

Each of these statements is expected to be **rejected**:

```
### Constraint enforcement (each of these must be refused)
  ERROR:  new row for relation "students" violates check constraint "ck_students_gpa"
  DETAIL:  Failing row contains (1, 9, 1, 3, S-00001, undergraduate, 2023, 9.99, ...)

  ERROR:  new row for relation "assignments" violates check constraint "ck_assignments_weight"
  DETAIL:  Failing row contains (16, 1, Bad, 150.00, 100.00, ...)

  ERROR:  duplicate key value violates unique constraint "uq_users_username"
  DETAIL:  Key (username)=(admin) already exists.

  ERROR:  new row for relation "course_sections" violates check constraint "ck_sections_enrolled"
  DETAIL:  Failing row contains (1, CS301, Database Systems, Spring, 2025, ..., 56).
```

The last one is the important one: the `CHECK` constraint is a
backstop that holds even if the trigger is disabled, because it is a
declarative rule rather than procedural code.

### 2.8 Analytical queries

Eight queries against the brief's minimum of five–seven. Full source
with the original comments: [`supabase/migrations/0004_queries.sql`](../supabase/migrations/0004_queries.sql).
The same SQL runs in the application's Reports screen, so the output
below is the output the deployed app produces.

Between them they use: inner and outer joins, correlated and
uncorrelated subqueries, `GROUP BY`, `HAVING`, `ORDER BY`, `SUM`,
`AVG`, `COUNT`, `COUNT(DISTINCT)`, `MIN`, `MAX`, `STDDEV_SAMP`,
CTEs, window functions with `PARTITION BY`, `CASE` expressions,
`FILTER` clauses, and `NULLIF`-guarded division.

#### Query 1 — Enrolment pressure per department
*4-table join, `GROUP BY`, `HAVING`, percentage arithmetic*

```
+-------------------------+----------+------------+--------------+----------------------+
|        dept_name        | students | enrolments | avg_capacity | seat_utilisation_pct |
+-------------------------+----------+------------+--------------+----------------------+
| Mathematics             |        4 |          4 |         30.0 |                 13.3 |
| Mechanical Engineering  |        4 |          8 |         32.5 |                 12.3 |
| Electrical Engineering  |        4 |          8 |         34.5 |                 11.6 |
| Computer Science        |        5 |         21 |         42.6 |                 10.9 |
| Psychology              |        4 |          8 |         40.0 |                 10.0 |
| Business Administration |        2 |          4 |         72.5 |                  2.8 |
+-------------------------+----------+------------+--------------+----------------------+
```

Business reading: Computer Science carries 21 of the 53 enrolments on
the narrowest average capacity, while Business Administration runs at
2.8% utilisation in rooms built for 70. BA is running large lecture
theatre sessions for two students.

#### Query 2 — Course difficulty
*`AVG`, `MIN`, `MAX`, `STDDEV_SAMP`, uncorrelated subquery*

```
+-------------+--------+----------+--------+---------------+
| course_code | graded | avg_mark | spread | vs_school_avg |
+-------------+--------+----------+--------+---------------+
| PS101       |      4 |    56.75 |   9.43 |        -14.06 |
| BA110       |      2 |    57.50 |   4.95 |        -13.31 |
| MA200       |      4 |    59.00 |  22.88 |        -11.81 |
| ME210       |      4 |    61.13 |  14.69 |         -9.68 |
| PS340       |      4 |    69.75 |   9.43 |         -1.06 |
| CS401       |      5 |    70.00 |  22.75 |         -0.81 |
| BA250       |      2 |    70.50 |   4.95 |         -0.31 |
| ME320       |      4 |    70.88 |  20.48 |          0.07 |
+-------------+--------+----------+--------+---------------+
```

`spread` separates the two explanations for a low average. PS101 is
9.43 points wide — a uniformly weaker cohort. MA200 is 22.88 wide —
the same course producing very different results depending on who
sat it, which points at assessment design rather than ability.

#### Query 3 — Student standing, top 5 by GPA
*CTE, window function `RANK` with `PARTITION BY`, derived table*

```sql
WITH student_results AS (
    SELECT s.student_id, s.student_no, d.dept_name,
           COUNT(e.enrollment_id) AS courses_taken,
           SUM(cs.credits)       AS credit_hours,
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
SELECT * FROM ranked WHERE position <= 5 ORDER BY position;
```

```
+----------+---------------+------------+------------------------+---------------+--------------+----------+--------------+
| position | dept_position | student_no |       dept_name        | courses_taken | credit_hours | avg_mark | computed_gpa |
+----------+---------------+------------+------------------------+---------------+--------------+----------+--------------+
|        1 |             1 | S-00016    | Mathematics            |             1 |            3 |    93.00 |         3.72 |
|        2 |             1 | S-00006    | Electrical Engineering |             2 |            7 |    90.50 |         3.58 |
|        3 |             1 | S-00002    | Computer Science       |             3 |           11 |    85.00 |         3.40 |
|        4 |             2 | S-00021    | Electrical Engineering |             2 |            7 |    85.50 |         3.38 |
|        5 |             3 | S-00005    | Electrical Engineering |             2 |            7 |    83.50 |         3.30 |
+----------+---------------+------------+------------------------+---------------+--------------+----------+--------------+
```

`position` ranks across the faculty, `dept_position` within a
department. The two together stop a strong department from looking
uniformly strong: S-00005 is fifth overall but only third in Electrical
Engineering, because two of its departmental peers are ahead of it.

#### Query 4 — Teaching load per instructor
*`LEFT JOIN`, `COALESCE`, `NULLIF` division guard*

```
+--------------+-------------------------+----------+-----------------+-----------------+
|  instructor  |        dept_name        | sections | students_taught | utilisation_pct |
+--------------+-------------------------+----------+-----------------+-----------------+
| Mert Kaya    | Computer Science        |        5 |              22 |            10.2 |
| Aylin Koc    | Psychology              |        3 |               8 |             6.4 |
| Burak Ozturk | Mechanical Engineering  |        2 |               8 |            12.3 |
| Seda Demir   | Electrical Engineering  |        3 |               8 |             7.0 |
| Cem Sahin    | Mathematics             |        2 |               4 |             6.7 |
| Deniz Acar   | Business Administration |        3 |               4 |             1.9 |
+--------------+-------------------------+----------+-----------------+-----------------+
```

`LEFT JOIN` matters here: an instructor with no sections still appears
with zero rather than vanishing, which is what makes "is anyone
carrying nothing?" answerable.

#### Query 5 — Grade distribution
*`CASE`, CTE, window function `SUM() OVER ()`*

```
+--------------------------+---------+---------------+
|           band           | records | pct_of_cohort |
+--------------------------+---------+---------------+
| A  (93-100) excellent    |       4 |           8.5 |
| B  (87-92)  good         |       6 |          12.8 |
| C  (80-86)  satisfactory |       6 |          12.8 |
| D  (70-79)  pass         |      10 |          21.3 |
| E  (50-69)  weak         |      15 |          31.9 |
| F  (0-49)   fail         |       6 |          12.8 |
+--------------------------+---------+---------------+
```

`SUM(COUNT(*)) OVER ()` is the running total of the grouped counts,
which gives each band its share of the cohort without a second pass
over the data.

#### Query 6 — Departments beating the school average
*uncorrelated subquery inside `HAVING`*

```
+------------------------+----------+----------+------------+
|       dept_name        | students | dept_avg | school_avg |
+------------------------+----------+----------+------------+
| Electrical Engineering |        4 |    84.00 |      70.81 |
| Computer Science       |        5 |    75.33 |      70.81 |
+------------------------+----------+----------+------------+
```

The subquery appears three times — once for display, once in `HAVING`,
once for the variance. It is uncorrelated, so PostgreSQL evaluates it
once rather than per group.

#### Query 7 — Assignment submission rates
*`LEFT JOIN`, `FILTER`, `NULLIF` guard*

```
+-------------+-------------------------------+-----------+----------+---------------------+------+
| course_code |             title             | submitted | enrolled | submission_rate_pct | late |
+-------------+-------------------------------+-----------+----------+---------------------+------+
| MA200       | Problem Set 1 - Vector Spaces |         4 |        4 |               100.0 |    1 |
| CS301       | Assignment 1 - ER Modelling   |         5 |        5 |               100.0 |    0 |
| ME210       | Problem Set 1                 |         4 |        4 |               100.0 |    1 |
| EE305       | Assignment 1 - GPIO Drivers   |         4 |        4 |               100.0 |    0 |
| CS302       | Assignment 1 - HTML Forms     |         5 |        5 |               100.0 |    0 |
| ME320       | Problem Set 2                 |         4 |        4 |               100.0 |    1 |
+-------------+-------------------------------+-----------+----------+---------------------+------+
```

An `INNER JOIN` would hide any assessment nobody submitted, which is
exactly the one a coordinator needs to see. `COUNT(e.student_id)`
against `COUNT(sm.submission_id)` is what makes the denominator the
enrolled count rather than the submission count.

#### Query 8 — Attendance risk
*5-table join, `HAVING` on an aggregate, `FILTER`*

```
+------------+--------------+-------------------------+----------+----------+------------------+
| student_no | student_name |        dept_name        | absences | sessions | absence_rate_pct |
+------------+--------------+-------------------------+----------+----------+------------------+
| S-00002    | Ayse Kaya    | Computer Science        |        5 |       20 |             25.0 |
| S-00011    | Kerem Koc    | Business Administration |        2 |        8 |             25.0 |
| S-00014    | Melis Duman  | Psychology              |        2 |        8 |             25.0 |
| S-00015    | Onur Tekin   | Psychology              |        2 |        8 |             25.0 |
+------------+--------------+-------------------------+----------+----------+------------------+
```

`HAVING COUNT(*) FILTER (WHERE status = 'absent') >= 2` filters a
grouped result back down to individuals, which `WHERE` could not do —
the count does not exist until after grouping.

### 2.9 Summary of what each requirement asked for

| Requirement | Delivered |
|---|---|
| At least 6 tables | 13 |
| User table for login | `users`, bcrypt digests, verified login |
| User groups with distinct roles | `roles` + `user_roles`, 5 roles, 5 access levels |
| Foreign keys relating tables | 18 foreign keys, per-relationship `ON DELETE` |
| Constraints, defaults, PKs | 62 constraints, 6 ENUM types, defaults on all timestamps |
| Insert / delete / update queries | throughout `0003_seed.sql` and `src/crud/` |
| At least 5 PL/SQL blocks | 8 (functions, a procedure, 6 triggers) |
| At least 5–7 analytical queries | 8 |
| Queries displayed from the client | Reports screen, live against the database |

---

## 3. Software

### 3.1 Database connection code

Full source: [`src/db.py`](../src/db.py)

```python
import os
from contextlib import contextmanager
from typing import Any, Iterable

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DB_TARGET = os.getenv("DB_TARGET", "supabase")


class DatabaseError(RuntimeError):
    """Raised for failures the UI should show as a message."""


def _connection_string() -> str:
    if DB_TARGET == "local":
        dsn = os.getenv("LOCAL_DATABASE_URL")
        if not dsn:
            raise DatabaseError("LOCAL_DATABASE_URL is not set.")
        return dsn

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise DatabaseError("DATABASE_URL is not set.")
    return dsn


@contextmanager
def get_connection(actor: str | None = None):
    try:
        conn = psycopg2.connect(_connection_string(), connect_timeout=10)
    except psycopg2.Error as exc:
        raise DatabaseError(f"Could not connect: {exc}") from exc

    try:
        with conn.cursor() as cur:
            if actor:
                # set_config takes a plain string literal, so the value
                # is passed as a parameter rather than interpolated.
                cur.execute("SELECT set_config('app.user', %s, false)",
                            (str(actor),))
        yield conn
        conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        raise DatabaseError(_friendly_error(exc)) from exc
    finally:
        conn.close()
```

Three decisions in this file:

**A connection per operation, not a cached one.** Streamlit reruns the
entire script on every interaction, and psycopg2 connections are not
thread-safe. A connection stored in `session_state` would be shared
across threads. The `@contextmanager` guarantees the connection is
closed even if the body raises.

**The actor travels with the connection.** `set_config('app.user', ...)`
is what lets `trg_audit_grade_change` record who entered a grade. The
name is passed as a bind parameter, never interpolated, because it is
user-controlled.

**Errors are translated for humans.** SQLSTATE codes are preserved
alongside the message, so the UI can explain the rule that fired rather
than showing a stack trace:

```python
def _friendly_error(exc: psycopg2.Error) -> str:
    pgcode = getattr(exc, "pgcode", None)
    message = str(exc).strip().splitlines()[0]

    if pgcode == "23505":
        return f"Duplicate value - {message} (SQLSTATE 23505: unique_violation)"
    if pgcode == "23503":
        return (f"Related record missing or still in use - {message} "
                f"(SQLSTATE 23503: foreign_key_violation)")
    if pgcode == "23514":
        return (f"Value rejected by a CHECK constraint - {message} "
                f"(SQLSTATE 23514: check_violation)")
    if pgcode == "P0001":
        return message          # RAISE EXCEPTION from our own code
    return f"Database error - {message} (SQLSTATE {pgcode})"
```

### 3.2 Authentication

Full source: [`src/auth.py`](../src/auth.py)

```python
BCRYPT_ROUNDS = 12

def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(
        plaintext.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("utf-8")


def authenticate(username: str, password: str) -> tuple[User | None, str]:
    rows = db.query_df(
        "SELECT user_id, username, email, password_hash, "
        "first_name, last_name, status FROM users WHERE username = %s",
        (username.strip().lower(),),
    ).to_dict("records")

    if not rows:
        # Spend comparable time so response timing does not reveal
        # whether the username exists.
        verify_password(password, hash_password("decoy"))
        return None, "Incorrect username or password."

    row = rows[0]
    if not verify_password(password, row["password_hash"]):
        return None, "Incorrect username or password."     # identical message

    if row["status"] != "active":
        return None, f"This account is '{row['status']}'..."
    ...
```

Both failure paths return the same string, and the unknown-user path
still performs a bcrypt verification. Together these mean neither the
message nor the response time reveals whether an account exists.

**Roles and the screen gate.** Each screen declares the roles allowed
to open it, and the gate is checked before the screen function runs:

```python
SCREENS = [
    ("dashboard",  "Dashboard", "📊", screen_dashboard,  ()),                    # all
    ("students",   "Students",  "🎓", screen_students,   ("admin", "registrar")),
    ("sections",   "Courses",   "📚", screen_sections,   ("admin", "registrar", "instructor")),
    ("reports",    "Reports",   "📈", screen_reports,    ("admin", "registrar", "instructor", "student")),
    ("admin",      "User Admin","🔐", screen_admin,      ("admin",)),
]
```

An empty tuple means "no restriction", so the dashboard is reachable
by every signed-in user. Verified by asserting the exact screen counts
per role: **admin 9, registrar 8, instructor 6, student 2.**

### 3.3 Screens

Ten screens; the brief requires five.

| # | Screen | Inserts | Updates | Deletes |
|---|---|---|---|---|
| 1 | Login / Register | user account | — | — |
| 2 | Dashboard | — | — | — |
| 3 | Students | ✓ | ✓ | ✓ |
| 4 | Instructors | ✓ | ✓ | ✓ |
| 5 | Courses & Sections | ✓ | ✓ | ✓ |
| 6 | Enrolments | ✓ (procedure) | ✓ (trigger) | withdraw |
| 7 | Assignments | ✓ | ✓ | ✓ |
| 8 | Attendance | ✓ | ✓ (upsert) | ✓ |
| 9 | Reports | — | — | — |
| 10 | User Administration | ✓ | status, roles | — |

Each CRUD screen is a four-tab layout — View, Insert, Update, Delete —
so every operation required by the brief is reachable in one screen.

### 3.4 Screenshots

Captured by `scripts/capture_screens.py`, which drives the real
application with Playwright, signs in through the actual login form,
and navigates the sidebar — so every image below is the running
application, not a mock-up.

| # | Screen | What it shows | File |
|---|---|---|---|
| 1 | Login | Sign-in and self-registration tabs, with the demo accounts listed | [`01-login.png`](screens/01-login.png) |
| 2 | Dashboard | Six metric tiles, current-term sections, grade distribution, departments, and the recent grade-change audit trail | [`02-dashboard.png`](screens/02-dashboard.png) |
| 3 | Students | Student list with department, level, GPA, advisor and course count | [`03-students.png`](screens/03-students.png) |
| 4 | Instructors | Staff list with rank, hire date, salary and sections taught | [`04-instructors.png`](screens/04-instructors.png) |
| 5 | Courses & Sections | Section list showing instructor, room, capacity, enrolled count and fill percentage from the PL/pgSQL function | [`05-courses.png`](screens/05-courses.png) |
| 6 | Enrolments | Enrolment list with status and letter grade; the Enrol / Grade / Withdraw tabs | [`06-enrolments.png`](screens/06-enrolments.png) |
| 7 | Assignments | Assignments with submitted, enrolled, average score | [`07-assignments.png`](screens/07-assignments.png) |
| 8 | Attendance | Attendance records per section, session and student | [`08-attendance.png`](screens/08-attendance.png) |
| 9 | Reports | Query 1's result in the application, with summary statistics and CSV download | [`09-reports.png`](screens/09-reports.png) |
| 10 | User Admin | Account management: status, role grants and revocations | [`10-user-admin.png`](screens/10-user-admin.png) |
| 11 | Reports → PL/pgSQL | **The procedural blocks executed live.** Each row is the application calling a function or procedure; the last two rows are the procedure's own `RAISE EXCEPTION` refusals | [`11-reports-plpgsql.png`](screens/11-reports-plpgsql.png) |
| 12 | Reports → SQL source | The query text, so the SQL graded on paper is visibly the SQL the app runs | [`12-reports-sql.png`](screens/12-reports-sql.png) |

#### Development environment

`supabase/migrations/verify_plpgsql.sql` run against PostgreSQL:

```
-- Applied to a clean database, in order
psql "$DATABASE_URL" -f supabase/migrations/0001_schema.sql
psql "$DATABASE_URL" -f supabase/migrations/0002_plpgsql.sql
psql "$DATABASE_URL" -f supabase/migrations/0003_seed.sql
psql "$DATABASE_URL" -f supabase/migrations/verify_plpgsql.sql
```

The full captured session, covering all 8 queries and all 8 procedural
blocks with their output, is in
[`docs/captured_output.txt`](captured_output.txt).

---

## 3.5 Role-based access, demonstrated

The sidebar is built per role at runtime, so the difference is visible
rather than merely asserted:

| Role | Screens visible |
|---|---|
| `admin` | 9 — everything |
| `registrar` | 8 — everything except User Admin |
| `i.kaya` (instructor) | 6 — Dashboard, Courses, Enrolments, Assignments, Attendance, Reports |
| `student1` | 2 — Dashboard, Reports |

An instructor cannot reach the student roster, and no student can
reach any CRUD screen. These counts are asserted in
`scripts/app_test.py`, so a regression fails the suite rather than
going unnoticed.

### 3.5 Role-based access, demonstrated

The sidebar is built per role at runtime, so the difference is visible
rather than merely asserted:

| Role | Screens visible |
|---|---|
| `admin` | 9 — everything |
| `registrar` | 8 — everything except User Admin |
| `i.kaya` (instructor) | 6 — Dashboard, Courses, Enrolments, Assignments, Attendance, Reports |
| `student1` | 2 — Dashboard, Reports |

An instructor cannot reach the student roster, and no student can
reach any CRUD screen. These counts are asserted in
`scripts/app_test.py`, so a regression fails the suite rather than
going unnoticed.

### 3.6 Deployment

**Database — Supabase.** Create a project, then *Project Settings →
Database → Connection string*. Use the session pooler (port 5432), not
the transaction pooler (6543), which does not support the prepared
statements psycopg2 issues.

**Application — Streamlit Community Cloud.** Push the repository, then
in *Deployments → New app*:

- **Repository:** this repository
- **Branch / main file:** `main` / `app.py`
- **Requirements:** `requirements.txt`
- **Secrets:** add `DATABASE_URL` from the Supabase connection string

`.env` is git-ignored and never committed; only `.env.example` is in
the repository.

### 3.7 Testing

Three suites, all executable:

```bash
python scripts/smoke_test.py                                  # 34 checks
python scripts/app_test.py                                    # 59 checks
psql "$DATABASE_URL" -f supabase/migrations/verify_plpgsql.sql # 14 checks
```

`smoke_test.py` exercises every module in `src/` against the live
database: authentication success and failure, role gating, all eight
reports, and each write path including its rollback.

`app_test.py` uses `streamlit.testing.v1.AppTest` to run the real
`app.py` headlessly. It signs in as each role, asserts the exact
screens that role can reach, renders all nine, executes all eight
reports, and checks the procedural-block output. This is not a syntax
check — a missing column or a render-time exception fails it.

| Suite | Checks | Result |
|---|---|---|
| `verify_plpgsql.sql` | 14 groups, 19 assertions | all pass |
| `smoke_test.py` | 34 | all pass |
| `app_test.py` | 59 | all pass |

---

## 4. GitHub

**Repository:** `<REPOSITORY_URL>`

Ensure it is **public** before submitting.

### 4.1 What is published

The brief requires the ERD, DDL, DML, queries and GUI code to be in
the repository. All of it is:

| Requirement | Location |
|---|---|
| ER diagram | [`docs/erd.md`](erd.md) |
| DDL | `supabase/migrations/0001_schema.sql` |
| PL/pgSQL blocks | `supabase/migrations/0002_plpgsql.sql` |
| DML / seed | `supabase/migrations/0003_seed.sql` |
| SQL queries | `supabase/migrations/0004_queries.sql` |
| Query and trigger output | [`docs/captured_output.txt`](captured_output.txt) |
| DB connection code | `src/db.py` |
| Authentication | `src/auth.py` |
| GUI code | `app.py`, `src/screens.py` |
| CRUD layer | `src/crud/__init__.py` |
| This report | `docs/report.md` |

### 4.2 Commit history

| Commit | Contents |
|---|---|
| `c081f46` | schema, procedural blocks, seed data, analytical queries, data layer |
| `b5f55be` | Streamlit application, authentication, both test suites |

### 4.3 Documentation

- [`README.md`](../README.md) — setup, screen index, design notes,
  security summary
- [`docs/erd.md`](erd.md) — ER diagram, integrity actions, the
  constraints an ER diagram cannot show
- `supabase/migrations/*.sql` — each file opens with a comment block
  explaining its purpose and the reasoning behind its non-obvious parts
- `.env.example` — every setting, documented; the real `.env` is
  git-ignored

### 4.4 Collaboration

Add group members as collaborators under *Settings → Collaborators* so
everyone can push. The `.gitignore` already excludes `.env`, so the
database password cannot be committed by accident.

---

## Appendix A — Complete object inventory

```
Tables        13   (12 domain + grade_audit)
Views          1   (v_roster)
Functions      8   (3 query functions + 1 procedure + 4 trigger functions)
Triggers       6   (3 counter, 2 updated_at, 1 grade audit)
Constraints   62   (PK, FK, UNIQUE, CHECK)
ENUM types     6
```

## Appendix B — Test output summary

```
CMPE344 - Python data layer smoke test
  34 passed, 0 failed

CMPE344 - Streamlit application test
  59 passed, 0 failed

PostgreSQL procedural block verification
  19 PASS assertions, 0 FAIL
  (the ERROR lines in section 2.6 and 2.7 are the *expected* refusals)
```