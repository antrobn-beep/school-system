"""
create_admin.py
Creates (or upgrades) a single account with full oversight of the entire
school system: username 'admin', password 'admin'.

Run this against your EXISTING database (it does NOT reset it):
    python3 create_admin.py

SECURITY NOTE: 'admin' / 'admin' is a placeholder credential for getting
started. Before this system is used with real student/staff data, change
this password (db.set_password('admin', 'a-real-password')) or better,
issue yourself a proper one via generate_temp_password_for() + a forced
reset on first login.
"""

from db import SchoolDB

db = SchoolDB("school.db")  # reset=False: does NOT wipe existing data

ADMIN_STAFF_NO = "admin"

existing = db.conn.execute("SELECT 1 FROM teachers WHERE staff_no=?", (ADMIN_STAFF_NO,)).fetchone()
if existing:
    print(f"Account '{ADMIN_STAFF_NO}' already exists -- resetting its password to 'admin' "
          f"and re-confirming its roles.")
    db.set_password(ADMIN_STAFF_NO, "admin", force_reset=False)
else:
    db.add_teacher("System", "Administrator", staff_no=ADMIN_STAFF_NO, password="admin")
    print(f"Created account '{ADMIN_STAFF_NO}' / password 'admin'.")

# Grant every executive role -- this is what makes 'admin' see and do
# EVERYTHING: every class, every department, every report, results
# publishing, and end-of-year promotion/graduation.
for role in ("principal", "deputy_principal_academics", "deputy_principal_admin", "dean_of_studies"):
    db.grant_role(ADMIN_STAFF_NO, role)

print("\nRoles granted:")
for r in db.get_roles(ADMIN_STAFF_NO):
    print(f"  {r['role']} ({r['scope']})")

print(f"\nVisible classes: {db.visible_classes(ADMIN_STAFF_NO)}")
print(f"Can publish results / run promotions: {db.can_publish_results(ADMIN_STAFF_NO)}")
print(f"Has full oversight: {db.has_full_oversight(ADMIN_STAFF_NO)}")

db.close()
print("\nDone. Log in with staff_no 'admin' and password 'admin'.")
