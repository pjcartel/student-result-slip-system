"""School Management System - Student Result Slips (Flask + SQLite)."""
import csv
import io
import os
import secrets
import sqlite3
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("RESULTS_DB", os.path.join(BASE, "results.db"))

app = Flask(__name__)
SCHOOL_NAME = os.environ.get("SCHOOL_NAME", "Sample School")
# Set SECRET_KEY in the environment for any real deployment. If it is missing we
# generate a random key at startup (sessions then reset whenever the app restarts).
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)


# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    with open(os.path.join(BASE, "schema.sql")) as f:
        db.executescript(f.read())
    db.commit()
    db.close()


# ----------------------------------------------------------- grading logic
def grade_for(total):
    """Default grading scale -> (letter, grade points, remark)."""
    if total >= 70: return "A", 4.0, "Distinction"
    if total >= 60: return "B", 3.0, "Credit"
    if total >= 50: return "C", 2.0, "Pass"
    if total >= 40: return "D", 1.0, "Marginal Pass"
    return "E", 0.0, "Fail"


def classify(gpa):
    if gpa >= 3.5: return "First Class Standard"
    if gpa >= 3.0: return "Second Class Upper Standard"
    if gpa >= 2.0: return "Second Class Lower Standard"
    if gpa >= 1.0: return "Pass Standard"
    return "Fail - Review Required"


def build_slip(student_id, semester_id):
    db = get_db()
    rows = db.execute(
        """SELECT u.code, u.title, u.credit_hours, r.cat, r.exam
           FROM results r JOIN units u ON u.id = r.unit_id
           WHERE r.student_id=? AND r.semester_id=? ORDER BY u.code""",
        (student_id, semester_id)).fetchall()
    lines, credits, points = [], 0, 0.0
    for r in rows:
        total = r["cat"] + r["exam"]
        letter, gp, remark = grade_for(total)
        credits += r["credit_hours"]
        points += gp * r["credit_hours"]
        lines.append(dict(code=r["code"], title=r["title"], ch=r["credit_hours"],
                          cat=r["cat"], exam=r["exam"], total=total,
                          grade=letter, gp=gp, remark=remark))
    gpa = round(points / credits, 2) if credits else 0.0
    return lines, credits, gpa


def transcript_data(student_id, published_only):
    """Per-semester GPA plus cumulative GPA (CGPA), weighted by credit hours."""
    db = get_db()
    sql = """SELECT sem.id, sem.label, u.credit_hours, r.cat, r.exam
             FROM results r JOIN units u ON u.id=r.unit_id
             JOIN semesters sem ON sem.id=r.semester_id
             WHERE r.student_id=?"""
    if published_only:
        sql += " AND sem.published=1"
    rows = db.execute(sql + " ORDER BY sem.id", (student_id,)).fetchall()
    sems, order = {}, []
    for r in rows:
        if r["id"] not in sems:
            sems[r["id"]] = dict(id=r["id"], label=r["label"], units=0, credits=0, points=0.0)
            order.append(r["id"])
        d = sems[r["id"]]
        d["units"] += 1
        d["credits"] += r["credit_hours"]
        d["points"] += grade_for(r["cat"] + r["exam"])[1] * r["credit_hours"]
    out = [sems[i] for i in order]
    for d in out:
        d["gpa"] = round(d["points"] / d["credits"], 2)
    tc = sum(d["credits"] for d in out)
    cgpa = round(sum(d["points"] for d in out) / tc, 2) if tc else 0.0
    return out, tc, cgpa


# -------------------------------------------------------------------- auth
def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def audit(action, details):
    """Record who did what. Caller commits (so it is atomic with the change)."""
    u = current_user()
    get_db().execute(
        "INSERT INTO audit_log(user_id, username, action, details) VALUES(?,?,?,?)",
        (u["id"] if u else None, u["username"] if u else "-", action, details))


def login_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            user = current_user()
            if not user:
                return redirect(url_for("login"))
            if roles and user["role"] not in roles:
                abort(403)
            g.user = user
            return fn(*a, **kw)
        return wrapper
    return deco


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["school_name"] = SCHOOL_NAME


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent, expected = request.form.get("_csrf"), session.get("csrf")
        if not sent or not expected or not secrets.compare_digest(sent, expected):
            abort(400, "Invalid CSRF token")


@app.context_processor
def inject_user():
    return {"me": current_user()}


@app.route("/", methods=["GET"])
def index():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    return redirect(url_for("my_slips" if user["role"] == "student" else "dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = get_db().execute("SELECT * FROM users WHERE username=?",
                             (request.form["username"].strip(),)).fetchone()
        if u and check_password_hash(u["password_hash"], request.form["password"]):
            session.clear()
            session["uid"] = u["id"]
            return redirect(url_for("index"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------ admin screens
@app.route("/dashboard")
@login_required("admin", "lecturer")
def dashboard():
    db = get_db()
    stats = {t: db.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
             for t in ("students", "units", "semesters", "results")}
    return render_template("dashboard.html", stats=stats)


@app.route("/students", methods=["GET", "POST"])
@login_required("admin")
def students():
    db = get_db()
    if request.method == "POST":
        try:
            reg = request.form["reg_no"].strip().upper()
            cur = db.execute(
                "INSERT INTO students(reg_no,name,programme,year_of_study) VALUES(?,?,?,?)",
                (reg, request.form["name"].strip(), request.form["programme"].strip(),
                 int(request.form["year"])))
            # student login: username = reg no, initial password = reg no
            db.execute("INSERT INTO users(username,password_hash,role,student_id) VALUES(?,?,?,?)",
                       (reg.lower(), generate_password_hash(reg), "student", cur.lastrowid))
            audit("student.add", f"{reg} {request.form['name'].strip()}")
            db.commit()
            flash(f"Student {reg} added. Initial password is the registration number.", "ok")
        except (sqlite3.IntegrityError, ValueError):
            db.rollback()
            flash("Could not add student (duplicate reg no or invalid data).", "error")
        return redirect(url_for("students"))
    rows = db.execute("SELECT * FROM students ORDER BY reg_no").fetchall()
    return render_template("students.html", rows=rows)


@app.route("/students/<int:sid>/delete", methods=["POST"])
@login_required("admin")
def delete_student(sid):
    db = get_db()
    st = db.execute("SELECT reg_no FROM students WHERE id=?", (sid,)).fetchone()
    db.execute("DELETE FROM students WHERE id=?", (sid,))
    if st:
        audit("student.delete", f"{st['reg_no']} and all their results")
    db.commit()
    flash("Student deleted.", "ok")
    return redirect(url_for("students"))


@app.route("/students/<int:sid>/reset-password", methods=["POST"])
@login_required("admin")
def reset_student_password(sid):
    db = get_db()
    st = db.execute("SELECT reg_no FROM students WHERE id=?", (sid,)).fetchone()
    if not st:
        abort(404)
    db.execute("UPDATE users SET password_hash=? WHERE student_id=?",
               (generate_password_hash(st["reg_no"]), sid))
    audit("student.password_reset", st["reg_no"])
    db.commit()
    flash(f"Password for {st['reg_no']} reset to their registration number.", "ok")
    return redirect(url_for("students"))


@app.route("/units", methods=["GET", "POST"])
@login_required("admin")
def units():
    db = get_db()
    if request.method == "POST":
        try:
            db.execute("INSERT INTO units(code,title,credit_hours) VALUES(?,?,?)",
                       (request.form["code"].strip().upper(), request.form["title"].strip(),
                        int(request.form["ch"])))
            db.commit()
            flash("Unit added.", "ok")
        except (sqlite3.IntegrityError, ValueError):
            db.rollback()
            flash("Could not add unit (duplicate code or invalid data).", "error")
        return redirect(url_for("units"))
    return render_template("units.html", rows=db.execute("SELECT * FROM units ORDER BY code").fetchall())


@app.route("/semesters", methods=["GET", "POST"])
@login_required("admin")
def semesters():
    db = get_db()
    if request.method == "POST":
        try:
            db.execute("INSERT INTO semesters(label) VALUES(?)", (request.form["label"].strip(),))
            db.commit()
            flash("Semester added.", "ok")
        except sqlite3.IntegrityError:
            db.rollback()
            flash("Semester already exists.", "error")
        return redirect(url_for("semesters"))
    return render_template("semesters.html",
                           rows=db.execute("SELECT * FROM semesters ORDER BY id DESC").fetchall())


@app.route("/semesters/<int:sid>/toggle", methods=["POST"])
@login_required("admin")
def toggle_semester(sid):
    db = get_db()
    db.execute("UPDATE semesters SET published = 1 - published WHERE id=?", (sid,))
    sem = db.execute("SELECT label, published FROM semesters WHERE id=?", (sid,)).fetchone()
    if sem:
        audit("semester.publish" if sem["published"] else "semester.unpublish", sem["label"])
    db.commit()
    return redirect(url_for("semesters"))


# ----------------------------------------------------------- result capture
@app.route("/results", methods=["GET", "POST"])
@login_required("admin", "lecturer")
def results():
    db = get_db()
    if request.method == "POST":
        try:
            sid, uid, sem = (int(request.form[k]) for k in ("student_id", "unit_id", "semester_id"))
            cat, exam = float(request.form["cat"]), float(request.form["exam"])
            if not (0 <= cat <= 30 and 0 <= exam <= 70):
                raise ValueError
            old = db.execute(
                "SELECT cat, exam FROM results WHERE student_id=? AND unit_id=? AND semester_id=?",
                (sid, uid, sem)).fetchone()
            db.execute(
                """INSERT INTO results(student_id,unit_id,semester_id,cat,exam,entered_by)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(student_id,unit_id,semester_id)
                   DO UPDATE SET cat=excluded.cat, exam=excluded.exam, entered_by=excluded.entered_by""",
                (sid, uid, sem, cat, exam, g.user["id"]))
            who = db.execute(
                """SELECT s.reg_no, u.code, m.label FROM students s, units u, semesters m
                   WHERE s.id=? AND u.id=? AND m.id=?""", (sid, uid, sem)).fetchone()
            if old:
                audit("result.update", f"{who['reg_no']} {who['code']} {who['label']}: "
                      f"CAT {old['cat']:g}->{cat:g}, Exam {old['exam']:g}->{exam:g}")
            else:
                audit("result.add", f"{who['reg_no']} {who['code']} {who['label']}: "
                      f"CAT {cat:g}, Exam {exam:g}")
            db.commit()
            flash("Result saved.", "ok")
        except (ValueError, KeyError, sqlite3.IntegrityError):
            db.rollback()
            flash("Invalid entry: CAT must be 0-30 and Exam 0-70.", "error")
        return redirect(url_for("results", semester=request.form.get("semester_id")))

    sem_id = request.args.get("semester", type=int)
    recent = []
    if sem_id:
        recent = db.execute(
            """SELECT r.id, s.reg_no, s.name, u.code, r.cat, r.exam
               FROM results r JOIN students s ON s.id=r.student_id JOIN units u ON u.id=r.unit_id
               WHERE r.semester_id=? ORDER BY s.reg_no, u.code""", (sem_id,)).fetchall()
    return render_template(
        "results.html", sem_id=sem_id, recent=recent,
        students=db.execute("SELECT * FROM students ORDER BY reg_no").fetchall(),
        units=db.execute("SELECT * FROM units ORDER BY code").fetchall(),
        sems=db.execute("SELECT * FROM semesters ORDER BY id DESC").fetchall(),
        grade_for=grade_for)


@app.route("/results/<int:rid>/delete", methods=["POST"])
@login_required("admin", "lecturer")
def delete_result(rid):
    db = get_db()
    row = db.execute(
        """SELECT r.semester_id, s.reg_no, u.code, m.label, r.cat, r.exam
           FROM results r JOIN students s ON s.id=r.student_id JOIN units u ON u.id=r.unit_id
           JOIN semesters m ON m.id=r.semester_id WHERE r.id=?""", (rid,)).fetchone()
    db.execute("DELETE FROM results WHERE id=?", (rid,))
    if row:
        audit("result.delete", f"{row['reg_no']} {row['code']} {row['label']}: "
              f"CAT {row['cat']:g}, Exam {row['exam']:g}")
    db.commit()
    return redirect(url_for("results", semester=row["semester_id"] if row else None))


# ------------------------------------------------------------- CSV import
IMPORT_MAX_BYTES = 1_000_000
IMPORT_MAX_ROWS = 2000


@app.route("/results/import", methods=["GET", "POST"])
@login_required("admin", "lecturer")
def import_results():
    db = get_db()
    sems = db.execute("SELECT * FROM semesters ORDER BY id DESC").fetchall()
    errors, imported = [], None
    if request.method == "POST":
        f = request.files.get("file")
        try:
            sem_id = int(request.form["semester_id"])
        except (KeyError, ValueError):
            sem_id = None
        sem = db.execute("SELECT * FROM semesters WHERE id=?", (sem_id,)).fetchone() if sem_id else None
        if not sem:
            errors.append("Choose a semester.")
        if not f or not f.filename:
            errors.append("Choose a CSV file.")
        if not errors:
            raw = f.read(IMPORT_MAX_BYTES + 1)
            if len(raw) > IMPORT_MAX_BYTES:
                errors.append("File is too large (limit 1 MB).")
            else:
                try:
                    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
                    cols = [c.strip().lower() for c in (reader.fieldnames or [])]
                    if cols[:4] != ["reg_no", "unit_code", "cat", "exam"] and \
                            not {"reg_no", "unit_code", "cat", "exam"} <= set(cols):
                        errors.append("Header must contain: reg_no, unit_code, cat, exam")
                    else:
                        reader.fieldnames = cols
                        good, seen = [], set()
                        for n, row in enumerate(reader, start=2):
                            if n - 1 > IMPORT_MAX_ROWS:
                                errors.append(f"Too many rows (limit {IMPORT_MAX_ROWS}).")
                                break
                            reg = (row.get("reg_no") or "").strip().upper()
                            code = (row.get("unit_code") or "").strip().upper()
                            st = db.execute("SELECT id FROM students WHERE reg_no=?", (reg,)).fetchone()
                            un = db.execute("SELECT id FROM units WHERE code=?", (code,)).fetchone()
                            try:
                                cat, exam = float(row.get("cat")), float(row.get("exam"))
                                if not (0 <= cat <= 30 and 0 <= exam <= 70):
                                    raise ValueError
                            except (TypeError, ValueError):
                                errors.append(f"Line {n}: CAT must be 0-30 and Exam 0-70 (numbers).")
                                continue
                            if not st:
                                errors.append(f"Line {n}: unknown student '{reg}'.")
                            elif not un:
                                errors.append(f"Line {n}: unknown unit '{code}'.")
                            elif (reg, code) in seen:
                                errors.append(f"Line {n}: duplicate {reg} / {code} in file.")
                            else:
                                seen.add((reg, code))
                                good.append((st["id"], un["id"], cat, exam))
                        if not errors and not good:
                            errors.append("The file has no data rows.")
                        if not errors:  # all-or-nothing
                            for sid, uid, cat, exam in good:
                                db.execute(
                                    """INSERT INTO results(student_id,unit_id,semester_id,cat,exam,entered_by)
                                       VALUES(?,?,?,?,?,?)
                                       ON CONFLICT(student_id,unit_id,semester_id)
                                       DO UPDATE SET cat=excluded.cat, exam=excluded.exam,
                                                     entered_by=excluded.entered_by""",
                                    (sid, uid, sem_id, cat, exam, g.user["id"]))
                            audit("result.import", f"{len(good)} marks into {sem['label']} from {f.filename}")
                            db.commit()
                            imported = len(good)
                except (UnicodeDecodeError, csv.Error):
                    errors.append("Could not read the file. Save it as a UTF-8 CSV.")
    return render_template("import.html", sems=sems, errors=errors[:30],
                           more=max(0, len(errors) - 30), imported=imported)


# ------------------------------------------------------------- audit log
@app.route("/audit")
@login_required("admin")
def audit_log():
    rows = get_db().execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT 200").fetchall()
    return render_template("audit.html", rows=rows)


# ------------------------------------------------------------- transcript
@app.route("/transcript/<int:student_id>")
@login_required()
def transcript(student_id):
    user = g.user
    if user["role"] == "student" and user["student_id"] != student_id:
        abort(403)
    stu = get_db().execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    if not stu:
        abort(404)
    sems, credits, cgpa = transcript_data(student_id, published_only=user["role"] == "student")
    if not sems:
        abort(404)
    return render_template("transcript.html", stu=stu, sems=sems, credits=credits,
                           cgpa=cgpa, standing=classify(cgpa))


# ------------------------------------------------------------- result slips
@app.route("/my-slips")
@login_required("student")
def my_slips():
    sems = get_db().execute(
        """SELECT DISTINCT sem.id, sem.label FROM results r
           JOIN semesters sem ON sem.id=r.semester_id
           WHERE r.student_id=? AND sem.published=1 ORDER BY sem.id DESC""",
        (g.user["student_id"],)).fetchall()
    return render_template("my_slips.html", sems=sems, sid=g.user["student_id"])


@app.route("/lookup")
@login_required("admin", "lecturer")
def lookup():
    db = get_db()
    return render_template("lookup.html",
                           students=db.execute("SELECT * FROM students ORDER BY reg_no").fetchall(),
                           sems=db.execute("SELECT * FROM semesters ORDER BY id DESC").fetchall())


@app.route("/slip/<int:student_id>/<int:semester_id>")
@login_required()
def slip(student_id, semester_id):
    db = get_db()
    user = g.user
    # Access control: students may only see their own, published slips.
    if user["role"] == "student" and user["student_id"] != student_id:
        abort(403)
    stu = db.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    sem = db.execute("SELECT * FROM semesters WHERE id=?", (semester_id,)).fetchone()
    if not stu or not sem:
        abort(404)
    if user["role"] == "student" and not sem["published"]:
        abort(403)
    lines, credits, gpa = build_slip(student_id, semester_id)
    if not lines:
        abort(404)
    return render_template("slip.html", stu=stu, sem=sem, lines=lines, credits=credits,
                           gpa=gpa, standing=classify(gpa),
                           draft=not sem["published"])


@app.route("/change-password", methods=["GET", "POST"])
@login_required()
def change_password():
    if request.method == "POST":
        if not check_password_hash(g.user["password_hash"], request.form["old"]):
            flash("Current password is incorrect.", "error")
        elif len(request.form["new"]) < 8:
            flash("New password must be at least 8 characters.", "error")
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash=? WHERE id=?",
                       (generate_password_hash(request.form["new"]), g.user["id"]))
            db.commit()
            flash("Password updated.", "ok")
            return redirect(url_for("index"))
    return render_template("password.html")


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        init_db()
    app.run(debug=False, port=5000)
