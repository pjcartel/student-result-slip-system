PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS students (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    reg_no     TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    programme  TEXT NOT NULL,
    year_of_study INTEGER NOT NULL CHECK (year_of_study BETWEEN 1 AND 6)
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin','lecturer','student')),
    student_id    INTEGER UNIQUE REFERENCES students(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS units (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    credit_hours INTEGER NOT NULL CHECK (credit_hours BETWEEN 1 AND 6)
);

CREATE TABLE IF NOT EXISTS semesters (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    label     TEXT NOT NULL UNIQUE,            -- e.g. "2026 Sept-Dec"
    published INTEGER NOT NULL DEFAULT 0       -- students see slips only when 1
);

CREATE TABLE IF NOT EXISTS results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    unit_id     INTEGER NOT NULL REFERENCES units(id)    ON DELETE RESTRICT,
    semester_id INTEGER NOT NULL REFERENCES semesters(id) ON DELETE CASCADE,
    cat         REAL NOT NULL CHECK (cat  BETWEEN 0 AND 30),
    exam        REAL NOT NULL CHECK (exam BETWEEN 0 AND 70),
    entered_by  INTEGER REFERENCES users(id),
    UNIQUE (student_id, unit_id, semester_id)
);

CREATE INDEX IF NOT EXISTS idx_results_student_sem ON results(student_id, semester_id);
