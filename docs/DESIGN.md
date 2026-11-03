# School Management System: Student Result Slips

## 1. Problem statement
Manual preparation of result slips is slow and error-prone: marks are copied between spreadsheets, grades and GPAs are calculated by hand, and students have no controlled way to get their results. This system captures marks once, computes grades and GPA automatically, and issues a printable result slip that each student can access only for themselves, and only after the results are published.

## 2. Requirements

**Functional**
1. Authenticate users with three roles: Administrator, Lecturer, Student.
2. Administrator manages students, units, semesters, and publishes/unpublishes a semester's results.
3. Lecturer/Administrator captures CAT (out of 30) and Exam (out of 70) marks per student, unit and semester; re-entering a mark updates it.
4. System computes total, letter grade, grade points, semester GPA and academic standing.
5. System generates a printable result slip (print / save as PDF from the browser).
6. Students view only their own slips, and only for published semesters.
7. The system computes a cumulative GPA (CGPA) across all of a student's semesters and issues a printable transcript summary.
8. Marks can be imported in bulk from a CSV file, all-or-nothing, with line-by-line error reporting.
9. Every change (marks, imports, publishing, student add/delete, password reset) is recorded in an audit log.
10. The Administrator can reset a student's password.

**Non-functional**: passwords hashed; CSRF protection on all POSTs; server-side validation of marks; role-based access control; works on any browser; zero external services (SQLite).

## 3. Actors and use cases

| Actor | Use cases |
|---|---|
| Administrator | Manage students / units / semesters; publish results; capture, correct or import marks; preview any slip (including drafts); reset student passwords; review the audit log |
| Lecturer | Capture, correct and import marks; view slips and transcripts |
| Student | Sign in; change password; view and print own published slips and transcript (CGPA) |

## 4. Architecture
Three-tier monolith:

```
Browser (HTML/CSS, print stylesheet)
   |  HTTP + session cookie
Flask application (app.py)
   |  routes -> access control (login_required) -> business rules (grade_for, build_slip)
SQLite database (schema.sql)
```

Chosen for simplicity and portability: one process, one file database, no build step. The business rules (grading, GPA) are isolated in pure functions so they can be unit tested and changed without touching routes or templates.

## 5. Database design

```
students 1 --- 0..1 users            (a student account links to one student)
students 1 --- * results * --- 1 units
semesters 1 --- * results
users 1 --- * results (entered_by)
users 1 --- * audit_log (who made each change)
```

| Table | Key columns |
|---|---|
| students | id, reg_no (unique), name, programme, year_of_study |
| users | id, username (unique), password_hash, role, student_id (unique FK) |
| units | id, code (unique), title, credit_hours |
| semesters | id, label (unique), published (0/1) |
| results | id, student_id, unit_id, semester_id, cat, exam, entered_by; **unique (student, unit, semester)** |
| audit_log | id, created_at (UTC), user_id, username, action, details |

Integrity rules enforced in the database: CHECK constraints (CAT 0-30, Exam 0-70, valid role, credit hours 1-6), foreign keys, and the unique triple that prevents duplicate marks. Totals, grades and GPA are **derived at read time, not stored**, so they can never disagree with the marks.

Normalisation: 3NF. Marks are stored once; unit and student attributes live in their own tables.

## 6. Business rules

Total = CAT + Exam.

| Total | Grade | Points | Remark |
|---|---|---|---|
| 70-100 | A | 4.0 | Distinction |
| 60-69 | B | 3.0 | Credit |
| 50-59 | C | 2.0 | Pass |
| 40-49 | D | 1.0 | Marginal Pass |
| below 40 | E | 0.0 | Fail |

GPA = sum(grade points x credit hours) / sum(credit hours).
Standing: >=3.5 First Class, >=3.0 Second Upper, >=2.0 Second Lower, >=1.0 Pass, otherwise review required.

CGPA = sum of grade points x credit hours over all semesters / sum of credit hours over all semesters (credit-weighted, not an average of semester GPAs). Students see only published semesters in their CGPA.

> The scale and class boundaries are a reasonable assumption. They live in `grade_for()` and `classify()` in `app.py`; change them there to match your your school's official grading regulations.

## 7. Security design
- Passwords stored with Werkzeug salted hashes (never plain text).
- Per-session CSRF token required on every POST (constant-time comparison).
- Role checks via `@login_required(roles)`; student routes additionally verify `student_id` ownership and `semester.published` (returns 403 otherwise).
- All SQL uses parameter binding (no SQL injection); Jinja autoescaping prevents XSS.
- Marks validated server-side regardless of browser checks.
- CSV uploads are limited to 1 MB and 2000 rows, must be UTF-8, and are applied in one transaction (all-or-nothing).
- Audit entries are written in the same transaction as the change they describe; the audit log is admin-only.
- Production: set `SECRET_KEY`, serve over HTTPS, change the seeded demo passwords.

## 8. Main flow: issuing a slip
1. Admin adds semester, students, units.
2. Lecturer enters CAT/Exam marks (validated, upserted).
3. Admin reviews draft slips (watermarked "DRAFT"), then publishes the semester.
4. Student signs in, opens "My slips", views and prints the slip.

## 9. Testing
`test_app.py` verifies: grade boundaries, GPA arithmetic, student isolation (cannot open another student's slip), unpublished slips hidden from students, role restrictions, CSRF rejection, mark validation, upsert behaviour, CGPA across semesters, CSV import (valid file, all-or-nothing rejection, bad header, student access denied), audit log entries, and admin password reset. The tests were also checked by deliberately breaking the code to confirm they fail. All pass.

## 10. Limitations and future work
Single-institution scale (SQLite and Flask's development server). Not yet built: password reset by email, per-class lecturer permissions, an official PDF with QR verification, and a REST API. These are natural extensions.
