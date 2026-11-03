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


# ---------------------------------------------------------------- transcript / CGPA
import io

with A.app.app_context():
    sems, credits, cgpa = A.transcript_data(1, published_only=True)
assert [x["label"] for x in sems] == ["2026 May-Aug"] and credits == 14 and cgpa == 3.21, (sems, cgpa)
assert s.get("/transcript/1").status_code == 200          # own transcript
assert s.get("/transcript/2").status_code == 403          # someone else's
assert a.get("/transcript/1").status_code == 200          # staff
# publishing semester 2 adds it to the CGPA: (45 + 12) points / (14 + 3) credits
tok = csrf(a, "/semesters")
a.post("/semesters/2/toggle", data={"_csrf": tok})
with A.app.app_context():
    sems, credits, cgpa = A.transcript_data(1, published_only=True)
assert credits == 17 and cgpa == round(57 / 17, 2), (credits, cgpa)

# ---------------------------------------------------------------- CSV import
def count_sem2():
    with A.app.app_context():
        return A.get_db().execute("SELECT COUNT(*) c FROM results WHERE semester_id=2").fetchone()["c"]

def upload(client, text, sem=2):
    t = csrf(client, "/results/import")
    return client.post("/results/import", data={"_csrf": t, "semester_id": sem,
                       "file": (io.BytesIO(text.encode()), "marks.csv")},
                       content_type="multipart/form-data")

assert s.get("/results/import").status_code == 403         # students cannot import
before = count_sem2()
r = upload(a, "reg_no,unit_code,cat,exam\n2022/002,CS301,15,30\n2023/001,CS302,25,58\n")
assert "Imported 2 marks" in r.get_data(as_text=True) and count_sem2() == before + 2
r = upload(a, "reg_no,unit_code,cat,exam\n2022/002,CS302,12,28\n9999/999,CS301,10,10\n2023/001,CS303,40,10\n")
txt = r.get_data(as_text=True)
assert "Nothing was imported" in txt and "unknown student" in txt and "CAT must be 0-30" in txt
assert count_sem2() == before + 2                          # all-or-nothing: the good row was not saved
r = upload(a, "name,mark\nx,1\n")
assert "Header must contain" in r.get_data(as_text=True)

# ---------------------------------------------------------------- audit log
assert s.get("/audit").status_code == 403
page = a.get("/audit").get_data(as_text=True)
assert "result.add" in page and "result.update" in page and "CAT 20-&gt;25" in page
assert "result.import" in page and "semester.publish" in page

# ---------------------------------------------------------------- password reset
tok = csrf(s, "/change-password")
s.post("/change-password", data={"_csrf": tok, "old": "2022/001", "new": "newpass123"})
def can_login(user, pw):
    c = A.app.test_client()
    t = re.search(r'name="_csrf" value="(\w+)"', c.get("/login").get_data(as_text=True)).group(1)
    c.post("/login", data={"username": user, "password": pw, "_csrf": t})
    return c.get("/my-slips").status_code == 200
assert can_login("2022/001", "newpass123") and not can_login("2022/001", "2022/001")
assert s.post("/students/1/reset-password", data={"_csrf": tok}).status_code == 403   # students cannot reset
tok = csrf(a, "/students")
a.post("/students/1/reset-password", data={"_csrf": tok})
assert can_login("2022/001", "2022/001") and not can_login("2022/001", "newpass123")
assert "student.password_reset" in a.get("/audit").get_data(as_text=True)

print("All tests passed")
