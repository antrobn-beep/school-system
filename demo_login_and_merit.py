"""
demo_login_and_merit.py
Demonstrates:
1. Teacher login (password-based).
2. A form-wide merit list ranking every student individually.
3. The permission wall: a class teacher can only print THEIR class's list;
   the form supervisor can print any class in their form, or the merit list;
   a teacher with no role in a class/form is blocked outright.
"""

from db import SchoolDB, PermissionDenied, AuthError
from auth import login

db = SchoolDB("school.db", reset=True)

# --- staff, with passwords this time ---
ct_white = db.add_teacher("Paul", "Kiplagat", password="paul123")     # class teacher of 1W
ct_green = db.add_teacher("Grace", "Wanjiru", password="grace123")    # class teacher of 1G
supervisor = db.add_teacher("Ann", "Kamau", password="ann123")        # Form 1 supervisor
maths_teacher = db.add_teacher("James", "Otieno", subjects=["Maths"], password="james123")

db.set_class_teacher(1, "White", ct_white)
db.set_class_teacher(1, "Green", ct_green)
db.set_supervisor(1, supervisor)
db.grant_role(supervisor, "dean_of_studies")  # so she can also publish results
db.assign_subject_teacher(maths_teacher, "Maths", 1, "White")
db.assign_subject_teacher(maths_teacher, "Maths", 1, "Green")

exam_id = db.add_exam("Term 1 Midterm", term=1, year=2026)

# --- students & marks across two Form 1 streams ---
white_students = [db.add_student("Brian", "Otieno", 1, "White"),
                   db.add_student("Faith", "Achieng", 1, "White")]
green_students = [db.add_student("Kevin", "Njoroge", 1, "Green"),
                   db.add_student("Diana", "Mueni", 1, "Green")]

for adm_no, score in zip(white_students, [78, 91]):
    db.save_draft_mark(maths_teacher, adm_no, "Maths", exam_id, score)
    db.submit_mark(maths_teacher, adm_no, "Maths", exam_id)

for adm_no, score in zip(green_students, [55, 88]):
    db.save_draft_mark(maths_teacher, adm_no, "Maths", exam_id, score)
    db.submit_mark(maths_teacher, adm_no, "Maths", exam_id)

# ---------------------------------------------------------------
# 1. Login
# ---------------------------------------------------------------
print("--- Login ---")
try:
    login(db, ct_white, "wrong-password")
    print("ERROR: logged in with wrong password (should not happen)")
except AuthError as e:
    print(f"Correctly rejected bad password: {e}")

paul_session = login(db, ct_white, "paul123")
grace_session = login(db, ct_green, "grace123")
ann_session = login(db, supervisor, "ann123")
james_session = login(db, maths_teacher, "james123")
print("All valid logins succeeded.")

# ---------------------------------------------------------------
# 2. Class list permission wall
# ---------------------------------------------------------------
print("\n--- Class list permissions ---")

# Paul (class teacher of 1W) CAN print 1W's class list
path = paul_session.print_class_list(1, "White", "/mnt/user-data/outputs/class_list_1W_by_paul.pdf", exam_id=exam_id)
print(f"Paul printed his own class (1W) list: {path}")

# Paul CANNOT print 1G's class list (not his class, not a supervisor)
try:
    paul_session.print_class_list(1, "Green", "/tmp/should_not_exist.pdf", exam_id=exam_id)
    print("ERROR: Paul printed 1G's list (should not happen)")
except PermissionDenied as e:
    print(f"Correctly blocked: {e}")

# Ann (Form 1 supervisor) CAN print ANY class list in Form 1, including 1G
path = ann_session.print_class_list(1, "Green", "/mnt/user-data/outputs/class_list_1G_by_ann.pdf", exam_id=exam_id)
print(f"Ann (supervisor) printed 1G's list: {path}")

# James (subject teacher only, not a class teacher/supervisor anywhere) CANNOT print any class list
try:
    james_session.print_class_list(1, "White", "/tmp/should_not_exist2.pdf", exam_id=exam_id)
    print("ERROR: James printed a class list (should not happen)")
except PermissionDenied as e:
    print(f"Correctly blocked: {e}")

# ---------------------------------------------------------------
# 2b. Publish results -- merit lists require this now (except for executives)
# ---------------------------------------------------------------
ann_session.publish_results(exam_id)
print(f"\nAnn (Dean of Studies) published exam {exam_id}'s results.")

# ---------------------------------------------------------------
# 3. Merit list permission wall (form supervisor only)
# ---------------------------------------------------------------
print("\n--- Merit list permissions ---")

path = ann_session.print_merit_list(1, exam_id, "/mnt/user-data/outputs/merit_list_form1.pdf")
print(f"Ann (supervisor) printed the Form 1 merit list: {path}")

try:
    paul_session.print_merit_list(1, exam_id, "/tmp/should_not_exist3.pdf")
    print("ERROR: Paul (a class teacher, not the supervisor) printed the merit list (should not happen)")
except PermissionDenied as e:
    print(f"Correctly blocked: {e}")

# ---------------------------------------------------------------
# 4. What the merit list actually contains
# ---------------------------------------------------------------
print("\n--- Form 1 merit list contents ---")
for entry in db.form_merit_list(1, exam_id):
    print(f"  #{entry['position']}: {entry['name']:<15} {entry['class_code']}  "
          f"mean={entry['mean']:<6} grade={entry['mean_grade']}")

db.close()
