"""Run: python test_app.py"""
import os, re, tempfile

os.environ["RESULTS_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
import app as A
import seed  # noqa: F401  (seeds the temp DB)

A.app.config["TESTING"] = True


def client_login(user, pw):
    c = A.app.test_client()
    r = c.get("/login")
    tok = re.search(r'name="_csrf" value="(\w+)"', r.get_data(as_text=True)).group(1)
    c.post("/login", data={"username": user, "password": pw, "_csrf": tok})
    return c


def csrf(c, path):
    html = c.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="(\w+)"', html).group(1)


# grading
assert A.grade_for(70)[0] == "A" and A.grade_for(69.5)[0] == "B"
assert A.grade_for(39.9)[0] == "E" and A.grade_for(40)[0] == "D"
assert A.classify(3.5) == "First Class Standard"

# GPA for Amina (student 1): totals 76,67,58,87,68 -> A,B,C,A,B ; CH 3,3,3,3,2
with A.app.app_context():
    lines, credits, gpa = A.build_slip(1, 1)
assert credits == 14 and gpa == round((4*3+3*3+2*3+4*3+3*2)/14, 2), gpa

# student access control
s = client_login("2022/001", "2022/001")
assert s.get("/slip/1/1").status_code == 200          # own, published
assert s.get("/slip/2/1").status_code == 403          # someone else's
assert s.get("/slip/1/2").status_code in (403, 404)   # unpublished semester
assert s.get("/students").status_code == 403          # admin only
assert s.get("/results").status_code == 403

# anonymous
assert A.app.test_client().get("/slip/1/1").status_code == 302

# CSRF
a = client_login("admin", "admin123")
assert a.post("/units", data={"code": "X", "title": "Y", "ch": 3}).status_code == 400

# validation: CAT over 30 rejected
tok = csrf(a, "/results")
a.post("/results", data={"_csrf": tok, "student_id": 1, "unit_id": 1, "semester_id": 2,
                         "cat": 31, "exam": 50})
with A.app.app_context():
    n = A.get_db().execute("SELECT COUNT(*) c FROM results WHERE semester_id=2").fetchone()["c"]
assert n == 0

# valid insert + upsert, then admin can preview draft slip, student cannot
for cat in (20, 25):
    a.post("/results", data={"_csrf": tok, "student_id": 1, "unit_id": 1, "semester_id": 2,
                             "cat": cat, "exam": 50})
with A.app.app_context():
    rows = A.get_db().execute("SELECT cat FROM results WHERE semester_id=2").fetchall()
assert len(rows) == 1 and rows[0]["cat"] == 25
assert a.get("/slip/1/2").status_code == 200
assert s.get("/slip/1/2").status_code == 403

print("All tests passed")
