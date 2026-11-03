"""School Management System - Student Result Slips (Flask + SQLite)."""
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


# -------------------------------------------------------------------- auth
def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


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
    db.execute("DELETE FROM students WHERE id=?", (sid,))
    db.commit()
    flash("Student deleted.", "ok")
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
            db.execute(
                """INSERT INTO results(student_id,unit_id,semester_id,cat,exam,entered_by)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(student_id,unit_id,semester_id)
                   DO UPDATE SET cat=excluded.cat, exam=excluded.exam, entered_by=excluded.entered_by""",
                (sid, uid, sem, cat, exam, g.user["id"]))
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
    row = db.execute("SELECT semester_id FROM results WHERE id=?", (rid,)).fetchone()
    db.execute("DELETE FROM results WHERE id=?", (rid,))
    db.commit()
    return redirect(url_for("results", semester=row["semester_id"] if row else None))


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
