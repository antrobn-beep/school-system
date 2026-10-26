"""
auth.py
A simple login layer on top of db.py. Once a teacher logs in, they get a
TeacherSession that can only print/view what their role entitles them to:

- Class list for class X    -> only X's class teacher, or the supervisor of
                                X's form, may print it.
- Report card for a student -> only that student's class teacher, or the
                                supervisor of that student's form, may print it.
- Form-wide merit list       -> only the supervisor of that form may print it
                                (it spans every stream, so it's a form-level view).
- Marks views / entry        -> delegated to db.py's existing per-class scoping
                                (class teacher / supervisor / assigned subject teacher).

Anyone who isn't authorized gets a PermissionDenied -- nothing is printed.
"""

from typing import Optional

from db import SchoolDB, PermissionDenied, AuthError
from class_list import generate_class_list
from report import generate_report_card
from merit_list import generate_merit_list
from school_reports import generate_school_merit_list, generate_department_performance


def login(db: SchoolDB, identifier: str, password: str) -> "TeacherSession":
    """identifier can be a staff_no (e.g. 'TSC0001') or the teacher's email."""
    if not db.verify_password(identifier, password):
        raise AuthError("Invalid username or password.")
    staff_no = db._resolve_login_identifier(identifier)
    session = TeacherSession(db, staff_no)
    session.must_reset_password = db.must_reset_password(staff_no)
    return session


class TeacherSession:
    def __init__(self, db: SchoolDB, staff_no: str):
        self.db = db
        self.staff_no = staff_no
        self.scope = db.teacher_scope(staff_no)
        self.roles = {r["role"]: r["scope"] for r in db.get_roles(staff_no)}

    # ---- permission checks ----
    def _is_class_teacher_of(self, class_code: str) -> bool:
        return class_code in self.scope["class_teacher_of"]

    def _supervises_form(self, form_number: int) -> bool:
        return form_number in self.scope["supervises_forms"]

    def _can_manage_class(self, form_number: int, stream_code: str) -> bool:
        class_code = f"{form_number}{stream_code}"
        return self._is_class_teacher_of(class_code) or self._supervises_form(form_number)

    def _is_executive(self) -> bool:
        """Principal, either Deputy Principal, or Dean of Studies: full school-wide access."""
        return self.db.has_full_oversight(self.staff_no)

    # ---- capability checks, for the UI to show/hide/explain instead of erroring ----
    def can_edit_submitted_mark(self, adm_no: str) -> bool:
        return self.db.can_edit_submitted_mark(self.staff_no, adm_no)

    def who_can_edit_submitted_mark(self, adm_no: str) -> str:
        return self.db.who_can_edit_submitted_mark(adm_no)

    def can_manage_staffing(self, form_number: int) -> bool:
        return self.db.can_manage_staffing(self.staff_no, form_number)

    def can_print_class_list_for(self, form_number: int, stream_code: str) -> bool:
        return self.db.can_print_class_list(self.staff_no, form_number, stream_code)

    def can_print_form_merit_list_for(self, form_number: int) -> bool:
        return self.db.can_print_form_merit_list(self.staff_no, form_number)

    def can_print_school_merit_list(self) -> bool:
        return self.db.can_print_school_merit_list(self.staff_no)

    def can_view_audit_log(self) -> bool:
        return self.db.can_view_audit_log(self.staff_no)

    # ---- class list ----
    def print_class_list(self, form_number: int, stream_name_or_code: str,
                          output_path: str, exam_id: Optional[int] = None) -> str:
        stream_code = self.db._resolve_stream_code(form_number, stream_name_or_code)
        if not (self._can_manage_class(form_number, stream_code) or self._is_executive()):
            raise PermissionDenied(
                f"Teacher {self.staff_no} is not the class teacher of {form_number}{stream_code} "
                f"(and does not supervise Form {form_number} or hold an executive role), so they "
                f"may not print this class list."
            )
        return generate_class_list(self.db, form_number, stream_code, output_path, exam_id=exam_id)

    # ---- individual report card ----
    def print_report_card(self, student_adm_no: str, exam_id: int, output_path: str) -> str:
        student = self.db.conn.execute(
            "SELECT form_number, stream_code FROM students WHERE adm_no=?", (student_adm_no,)
        ).fetchone()
        if not student:
            raise PermissionDenied(f"No such student '{student_adm_no}'.")
        if not (self._can_manage_class(student["form_number"], student["stream_code"]) or self._is_executive()):
            class_code = f"{student['form_number']}{student['stream_code']}"
            raise PermissionDenied(
                f"Teacher {self.staff_no} is not the class teacher of {class_code} "
                f"(and does not supervise Form {student['form_number']} or hold an executive "
                f"role), so they may not print this student's report card."
            )
        if not self._is_executive() and not self.db.is_exam_published(exam_id):
            raise PermissionDenied(
                "Results for this exam have not been published yet. The report card can only be "
                "printed once an executive publishes results."
            )
        return generate_report_card(self.db, student_adm_no, exam_id, output_path)

    # ---- form-wide merit list ----
    def print_merit_list(self, form_number: int, exam_id: int, output_path: str) -> str:
        if not (self._supervises_form(form_number) or self._is_executive() or "dean_of_studies" in self.roles):
            raise PermissionDenied(
                f"Teacher {self.staff_no} does not supervise Form {form_number} and holds no "
                f"executive role, so they may not print the form-wide merit list."
            )
        if not self._is_executive() and not self.db.is_exam_published(exam_id):
            raise PermissionDenied(
                "Results for this exam have not been published yet. The merit list can only be "
                "printed once an executive publishes results."
            )
        return generate_merit_list(self.db, form_number, exam_id, output_path)

    # ---- school-wide merit list: executives + Dean of Studies only ----
    def print_school_merit_list(self, exam_id: int, output_path: str) -> str:
        if not (self._is_executive() or "dean_of_studies" in self.roles):
            raise PermissionDenied(
                f"Teacher {self.staff_no} may not print the school-wide merit list -- only the "
                f"Principal, a Deputy Principal, or the Dean of Studies can."
            )
        return generate_school_merit_list(self.db, exam_id, output_path)

    # ---- department performance: the HOD of that department, or executives ----
    def print_department_performance(self, department_code: str, exam_id: int, output_path: str) -> str:
        if not (self.db.has_role(self.staff_no, "hod", scope=department_code) or self._is_executive()
                or "dean_of_studies" in self.roles):
            raise PermissionDenied(
                f"Teacher {self.staff_no} is not the HOD of department '{department_code}' "
                f"(and holds no executive role), so they may not print this report."
            )
        return generate_department_performance(self.db, department_code, exam_id, output_path)

    # ---- results publishing ----
    def publish_results(self, exam_id: int):
        self.db.publish_exam_results(self.staff_no, exam_id)  # db enforces the role check itself

    # ---- moderation (HOD only, enforced inside db.py) ----
    def add_moderation_note(self, subject: str, form_number: int, stream_name_or_code: str,
                             exam_id: int, note: str):
        self.db.add_moderation_note(self.staff_no, subject, form_number, stream_name_or_code, exam_id, note)

    # ---- attendance / discipline (class teacher or supervisor, enforced inside db.py) ----
    def record_attendance(self, student_adm_no: str, date: str, status: str):
        self.db.record_attendance(self.staff_no, student_adm_no, date, status)

    def add_discipline_record(self, student_adm_no: str, date: str, description: str, severity: str = "minor"):
        self.db.add_discipline_record(self.staff_no, student_adm_no, date, description, severity)

    # ---- scoped marks view (delegates to db.py's existing per-class scoping) ----
    def view_class_marks(self, form_number: int, stream_name_or_code: str, exam_id: int):
        return self.db.marks_view_for_teacher(self.staff_no, form_number, stream_name_or_code, exam_id)

    def enter_mark(self, student_adm_no: str, subject: str, exam_id: int, score: float):
        self.db.save_draft_mark(self.staff_no, student_adm_no, subject, exam_id, score)

    def submit_mark(self, student_adm_no: str, subject: str, exam_id: int):
        self.db.submit_mark(self.staff_no, student_adm_no, subject, exam_id)

    def edit_submitted_mark(self, student_adm_no: str, subject: str, exam_id: int, new_score: float):
        self.db.edit_submitted_mark(self.staff_no, student_adm_no, subject, exam_id, new_score)
