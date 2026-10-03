# Entity–Relationship Diagram

Rendered by GitHub from the Mermaid source below. To edit the diagram,
change the fenced block — not an exported image.

## Relationship summary

| From | To | Cardinality | Meaning |
|---|---|---|---|
| `users` | `user_roles` | 1 : M | a user holds zero or more roles |
| `roles` | `user_roles` | 1 : M | a role is held by zero or more users |
| `users` | `students` | 1 : 0..1 | a login may have one student profile |
| `users` | `instructors` | 1 : 0..1 | a login may have one instructor profile |
| `departments` | `students` | 1 : M | a department has many students |
| `departments` | `instructors` | 1 : M | a department has many instructors |
| `students` | `students` | 1 : 0..1 | a student may be advised by another student |
| `students` | `enrollments` | 1 : M | a student enrols in many sections |
| `course_sections` | `enrollments` | 1 : M | a section has many enrolments |
| `instructors` | `course_sections` | 1 : M | an instructor may teach many sections |
| `classrooms` | `course_sections` | 1 : M | a room hosts many sections |
| `course_sections` | `assignments` | 1 : M | a section has many assignments |
| `assignments` | `submissions` | 1 : M | an assignment receives many submissions |
| `students` | `submissions` | 1 : M | a student submits many assignments |
| `users` | `submissions` | 1 : 0..1 | a user may be recorded as the grader |
| `course_sections` | `attendance` | 1 : M | attendance is recorded per section |
| `students` | `attendance` | 1 : M | attendance is recorded per student |
| `enrollments` | `grade_audit` | 1 : M | every grade change is audited |

`grade_audit` deliberately has **no foreign keys**. An audit trail that
cascaded away when the row it describes was deleted would not be an
audit trail.

## Diagram

```mermaid
erDiagram
    USERS ||--o{ USER_ROLES : "holds"
    ROLES ||--o{ USER_ROLES : "granted by"
    USERS ||--o| STUDENTS : "may have profile"
    USERS ||--o| INSTRUCTORS : "may have profile"
    USERS ||--o{ SUBMISSIONS : "grades"

    DEPARTMENTS ||--o{ STUDENTS : "enrols"
    DEPARTMENTS ||--o{ INSTRUCTORS : "employs"

    STUDENTS ||--o| STUDENTS : "advised by"
    STUDENTS ||--o{ ENROLLMENTS : "makes"
    STUDENTS ||--o{ SUBMISSIONS : "submits"
    STUDENTS ||--o{ ATTENDANCE : "attends"

    INSTRUCTORS ||--o{ COURSE_SECTIONS : "teaches"
    CLASSROOMS ||--o{ COURSE_SECTIONS : "hosts"

    COURSE_SECTIONS ||--o{ ENROLLMENTS : "receives"
    COURSE_SECTIONS ||--o{ ASSIGNMENTS : "sets"
    COURSE_SECTIONS ||--o{ ATTENDANCE : "records"

    ASSIGNMENTS ||--o{ SUBMISSIONS : "receives"
    ENROLLMENTS ||--o{ GRADE_AUDIT : "audited as"

    USERS {
        serial   user_id PK
        varchar   username UK "lowercase, 3-50 chars"
        varchar   email UK
        text      password_hash "bcrypt cost 12"
        varchar   first_name
        varchar   last_name        "pending | active | suspended"
        timestamptz created_at
        timestamptz updated_at
    }

    ROLES {
        serial   role_id PK
        varchar  role_name UK
        varchar  description
        smallint access_level "1-5, higher sees more"
    }

    USER_ROLES {
        int      user_id PK,FK
        int      role_id PK,FK
        timestamptz granted_at
    }

    DEPARTMENTS {
        serial   department_id PK
        varchar  dept_code UK
        varchar  dept_name
        numeric  annual_budget ">= 0"
        smallint established_year
    }

    CLASSROOMS {
        serial   classroom_id PK
        varchar  building UK
        varchar  room_number UK
        integer  capacity "> 0"
        boolean  has_projector
    }

    INSTRUCTORS {
        serial   instructor_id PK
        int      user_id FK,UK "-> USERS"
        int      department_id FK "-> DEPARTMENTS"
        varchar  employee_no UK
        date     hire_date
        varchar  rank_title
        numeric  salary
    }

    STUDENTS {
        serial   student_id PK
        int      user_id FK,UK "-> USERS"
        int      department_id FK "-> DEPARTMENTS"
        int      advisor_id FK "-> STUDENTS, self reference"
        varchar  student_no UK
        varchar  program_level "undergraduate | graduate | phd"
        smallint enrollment_year
        numeric  gpa "0.00 - 4.00"
        timestamptz updated_at
    }

    COURSE_SECTIONS {
        serial   section_id PK
        varchar  course_code UK
        varchar  course_name
        varchar  term "Fall | Spring | Summer"
        smallint academic_year
        int      instructor_id FK "-> INSTRUCTORS, SET NULL on delete"
        int      classroom_id FK "-> CLASSROOMS, SET NULL on delete"
        smallint credits "1-6"
        integer  capacity "> 0"
        integer  enrolled_count "0..capacity, maintained by trigger"
    }

    ENROLLMENTS {
        serial   enrollment_id PK
        int      student_id FK,UK "-> STUDENTS, CASCADE"
        int      section_id FK,UK "-> COURSE_SECTIONS, CASCADE"
        timestamptz enrolled_at
        varchar  status "enrolled | dropped | completed | failed"
        numeric  final_grade "0-100, null while open"
        varchar  grade_letter "AA BB CC DD FF NA"
    }

    ASSIGNMENTS {
        serial   assignment_id PK
        int      section_id FK "-> COURSE_SECTIONS, CASCADE"
        varchar  title
        numeric  weight "0-100"
        numeric  max_points "> 0"
        date     due_date
    }

    SUBMISSIONS {
        serial   submission_id PK
        int      assignment_id FK,UK "-> ASSIGNMENTS, CASCADE"
        int      student_id FK,UK "-> STUDENTS, CASCADE"
        timestamptz submitted_at
        numeric  score
        text     feedback
        int      graded_by FK "-> USERS, SET NULL"
        boolean  is_late
    }

    ATTENDANCE {
        serial   attendance_id PK
        int      section_id FK,UK "-> COURSE_SECTIONS, CASCADE"
        int      student_id FK,UK "-> STUDENTS, CASCADE"
        date     session_date UK "part of unique key"
        varchar  status "present | absent | late | excused"
    }

    GRADE_AUDIT {
        serial   audit_id PK
        int      enrollment_id "no FK, on purpose"
        int      student_id "no FK, on purpose"
        int      section_id "no FK, on purpose"
        numeric  old_grade
        numeric  new_grade
        varchar  old_letter
        varchar  new_letter
        varchar  changed_by "from app.user session setting"
        timestamptz changed_at
    }
```

## Referential integrity actions

Choosing `ON DELETE` behaviour per relationship is a design decision,
not a default:

| Relationship | Action | Reasoning |
|---|---|---|
| `students.user_id` | `CASCADE` | A student profile has no meaning without its login |
| `enrollments.student_id` | `CASCADE` | Enrolment is meaningless without the student |
| `enrollments.section_id` | `CASCADE` | Same, in the other direction |
| `submissions.assignment_id` | `CASCADE` | Removing an assignment removes its submissions |
| `attendance.section_id` | `CASCADE` | Same |
| `instructors.user_id` | `CASCADE` | A profile without a login is unreachable |
| `course_sections.instructor_id` | `SET NULL` | Deleting a staff member must not delete real teaching history; the section survives, unassigned |
| `course_sections.classroom_id` | `SET NULL` | Reassigning a room should not delete a course |
| `submissions.graded_by` | `SET NULL` | Losing the grader's account must not delete the mark |
| `users.user_roles` | `CASCADE` | Roles vanish with the account, which is correct |
| `users.user_roles` → `roles` | `RESTRICT` | A role still in use cannot be deleted out from under its holders |

## Constraints not visible in an ER diagram

An ER diagram shows keys and cardinality. These rules live in the DDL
and are worth stating separately:

| Constraint | Rule enforced |
|---|---|
| `ck_enrollments_final` | A closed enrolment (`completed`/`failed`) must carry a grade; an open one (`enrolled`/`dropped`) must not |
| `ck_sections_enrolled` | `enrolled_count` can never exceed `capacity` |
| `ck_students_gpa` | GPA stays within 0.00–4.00 |
| `ck_assignments_weight` | An assessment's weight is within 0–100 |
| `ck_users_username` | Lowercase, 3–50 characters |
| `uq_enrollments_student_section` | One enrolment per student per section; re-enrolling revives the withdrawn row instead of adding a second |
| `uq_submissions_assignment_student` | One submission per student per assignment |
| `uq_attendance_session` | One attendance mark per student per session, which lets the form upsert |