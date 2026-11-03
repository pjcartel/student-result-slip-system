"""Create the database and load demo data. Run: python seed.py"""
import os
from werkzeug.security import generate_password_hash
import app as A

if os.path.exists(A.DB_PATH):
    os.remove(A.DB_PATH)
A.init_db()

import sqlite3
db = sqlite3.connect(A.DB_PATH)
db.execute("PRAGMA foreign_keys = ON")

def user(name, pw, role, sid=None):
    db.execute("INSERT INTO users(username,password_hash,role,student_id) VALUES(?,?,?,?)",
               (name, generate_password_hash(pw), role, sid))

user("admin", "admin123", "admin")
user("lecturer", "lecturer123", "lecturer")

students = [("2022/001", "Amina Wanjiru", "Computer Studies", 3),
            ("2022/002", "Brian Otieno", "Computer Studies", 3),
            ("2023/001", "Cynthia Mutua", "Computer Studies", 2)]
for reg, name, prog, yr in students:
    cur = db.execute("INSERT INTO students(reg_no,name,programme,year_of_study) VALUES(?,?,?,?)",
                     (reg, name, prog, yr))
    user(reg.lower(), reg, "student", cur.lastrowid)

units = [("CS301", "Database Systems", 3), ("CS302", "Software Engineering", 3),
         ("CS303", "Computer Networks", 3), ("CS304", "Web Application Development", 3),
         ("CS305", "Systems Analysis and Design", 2)]
for u in units:
    db.execute("INSERT INTO units(code,title,credit_hours) VALUES(?,?,?)", u)

db.execute("INSERT INTO semesters(label,published) VALUES('2026 May-Aug',1)")
db.execute("INSERT INTO semesters(label,published) VALUES('2026 Sept-Dec',0)")

marks = {1: [(24, 52), (22, 45), (18, 40), (27, 60), (20, 48)],
         2: [(15, 30), (12, 28), (20, 35), (10, 25), (14, 22)],
         3: [(28, 66), (25, 58), (26, 62), (29, 65), (27, 60)]}
for sid, rows in marks.items():
    for uid, (cat, exam) in enumerate(rows, start=1):
        db.execute("INSERT INTO results(student_id,unit_id,semester_id,cat,exam,entered_by) VALUES(?,?,?,?,?,2)",
                   (sid, uid, 1, cat, exam))
db.commit()
db.close()
print("Seeded. Logins: admin/admin123, lecturer/lecturer123, student: 2022/001 (reg no lowercase) / password = reg no")
