"""
db.py
Real (SQLite) persistence layer for the school system -- EduLink core.

Covers everything from before (forms, streams, subjects, teachers, students,
class teachers, class supervisors) PLUS a marks workflow:

Marks workflow rules
---------------------
1. A subject teacher enters a student's mark for an exam -> saved as a DRAFT.
2. While a mark is a DRAFT, the subject teacher who owns it can keep editing
   it (corrections before submission).
3. When the subject teacher SUBMITS a mark (or a whole class's marks), its
   status flips to SUBMITTED and locks for that teacher.
4. Once SUBMITTED, only the class teacher of that student's class (i.e. the
   class teacher of that specific form+stream, e.g. "1W") may edit it.
   The original subject teacher can no longer edit a submitted mark.

Everything is persisted in a SQLite file (default: school.db), so data
survives across runs -- this is the "real database" version of the system.

Visibility rules
-----------------
A teacher only sees the classes they actually have a role in:
- Class teacher of "1W"       -> sees everything for class 1W (all subjects, all students).
- Class supervisor of Form 1  -> sees everything for every stream in Form 1.
- Subject teacher assigned to teach e.g. Biology in "2G" -> sees ONLY Biology
  marks for students in 2G. The same teacher may teach Chemistry in a
  different class ("3R") and would see only Chemistry there.

A teacher must be explicitly assigned (assign_subject_teacher) to a specific
class before they can enter marks for it -- just "knowing" a subject isn't
enough, they must be timetabled for that class.
"""

import sqlite3
import hashlib
import os
import secrets
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any


DEFAULT_STREAMS = [
    "White", "Purple", "Green",
]



SUBJECT_CODE_OVERRIDES = {"cre": "CRE", "english": "ENG", "kiswahili": "KIS", "aviation": "AVI"}

# KCSE-style grading scale (percentage -> letter grade)
GRADE_BANDS = [
    (80, "A"), (75, "A-"), (70, "B+"), (65, "B"), (60, "B-"),
    (55, "C+"), (50, "C"), (45, "C-"), (40, "D+"), (35, "D"),
    (30, "D-"), (0, "E"),
]

# KCSE-style points per grade (A=12 down to E=1)
GRADE_POINTS = {
    "A": 12, "A-": 11, "B+": 10, "B": 9, "B-": 8, "C+": 7,
    "C": 6, "C-": 5, "D+": 4, "D": 3, "D-": 2, "E": 1,
}

EXECUTIVE_ROLES = {"principal", "deputy_principal_academics", "deputy_principal_admin", "dean_of_studies", "hod"}


class PermissionDenied(Exception):
    """Raised when a teacher tries to do something their role doesn't allow."""


class AuthError(Exception):
    """Raised on a failed login (unknown staff number or wrong password)."""


class NotFound(Exception):
    """Raised when a referenced record does not exist."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def subject_code(name: str) -> str:
    return SUBJECT_CODE_OVERRIDES.get(name.strip().lower(), name.strip()[:3].upper())


def grade_for(score: float) -> str:
    for cutoff, letter in GRADE_BANDS:
        if score >= cutoff:
            return letter
    return "E"


def points_for(grade: str) -> int:
    return GRADE_POINTS.get(grade, 0)


class SchoolDB:
    def __init__(self, path: str = "school.db", reset: bool = False):
        self.path = path
        if reset:
            import os
            if path != ":memory:" and os.path.exists(path):
                os.remove(path)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()
        self._migrate()
        self._seed_defaults()

    # ------------------------------------------------------------------
    # schema / seeding
    # ------------------------------------------------------------------
    def _init_schema(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS forms (
                number INTEGER PRIMARY KEY CHECK(number BETWEEN 1 AND 4)
            );

            CREATE TABLE IF NOT EXISTS teachers (
                staff_no      TEXT PRIMARY KEY,
                first_name    TEXT NOT NULL,
                last_name     TEXT NOT NULL,
                password_hash TEXT,
                password_salt TEXT
            );

            CREATE TABLE IF NOT EXISTS subjects (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS teacher_subjects (
                staff_no     TEXT NOT NULL REFERENCES teachers(staff_no),
                subject_code TEXT NOT NULL REFERENCES subjects(code),
                PRIMARY KEY (staff_no, subject_code)
            );

            CREATE TABLE IF NOT EXISTS streams (
                form_number             INTEGER NOT NULL REFERENCES forms(number),
                code                    TEXT NOT NULL,
                name                    TEXT NOT NULL,
                class_teacher_staff_no  TEXT REFERENCES teachers(staff_no),
                PRIMARY KEY (form_number, code)
            );

            CREATE TABLE IF NOT EXISTS form_supervisors (
                form_number INTEGER PRIMARY KEY REFERENCES forms(number),
                staff_no    TEXT NOT NULL REFERENCES teachers(staff_no)
            );

            CREATE TABLE IF NOT EXISTS students (
                adm_no      TEXT PRIMARY KEY,
                first_name  TEXT NOT NULL,
                last_name   TEXT NOT NULL,
                form_number INTEGER NOT NULL,
                stream_code TEXT NOT NULL,
                FOREIGN KEY (form_number, stream_code) REFERENCES streams(form_number, code)
            );

            CREATE TABLE IF NOT EXISTS exams (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                term INTEGER NOT NULL,
                year INTEGER NOT NULL,
                UNIQUE(name, term, year)
            );

            CREATE TABLE IF NOT EXISTS subject_assignments (
                staff_no     TEXT NOT NULL REFERENCES teachers(staff_no),
                subject_code TEXT NOT NULL REFERENCES subjects(code),
                form_number  INTEGER NOT NULL,
                stream_code  TEXT NOT NULL,
                PRIMARY KEY (staff_no, subject_code, form_number, stream_code),
                FOREIGN KEY (form_number, stream_code) REFERENCES streams(form_number, code)
            );

            CREATE TABLE IF NOT EXISTS marks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                student_adm_no  TEXT NOT NULL REFERENCES students(adm_no),
                subject_code    TEXT NOT NULL REFERENCES subjects(code),
                exam_id         INTEGER NOT NULL REFERENCES exams(id),
                score           REAL NOT NULL CHECK(score >= 0 AND score <= 100),
                status          TEXT NOT NULL CHECK(status IN ('draft','submitted')) DEFAULT 'draft',
                entered_by      TEXT NOT NULL REFERENCES teachers(staff_no),
                last_edited_by  TEXT REFERENCES teachers(staff_no),
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                UNIQUE(student_adm_no, subject_code, exam_id)
            );

            -- ---- departments & school-level roles ----
            CREATE TABLE IF NOT EXISTS departments (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS department_subjects (
                department_code TEXT NOT NULL REFERENCES departments(code),
                subject_code    TEXT NOT NULL REFERENCES subjects(code),
                PRIMARY KEY (department_code, subject_code)
            );

            -- role in {'principal','deputy_principal_academics','deputy_principal_admin',
            --          'dean_of_studies','hod'}; scope is 'school' except for 'hod' where
            -- scope is the department code.
            CREATE TABLE IF NOT EXISTS staff_roles (
                staff_no TEXT NOT NULL REFERENCES teachers(staff_no),
                role     TEXT NOT NULL,
                scope    TEXT NOT NULL DEFAULT 'school',
                PRIMARY KEY (staff_no, role, scope)
            );

            -- ---- results publishing & moderation ----
            CREATE TABLE IF NOT EXISTS exam_publications (
                exam_id      INTEGER PRIMARY KEY REFERENCES exams(id),
                published_by TEXT NOT NULL REFERENCES teachers(staff_no),
                published_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS moderation_notes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_no     TEXT NOT NULL REFERENCES teachers(staff_no),
                subject_code TEXT NOT NULL,
                form_number  INTEGER NOT NULL,
                stream_code  TEXT NOT NULL,
                exam_id      INTEGER NOT NULL REFERENCES exams(id),
                note         TEXT NOT NULL,
                created_at   TEXT NOT NULL
            );

            -- ---- attendance ----
            CREATE TABLE IF NOT EXISTS attendance (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                student_adm_no TEXT NOT NULL REFERENCES students(adm_no),
                date           TEXT NOT NULL,
                status         TEXT NOT NULL CHECK(status IN ('present','absent','late')),
                recorded_by    TEXT NOT NULL REFERENCES teachers(staff_no),
                UNIQUE(student_adm_no, date)
            );

            -- ---- timetable ----
            CREATE TABLE IF NOT EXISTS timetable (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                form_number  INTEGER NOT NULL,
                stream_code  TEXT NOT NULL,
                day_of_week  TEXT NOT NULL,
                period       INTEGER NOT NULL,
                subject_code TEXT NOT NULL REFERENCES subjects(code),
                staff_no     TEXT NOT NULL REFERENCES teachers(staff_no),
                UNIQUE(form_number, stream_code, day_of_week, period)
            );

            -- ---- discipline ----
            CREATE TABLE IF NOT EXISTS discipline_records (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                student_adm_no TEXT NOT NULL REFERENCES students(adm_no),
                date           TEXT NOT NULL,
                description    TEXT NOT NULL,
                severity       TEXT NOT NULL CHECK(severity IN ('minor','moderate','major')),
                recorded_by    TEXT NOT NULL REFERENCES teachers(staff_no)
            );

            -- ---- fees ----
            CREATE TABLE IF NOT EXISTS fee_invoices (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                student_adm_no TEXT NOT NULL REFERENCES students(adm_no),
                term           INTEGER NOT NULL,
                year           INTEGER NOT NULL,
                amount_billed  REAL NOT NULL,
                UNIQUE(student_adm_no, term, year)
            );

            CREATE TABLE IF NOT EXISTS fee_payments (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                student_adm_no TEXT NOT NULL REFERENCES students(adm_no),
                amount         REAL NOT NULL,
                date           TEXT NOT NULL,
                method         TEXT,
                recorded_by    TEXT REFERENCES teachers(staff_no)
            );

            -- ---- audit log ----
            CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                entity     TEXT NOT NULL,
                entity_id  TEXT NOT NULL,
                action     TEXT NOT NULL,
                actor      TEXT NOT NULL,
                details    TEXT,
                created_at TEXT NOT NULL
            );

            -- ---- parents / guardians ----
            CREATE TABLE IF NOT EXISTS parents (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name    TEXT NOT NULL,
                last_name     TEXT NOT NULL,
                phone         TEXT,
                email         TEXT UNIQUE,
                password_hash TEXT,
                password_salt TEXT
            );

            CREATE TABLE IF NOT EXISTS parent_students (
                parent_id      INTEGER NOT NULL REFERENCES parents(id),
                student_adm_no TEXT NOT NULL REFERENCES students(adm_no),
                PRIMARY KEY (parent_id, student_adm_no)
            );

            -- ---- notifications (stub: queued, not actually dispatched) ----
            CREATE TABLE IF NOT EXISTS notifications (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_type TEXT NOT NULL,
                recipient_id   TEXT NOT NULL,
                channel        TEXT NOT NULL CHECK(channel IN ('sms','email')),
                message        TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT 'queued',
                created_at     TEXT NOT NULL
            );
            -- ---- alumni (Form 4 graduates leave the students table and land here) ----
            CREATE TABLE IF NOT EXISTS alumni (
                adm_no          TEXT PRIMARY KEY,
                first_name      TEXT NOT NULL,
                last_name       TEXT NOT NULL,
                graduation_year INTEGER NOT NULL,
                final_class     TEXT NOT NULL,
                contact_email   TEXT,
                contact_phone   TEXT
            );

            -- ---- fee structure (billing rate per form/term/year) ----
            CREATE TABLE IF NOT EXISTS fee_structures (
                form_number INTEGER NOT NULL,
                term        INTEGER NOT NULL,
                year        INTEGER NOT NULL,
                amount      REAL NOT NULL,
                PRIMARY KEY (form_number, term, year)
            );

            -- ---- school expenses ----
            CREATE TABLE IF NOT EXISTS expenses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category    TEXT NOT NULL,
                amount      REAL NOT NULL,
                date        TEXT NOT NULL,
                description TEXT,
                recorded_by TEXT REFERENCES teachers(staff_no)
            );

            -- ---- exam paper timetable ----
            CREATE TABLE IF NOT EXISTS exam_timetable (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                exam_id      INTEGER NOT NULL REFERENCES exams(id),
                subject_code TEXT NOT NULL REFERENCES subjects(code),
                date         TEXT NOT NULL,
                start_time   TEXT,
                end_time     TEXT,
                UNIQUE(exam_id, subject_code)
            );

            -- ---- school calendar ----
            CREATE TABLE IF NOT EXISTS calendar_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                description TEXT,
                date        TEXT NOT NULL,
                end_date    TEXT,
                category    TEXT NOT NULL DEFAULT 'general',
                form_number INTEGER,
                created_by  TEXT REFERENCES teachers(staff_no),
                created_at  TEXT NOT NULL
            );

            -- ---- staffing delegation: who besides oversight may assign class
            -- teachers/subject teachers/supervisors for a given form ----
            CREATE TABLE IF NOT EXISTS staffing_managers (
                staff_no    TEXT NOT NULL REFERENCES teachers(staff_no),
                form_number INTEGER NOT NULL,
                PRIMARY KEY (staff_no, form_number)
            );

            -- ---- teacher groups (committees, clubs, ad-hoc groupings) ----
            CREATE TABLE IF NOT EXISTS teacher_groups (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS teacher_group_members (
                group_id INTEGER NOT NULL REFERENCES teacher_groups(id),
                staff_no TEXT NOT NULL REFERENCES teachers(staff_no),
                PRIMARY KEY (group_id, staff_no)
            );

            -- ---- payment channels (bank, paybill, till...) for auto-detected fee payments ----
            CREATE TABLE IF NOT EXISTS payment_channels (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'bank'
            );

            -- ---- pledges (parent commitments to pay later) ----
            CREATE TABLE IF NOT EXISTS pledges (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                student_adm_no TEXT NOT NULL REFERENCES students(adm_no),
                amount         REAL NOT NULL,
                expected_date  TEXT,
                notes          TEXT,
                fulfilled      INTEGER NOT NULL DEFAULT 0,
                created_at     TEXT NOT NULL
            );

            -- ---- configurable settings (admission/staff numbering criteria, etc.) ----
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            -- ---- HR: staff contracts ----
            CREATE TABLE IF NOT EXISTS staff_contracts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_no      TEXT NOT NULL REFERENCES teachers(staff_no),
                contract_type TEXT NOT NULL,
                start_date    TEXT NOT NULL,
                end_date      TEXT,
                notes         TEXT,
                created_at    TEXT NOT NULL
            );

            -- ---- HR: staff leave ----
            CREATE TABLE IF NOT EXISTS staff_leave (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_no     TEXT NOT NULL REFERENCES teachers(staff_no),
                leave_type   TEXT NOT NULL,
                start_date   TEXT NOT NULL,
                end_date     TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
                notes        TEXT,
                requested_at TEXT NOT NULL,
                decided_by   TEXT REFERENCES teachers(staff_no),
                decided_at   TEXT
            );

            -- ---- in-app staff messaging ----
            CREATE TABLE IF NOT EXISTS messages (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_staff_no   TEXT NOT NULL REFERENCES teachers(staff_no),
                recipient_staff_no TEXT NOT NULL REFERENCES teachers(staff_no),
                subject           TEXT NOT NULL,
                body              TEXT NOT NULL,
                sent_at           TEXT NOT NULL,
                read_at           TEXT
            );
            """
        )
        self.conn.commit()

    def _migrate(self):
        """Add columns to tables that existed before this feature set (idempotent)."""
        migrations = [
            ("exams", "category TEXT DEFAULT 'General'"),
            ("exams", "weight REAL DEFAULT 1.0"),
            ("fee_payments", "term INTEGER"),
            ("fee_payments", "year INTEGER"),
            ("teachers", "email TEXT"),
            ("teachers", "force_password_reset INTEGER DEFAULT 0"),
            ("fee_payments", "channel_code TEXT"),
            ("fee_payments", "reference TEXT"),
            ("students", "gender TEXT"),
            ("expenses", "receipt_path TEXT"),
            ("teachers", "account_locked INTEGER DEFAULT 0"),
        ]
        for table, coldef in migrations:
            try:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
            except sqlite3.OperationalError:
                pass  # column already exists
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS password_resets (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                account_type TEXT NOT NULL CHECK(account_type IN ('teacher','parent')),
                account_id   TEXT NOT NULL,
                token_hash   TEXT NOT NULL,
                expires_at   TEXT NOT NULL,
                used         INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def _seed_defaults(self):
        cur = self.conn.cursor()
        
        # Ensure forms 1 to 4 exist without resetting anything
        for n in (1, 2, 3, 4):
            self.conn.execute("INSERT OR IGNORE INTO forms(number) VALUES (?)", (n,))
            
            # Ensure default streams exist for each form
            for name in DEFAULT_STREAMS:
                code = name[0].upper()
                self.conn.execute(
                    "INSERT OR IGNORE INTO streams(form_number, code, name) VALUES (?,?,?)",
                    (n, code, name),
                )
                
        # Ensure default subjects exist without overwriting custom changes
        for name in DEFAULT_SUBJECTS:
            self.conn.execute(
                "INSERT OR IGNORE INTO subjects(code, name) VALUES (?,?)",
                (subject_code(name), name),
            )
            
        self.conn.commit()

    # ------------------------------------------------------------------
    # streams
    # ------------------------------------------------------------------
    def add_stream(self, form_number: int, name: str):
        code = name.strip()[0].upper()
        existing = self.conn.execute(
            "SELECT 1 FROM streams WHERE form_number=? AND code=?", (form_number, code)
        ).fetchone()
        if existing:
            raise ValueError(f"Form {form_number}: a stream starting with '{code}' already exists.")
        self.conn.execute(
            "INSERT INTO streams(form_number, code, name) VALUES (?,?,?)",
            (form_number, code, name.strip().capitalize()),
        )
        self.conn.commit()

    def remove_stream(self, form_number: int, name_or_code: str, reassign_to: Optional[str] = None):
        code = self._resolve_stream_code(form_number, name_or_code)
        affected = self.conn.execute(
            "SELECT adm_no FROM students WHERE form_number=? AND stream_code=?",
            (form_number, code),
        ).fetchall()
        if affected and not reassign_to:
            raise ValueError(
                f"Form {form_number}-{code} has {len(affected)} student(s). "
                f"Pass reassign_to='<stream name or code>' first."
            )
        if affected:
            new_code = self._resolve_stream_code(form_number, reassign_to)
            self.conn.execute(
                "UPDATE students SET stream_code=? WHERE form_number=? AND stream_code=?",
                (new_code, form_number, code),
            )
        self.conn.execute("DELETE FROM streams WHERE form_number=? AND code=?", (form_number, code))
        self.conn.commit()

    def _resolve_stream_code(self, form_number: int, name_or_code: str) -> str:
        candidate = name_or_code.strip()
        row = self.conn.execute(
            "SELECT code FROM streams WHERE form_number=? AND (code=? OR LOWER(name)=?)",
            (form_number, candidate.upper(), candidate.lower()),
        ).fetchone()
        if not row:
            raise NotFound(f"Form {form_number}: no such stream '{name_or_code}'.")
        return row["code"]

    # ------------------------------------------------------------------
    # subjects
    # ------------------------------------------------------------------
    def add_subject(self, name: str):
        code = subject_code(name)
        self.conn.execute(
            "INSERT OR IGNORE INTO subjects(code, name) VALUES (?,?)", (code, name.strip())
        )
        self.conn.commit()
        return code

    def remove_subject(self, name_or_code: str):
        self.conn.execute(
            "DELETE FROM subjects WHERE code=? OR LOWER(name)=?",
            (name_or_code.strip().upper(), name_or_code.strip().lower()),
        )
        self.conn.commit()

    def list_subjects(self) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT code, name FROM subjects ORDER BY name").fetchall()

    # ------------------------------------------------------------------
    # teachers
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # configurable settings (ID numbering criteria, etc.)
    # ------------------------------------------------------------------
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        self.conn.execute(
            "INSERT INTO settings(key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        self.conn.commit()

    def set_id_criteria(self, kind: str, prefix: Optional[str] = None, padding: Optional[int] = None,
                         next_number: Optional[int] = None):
        """Configure how admission numbers or staff numbers are auto-generated.
        kind is 'admission' or 'staff'. Anything left as None is unchanged."""
        if kind not in ("admission", "staff"):
            raise ValueError("kind must be 'admission' or 'staff'.")
        if prefix is not None:
            self.set_setting(f"{kind}_prefix", prefix.strip().upper())
        if padding is not None:
            self.set_setting(f"{kind}_padding", str(int(padding)))
        if next_number is not None:
            self.set_setting(f"{kind}_next", str(int(next_number)))

    def get_id_criteria(self, kind: str) -> Dict[str, Any]:
        default_prefix = "ADM" if kind == "admission" else "TSC"
        return {
            "prefix": self.get_setting(f"{kind}_prefix", default_prefix),
            "padding": int(self.get_setting(f"{kind}_padding", "4")),
            "next": int(self.get_setting(f"{kind}_next", "1")),
        }

    def _generate_id(self, kind: str) -> str:
        criteria = self.get_id_criteria(kind)
        formatted = f"{criteria['prefix']}{criteria['next']:0{criteria['padding']}d}"
        self.set_setting(f"{kind}_next", str(criteria["next"] + 1))
        return formatted

    def add_teacher(self, first_name: str, last_name: str, staff_no: Optional[str] = None,
                     subjects: Optional[List[str]] = None, password: Optional[str] = None,
                     email: Optional[str] = None) -> str:
        """Username is staff_no, auto-generated from your configured numbering
        criteria (see set_id_criteria) if not supplied explicitly.
        If no password is given, a random temporary one is generated and the
        account is flagged to force a password change on first login -- the
        temp password is returned via the (staff_no, temp_password) tuple-like
        access on the caller side (see generate_temp_password_for)."""
        if not staff_no:
            staff_no = self._generate_id("staff")
        self.conn.execute(
            "INSERT INTO teachers(staff_no, first_name, last_name, email) VALUES (?,?,?,?)",
            (staff_no, first_name.strip(), last_name.strip(), email.strip().lower() if email else None),
        )
        for subj in (subjects or []):
            code = subject_code(subj)
            self.conn.execute(
                "INSERT OR IGNORE INTO teacher_subjects(staff_no, subject_code) VALUES (?,?)",
                (staff_no, code),
            )
        self.conn.commit()
        if password:
            self.set_password(staff_no, password)
        return staff_no

    def generate_temp_password_for(self, staff_no: str) -> str:
        """Issue a random temporary password and force a change at next login.
        Use this for accounts created by an admin without the teacher present
        (e.g. Dean of Studies onboarding a new teacher)."""
        self._require_teacher(staff_no)
        temp_password = secrets.token_urlsafe(9)  # e.g. 'kQ3f7z-Xp2mN1w'
        self.set_password(staff_no, temp_password, force_reset=True)
        return temp_password

    def _resolve_login_identifier(self, identifier: str) -> Optional[str]:
        """Login can be by staff_no OR email; resolve either to a staff_no."""
        row = self.conn.execute(
            "SELECT staff_no FROM teachers WHERE staff_no=? OR LOWER(email)=?",
            (identifier, identifier.strip().lower()),
        ).fetchone()
        return row["staff_no"] if row else None

    def set_password(self, staff_no: str, password: str, force_reset: bool = False):
        self._require_teacher(staff_no)
        salt = secrets.token_hex(16)
        pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000).hex()
        self.conn.execute(
            "UPDATE teachers SET password_hash=?, password_salt=?, force_password_reset=? WHERE staff_no=?",
            (pw_hash, salt, 1 if force_reset else 0, staff_no),
        )
        self.conn.commit()

    def must_reset_password(self, staff_no: str) -> bool:
        row = self.conn.execute(
            "SELECT force_password_reset FROM teachers WHERE staff_no=?", (staff_no,)
        ).fetchone()
        return bool(row and row["force_password_reset"])

    def verify_password(self, identifier: str, password: str) -> bool:
        """identifier can be a staff_no (e.g. 'TSC0001') or the teacher's email."""
        staff_no = self._resolve_login_identifier(identifier)
        if staff_no is None:
            return False
        row = self.conn.execute(
            "SELECT password_hash, password_salt FROM teachers WHERE staff_no=?", (staff_no,)
        ).fetchone()
        if row is None or not row["password_hash"]:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(row["password_salt"]), 100_000
        ).hex()
        return secrets.compare_digest(candidate, row["password_hash"])

    def teacher_teaches(self, staff_no: str, subject_code_: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM teacher_subjects WHERE staff_no=? AND subject_code=?",
            (staff_no, subject_code_.upper()),
        ).fetchone()
        return row is not None

    def grant_staffing_manager(self, form_number: int, staff_no: str):
        """Delegate 'who may assign class teachers/supervisors/subject teachers
        for this form' to someone besides Principal/Deputy/Dean."""
        self._require_teacher(staff_no)
        self.conn.execute(
            "INSERT OR IGNORE INTO staffing_managers(staff_no, form_number) VALUES (?,?)",
            (staff_no, form_number),
        )
        self.conn.commit()

    def revoke_staffing_manager(self, form_number: int, staff_no: str):
        self.conn.execute(
            "DELETE FROM staffing_managers WHERE staff_no=? AND form_number=?", (staff_no, form_number)
        )
        self.conn.commit()

    def is_staffing_manager(self, staff_no: str, form_number: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM staffing_managers WHERE staff_no=? AND form_number=?", (staff_no, form_number)
        ).fetchone() is not None

    def can_manage_staffing(self, staff_no: str, form_number: int) -> bool:
        """Who may add/remove class teachers, supervisors, and subject teacher
        assignments for a form: full-oversight executives, or a delegated
        staffing manager for that specific form."""
        return self.has_full_oversight(staff_no) or self.is_staffing_manager(staff_no, form_number)

    def staffing_managers_of(self, form_number: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            """SELECT t.* FROM staffing_managers sm JOIN teachers t ON t.staff_no = sm.staff_no
               WHERE sm.form_number=?""",
            (form_number,),
        ).fetchall()

    def staffing_manager_forms(self, staff_no: str) -> List[int]:
        """Every form this teacher has been delegated staffing management for."""
        return [r["form_number"] for r in self.conn.execute(
            "SELECT form_number FROM staffing_managers WHERE staff_no=? ORDER BY form_number", (staff_no,)
        ).fetchall()]

    def set_class_teacher(self, form_number: int, stream_name_or_code: str, staff_no: str,
                           requested_by: Optional[str] = None):
        if requested_by is not None and not self.can_manage_staffing(requested_by, form_number):
            raise PermissionDenied(
                f"Teacher {requested_by} may not assign staff for Form {form_number} -- only an "
                f"executive or a delegated staffing manager for this form can."
            )
        code = self._resolve_stream_code(form_number, stream_name_or_code)
        self._require_teacher(staff_no)
        self.conn.execute(
            "UPDATE streams SET class_teacher_staff_no=? WHERE form_number=? AND code=?",
            (staff_no, form_number, code),
        )
        self.conn.commit()
        self._audit("streams", f"{form_number}{code}", "set_class_teacher", requested_by or "system", staff_no)

    def set_supervisor(self, form_number: int, staff_no: str, requested_by: Optional[str] = None):
        if requested_by is not None and not self.can_manage_staffing(requested_by, form_number):
            raise PermissionDenied(
                f"Teacher {requested_by} may not assign staff for Form {form_number} -- only an "
                f"executive or a delegated staffing manager for this form can."
            )
        self._require_teacher(staff_no)
        self.conn.execute(
            "INSERT INTO form_supervisors(form_number, staff_no) VALUES (?,?) "
            "ON CONFLICT(form_number) DO UPDATE SET staff_no=excluded.staff_no",
            (form_number, staff_no),
        )
        self.conn.commit()
        self._audit("form_supervisors", str(form_number), "set_supervisor", requested_by or "system", staff_no)

    def remove_class_teacher(self, form_number: int, stream_name_or_code: str,
                              requested_by: Optional[str] = None):
        """Unassign the current class teacher from a class, leaving it Unassigned."""
        if requested_by is not None and not self.can_manage_staffing(requested_by, form_number):
            raise PermissionDenied(
                f"Teacher {requested_by} may not change staffing for Form {form_number} -- only an "
                f"executive or a delegated staffing manager for this form can."
            )
        code = self._resolve_stream_code(form_number, stream_name_or_code)
        self.conn.execute(
            "UPDATE streams SET class_teacher_staff_no=NULL WHERE form_number=? AND code=?",
            (form_number, code),
        )
        self.conn.commit()
        self._audit("streams", f"{form_number}{code}", "remove_class_teacher", requested_by or "system", "")

    def remove_supervisor(self, form_number: int, requested_by: Optional[str] = None):
        """Unassign the current class supervisor/principal from a form."""
        if requested_by is not None and not self.can_manage_staffing(requested_by, form_number):
            raise PermissionDenied(
                f"Teacher {requested_by} may not change staffing for Form {form_number} -- only an "
                f"executive or a delegated staffing manager for this form can."
            )
        self.conn.execute("DELETE FROM form_supervisors WHERE form_number=?", (form_number,))
        self.conn.commit()
        self._audit("form_supervisors", str(form_number), "remove_supervisor", requested_by or "system", "")

    def class_teacher_of_student(self, adm_no: str) -> Optional[sqlite3.Row]:
        row = self.conn.execute(
            """
            SELECT t.* FROM students s
            JOIN streams st ON st.form_number = s.form_number AND st.code = s.stream_code
            JOIN teachers t ON t.staff_no = st.class_teacher_staff_no
            WHERE s.adm_no = ?
            """,
            (adm_no,),
        ).fetchone()
        return row

    def _require_teacher(self, staff_no: str):
        if not self.conn.execute("SELECT 1 FROM teachers WHERE staff_no=?", (staff_no,)).fetchone():
            raise NotFound(f"No such teacher '{staff_no}'.")

    # ------------------------------------------------------------------
    # subject-teacher <-> class assignments (drives visibility)
    # ------------------------------------------------------------------
    def assign_subject_teacher(self, staff_no: str, subject: str, form_number: int,
                                stream_name_or_code: str, requested_by: Optional[str] = None):
        """Timetable a teacher to teach `subject` in one specific class (form+stream)."""
        if requested_by is not None and not self.can_manage_staffing(requested_by, form_number):
            raise PermissionDenied(
                f"Teacher {requested_by} may not assign staff for Form {form_number} -- only an "
                f"executive or a delegated staffing manager for this form can."
            )
        self._require_teacher(staff_no)
        code = subject_code(subject)
        if not self.conn.execute("SELECT 1 FROM subjects WHERE code=?", (code,)).fetchone():
            raise NotFound(f"No such subject '{subject}'.")
        stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
        self.conn.execute(
            "INSERT OR IGNORE INTO teacher_subjects(staff_no, subject_code) VALUES (?,?)",
            (staff_no, code),
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO subject_assignments"
            "(staff_no, subject_code, form_number, stream_code) VALUES (?,?,?,?)",
            (staff_no, code, form_number, stream_code),
        )
        self.conn.commit()

    def unassign_subject_teacher(self, staff_no: str, subject: str, form_number: int,
                                  stream_name_or_code: str, requested_by: Optional[str] = None):
        if requested_by is not None and not self.can_manage_staffing(requested_by, form_number):
            raise PermissionDenied(
                f"Teacher {requested_by} may not change staffing for Form {form_number} -- only an "
                f"executive or a delegated staffing manager for this form can."
            )
        code = subject_code(subject)
        stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
        self.conn.execute(
            "DELETE FROM subject_assignments WHERE staff_no=? AND subject_code=? "
            "AND form_number=? AND stream_code=?",
            (staff_no, code, form_number, stream_code),
        )
        self.conn.commit()
        self._audit("subject_assignments", f"{form_number}{stream_code}:{code}", "unassign",
                    requested_by or "system", staff_no)

    def is_assigned(self, staff_no: str, subject_code_: str, form_number: int, stream_code: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM subject_assignments WHERE staff_no=? AND subject_code=? "
            "AND form_number=? AND stream_code=?",
            (staff_no, subject_code_, form_number, stream_code),
        ).fetchone()
        return row is not None

    def teacher_scope(self, staff_no: str) -> Dict[str, Any]:
        """What this teacher has a role in: classes they head, forms they supervise,
        and specific (subject, class) pairs they're timetabled to teach."""
        class_teacher_rows = self.conn.execute(
            "SELECT form_number, code FROM streams WHERE class_teacher_staff_no=?", (staff_no,)
        ).fetchall()
        supervisor_rows = self.conn.execute(
            "SELECT form_number FROM form_supervisors WHERE staff_no=?", (staff_no,)
        ).fetchall()
        subject_rows = self.conn.execute(
            "SELECT subject_code, form_number, stream_code FROM subject_assignments WHERE staff_no=?",
            (staff_no,),
        ).fetchall()
        return {
            "class_teacher_of": [f"{r['form_number']}{r['code']}" for r in class_teacher_rows],
            "supervises_forms": [r["form_number"] for r in supervisor_rows],
            "subject_assignments": [
                (r["subject_code"], f"{r['form_number']}{r['stream_code']}") for r in subject_rows
            ],
        }

    def visible_classes(self, staff_no: str) -> List[str]:
        """Every class code (e.g. '1W') this teacher has any reason to see.
        Principal/Deputy Principals/Dean of Studies see every class, school-wide."""
        if self.has_full_oversight(staff_no):
            rows = self.conn.execute("SELECT form_number, code FROM streams ORDER BY form_number, code").fetchall()
            return [f"{r['form_number']}{r['code']}" for r in rows]
        scope = self.teacher_scope(staff_no)
        classes = set(scope["class_teacher_of"])
        for form_num in scope["supervises_forms"]:
            rows = self.conn.execute(
                "SELECT code FROM streams WHERE form_number=?", (form_num,)
            ).fetchall()
            classes.update(f"{form_num}{r['code']}" for r in rows)
        for _subj, class_code in scope["subject_assignments"]:
            classes.add(class_code)
        return sorted(classes)

    def class_roster_view(self, staff_no: str, form_number: int, stream_name_or_code: str) -> Dict[str, Any]:
        """The roster for one class, scoped to what this teacher may see.
        Raises PermissionDenied if the teacher has no role in this class at all."""
        stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
        class_code = f"{form_number}{stream_code}"
        scope = self.teacher_scope(staff_no)

        full_access = (class_code in scope["class_teacher_of"] or form_number in scope["supervises_forms"]
                       or self.has_full_oversight(staff_no))
        allowed_subjects = None if full_access else {
            subj for subj, cc in scope["subject_assignments"] if cc == class_code
        }
        if not full_access and not allowed_subjects:
            raise PermissionDenied(
                f"Teacher {staff_no} has no role (class teacher, supervisor, or subject "
                f"teacher) in class {class_code}."
            )

        students = self.conn.execute(
            "SELECT * FROM students WHERE form_number=? AND stream_code=? ORDER BY last_name",
            (form_number, stream_code),
        ).fetchall()
        return {"class_code": class_code, "students": students, "allowed_subjects": allowed_subjects}

    def marks_view_for_teacher(self, staff_no: str, form_number: int, stream_name_or_code: str,
                                exam_id: int) -> List[Dict[str, Any]]:
        """Marks for a class, scoped to this teacher: full class teacher/supervisor sees
        every subject; a subject teacher sees only the subject(s) they're assigned there."""
        roster = self.class_roster_view(staff_no, form_number, stream_name_or_code)
        result = []
        for student in roster["students"]:
            marks = self.get_marks_for_student(student["adm_no"], exam_id)
            if roster["allowed_subjects"] is not None:
                marks = [m for m in marks if m["subject_code"] in roster["allowed_subjects"]]
            result.append({
                "adm_no": student["adm_no"],
                "name": f"{student['first_name']} {student['last_name']}",
                "marks": [{"subject": m["subject_name"], "score": m["score"], "status": m["status"]}
                          for m in marks],
            })
        return result

    # ------------------------------------------------------------------
    # students
    # ------------------------------------------------------------------
    def add_student(self, first_name: str, last_name: str, form_number: int,
                     stream_name_or_code: str, adm_no: Optional[str] = None,
                     gender: Optional[str] = None) -> str:
        code = self._resolve_stream_code(form_number, stream_name_or_code)
        if not adm_no:
            adm_no = self._generate_id("admission")
        self.conn.execute(
            "INSERT INTO students(adm_no, first_name, last_name, form_number, stream_code, gender) "
            "VALUES (?,?,?,?,?,?)",
            (adm_no, first_name.strip(), last_name.strip(), form_number, code,
             gender.strip().upper()[:1] if gender else None),
        )
        self.conn.commit()
        return adm_no

    def class_code_of(self, adm_no: str) -> str:
        row = self.conn.execute(
            "SELECT form_number, stream_code FROM students WHERE adm_no=?", (adm_no,)
        ).fetchone()
        if not row:
            raise NotFound(f"No such student '{adm_no}'.")
        return f"{row['form_number']}{row['stream_code']}"

    # ------------------------------------------------------------------
    # exams (setup: category, weight towards the term average, paper timetable)
    # ------------------------------------------------------------------
    def add_exam(self, name: str, term: int, year: int, category: str = "General", weight: float = 1.0) -> int:
        self.conn.execute(
            "INSERT OR IGNORE INTO exams(name, term, year, category, weight) VALUES (?,?,?,?,?)",
            (name, term, year, category, weight),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM exams WHERE name=? AND term=? AND year=?", (name, term, year)
        ).fetchone()
        return row["id"]

    def schedule_exam_paper(self, exam_id: int, subject: str, date: str,
                             start_time: Optional[str] = None, end_time: Optional[str] = None):
        code = subject_code(subject)
        self.conn.execute(
            "INSERT OR REPLACE INTO exam_timetable(exam_id, subject_code, date, start_time, end_time) "
            "VALUES (?,?,?,?,?)",
            (exam_id, code, date, start_time, end_time),
        )
        self.conn.commit()

    def get_exam_timetable(self, exam_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            """SELECT et.*, sub.name AS subject_name FROM exam_timetable et
               JOIN subjects sub ON sub.code = et.subject_code
               WHERE et.exam_id=? ORDER BY et.date, et.start_time""",
            (exam_id,),
        ).fetchall()

    def term_report(self, adm_no: str, term: int, year: int) -> Dict[str, Any]:
        """Combines every exam in a term into one weighted report per subject,
        using each exam's configured weight (e.g. CAT counts less than Endterm)."""
        exams = self.conn.execute("SELECT * FROM exams WHERE term=? AND year=?", (term, year)).fetchall()
        per_subject: Dict[str, List] = {}
        for exam in exams:
            for m in self.get_marks_for_student(adm_no, exam["id"]):
                per_subject.setdefault(m["subject_code"], []).append(
                    (m["score"], exam["weight"] or 1.0, m["subject_name"])
                )
        subject_results = []
        for code, entries in per_subject.items():
            total_weight = sum(w for _, w, _ in entries)
            weighted = sum(sc * w for sc, w, _ in entries) / total_weight if total_weight else 0.0
            subject_results.append({
                "subject": entries[0][2], "weighted_score": round(weighted, 2), "grade": grade_for(weighted),
            })
        subject_results.sort(key=lambda s: s["subject"])
        overall = round(sum(s["weighted_score"] for s in subject_results) / len(subject_results), 2) \
            if subject_results else None
        return {"adm_no": adm_no, "term": term, "year": year, "subjects": subject_results,
                "overall_mean": overall, "overall_grade": grade_for(overall) if overall is not None else "-"}

    # ------------------------------------------------------------------
    # marks workflow
    # ------------------------------------------------------------------
    def save_draft_mark(self, teacher_staff_no: str, student_adm_no: str,
                         subject: str, exam_id: int, score: float):
        """Subject teacher enters/edits a mark. Only allowed while it doesn't
        exist yet, or while it's still a draft owned by this teacher -- and only
        if this teacher is specifically assigned to teach that subject in that
        student's class (not just qualified to teach it somewhere else)."""
        code = subject_code(subject) if len(subject) != 3 or subject.upper() not in self._subject_codes() else subject.upper()

        student = self.conn.execute(
            "SELECT form_number, stream_code FROM students WHERE adm_no=?", (student_adm_no,)
        ).fetchone()
        if not student:
            raise NotFound(f"No such student '{student_adm_no}'.")

        if self.is_exam_published(exam_id) and not self.has_full_oversight(teacher_staff_no):
            raise PermissionDenied(
                "Results for this exam have been published and are now locked. Only an executive "
                "can unpublish before marks can be entered or edited again."
            )

        if not self.is_assigned(teacher_staff_no, code, student["form_number"], student["stream_code"]) \
                and not self.has_full_oversight(teacher_staff_no):
            class_code = f"{student['form_number']}{student['stream_code']}"
            raise PermissionDenied(
                f"Teacher {teacher_staff_no} is not assigned to teach '{subject}' in class "
                f"{class_code}. Use assign_subject_teacher() to timetable them first."
            )

        existing = self.conn.execute(
            "SELECT * FROM marks WHERE student_adm_no=? AND subject_code=? AND exam_id=?",
            (student_adm_no, code, exam_id),
        ).fetchone()

        if existing is None:
            self.conn.execute(
                "INSERT INTO marks(student_adm_no, subject_code, exam_id, score, status, "
                "entered_by, last_edited_by, created_at, updated_at) "
                "VALUES (?,?,?,?,'draft',?,?,?,?)",
                (student_adm_no, code, exam_id, score, teacher_staff_no, teacher_staff_no, _now(), _now()),
            )
        else:
            if existing["status"] == "submitted" and not self.has_full_oversight(teacher_staff_no):
                raise PermissionDenied(
                    "This mark has already been submitted; the subject teacher can no longer "
                    "edit it. Only the class teacher can correct submitted marks (use "
                    "edit_submitted_mark)."
                )
            if existing["entered_by"] != teacher_staff_no and not self.has_full_oversight(teacher_staff_no):
                raise PermissionDenied(
                    "This draft was entered by a different subject teacher; you may not edit it."
                )
            self.conn.execute(
                "UPDATE marks SET score=?, last_edited_by=?, updated_at=? WHERE id=?",
                (score, teacher_staff_no, _now(), existing["id"]),
            )
        self.conn.commit()
        self._audit("marks", f"{student_adm_no}:{code}:{exam_id}", "save_draft", teacher_staff_no, f"score={score}")

    def submit_mark(self, teacher_staff_no: str, student_adm_no: str, subject: str, exam_id: int):
        """Subject teacher presses 'submit' -> locks the mark from further
        editing by the subject teacher; only the class teacher can edit it now."""
        if self.is_exam_published(exam_id) and not self.has_full_oversight(teacher_staff_no):
            raise PermissionDenied(
                "Results for this exam have been published and are now locked. Only an executive "
                "can unpublish before marks can be submitted again."
            )
        code = subject.upper() if subject.upper() in self._subject_codes() else subject_code(subject)
        row = self.conn.execute(
            "SELECT * FROM marks WHERE student_adm_no=? AND subject_code=? AND exam_id=?",
            (student_adm_no, code, exam_id),
        ).fetchone()
        if row is None:
            raise NotFound("No draft mark to submit -- enter it first.")
        if row["status"] == "submitted":
            return  # already submitted, nothing to do
        if row["entered_by"] != teacher_staff_no and not self.has_full_oversight(teacher_staff_no):
            raise PermissionDenied("Only the subject teacher who entered this draft may submit it.")
        self.conn.execute(
            "UPDATE marks SET status='submitted', updated_at=? WHERE id=?", (_now(), row["id"])
        )
        self.conn.commit()
        self._audit("marks", f"{student_adm_no}:{code}:{exam_id}", "submit", teacher_staff_no, "")

    def save_class_marks_bulk(self, teacher_staff_no: str, subject: str, exam_id: int,
                               form_number: int, stream_name_or_code: str,
                               scores: Dict[str, Optional[float]]) -> Dict[str, Any]:
        """Spreadsheet-style bulk entry AND correction in one grid: given
        {adm_no: score} for a whole class, saves every changed, non-blank
        score. A row still in draft (or with no mark yet) goes through the
        normal entry path; a row that's already submitted is routed through
        the class-teacher correction path instead -- so the same grid handles
        both entering new marks and correcting submitted ones. Blank scores
        are skipped (a partially-filled grid is fine). Returns a summary so
        one bad row doesn't lose the rest."""
        stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
        code = subject_code(subject) if len(subject) != 3 or subject.upper() not in self._subject_codes() else subject.upper()
        roster = {
            r["adm_no"] for r in self.conn.execute(
                "SELECT adm_no FROM students WHERE form_number=? AND stream_code=?",
                (form_number, stream_code),
            ).fetchall()
        }
        saved, skipped, errors = [], [], []
        for adm_no, score in scores.items():
            if adm_no not in roster:
                continue
            if score is None:
                skipped.append(adm_no)
                continue
            existing = self.conn.execute(
                "SELECT status FROM marks WHERE student_adm_no=? AND subject_code=? AND exam_id=?",
                (adm_no, code, exam_id),
            ).fetchone()
            try:
                if existing and existing["status"] == "submitted":
                    self.edit_submitted_mark(teacher_staff_no, adm_no, subject, exam_id, score)
                else:
                    self.save_draft_mark(teacher_staff_no, adm_no, subject, exam_id, score)
                saved.append(adm_no)
            except (PermissionDenied, NotFound, ValueError) as e:
                errors.append({"adm_no": adm_no, "error": str(e)})
        return {"saved": len(saved), "skipped": len(skipped), "errors": errors}

    def submit_class_marks(self, teacher_staff_no: str, subject: str, exam_id: int,
                            form_number: int, stream_name_or_code: str) -> int:
        """Bulk-submit every draft this teacher entered for one class/subject/exam.
        Returns the number of marks submitted."""
        code = subject.upper() if subject.upper() in self._subject_codes() else subject_code(subject)
        stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
        rows = self.conn.execute(
            """
            SELECT m.id FROM marks m
            JOIN students s ON s.adm_no = m.student_adm_no
            WHERE m.subject_code=? AND m.exam_id=? AND m.entered_by=? AND m.status='draft'
              AND s.form_number=? AND s.stream_code=?
            """,
            (code, exam_id, teacher_staff_no, form_number, stream_code),
        ).fetchall()
        for row in rows:
            self.conn.execute(
                "UPDATE marks SET status='submitted', updated_at=? WHERE id=?", (_now(), row["id"])
            )
        self.conn.commit()
        return len(rows)

    def edit_submitted_mark(self, teacher_staff_no: str, student_adm_no: str,
                             subject: str, exam_id: int, new_score: float):
        """Only the class teacher of the student's class may correct a submitted mark."""
        code = subject.upper() if subject.upper() in self._subject_codes() else subject_code(subject)
        row = self.conn.execute(
            "SELECT * FROM marks WHERE student_adm_no=? AND subject_code=? AND exam_id=?",
            (student_adm_no, code, exam_id),
        ).fetchone()
        if row is None:
            raise NotFound("No such mark exists yet.")

        class_teacher = self.class_teacher_of_student(student_adm_no)
        is_class_teacher = class_teacher is not None and class_teacher["staff_no"] == teacher_staff_no
        if not is_class_teacher and not self.has_full_oversight(teacher_staff_no):
            raise PermissionDenied(
                "Only the class teacher of this student's class may edit a submitted mark."
            )
        if self.is_exam_published(exam_id) and not self.has_full_oversight(teacher_staff_no):
            raise PermissionDenied(
                "Results for this exam have been published. Even the class teacher can no longer "
                "edit marks -- only an executive can unpublish first."
            )
        self.conn.execute(
            "UPDATE marks SET score=?, status='submitted', last_edited_by=?, updated_at=? WHERE id=?",
            (new_score, teacher_staff_no, _now(), row["id"]),
        )
        self.conn.commit()
        self._audit("marks", f"{student_adm_no}:{code}:{exam_id}", "class_teacher_edit",
                     teacher_staff_no, f"new_score={new_score}")

    def _subject_codes(self):
        return {r["code"] for r in self.conn.execute("SELECT code FROM subjects")}

    def get_marks_for_student(self, adm_no: str, exam_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT m.*, sub.name AS subject_name FROM marks m
            JOIN subjects sub ON sub.code = m.subject_code
            WHERE m.student_adm_no=? AND m.exam_id=?
            ORDER BY sub.name
            """,
            (adm_no, exam_id),
        ).fetchall()

    # ------------------------------------------------------------------
    # report form
    # ------------------------------------------------------------------
    def build_report_data(self, adm_no: str, exam_id: int) -> Dict[str, Any]:
        student = self.conn.execute("SELECT * FROM students WHERE adm_no=?", (adm_no,)).fetchone()
        if not student:
            raise NotFound(f"No such student '{adm_no}'.")
        exam = self.conn.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone()
        if not exam:
            raise NotFound(f"No such exam id {exam_id}.")

        marks = self.get_marks_for_student(adm_no, exam_id)
        subjects_data = [
            {
                "subject": m["subject_name"],
                "score": m["score"],
                "grade": grade_for(m["score"]),
                "status": m["status"],
            }
            for m in marks
        ]
        total = sum(s["score"] for s in subjects_data)
        mean = total / len(subjects_data) if subjects_data else 0.0

        ct = self.class_teacher_of_student(adm_no)
        sup = self.conn.execute(
            """
            SELECT t.* FROM form_supervisors fs
            JOIN teachers t ON t.staff_no = fs.staff_no
            WHERE fs.form_number = ?
            """,
            (student["form_number"],),
        ).fetchone()

        return {
            "student_name": f"{student['first_name']} {student['last_name']}",
            "adm_no": student["adm_no"],
            "class_code": f"{student['form_number']}{student['stream_code']}",
            "exam_name": exam["name"],
            "term": exam["term"],
            "year": exam["year"],
            "subjects": subjects_data,
            "total": total,
            "mean": round(mean, 2),
            "mean_grade": grade_for(mean) if subjects_data else "-",
            "class_teacher": f"{ct['first_name']} {ct['last_name']}" if ct else "Unassigned",
            "supervisor": f"{sup['first_name']} {sup['last_name']}" if sup else "Unassigned",
        }

    def form_merit_list(self, form_number: int, exam_id: int) -> List[Dict[str, Any]]:
        """Every student in the form, across ALL its streams, ranked against each
        other individually by mean score for one exam (a form-wide merit list)."""
        students = self.conn.execute(
            "SELECT * FROM students WHERE form_number=? ORDER BY last_name", (form_number,)
        ).fetchall()

        entries = []
        for s in students:
            marks = self.get_marks_for_student(s["adm_no"], exam_id)
            if not marks:
                continue
            total = sum(m["score"] for m in marks)
            mean = total / len(marks)
            entries.append({
                "adm_no": s["adm_no"],
                "name": f"{s['first_name']} {s['last_name']}",
                "class_code": f"{s['form_number']}{s['stream_code']}",
                "subjects_done": len(marks),
                "total": round(total, 2),
                "mean": round(mean, 2),
                "mean_grade": grade_for(mean),
            })

        entries.sort(key=lambda e: (-e["mean"], e["adm_no"]))
        for i, e in enumerate(entries, start=1):
            e["position"] = i
        return entries

    def form_staffing_data(self, form_number: int) -> Dict[str, Any]:
        """Staffing for one form: every stream paired with its class teacher's
        name, plus the class principal/supervisor -- whose 'class' is the
        form itself, not any one stream."""
        streams = self.conn.execute(
            "SELECT * FROM streams WHERE form_number=? ORDER BY code", (form_number,)
        ).fetchall()

        stream_rows = []
        for st in streams:
            ct_name = "Unassigned"
            if st["class_teacher_staff_no"]:
                ct = self.conn.execute(
                    "SELECT * FROM teachers WHERE staff_no=?", (st["class_teacher_staff_no"],)
                ).fetchone()
                if ct:
                    ct_name = f"{ct['first_name']} {ct['last_name']}"
            stream_rows.append({
                "class_code": f"{form_number}{st['code']}",
                "stream_name": st["name"],
                "class_teacher": ct_name,
            })

        sup = self.conn.execute(
            """SELECT t.* FROM form_supervisors fs JOIN teachers t ON t.staff_no = fs.staff_no
               WHERE fs.form_number=?""",
            (form_number,),
        ).fetchone()
        supervisor_name = f"{sup['first_name']} {sup['last_name']}" if sup else "Unassigned"

        return {
            "form_number": form_number,
            "streams": stream_rows,
            "supervisor_name": supervisor_name,
            "supervisor_class": f"Form {form_number}",
        }

    def stream_subject_performance(self, form_number: int, exam_id: int) -> Dict[str, Any]:
        """For every subject that has marks in this form/exam, each stream's mean
        score in that subject, plus which stream ranks best in each subject."""
        rows = self.conn.execute(
            """
            SELECT s.stream_code, sub.code AS subject_code, sub.name AS subject_name,
                   AVG(m.score) AS mean_score, COUNT(*) AS n
            FROM marks m
            JOIN students s ON s.adm_no = m.student_adm_no
            JOIN subjects sub ON sub.code = m.subject_code
            WHERE s.form_number = ? AND m.exam_id = ?
            GROUP BY s.stream_code, sub.code
            """,
            (form_number, exam_id),
        ).fetchall()

        stream_names = {
            r["code"]: r["name"] for r in self.conn.execute(
                "SELECT code, name FROM streams WHERE form_number=?", (form_number,)
            ).fetchall()
        }

        matrix: Dict[str, Dict[str, Dict[str, Any]]] = {}
        subject_names: Dict[str, str] = {}
        streams_with_data = set()
        for r in rows:
            subject_names[r["subject_code"]] = r["subject_name"]
            streams_with_data.add(r["stream_code"])
            matrix.setdefault(r["subject_code"], {})[r["stream_code"]] = {
                "mean": round(r["mean_score"], 2), "n": r["n"],
            }

        # rank streams within each subject
        for subj_code, stream_scores in matrix.items():
            ranked = sorted(stream_scores.items(), key=lambda kv: -kv[1]["mean"])
            for i, (stream_code, entry) in enumerate(ranked, start=1):
                entry["rank"] = i

        ordered_streams = sorted(streams_with_data)
        ordered_subjects = sorted(subject_names.keys(), key=lambda c: subject_names[c])

        return {
            "form_number": form_number,
            "subjects": [{"code": c, "name": subject_names[c]} for c in ordered_subjects],
            "streams": [{"code": c, "name": stream_names.get(c, c), "class_code": f"{form_number}{c}"}
                        for c in ordered_streams],
            "matrix": matrix,  # matrix[subject_code][stream_code] = {mean, n, rank}
        }

    def close(self):
        self.conn.close()

    # ------------------------------------------------------------------
    # component visibility toggles (feature flags)
    # ------------------------------------------------------------------
    TOGGLEABLE_FEATURES = [
        "students", "staff", "departments", "marks", "attendance", "timetable",
        "calendar", "fees", "exams", "analytics", "messages", "alumni",
        "academic_setup", "notifications", "reports",
    ]

    def is_feature_enabled(self, name: str) -> bool:
        return self.get_setting(f"feature_{name}_enabled", "1") == "1"

    def set_feature_enabled(self, name: str, enabled: bool, requested_by: str):
        if not self.has_full_oversight(requested_by):
            raise PermissionDenied(f"Teacher {requested_by} may not change component visibility -- only an executive can.")
        if name not in self.TOGGLEABLE_FEATURES:
            raise ValueError(f"Unknown feature '{name}'.")
        self.set_setting(f"feature_{name}_enabled", "1" if enabled else "0")
        self._audit("settings", f"feature_{name}", "toggle", requested_by, "enabled" if enabled else "disabled")

    def all_feature_states(self) -> Dict[str, bool]:
        return {name: self.is_feature_enabled(name) for name in self.TOGGLEABLE_FEATURES}

    # ------------------------------------------------------------------
    # system-wide and per-account login lockout
    # ------------------------------------------------------------------
    def is_system_locked(self) -> bool:
        return self.get_setting("system_locked", "0") == "1"

    def set_system_locked(self, locked: bool, requested_by: str):
        if not self.has_full_oversight(requested_by):
            raise PermissionDenied(f"Teacher {requested_by} may not lock/unlock the system -- only an executive can.")
        self.set_setting("system_locked", "1" if locked else "0")
        self._audit("settings", "system_locked", "toggle", requested_by, "locked" if locked else "unlocked")

    def is_account_locked(self, staff_no: str) -> bool:
        row = self.conn.execute("SELECT account_locked FROM teachers WHERE staff_no=?", (staff_no,)).fetchone()
        return bool(row and row["account_locked"])

    def set_account_locked(self, staff_no: str, locked: bool, requested_by: str):
        if not self.has_full_oversight(requested_by):
            raise PermissionDenied(f"Teacher {requested_by} may not lock/unlock accounts -- only an executive can.")
        self._require_teacher(staff_no)
        self.conn.execute("UPDATE teachers SET account_locked=? WHERE staff_no=?", (1 if locked else 0, staff_no))
        self.conn.commit()
        self._audit("teachers", staff_no, "account_lock", requested_by, "locked" if locked else "unlocked")

    # ------------------------------------------------------------------
    # OTP-based local account recovery (admin-facing, no plaintext passwords
    # ever stored or shown -- generates a fresh short-lived code instead,
    # same trust model as the existing password-reset tokens)
    # ------------------------------------------------------------------
    def generate_otp_for(self, staff_no: str, requested_by: str) -> str:
        if not self.has_full_oversight(requested_by):
            raise PermissionDenied(f"Teacher {requested_by} may not generate a recovery OTP -- only an executive can.")
        self._require_teacher(staff_no)
        otp = f"{secrets.randbelow(1_000_000):06d}"
        token_hash = hashlib.sha256(otp.encode()).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO password_resets(account_type, account_id, token_hash, expires_at, created_at) "
            "VALUES ('teacher',?,?,?,?)",
            (staff_no, token_hash, expires_at, _now()),
        )
        self.conn.commit()
        self._audit("teachers", staff_no, "otp_generated", requested_by, "")
        return otp  # shown once to the admin; never stored in retrievable form

    # ------------------------------------------------------------------
    # bulk / retroactive ID renumbering (executive only)
    # ------------------------------------------------------------------
    def bulk_renumber_students(self, requested_by: str, prefix: str, padding: int, start: int,
                                form_number: Optional[int] = None) -> Dict[str, str]:
        """Retroactively renumbers admission numbers for existing students
        (optionally scoped to one form), in a stable order, updating every
        table that references the old adm_no. Returns {old_adm_no: new_adm_no}."""
        if not self.has_full_oversight(requested_by):
            raise PermissionDenied(f"Teacher {requested_by} may not bulk-renumber students -- only an executive can.")
        query = "SELECT adm_no FROM students"
        params: List[Any] = []
        if form_number is not None:
            query += " WHERE form_number=?"
            params.append(form_number)
        query += " ORDER BY form_number, stream_code, last_name, adm_no"
        students = self.conn.execute(query, params).fetchall()

        referencing = [
            ("marks", "student_adm_no"), ("attendance", "student_adm_no"),
            ("discipline_records", "student_adm_no"), ("fee_payments", "student_adm_no"),
            ("fee_invoices", "student_adm_no"), ("pledges", "student_adm_no"),
            ("parent_students", "student_adm_no"),
        ]
        mapping: Dict[str, str] = {}
        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys = OFF")
        n = start
        # Two passes: first move every old adm_no to a temporary unique value,
        # then assign the real new numbers -- avoids collisions when a new
        # number happens to equal a not-yet-renumbered old one.
        temp_map = {}
        for s in students:
            temp = f"__tmp__{s['adm_no']}"
            temp_map[s["adm_no"]] = temp
            self.conn.execute("UPDATE students SET adm_no=? WHERE adm_no=?", (temp, s["adm_no"]))
            for table, col in referencing:
                self.conn.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (temp, s["adm_no"]))
        for s in students:
            new_no = f"{prefix}{n:0{padding}d}"
            n += 1
            temp = temp_map[s["adm_no"]]
            self.conn.execute("UPDATE students SET adm_no=? WHERE adm_no=?", (new_no, temp))
            for table, col in referencing:
                self.conn.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (new_no, temp))
            mapping[s["adm_no"]] = new_no
        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.set_setting("admission_next", str(n))
        self._audit("students", "bulk", "renumber", requested_by, f"{len(mapping)} students renumbered")
        return mapping

    def bulk_renumber_staff(self, requested_by: str, prefix: str, padding: int, start: int) -> Dict[str, str]:
        """Retroactively renumbers staff numbers for every teacher, updating
        every referencing table. Returns {old_staff_no: new_staff_no}."""
        if not self.has_full_oversight(requested_by):
            raise PermissionDenied(f"Teacher {requested_by} may not bulk-renumber staff -- only an executive can.")
        teachers = self.conn.execute("SELECT staff_no FROM teachers ORDER BY last_name, staff_no").fetchall()
        mapping: Dict[str, str] = {}
        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys = OFF")
        n = start
        temp_map = {}
        for t in teachers:
            temp = f"__tmp__{t['staff_no']}"
            temp_map[t["staff_no"]] = temp
            self.conn.execute("UPDATE teachers SET staff_no=? WHERE staff_no=?", (temp, t["staff_no"]))
            for table, col in self._STAFF_NO_REFERENCING_TABLES:
                self.conn.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (temp, t["staff_no"]))
        for t in teachers:
            new_no = f"{prefix}{n:0{padding}d}"
            n += 1
            temp = temp_map[t["staff_no"]]
            self.conn.execute("UPDATE teachers SET staff_no=? WHERE staff_no=?", (new_no, temp))
            for table, col in self._STAFF_NO_REFERENCING_TABLES:
                self.conn.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (new_no, temp))
            mapping[t["staff_no"]] = new_no
        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.set_setting("staff_next", str(n))
        self._audit("teachers", "bulk", "renumber", requested_by, f"{len(mapping)} staff renumbered")
        return mapping

    # ------------------------------------------------------------------
    # edit / permanently remove a teacher
    # ------------------------------------------------------------------
    # Every table that stores a staff_no pointing at teachers(staff_no).
    # Used both for a safe rename and for cleaning up before a hard delete.
    _STAFF_NO_REFERENCING_TABLES = [
        ("teacher_subjects", "staff_no"),
        ("streams", "class_teacher_staff_no"),
        ("form_supervisors", "staff_no"),
        ("subject_assignments", "staff_no"),
        ("staffing_managers", "staff_no"),
        ("teacher_group_members", "staff_no"),
        ("staff_roles", "staff_no"),
        ("staff_contracts", "staff_no"),
        ("staff_leave", "staff_no"),
        ("staff_leave", "decided_by"),
        ("timetable", "staff_no"),
        ("messages", "sender_staff_no"),
        ("messages", "recipient_staff_no"),
        ("marks", "entered_by"),
        ("marks", "last_edited_by"),
        ("discipline_records", "recorded_by"),
        ("attendance", "recorded_by"),
        ("fee_payments", "recorded_by"),
        ("expenses", "recorded_by"),
        ("exam_publications", "published_by"),
        ("moderation_notes", "staff_no"),
        ("calendar_events", "created_by"),
    ]

    def update_teacher(self, staff_no: str, new_staff_no: Optional[str] = None,
                        first_name: Optional[str] = None, last_name: Optional[str] = None):
        """Edit a teacher's name and/or staff number. Renaming the staff number
        updates every table that references it, so nothing goes stale."""
        if not self.conn.execute("SELECT 1 FROM teachers WHERE staff_no=?", (staff_no,)).fetchone():
            raise NotFound(f"No such teacher '{staff_no}'.")

        final_staff_no = staff_no
        if new_staff_no and new_staff_no.strip() != staff_no:
            new_staff_no = new_staff_no.strip()
            if self.conn.execute("SELECT 1 FROM teachers WHERE staff_no=?", (new_staff_no,)).fetchone():
                raise ValueError(f"Staff number '{new_staff_no}' is already in use.")
            self.conn.commit()  # PRAGMA foreign_keys is a no-op inside an open transaction
            self.conn.execute("PRAGMA foreign_keys = OFF")
            self.conn.execute("UPDATE teachers SET staff_no=? WHERE staff_no=?", (new_staff_no, staff_no))
            for table, col in self._STAFF_NO_REFERENCING_TABLES:
                self.conn.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (new_staff_no, staff_no))
            self.conn.commit()
            self.conn.execute("PRAGMA foreign_keys = ON")
            final_staff_no = new_staff_no

        if first_name is not None:
            self.conn.execute("UPDATE teachers SET first_name=? WHERE staff_no=?", (first_name.strip(), final_staff_no))
        if last_name is not None:
            self.conn.execute("UPDATE teachers SET last_name=? WHERE staff_no=?", (last_name.strip(), final_staff_no))
        self.conn.commit()
        self._audit("teachers", final_staff_no, "update_profile", "system", "")
        return final_staff_no

    def remove_teacher_permanently(self, staff_no: str, requested_by: str):
        """Hard-deletes a teacher account. Clears every current-state
        assignment (class teacher, supervisor, subject assignments, roles,
        delegations, timetable slots, groups, contracts, leave, messages).
        Historical audit fields (who entered a mark, recorded attendance,
        etc.) are left as-is -- those rows describe students/events, not the
        teacher, so they're kept for the historical record even though the
        account itself is gone."""
        if not self.has_full_oversight(requested_by):
            raise PermissionDenied(f"Teacher {requested_by} may not permanently remove staff -- only an executive can.")
        if not self.conn.execute("SELECT 1 FROM teachers WHERE staff_no=?", (staff_no,)).fetchone():
            raise NotFound(f"No such teacher '{staff_no}'.")

        self.conn.execute("UPDATE streams SET class_teacher_staff_no=NULL WHERE class_teacher_staff_no=?", (staff_no,))
        self.conn.execute("DELETE FROM form_supervisors WHERE staff_no=?", (staff_no,))
        self.conn.execute("DELETE FROM subject_assignments WHERE staff_no=?", (staff_no,))
        self.conn.execute("DELETE FROM teacher_subjects WHERE staff_no=?", (staff_no,))
        self.conn.execute("DELETE FROM staffing_managers WHERE staff_no=?", (staff_no,))
        self.conn.execute("DELETE FROM teacher_group_members WHERE staff_no=?", (staff_no,))
        self.conn.execute("DELETE FROM staff_roles WHERE staff_no=?", (staff_no,))
        self.conn.execute("DELETE FROM staff_contracts WHERE staff_no=?", (staff_no,))
        self.conn.execute("DELETE FROM staff_leave WHERE staff_no=?", (staff_no,))
        self.conn.execute("UPDATE staff_leave SET decided_by=NULL WHERE decided_by=?", (staff_no,))
        self.conn.execute("DELETE FROM timetable WHERE staff_no=?", (staff_no,))
        self.conn.execute("DELETE FROM messages WHERE sender_staff_no=? OR recipient_staff_no=?", (staff_no, staff_no))
        self.conn.commit()  # PRAGMA foreign_keys is a no-op inside an open transaction

        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.execute("DELETE FROM teachers WHERE staff_no=?", (staff_no,))
        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._audit("teachers", staff_no, "remove_permanently", requested_by, "")

    # ------------------------------------------------------------------
    # permanently remove a student
    # ------------------------------------------------------------------
    def remove_student_permanently(self, adm_no: str, requested_by: str):
        """Hard-deletes a student and everything specifically about them:
        marks, attendance, discipline, fee invoices/payments, pledges, and
        parent links."""
        if not self.has_full_oversight(requested_by):
            raise PermissionDenied(f"Teacher {requested_by} may not permanently remove students -- only an executive can.")
        if not self.conn.execute("SELECT 1 FROM students WHERE adm_no=?", (adm_no,)).fetchone():
            raise NotFound(f"No such student '{adm_no}'.")

        for table, col in [
            ("marks", "student_adm_no"), ("attendance", "student_adm_no"),
            ("discipline_records", "student_adm_no"), ("fee_payments", "student_adm_no"),
            ("fee_invoices", "student_adm_no"), ("pledges", "student_adm_no"),
            ("parent_students", "student_adm_no"),
        ]:
            self.conn.execute(f"DELETE FROM {table} WHERE {col}=?", (adm_no,))
        self.conn.execute("DELETE FROM students WHERE adm_no=?", (adm_no,))
        self.conn.commit()
        self._audit("students", adm_no, "remove_permanently", requested_by, "")

    # ------------------------------------------------------------------
    # grid views: every teacher's classes/subjects, every student in one list
    # ------------------------------------------------------------------
    def teacher_grid(self) -> List[Dict[str, Any]]:
        """One row per teacher: their roles, plus every class they're the
        class teacher of, supervisor of, or assigned to teach a subject in."""
        teachers = self.conn.execute("SELECT * FROM teachers ORDER BY staff_no").fetchall()
        result = []
        for t in teachers:
            scope = self.teacher_scope(t["staff_no"])
            teaching = []
            for subj_code, class_code in scope["subject_assignments"]:
                subj_name = self.conn.execute("SELECT name FROM subjects WHERE code=?", (subj_code,)).fetchone()
                teaching.append(f"{subj_name['name'] if subj_name else subj_code} ({class_code})")
            result.append({
                "staff_no": t["staff_no"], "name": f"{t['first_name']} {t['last_name']}",
                "roles": [r["role"] for r in self.get_roles(t["staff_no"])],
                "class_teacher_of": scope["class_teacher_of"],
                "supervises_forms": [f"Form {f}" for f in scope["supervises_forms"]],
                "teaching": teaching,
            })
        return result

    def student_grid(self, form_number: Optional[int] = None) -> List[sqlite3.Row]:
        """Every student, optionally scoped to one form, for a full listing grid."""
        if form_number is not None:
            return self.conn.execute(
                "SELECT * FROM students WHERE form_number=? ORDER BY form_number, stream_code, last_name",
                (form_number,),
            ).fetchall()
        return self.conn.execute("SELECT * FROM students ORDER BY form_number, stream_code, last_name").fetchall()

    # ------------------------------------------------------------------
    # 1. missing-marks reminder dashboard (school-wide)
    # ------------------------------------------------------------------
    def missing_marks_dashboard(self, exam_id: int) -> List[Dict[str, Any]]:
        """Every (subject, class) a teacher is assigned to that still has
        students without a mark for this exam -- the Dean's 'who hasn't
        submitted yet' view, across the whole school."""
        assignments = self.conn.execute(
            "SELECT staff_no, subject_code, form_number, stream_code FROM subject_assignments"
        ).fetchall()
        result = []
        for a in assignments:
            subj_name = self.conn.execute(
                "SELECT name FROM subjects WHERE code=?", (a["subject_code"],)
            ).fetchone()
            missing = self.missing_marks(a["form_number"], a["stream_code"], a["subject_code"], exam_id)
            if missing:
                teacher = self.conn.execute(
                    "SELECT first_name, last_name FROM teachers WHERE staff_no=?", (a["staff_no"],)
                ).fetchone()
                result.append({
                    "staff_no": a["staff_no"],
                    "teacher_name": f"{teacher['first_name']} {teacher['last_name']}" if teacher else a["staff_no"],
                    "subject": subj_name["name"] if subj_name else a["subject_code"],
                    "class_code": f"{a['form_number']}{a['stream_code']}",
                    "missing_count": len(missing), "missing_students": missing,
                })
        result.sort(key=lambda r: -r["missing_count"])
        return result

    def send_missing_marks_reminder(self, staff_no: str, subject: str, class_code: str, missing_count: int):
        self.queue_notification(
            "teacher", staff_no, "email",
            f"Reminder: {missing_count} student(s) in {class_code} still need {subject} marks entered.",
        )

    # ------------------------------------------------------------------
    # 2. target-grade projection
    # ------------------------------------------------------------------
    def target_grade_projection(self, adm_no: str, exam_id: int, target_grade: str) -> Dict[str, Any]:
        """For each subject the student has a score in, the mark gap to reach
        a target grade (negative gap means they've already cleared it)."""
        target_grade = target_grade.strip().upper()
        cutoff = next((c for c, g in GRADE_BANDS if g == target_grade), None)
        if cutoff is None:
            raise ValueError(f"'{target_grade}' is not a valid grade (A, A-, B+, ... E).")
        marks = self.get_marks_for_student(adm_no, exam_id)
        subjects = []
        for m in marks:
            gap = round(cutoff - m["score"], 2)
            subjects.append({
                "subject": m["subject_name"], "current_score": m["score"], "current_grade": grade_for(m["score"]),
                "target_grade": target_grade, "target_cutoff": cutoff,
                "gap": gap, "achieved": gap <= 0,
            })
        current_mean = round(sum(m["score"] for m in marks) / len(marks), 2) if marks else None
        return {
            "adm_no": adm_no, "target_grade": target_grade, "subjects": subjects,
            "current_mean": current_mean, "mean_gap": round(cutoff - current_mean, 2) if current_mean is not None else None,
        }

    # ------------------------------------------------------------------
    # 3. HR: staff contracts & leave
    # ------------------------------------------------------------------
    def add_contract(self, staff_no: str, contract_type: str, start_date: str,
                      end_date: Optional[str] = None, notes: str = "") -> int:
        self._require_teacher(staff_no)
        cur = self.conn.execute(
            "INSERT INTO staff_contracts(staff_no, contract_type, start_date, end_date, notes, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (staff_no, contract_type, start_date, end_date, notes, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_contracts(self, staff_no: str) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM staff_contracts WHERE staff_no=? ORDER BY start_date DESC", (staff_no,)
        ).fetchall()

    def request_leave(self, staff_no: str, leave_type: str, start_date: str, end_date: str,
                       notes: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO staff_leave(staff_no, leave_type, start_date, end_date, notes, requested_at) "
            "VALUES (?,?,?,?,?,?)",
            (staff_no, leave_type, start_date, end_date, notes, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def decide_leave(self, leave_id: int, approver_staff_no: str, approve: bool):
        if not self.has_full_oversight(approver_staff_no):
            raise PermissionDenied(
                f"Teacher {approver_staff_no} may not approve/reject leave -- only an executive can."
            )
        self.conn.execute(
            "UPDATE staff_leave SET status=?, decided_by=?, decided_at=? WHERE id=?",
            ("approved" if approve else "rejected", approver_staff_no, _now(), leave_id),
        )
        self.conn.commit()

    def list_leave(self, staff_no: Optional[str] = None) -> List[sqlite3.Row]:
        if staff_no:
            return self.conn.execute(
                "SELECT * FROM staff_leave WHERE staff_no=? ORDER BY requested_at DESC", (staff_no,)
            ).fetchall()
        return self.conn.execute("SELECT * FROM staff_leave ORDER BY requested_at DESC").fetchall()

    # ------------------------------------------------------------------
    # 5. gender-stratified merit lists
    # ------------------------------------------------------------------
    def gender_stratified_merit_list(self, form_number: int, exam_id: int) -> Dict[str, List[Dict[str, Any]]]:
        full = self.form_merit_list(form_number, exam_id)
        genders = {
            r["adm_no"]: r["gender"] for r in self.conn.execute(
                "SELECT adm_no, gender FROM students WHERE form_number=?", (form_number,)
            ).fetchall()
        }
        male = [e for e in full if genders.get(e["adm_no"]) == "M"]
        female = [e for e in full if genders.get(e["adm_no"]) == "F"]
        unspecified = [e for e in full if genders.get(e["adm_no"]) not in ("M", "F")]
        for group in (male, female):
            for i, e in enumerate(group, start=1):
                e["gender_position"] = i
        return {"male": male, "female": female, "unspecified": unspecified}

    # ------------------------------------------------------------------
    # 6. in-app staff messaging
    # ------------------------------------------------------------------
    def send_message(self, sender_staff_no: str, recipient_staff_no: str, subject: str, body: str) -> int:
        self._require_teacher(recipient_staff_no)
        cur = self.conn.execute(
            "INSERT INTO messages(sender_staff_no, recipient_staff_no, subject, body, sent_at) "
            "VALUES (?,?,?,?,?)",
            (sender_staff_no, recipient_staff_no, subject, body, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def inbox(self, staff_no: str) -> List[sqlite3.Row]:
        return self.conn.execute(
            """SELECT m.*, t.first_name, t.last_name FROM messages m
               JOIN teachers t ON t.staff_no = m.sender_staff_no
               WHERE m.recipient_staff_no=? ORDER BY m.sent_at DESC""",
            (staff_no,),
        ).fetchall()

    def sent_messages(self, staff_no: str) -> List[sqlite3.Row]:
        return self.conn.execute(
            """SELECT m.*, t.first_name, t.last_name FROM messages m
               JOIN teachers t ON t.staff_no = m.recipient_staff_no
               WHERE m.sender_staff_no=? ORDER BY m.sent_at DESC""",
            (staff_no,),
        ).fetchall()

    def mark_message_read(self, message_id: int, staff_no: str):
        self.conn.execute(
            "UPDATE messages SET read_at=? WHERE id=? AND recipient_staff_no=? AND read_at IS NULL",
            (_now(), message_id, staff_no),
        )
        self.conn.commit()

    def unread_count(self, staff_no: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE recipient_staff_no=? AND read_at IS NULL", (staff_no,)
        ).fetchone()["c"]

    # ------------------------------------------------------------------
    # 7. discipline demerit-point system
    # ------------------------------------------------------------------
    def demerit_points_for_severity(self, severity: str) -> int:
        defaults = {"minor": 1, "moderate": 3, "major": 5}
        return int(self.get_setting(f"demerit_{severity}", str(defaults.get(severity, 1))))

    def set_demerit_points(self, severity: str, points: int):
        if severity not in ("minor", "moderate", "major"):
            raise ValueError("severity must be 'minor', 'moderate', or 'major'.")
        self.set_setting(f"demerit_{severity}", str(int(points)))

    def student_demerit_points(self, adm_no: str) -> int:
        records = self.get_discipline_records(adm_no)
        return sum(self.demerit_points_for_severity(r["severity"]) for r in records)

    def at_risk_students(self, form_number: int, threshold: int) -> List[Dict[str, Any]]:
        students = self.conn.execute("SELECT * FROM students WHERE form_number=?", (form_number,)).fetchall()
        flagged = []
        for s in students:
            points = self.student_demerit_points(s["adm_no"])
            if points >= threshold:
                flagged.append({
                    "adm_no": s["adm_no"], "name": f"{s['first_name']} {s['last_name']}",
                    "class_code": f"{form_number}{s['stream_code']}", "demerit_points": points,
                })
        flagged.sort(key=lambda r: -r["demerit_points"])
        return flagged

    # ------------------------------------------------------------------
    # capability checks -- power the "show what you can do" UI pattern
    # (instead of surfacing raw PermissionDenied text everywhere)
    # ------------------------------------------------------------------
    def can_edit_submitted_mark(self, staff_no: str, adm_no: str) -> bool:
        if self.has_full_oversight(staff_no):
            return True
        ct = self.class_teacher_of_student(adm_no)
        return ct is not None and ct["staff_no"] == staff_no

    def who_can_edit_submitted_mark(self, adm_no: str) -> str:
        """Human-readable name of the person a subject teacher should contact."""
        ct = self.class_teacher_of_student(adm_no)
        return f"{ct['first_name']} {ct['last_name']}" if ct else "the class supervisor"

    def can_print_class_list(self, staff_no: str, form_number: int, stream_code: str) -> bool:
        return self._can_manage_class(staff_no, form_number, stream_code) or self.has_full_oversight(staff_no)

    def can_print_form_merit_list(self, staff_no: str, form_number: int) -> bool:
        return (form_number in self.teacher_scope(staff_no)["supervises_forms"]
                or self.has_full_oversight(staff_no) or self.has_role(staff_no, "dean_of_studies"))

    def can_print_school_merit_list(self, staff_no: str) -> bool:
        return self.has_full_oversight(staff_no) or self.has_role(staff_no, "dean_of_studies")

    def can_view_audit_log(self, staff_no: str) -> bool:
        return self.has_full_oversight(staff_no)

    def can_moderate_subject(self, staff_no: str, subject: str) -> bool:
        if self.has_full_oversight(staff_no):
            return True
        code = subject_code(subject)
        dept_row = self.conn.execute(
            "SELECT department_code FROM department_subjects WHERE subject_code=?", (code,)
        ).fetchone()
        return bool(dept_row and self.has_role(staff_no, "hod", scope=dept_row["department_code"]))

    # ------------------------------------------------------------------
    # dashboard stats (students/teachers/streams counts)
    # ------------------------------------------------------------------
    def form_counts(self, form_number: int) -> Dict[str, Any]:
        streams = self.conn.execute(
            "SELECT code, name FROM streams WHERE form_number=? ORDER BY code", (form_number,)
        ).fetchall()
        stream_counts = []
        total_students = 0
        for s in streams:
            n = self.conn.execute(
                "SELECT COUNT(*) AS c FROM students WHERE form_number=? AND stream_code=?",
                (form_number, s["code"]),
            ).fetchone()["c"]
            total_students += n
            stream_counts.append({"code": s["code"], "name": s["name"], "count": n})
        teacher_count = self.conn.execute(
            """SELECT COUNT(DISTINCT staff_no) AS c FROM (
                 SELECT class_teacher_staff_no AS staff_no FROM streams WHERE form_number=? AND class_teacher_staff_no IS NOT NULL
                 UNION
                 SELECT staff_no FROM subject_assignments WHERE form_number=?
                 UNION
                 SELECT staff_no FROM form_supervisors WHERE form_number=?
               )""",
            (form_number, form_number, form_number),
        ).fetchone()["c"]
        return {"form_number": form_number, "streams": stream_counts,
                "total_students": total_students, "teacher_count": teacher_count}

    def school_counts(self) -> Dict[str, Any]:
        total_students = self.conn.execute("SELECT COUNT(*) AS c FROM students").fetchone()["c"]
        total_teachers = self.conn.execute("SELECT COUNT(*) AS c FROM teachers").fetchone()["c"]
        forms = [self.form_counts(n) for n in (1, 2, 3, 4)]
        return {"total_students": total_students, "total_teachers": total_teachers, "forms": forms}

    # ------------------------------------------------------------------
    # teacher groups (committees, clubs, ad-hoc groupings)
    # ------------------------------------------------------------------
    def create_teacher_group(self, name: str) -> int:
        cur = self.conn.execute("INSERT OR IGNORE INTO teacher_groups(name) VALUES (?)", (name.strip(),))
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM teacher_groups WHERE name=?", (name.strip(),)).fetchone()
        return row["id"]

    def add_to_group(self, group_id: int, staff_no: str):
        self._require_teacher(staff_no)
        self.conn.execute(
            "INSERT OR IGNORE INTO teacher_group_members(group_id, staff_no) VALUES (?,?)",
            (group_id, staff_no),
        )
        self.conn.commit()

    def remove_from_group(self, group_id: int, staff_no: str):
        self.conn.execute(
            "DELETE FROM teacher_group_members WHERE group_id=? AND staff_no=?", (group_id, staff_no)
        )
        self.conn.commit()

    def list_groups(self) -> List[Dict[str, Any]]:
        groups = self.conn.execute("SELECT * FROM teacher_groups ORDER BY name").fetchall()
        result = []
        for g in groups:
            members = self.conn.execute(
                """SELECT t.staff_no, t.first_name, t.last_name FROM teacher_group_members m
                   JOIN teachers t ON t.staff_no = m.staff_no WHERE m.group_id=?""",
                (g["id"],),
            ).fetchall()
            result.append({"id": g["id"], "name": g["name"],
                           "members": [f"{m['first_name']} {m['last_name']} ({m['staff_no']})" for m in members]})
        return result

    # ------------------------------------------------------------------
    # most improved students (compares two exams)
    # ------------------------------------------------------------------
    def most_improved(self, form_number: int, exam_id_before: int, exam_id_after: int,
                       top_n: int = 10) -> List[Dict[str, Any]]:
        students = self.conn.execute("SELECT * FROM students WHERE form_number=?", (form_number,)).fetchall()
        results = []
        for s in students:
            before = self.get_marks_for_student(s["adm_no"], exam_id_before)
            after = self.get_marks_for_student(s["adm_no"], exam_id_after)
            if not before or not after:
                continue
            mean_before = sum(m["score"] for m in before) / len(before)
            mean_after = sum(m["score"] for m in after) / len(after)
            results.append({
                "adm_no": s["adm_no"], "name": f"{s['first_name']} {s['last_name']}",
                "class_code": f"{form_number}{s['stream_code']}",
                "mean_before": round(mean_before, 2), "mean_after": round(mean_after, 2),
                "improvement": round(mean_after - mean_before, 2),
            })
        results.sort(key=lambda r: -r["improvement"])
        return results[:top_n]

    # ------------------------------------------------------------------
    # student profile updates
    # ------------------------------------------------------------------
    def update_student(self, adm_no: str, first_name: Optional[str] = None, last_name: Optional[str] = None,
                        form_number: Optional[int] = None, stream_name_or_code: Optional[str] = None):
        if not self.conn.execute("SELECT 1 FROM students WHERE adm_no=?", (adm_no,)).fetchone():
            raise NotFound(f"No such student '{adm_no}'.")
        if first_name is not None:
            self.conn.execute("UPDATE students SET first_name=? WHERE adm_no=?", (first_name.strip(), adm_no))
        if last_name is not None:
            self.conn.execute("UPDATE students SET last_name=? WHERE adm_no=?", (last_name.strip(), adm_no))
        if form_number is not None and stream_name_or_code is not None:
            stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
            self.conn.execute(
                "UPDATE students SET form_number=?, stream_code=? WHERE adm_no=?",
                (form_number, stream_code, adm_no),
            )
        self.conn.commit()
        self._audit("students", adm_no, "update_profile", "system", "")

    # ------------------------------------------------------------------
    # remedial / below-grade flagging
    # ------------------------------------------------------------------
    def students_below_threshold(self, form_number: int, exam_id: int, threshold: float,
                                  stream_name_or_code: Optional[str] = None,
                                  subject: Optional[str] = None) -> List[Dict[str, Any]]:
        """Flags students scoring below `threshold` (a percentage) -- either on
        one subject, or on their overall mean if no subject is given. Used for
        targeted remedial intervention lists."""
        query = "SELECT * FROM students WHERE form_number=?"
        params: List[Any] = [form_number]
        if stream_name_or_code:
            stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
            query += " AND stream_code=?"
            params.append(stream_code)
        students = self.conn.execute(query, params).fetchall()

        flagged = []
        code = subject_code(subject) if subject else None
        for s in students:
            marks = self.get_marks_for_student(s["adm_no"], exam_id)
            if code:
                relevant = [m for m in marks if m["subject_code"] == code]
                if not relevant:
                    continue
                score = relevant[0]["score"]
            else:
                if not marks:
                    continue
                score = sum(m["score"] for m in marks) / len(marks)
            if score < threshold:
                flagged.append({
                    "adm_no": s["adm_no"], "name": f"{s['first_name']} {s['last_name']}",
                    "class_code": f"{form_number}{s['stream_code']}", "score": round(score, 2),
                })
        flagged.sort(key=lambda r: r["score"])
        return flagged

    # ------------------------------------------------------------------
    # payment channels & auto-detected payments (simulated webhook)
    # ------------------------------------------------------------------
    def add_payment_channel(self, code: str, name: str, kind: str = "bank"):
        self.conn.execute(
            "INSERT OR REPLACE INTO payment_channels(code, name, kind) VALUES (?,?,?)",
            (code.strip().upper(), name.strip(), kind),
        )
        self.conn.commit()

    def list_payment_channels(self) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM payment_channels ORDER BY name").fetchall()

    def record_payment_via_channel(self, channel_code: str, reference: str, student_adm_no: str,
                                    amount: float, date: str, term: int, year: int) -> Dict[str, Any]:
        """Simulates an automatic payment detection webhook from a bank/paybill/
        till: given a channel + a unique reference, records the payment exactly
        once (idempotent on reference) and returns the updated live balance."""
        if not self.conn.execute("SELECT 1 FROM payment_channels WHERE code=?", (channel_code.upper(),)).fetchone():
            raise NotFound(f"No such payment channel '{channel_code}'.")
        if not self.conn.execute("SELECT 1 FROM students WHERE adm_no=?", (student_adm_no,)).fetchone():
            raise NotFound(f"No such student '{student_adm_no}'.")
        existing = self.conn.execute(
            "SELECT 1 FROM fee_payments WHERE reference=? AND channel_code=?", (reference, channel_code.upper())
        ).fetchone()
        if existing:
            # Idempotent: the same webhook reference should never double-credit an account
            return {"status": "duplicate_ignored", "balance": self.fee_balance(student_adm_no, term, year)}

        self.conn.execute(
            "INSERT INTO fee_payments(student_adm_no, amount, date, method, recorded_by, term, year, "
            "channel_code, reference) VALUES (?,?,?,?,?,?,?,?,?)",
            (student_adm_no, amount, date, "auto", None, term, year, channel_code.upper(), reference),
        )
        self.conn.commit()
        self._audit("fees", student_adm_no, "auto_payment",
                    f"channel:{channel_code}", f"{amount} ref={reference}")
        return {"status": "recorded", "balance": self.fee_balance(student_adm_no, term, year)}

    # ------------------------------------------------------------------
    # pledges & arrears
    # ------------------------------------------------------------------
    def add_pledge(self, student_adm_no: str, amount: float, expected_date: Optional[str] = None,
                    notes: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO pledges(student_adm_no, amount, expected_date, notes, created_at) VALUES (?,?,?,?,?)",
            (student_adm_no, amount, expected_date, notes, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def fulfil_pledge(self, pledge_id: int):
        self.conn.execute("UPDATE pledges SET fulfilled=1 WHERE id=?", (pledge_id,))
        self.conn.commit()

    def get_pledges(self, student_adm_no: str) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM pledges WHERE student_adm_no=? ORDER BY created_at DESC", (student_adm_no,)
        ).fetchall()

    def arrears_report(self, term: int, year: int) -> List[Dict[str, Any]]:
        """Outstanding balances plus any pledges made against them -- the
        'pledge vs arrears' reconciliation view."""
        balances = self.outstanding_balances(term, year)
        for b in balances:
            pledges = self.conn.execute(
                "SELECT COALESCE(SUM(amount),0) AS t FROM pledges WHERE student_adm_no=? AND fulfilled=0",
                (b["adm_no"],),
            ).fetchone()["t"]
            b["pledged"] = pledges
            b["unpledged_balance"] = round(b["balance"] - pledges, 2)
        return balances

    # ------------------------------------------------------------------
    # send results (queues a notification; see queue_notification for the
    # dispatch caveat -- no real SMS/email provider is connected yet)
    # ------------------------------------------------------------------
    def send_results_notification(self, adm_no: str, exam_id: int, channel: str = "sms") -> bool:
        if not self.is_exam_published(exam_id):
            raise PermissionDenied("Results for this exam are not published yet; cannot send them out.")
        parent_row = self.conn.execute(
            "SELECT parent_id FROM parent_students WHERE student_adm_no=? LIMIT 1", (adm_no,)
        ).fetchone()
        student = self.conn.execute("SELECT first_name, last_name FROM students WHERE adm_no=?", (adm_no,)).fetchone()
        name = f"{student['first_name']} {student['last_name']}" if student else adm_no
        recipient = str(parent_row["parent_id"]) if parent_row else adm_no
        self.queue_notification("parent" if parent_row else "student", recipient, channel,
                                f"Results for {name} ({adm_no}) are now available. Please check the parent portal.")
        return True

    # ------------------------------------------------------------------
    # school calendar
    # ------------------------------------------------------------------
    def add_event(self, title: str, date: str, created_by: str, description: str = "",
                   category: str = "general", end_date: Optional[str] = None,
                   form_number: Optional[int] = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO calendar_events(title, description, date, end_date, category, "
            "form_number, created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (title, description, date, end_date, category, form_number, created_by, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_events(self, start_date: Optional[str] = None, end_date: Optional[str] = None,
                     form_number: Optional[int] = None) -> List[sqlite3.Row]:
        query = "SELECT * FROM calendar_events WHERE 1=1"
        params: List[Any] = []
        if start_date:
            query += " AND date>=?"
            params.append(start_date)
        if end_date:
            query += " AND date<=?"
            params.append(end_date)
        if form_number is not None:
            query += " AND (form_number IS NULL OR form_number=?)"
            params.append(form_number)
        query += " ORDER BY date"
        return self.conn.execute(query, params).fetchall()

    def delete_event(self, event_id: int, staff_no: str):
        row = self.conn.execute("SELECT created_by FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            raise NotFound(f"No such event {event_id}.")
        if row["created_by"] != staff_no and not self.has_full_oversight(staff_no):
            raise PermissionDenied("Only the event's creator or an executive may delete it.")
        self.conn.execute("DELETE FROM calendar_events WHERE id=?", (event_id,))
        self.conn.commit()

    # ------------------------------------------------------------------
    # financial oversight helpers
    # ------------------------------------------------------------------
    def outstanding_balances(self, term: int, year: int) -> List[Dict[str, Any]]:
        """Every student with a non-zero fee balance for a term -- the finance
        dashboard's 'who still owes money' view."""
        invoices = self.conn.execute(
            "SELECT fi.student_adm_no, fi.amount_billed, s.first_name, s.last_name, "
            "s.form_number, s.stream_code FROM fee_invoices fi "
            "JOIN students s ON s.adm_no = fi.student_adm_no WHERE fi.term=? AND fi.year=?",
            (term, year),
        ).fetchall()
        result = []
        for inv in invoices:
            paid = self.conn.execute(
                "SELECT COALESCE(SUM(amount),0) AS total FROM fee_payments "
                "WHERE student_adm_no=? AND term=? AND year=?",
                (inv["student_adm_no"], term, year),
            ).fetchone()["total"]
            balance = round(inv["amount_billed"] - paid, 2)
            if balance > 0:
                result.append({
                    "adm_no": inv["student_adm_no"], "name": f"{inv['first_name']} {inv['last_name']}",
                    "class_code": f"{inv['form_number']}{inv['stream_code']}",
                    "billed": inv["amount_billed"], "paid": paid, "balance": balance,
                })
        result.sort(key=lambda r: -r["balance"])
        return result

    # ------------------------------------------------------------------
    # consolidated "all in one" student profile
    # ------------------------------------------------------------------
    def student_profile(self, adm_no: str, term: Optional[int] = None, year: Optional[int] = None) -> Dict[str, Any]:
        """Everything about one student in a single call: identity, class,
        attendance, discipline, fee balance, and their class's timetable."""
        student = self.conn.execute("SELECT * FROM students WHERE adm_no=?", (adm_no,)).fetchone()
        if not student:
            raise NotFound(f"No such active student '{adm_no}'.")
        profile = {
            "adm_no": student["adm_no"], "name": f"{student['first_name']} {student['last_name']}",
            "class_code": f"{student['form_number']}{student['stream_code']}",
            "attendance": self.attendance_summary(adm_no),
            "attendance_rate": self.attendance_rate(adm_no),
            "discipline": [dict(r) for r in self.get_discipline_records(adm_no)],
            "timetable": [dict(r) for r in self.get_timetable(student["form_number"], student["stream_code"])],
        }
        if term is not None and year is not None:
            profile["fee_balance"] = self.fee_balance(adm_no, term, year)
        return profile

    # ------------------------------------------------------------------
    # end-of-year promotion & graduation
    # ------------------------------------------------------------------
    def promote_students(self, from_form: int, to_form: Optional[int] = None,
                          exclude: Optional[List[str]] = None) -> int:
        """Move every student in from_form up to to_form (default from_form+1),
        keeping the same stream where possible. The class teacher and
        supervisor for each affected stream move up with their cohort too
        (the target form is assumed empty/vacated before this runs, which is
        how promote_all() sequences things). Form 4 cannot be promoted this
        way -- use graduate_form() instead."""
        to_form = to_form or from_form + 1
        if to_form > 4:
            raise ValueError("Form 4 students graduate via graduate_form(), not promote_students().")
        exclude = set(exclude or [])
        students = self.conn.execute(
            "SELECT adm_no, stream_code FROM students WHERE form_number=?", (from_form,)
        ).fetchall()
        moved = 0
        streams_moved = set()
        for s in students:
            if s["adm_no"] in exclude:
                continue
            target = self.conn.execute(
                "SELECT 1 FROM streams WHERE form_number=? AND code=?", (to_form, s["stream_code"])
            ).fetchone()
            if not target:
                raise NotFound(
                    f"Stream '{s['stream_code']}' does not exist in Form {to_form}; "
                    f"resolve the mismatch before promoting."
                )
            self.conn.execute(
                "UPDATE students SET form_number=? WHERE adm_no=?", (to_form, s["adm_no"])
            )
            moved += 1
            streams_moved.add(s["stream_code"])

        # Move (not copy) each affected stream's class teacher up with the cohort
        for stream_code in streams_moved:
            old_ct = self.conn.execute(
                "SELECT class_teacher_staff_no FROM streams WHERE form_number=? AND code=?",
                (from_form, stream_code),
            ).fetchone()
            if old_ct and old_ct["class_teacher_staff_no"]:
                self.conn.execute(
                    "UPDATE streams SET class_teacher_staff_no=? WHERE form_number=? AND code=?",
                    (old_ct["class_teacher_staff_no"], to_form, stream_code),
                )
                self.conn.execute(
                    "UPDATE streams SET class_teacher_staff_no=NULL WHERE form_number=? AND code=?",
                    (from_form, stream_code),
                )

        # Subject teachers and the class timetable follow the cohort as well, so
        # a teacher who moves up from 1W to 2W sees 2W and no longer the new 1W
        # intake. The target slot is vacated first (promote_all already empties
        # it by moving that cohort up a level before this one).
        for stream_code in streams_moved:
            self.conn.execute(
                "DELETE FROM subject_assignments WHERE form_number=? AND stream_code=?", (to_form, stream_code))
            self.conn.execute(
                "UPDATE subject_assignments SET form_number=? WHERE form_number=? AND stream_code=?",
                (to_form, from_form, stream_code))
            self.conn.execute(
                "DELETE FROM timetable WHERE form_number=? AND stream_code=?", (to_form, stream_code))
            self.conn.execute(
                "UPDATE timetable SET form_number=? WHERE form_number=? AND stream_code=?",
                (to_form, from_form, stream_code))

        # Move the form supervisor up with the cohort too
        old_sup = self.conn.execute(
            "SELECT staff_no FROM form_supervisors WHERE form_number=?", (from_form,)
        ).fetchone()
        if old_sup:
            self.conn.execute(
                "INSERT INTO form_supervisors(form_number, staff_no) VALUES (?,?) "
                "ON CONFLICT(form_number) DO UPDATE SET staff_no=excluded.staff_no",
                (to_form, old_sup["staff_no"]),
            )
            self.conn.execute("DELETE FROM form_supervisors WHERE form_number=?", (from_form,))

        self.conn.commit()
        self._audit("students", f"form{from_form}", "promote", "system",
                    f"{moved} students moved Form {from_form} -> Form {to_form}, staff transferred")
        return moved

    def graduate_form(self, form_number: int = 4, graduation_year: Optional[int] = None,
                       exclude: Optional[List[str]] = None, staff_no: Optional[str] = None) -> int:
        """Move every Form 4 (by default) student into the alumni table and off
        the active roll. Requires an executive role if staff_no is supplied.
        Moving students between forms is a whole-school administrative action,
        so any of the four executive roles (Principal, either Deputy Principal,
        or Dean of Studies) may do this -- not just the narrower set that can
        publish exam results.

        Graduates keep their academic history (marks, attendance, discipline,
        fees) even though their `students` row is removed -- those tables
        reference the same adm_no, which now lives on in the `alumni` table.
        Deleting the `students` row while that history still points at it
        would violate the foreign key constraint, so FK enforcement is
        toggled off for just this delete (same safe pattern used when
        permanently removing a teacher)."""
        if staff_no is not None and not self.has_full_oversight(staff_no):
            raise PermissionDenied(
                f"Teacher {staff_no} may not graduate a form -- only the Principal, a Deputy "
                f"Principal, or the Dean of Studies can."
            )
        graduation_year = graduation_year or datetime.now().year
        exclude = set(exclude or [])
        students = self.conn.execute("SELECT * FROM students WHERE form_number=?", (form_number,)).fetchall()
        count = 0
        for s in students:
            if s["adm_no"] in exclude:
                continue
            self.conn.execute(
                "INSERT OR REPLACE INTO alumni(adm_no, first_name, last_name, graduation_year, final_class) "
                "VALUES (?,?,?,?,?)",
                (s["adm_no"], s["first_name"], s["last_name"], graduation_year,
                 f"{form_number}{s['stream_code']}"),
            )
            count += 1

        if count:
            self.conn.commit()  # PRAGMA foreign_keys is a no-op inside an open transaction
            self.conn.execute("PRAGMA foreign_keys = OFF")
            for s in students:
                if s["adm_no"] in exclude:
                    continue
                self.conn.execute("DELETE FROM students WHERE adm_no=?", (s["adm_no"],))
            self.conn.commit()
            self.conn.execute("PRAGMA foreign_keys = ON")

        # The form is now vacated -- clear its staffing so the next promoted
        # cohort's teachers/supervisor (moved in by promote_students) aren't
        # shadowed by stale leftovers from the class that just graduated.
        self.conn.execute("UPDATE streams SET class_teacher_staff_no=NULL WHERE form_number=?", (form_number,))
        self.conn.execute("DELETE FROM form_supervisors WHERE form_number=?", (form_number,))
        self.conn.execute("DELETE FROM subject_assignments WHERE form_number=?", (form_number,))
        self.conn.execute("DELETE FROM timetable WHERE form_number=?", (form_number,))
        self.conn.commit()

        self._audit("students", f"form{form_number}", "graduate", staff_no or "system",
                    f"{count} students graduated in {graduation_year}")
        return count

    def promote_all(self, staff_no: str, graduation_year: Optional[int] = None) -> Dict[str, int]:
        """The full end-of-year rollover: Form 4 graduates, then 3->4, 2->3, 1->2
        (done top-down so no one is promoted twice in one call). Form 1 is left
        empty for the next intake."""
        if not self.has_full_oversight(staff_no):
            raise PermissionDenied(
                f"Teacher {staff_no} may not run end-of-year promotion -- only the Principal, "
                f"a Deputy Principal, or the Dean of Studies can."
            )
        graduated = self.graduate_form(4, graduation_year=graduation_year)
        p3_4 = self.promote_students(3, 4)
        p2_3 = self.promote_students(2, 3)
        p1_2 = self.promote_students(1, 2)
        return {"graduated": graduated, "3->4": p3_4, "2->3": p2_3, "1->2": p1_2}

    # ------------------------------------------------------------------
    # student search & alumni
    # ------------------------------------------------------------------
    def search_students(self, query: str, include_alumni: bool = False) -> List[Dict[str, str]]:
        q = f"%{query.strip().lower()}%"
        rows = self.conn.execute(
            "SELECT * FROM students WHERE LOWER(first_name) LIKE ? OR LOWER(last_name) LIKE ? "
            "OR LOWER(adm_no) LIKE ?",
            (q, q, q),
        ).fetchall()
        results = [
            {"adm_no": r["adm_no"], "name": f"{r['first_name']} {r['last_name']}",
             "class_code": f"{r['form_number']}{r['stream_code']}", "type": "current"}
            for r in rows
        ]
        if include_alumni:
            arows = self.conn.execute(
                "SELECT * FROM alumni WHERE LOWER(first_name) LIKE ? OR LOWER(last_name) LIKE ? "
                "OR LOWER(adm_no) LIKE ?",
                (q, q, q),
            ).fetchall()
            results += [
                {"adm_no": r["adm_no"], "name": f"{r['first_name']} {r['last_name']}",
                 "class_code": r["final_class"], "type": f"alumni ({r['graduation_year']})"}
                for r in arows
            ]
        return results

    def get_alumni(self, graduation_year: Optional[int] = None) -> List[sqlite3.Row]:
        if graduation_year is None:
            return self.conn.execute("SELECT * FROM alumni ORDER BY graduation_year DESC, last_name").fetchall()
        return self.conn.execute(
            "SELECT * FROM alumni WHERE graduation_year=? ORDER BY last_name", (graduation_year,)
        ).fetchall()

    def update_alumni_contact(self, adm_no: str, email: Optional[str] = None, phone: Optional[str] = None):
        if not self.conn.execute("SELECT 1 FROM alumni WHERE adm_no=?", (adm_no,)).fetchone():
            raise NotFound(f"No such alumnus '{adm_no}'.")
        if email is not None:
            self.conn.execute("UPDATE alumni SET contact_email=? WHERE adm_no=?", (email, adm_no))
        if phone is not None:
            self.conn.execute("UPDATE alumni SET contact_phone=? WHERE adm_no=?", (phone, adm_no))
        self.conn.commit()

    # ------------------------------------------------------------------
    # grade calculation / distribution
    # ------------------------------------------------------------------
    def subject_grade_breakdown(self, form_number: int, subject: str, exam_id: int) -> Dict[str, Any]:
        """Per-stream grade breakdown for one subject in one form/exam: grade
        letter counts, entries, mean marks, mean points, each stream's
        deviation from the form-wide mean for that subject, and who teaches
        it in that class -- plus a totals row across the whole form."""
        code = subject_code(subject)
        subj_row = self.conn.execute("SELECT name FROM subjects WHERE code=?", (code,)).fetchone()
        if not subj_row:
            raise NotFound(f"No such subject '{subject}'.")

        streams = self.conn.execute(
            "SELECT code, name, class_teacher_staff_no FROM streams WHERE form_number=? ORDER BY code",
            (form_number,),
        ).fetchall()

        grade_letters = [letter for _, letter in GRADE_BANDS]
        stream_rows = []
        all_scores: List[float] = []

        for st in streams:
            rows = self.conn.execute(
                """SELECT m.score FROM marks m JOIN students s ON s.adm_no = m.student_adm_no
                   WHERE m.subject_code=? AND m.exam_id=? AND s.form_number=? AND s.stream_code=?""",
                (code, exam_id, form_number, st["code"]),
            ).fetchall()
            scores = [r["score"] for r in rows]
            all_scores.extend(scores)

            counts = {g: 0 for g in grade_letters}
            for sc in scores:
                counts[grade_for(sc)] += 1

            mean_marks = round(sum(scores) / len(scores), 2) if scores else None
            mean_points = round(sum(points_for(grade_for(sc)) for sc in scores) / len(scores), 2) if scores else None

            teacher_name, teacher_staff_no = "Unassigned", "-"
            assign_row = self.conn.execute(
                "SELECT staff_no FROM subject_assignments WHERE subject_code=? AND form_number=? AND stream_code=?",
                (code, form_number, st["code"]),
            ).fetchone()
            if assign_row:
                t = self.conn.execute(
                    "SELECT first_name, last_name FROM teachers WHERE staff_no=?", (assign_row["staff_no"],)
                ).fetchone()
                if t:
                    teacher_name = f"{t['first_name']} {t['last_name']}"
                    teacher_staff_no = assign_row["staff_no"]

            stream_rows.append({
                "class_code": f"{form_number}{st['code']}", "stream_name": st["name"],
                "grade_counts": counts, "entries": len(scores),
                "mean_marks": mean_marks, "mean_points": mean_points,
                "mean_grade": grade_for(mean_marks) if mean_marks is not None else "-",
                "subject_teacher": teacher_name, "subject_teacher_staff_no": teacher_staff_no,
            })

        form_mean_marks = round(sum(all_scores) / len(all_scores), 2) if all_scores else None
        form_mean_points = round(sum(points_for(grade_for(sc)) for sc in all_scores) / len(all_scores), 2) \
            if all_scores else None

        for s in stream_rows:
            s["mean_marks_deviation"] = (
                round(s["mean_marks"] - form_mean_marks, 2)
                if s["mean_marks"] is not None and form_mean_marks is not None else None
            )
            s["mean_points_deviation"] = (
                round(s["mean_points"] - form_mean_points, 2)
                if s["mean_points"] is not None and form_mean_points is not None else None
            )

        totals_counts = {g: sum(s["grade_counts"][g] for s in stream_rows) for g in grade_letters}

        return {
            "form_number": form_number, "subject_name": subj_row["name"], "grade_letters": grade_letters,
            "streams": stream_rows, "totals": {
                "grade_counts": totals_counts, "entries": len(all_scores),
                "mean_marks": form_mean_marks, "mean_points": form_mean_points,
                "mean_grade": grade_for(form_mean_marks) if form_mean_marks is not None else "-",
            },
        }

    def grade_distribution(self, exam_id: int, form_number: Optional[int] = None,
                            stream_name_or_code: Optional[str] = None,
                            subject: Optional[str] = None) -> Dict[str, int]:
        """Count of each grade (A..E) for a whole exam, optionally narrowed to
        a form, a specific class, and/or a specific subject."""
        query = "SELECT m.score FROM marks m JOIN students s ON s.adm_no = m.student_adm_no WHERE m.exam_id=?"
        params: List[Any] = [exam_id]
        if subject:
            query += " AND m.subject_code=?"
            params.append(subject_code(subject))
        if form_number:
            query += " AND s.form_number=?"
            params.append(form_number)
            if stream_name_or_code:
                stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
                query += " AND s.stream_code=?"
                params.append(stream_code)
        scores = [r["score"] for r in self.conn.execute(query, params).fetchall()]
        dist = {letter: 0 for _, letter in GRADE_BANDS}
        for sc in scores:
            dist[grade_for(sc)] += 1
        return dist

    # ------------------------------------------------------------------
    # wholesome financial management
    # ------------------------------------------------------------------
    def set_fee_structure(self, form_number: int, term: int, year: int, amount: float):
        self.conn.execute(
            "INSERT OR REPLACE INTO fee_structures(form_number, term, year, amount) VALUES (?,?,?,?)",
            (form_number, term, year, amount),
        )
        self.conn.commit()

    def generate_invoices_for_form(self, form_number: int, term: int, year: int) -> int:
        """Bulk-bill every student in a form using the form's fee structure."""
        row = self.conn.execute(
            "SELECT amount FROM fee_structures WHERE form_number=? AND term=? AND year=?",
            (form_number, term, year),
        ).fetchone()
        if not row:
            raise NotFound(f"No fee structure set for Form {form_number}, Term {term} {year}.")
        students = self.conn.execute("SELECT adm_no FROM students WHERE form_number=?", (form_number,)).fetchall()
        for s in students:
            self.add_fee_invoice(s["adm_no"], term, year, row["amount"])
        return len(students)

    def record_payment(self, student_adm_no: str, amount: float, date: str, term: int, year: int,
                        method: str = "cash", recorded_by: Optional[str] = None):
        self.conn.execute(
            "INSERT INTO fee_payments(student_adm_no, amount, date, method, recorded_by, term, year) "
            "VALUES (?,?,?,?,?,?,?)",
            (student_adm_no, amount, date, method, recorded_by, term, year),
        )
        self.conn.commit()
        self._audit("fees", student_adm_no, "payment", recorded_by or "system",
                    f"{amount} via {method} (Term {term} {year})")

    def fee_balance(self, student_adm_no: str, term: int, year: int) -> Dict[str, float]:
        invoice = self.conn.execute(
            "SELECT amount_billed FROM fee_invoices WHERE student_adm_no=? AND term=? AND year=?",
            (student_adm_no, term, year),
        ).fetchone()
        billed = invoice["amount_billed"] if invoice else 0.0
        paid = self.conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS total FROM fee_payments "
            "WHERE student_adm_no=? AND term=? AND year=?",
            (student_adm_no, term, year),
        ).fetchone()["total"]
        return {"billed": billed, "paid": paid, "balance": round(billed - paid, 2)}

    def add_expense(self, category: str, amount: float, date: str, description: str = "",
                     recorded_by: Optional[str] = None, receipt_path: Optional[str] = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO expenses(category, amount, date, description, recorded_by, receipt_path) "
            "VALUES (?,?,?,?,?,?)",
            (category, amount, date, description, recorded_by, receipt_path),
        )
        self.conn.commit()
        self._audit("expenses", category, "add", recorded_by or "system", f"{amount} on {date}: {description}")
        return cur.lastrowid

    def list_expenses(self, year: Optional[int] = None) -> List[sqlite3.Row]:
        if year is not None:
            return self.conn.execute(
                "SELECT * FROM expenses WHERE date LIKE ? ORDER BY date DESC", (f"{year}-%",)
            ).fetchall()
        return self.conn.execute("SELECT * FROM expenses ORDER BY date DESC").fetchall()

    def financial_summary(self, term: int, year: int) -> Dict[str, float]:
        billed = self.conn.execute(
            "SELECT COALESCE(SUM(amount_billed),0) AS t FROM fee_invoices WHERE term=? AND year=?",
            (term, year),
        ).fetchone()["t"]
        collected = self.conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS t FROM fee_payments WHERE term=? AND year=?",
            (term, year),
        ).fetchone()["t"]
        expenses = self.conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS t FROM expenses WHERE date LIKE ?", (f"{year}-%",)
        ).fetchone()["t"]
        return {
            "billed": round(billed, 2), "collected": round(collected, 2),
            "outstanding": round(billed - collected, 2), "expenses": round(expenses, 2),
            "net": round(collected - expenses, 2),
        }

    # ------------------------------------------------------------------
    # wholesome attendance reporting
    # ------------------------------------------------------------------
    def attendance_rate(self, student_adm_no: str) -> Optional[float]:
        summary = self.attendance_summary(student_adm_no)
        total = sum(summary.values())
        if total == 0:
            return None
        return round(summary["present"] / total * 100, 1)

    def class_attendance_report(self, form_number: int, stream_name_or_code: str) -> List[Dict[str, Any]]:
        stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
        students = self.conn.execute(
            "SELECT * FROM students WHERE form_number=? AND stream_code=? ORDER BY last_name",
            (form_number, stream_code),
        ).fetchall()
        return [
            {"adm_no": s["adm_no"], "name": f"{s['first_name']} {s['last_name']}",
             "attendance_rate": self.attendance_rate(s["adm_no"])}
            for s in students
        ]

    def low_attendance_alert(self, form_number: int, stream_name_or_code: str,
                              threshold: float = 75.0) -> List[Dict[str, Any]]:
        report = self.class_attendance_report(form_number, stream_name_or_code)
        return [r for r in report if r["attendance_rate"] is not None and r["attendance_rate"] < threshold]

    # ------------------------------------------------------------------
    # audit log
    # ------------------------------------------------------------------
    def _audit(self, entity: str, entity_id: str, action: str, actor: str, details: str = ""):
        self.conn.execute(
            "INSERT INTO audit_log(entity, entity_id, action, actor, details, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (entity, entity_id, action, actor, details, _now()),
        )
        self.conn.commit()

    def get_audit_log(self, entity: Optional[str] = None, entity_id: Optional[str] = None) -> List[sqlite3.Row]:
        query = "SELECT * FROM audit_log WHERE 1=1"
        params: List[Any] = []
        if entity:
            query += " AND entity=?"
            params.append(entity)
        if entity_id:
            query += " AND entity_id=?"
            params.append(entity_id)
        query += " ORDER BY created_at DESC"
        return self.conn.execute(query, params).fetchall()

    # ------------------------------------------------------------------
    # school-level roles: Principal, Deputy Principals, Dean of Studies, HOD
    # ------------------------------------------------------------------
    def grant_role(self, staff_no: str, role: str, scope: str = "school"):
        if role not in EXECUTIVE_ROLES:
            raise ValueError(f"Unknown role '{role}'. Must be one of {sorted(EXECUTIVE_ROLES)}.")
        self._require_teacher(staff_no)
        self.conn.execute(
            "INSERT OR IGNORE INTO staff_roles(staff_no, role, scope) VALUES (?,?,?)",
            (staff_no, role, scope),
        )
        self.conn.commit()
        self._audit("staff_roles", staff_no, "grant_role", staff_no, f"{role}:{scope}")

    def revoke_role(self, staff_no: str, role: str, scope: str = "school"):
        self.conn.execute(
            "DELETE FROM staff_roles WHERE staff_no=? AND role=? AND scope=?",
            (staff_no, role, scope),
        )
        self.conn.commit()

    def has_role(self, staff_no: str, role: str, scope: Optional[str] = None) -> bool:
        if scope is None:
            row = self.conn.execute(
                "SELECT 1 FROM staff_roles WHERE staff_no=? AND role=?", (staff_no, role)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT 1 FROM staff_roles WHERE staff_no=? AND role=? AND scope=?",
                (staff_no, role, scope),
            ).fetchone()
        return row is not None

    def get_roles(self, staff_no: str) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT role, scope FROM staff_roles WHERE staff_no=?", (staff_no,)
        ).fetchall()

    def can_publish_results(self, staff_no: str) -> bool:
        """Principal, either Deputy Principal (academics), or Dean of Studies may publish."""
        return (self.has_role(staff_no, "principal")
                or self.has_role(staff_no, "deputy_principal_academics")
                or self.has_role(staff_no, "dean_of_studies"))

    def has_full_oversight(self, staff_no: str) -> bool:
        """Principal, either Deputy Principal, or Dean of Studies: sees and can
        act on EVERYTHING, bypassing the normal per-class/per-department scoping
        that applies to class teachers, supervisors, subject teachers, and HODs."""
        return any(self.has_role(staff_no, r) for r in
                   ("principal", "deputy_principal_academics", "deputy_principal_admin", "dean_of_studies"))

    # ------------------------------------------------------------------
    # departments & HOD
    # ------------------------------------------------------------------
    def add_department(self, name: str, subjects: Optional[List[str]] = None) -> str:
        code = name.strip()[:4].upper()
        self.conn.execute("INSERT OR IGNORE INTO departments(code, name) VALUES (?,?)", (code, name.strip()))
        for subj in (subjects or []):
            self.conn.execute(
                "INSERT OR IGNORE INTO department_subjects(department_code, subject_code) VALUES (?,?)",
                (code, subject_code(subj)),
            )
        self.conn.commit()
        return code

    def assign_hod(self, department_code: str, staff_no: str):
        if not self.conn.execute("SELECT 1 FROM departments WHERE code=?", (department_code,)).fetchone():
            raise NotFound(f"No such department '{department_code}'.")
        self.grant_role(staff_no, "hod", scope=department_code)

    def hod_of(self, department_code: str) -> Optional[sqlite3.Row]:
        row = self.conn.execute(
            """SELECT t.* FROM staff_roles sr JOIN teachers t ON t.staff_no = sr.staff_no
               WHERE sr.role='hod' AND sr.scope=?""",
            (department_code,),
        ).fetchone()
        return row

    def department_subjects_list(self, department_code: str) -> List[str]:
        return [r["subject_code"] for r in self.conn.execute(
            "SELECT subject_code FROM department_subjects WHERE department_code=?", (department_code,)
        ).fetchall()]

    def department_performance(self, department_code: str, exam_id: int) -> Dict[str, Any]:
        """Mean score per subject in this department, aggregated across the WHOLE
        SCHOOL (every form and stream) -- this is the HOD's view."""
        subj_codes = self.department_subjects_list(department_code)
        if not subj_codes:
            raise NotFound(f"Department '{department_code}' has no subjects assigned.")
        placeholders = ",".join("?" * len(subj_codes))
        rows = self.conn.execute(
            f"""
            SELECT sub.code AS subject_code, sub.name AS subject_name,
                   AVG(m.score) AS mean_score, COUNT(*) AS n
            FROM marks m JOIN subjects sub ON sub.code = m.subject_code
            WHERE m.subject_code IN ({placeholders}) AND m.exam_id=?
            GROUP BY sub.code
            """,
            (*subj_codes, exam_id),
        ).fetchall()
        subjects = [{"code": r["subject_code"], "name": r["subject_name"],
                     "mean_score": round(r["mean_score"], 2), "n": r["n"]} for r in rows]
        scored = [s["mean_score"] for s in subjects]
        dept_mean = round(sum(scored) / len(scored), 2) if scored else None
        return {"department_code": department_code, "department_mean": dept_mean, "subjects": subjects}

    # ------------------------------------------------------------------
    # moderation (HOD reviewing a subject teacher's marks before lock)
    # ------------------------------------------------------------------
    def add_moderation_note(self, staff_no: str, subject: str, form_number: int,
                             stream_name_or_code: str, exam_id: int, note: str):
        code = subject_code(subject)
        dept_row = self.conn.execute(
            "SELECT department_code FROM department_subjects WHERE subject_code=?", (code,)
        ).fetchone()
        is_hod = dept_row and self.has_role(staff_no, "hod", scope=dept_row["department_code"])
        if not is_hod and not self.has_full_oversight(staff_no):
            raise PermissionDenied(
                f"Teacher {staff_no} is not the HOD for the department that owns '{subject}'."
            )
        stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
        self.conn.execute(
            "INSERT INTO moderation_notes(staff_no, subject_code, form_number, stream_code, "
            "exam_id, note, created_at) VALUES (?,?,?,?,?,?,?)",
            (staff_no, code, form_number, stream_code, exam_id, note, _now()),
        )
        self.conn.commit()

    def get_moderation_notes(self, form_number: int, stream_name_or_code: str, exam_id: int) -> List[sqlite3.Row]:
        stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
        return self.conn.execute(
            "SELECT * FROM moderation_notes WHERE form_number=? AND stream_code=? AND exam_id=? "
            "ORDER BY created_at",
            (form_number, stream_code, exam_id),
        ).fetchall()

    # ------------------------------------------------------------------
    # results publishing gate
    # ------------------------------------------------------------------
    def publish_exam_results(self, staff_no: str, exam_id: int):
        if not self.can_publish_results(staff_no):
            raise PermissionDenied(
                f"Teacher {staff_no} may not publish results -- only the Principal, a Deputy "
                f"Principal (Academics), or the Dean of Studies can do this."
            )
        self.conn.execute(
            "INSERT OR REPLACE INTO exam_publications(exam_id, published_by, published_at) "
            "VALUES (?,?,?)",
            (exam_id, staff_no, _now()),
        )
        self.conn.commit()
        self._audit("exams", str(exam_id), "publish", staff_no, "")

    def is_exam_published(self, exam_id: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM exam_publications WHERE exam_id=?", (exam_id,)
        ).fetchone() is not None

    def unpublish_exam_results(self, staff_no: str, exam_id: int):
        """Reverses a publish -- only an executive can do this, and only they
        (not a class teacher) can edit marks again afterward, until re-published."""
        if not self.has_full_oversight(staff_no):
            raise PermissionDenied(
                f"Teacher {staff_no} may not unpublish results -- only the Principal, a Deputy "
                f"Principal, or the Dean of Studies can do this."
            )
        self.conn.execute("DELETE FROM exam_publications WHERE exam_id=?", (exam_id,))
        self.conn.commit()
        self._audit("exams", str(exam_id), "unpublish", staff_no, "")

    # ------------------------------------------------------------------
    # missing marks tracker
    # ------------------------------------------------------------------
    def missing_marks(self, form_number: int, stream_name_or_code: str, subject: str, exam_id: int) -> List[Dict[str, str]]:
        stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
        code = subject_code(subject)
        students = self.conn.execute(
            "SELECT adm_no, first_name, last_name FROM students WHERE form_number=? AND stream_code=?",
            (form_number, stream_code),
        ).fetchall()
        missing = []
        for s in students:
            row = self.conn.execute(
                "SELECT 1 FROM marks WHERE student_adm_no=? AND subject_code=? AND exam_id=?",
                (s["adm_no"], code, exam_id),
            ).fetchone()
            if not row:
                missing.append({"adm_no": s["adm_no"], "name": f"{s['first_name']} {s['last_name']}"})
        return missing

    # ------------------------------------------------------------------
    # KCSE-style clustering / mean points
    # ------------------------------------------------------------------
    def student_cluster_points(self, adm_no: str, exam_id: int, best_n: int = 7) -> Dict[str, Any]:
        marks = self.get_marks_for_student(adm_no, exam_id)
        scored = sorted(
            [{"subject": m["subject_name"], "score": m["score"], "grade": grade_for(m["score"]),
              "points": points_for(grade_for(m["score"]))} for m in marks],
            key=lambda x: -x["points"],
        )
        best = scored[:best_n]
        total_points = sum(s["points"] for s in best)
        mean_points = round(total_points / len(best), 2) if best else 0.0
        return {"adm_no": adm_no, "subjects_considered": best, "total_points": total_points,
                "mean_points": mean_points, "best_n": best_n}

    # ------------------------------------------------------------------
    # trend / deviation analysis
    # ------------------------------------------------------------------
    def student_trend(self, adm_no: str) -> List[Dict[str, Any]]:
        """This student's mean score across every exam they have marks for, in
        chronological order, with deviation from the previous exam."""
        exams = self.conn.execute(
            "SELECT DISTINCT e.* FROM exams e JOIN marks m ON m.exam_id = e.id "
            "WHERE m.student_adm_no=? ORDER BY e.year, e.term, e.id",
            (adm_no,),
        ).fetchall()
        trend = []
        prev_mean = None
        for exam in exams:
            marks = self.get_marks_for_student(adm_no, exam["id"])
            mean = round(sum(m["score"] for m in marks) / len(marks), 2) if marks else None
            deviation = round(mean - prev_mean, 2) if (mean is not None and prev_mean is not None) else None
            trend.append({"exam_id": exam["id"], "exam_name": exam["name"], "term": exam["term"],
                           "year": exam["year"], "mean": mean, "deviation": deviation})
            if mean is not None:
                prev_mean = mean
        return trend

    # ------------------------------------------------------------------
    # school-wide merit list (every form, every stream)
    # ------------------------------------------------------------------
    def school_merit_list(self, exam_id: int) -> List[Dict[str, Any]]:
        students = self.conn.execute("SELECT * FROM students ORDER BY last_name").fetchall()
        entries = []
        for s in students:
            marks = self.get_marks_for_student(s["adm_no"], exam_id)
            if not marks:
                continue
            total = sum(m["score"] for m in marks)
            mean = total / len(marks)
            entries.append({
                "adm_no": s["adm_no"], "name": f"{s['first_name']} {s['last_name']}",
                "class_code": f"{s['form_number']}{s['stream_code']}",
                "total": round(total, 2), "mean": round(mean, 2), "mean_grade": grade_for(mean),
            })
        entries.sort(key=lambda e: (-e["mean"], e["adm_no"]))
        for i, e in enumerate(entries, start=1):
            e["position"] = i
        return entries

    # ------------------------------------------------------------------
    # attendance
    # ------------------------------------------------------------------
    def record_attendance(self, staff_no: str, student_adm_no: str, date: str, status: str):
        student = self.conn.execute(
            "SELECT form_number, stream_code FROM students WHERE adm_no=?", (student_adm_no,)
        ).fetchone()
        if not student:
            raise NotFound(f"No such student '{student_adm_no}'.")
        if not self._can_manage_class(staff_no, student["form_number"], student["stream_code"]):
            raise PermissionDenied(
                f"Teacher {staff_no} is not the class teacher/supervisor of this student's class."
            )
        self.conn.execute(
            "INSERT INTO attendance(student_adm_no, date, status, recorded_by) VALUES (?,?,?,?) "
            "ON CONFLICT(student_adm_no, date) DO UPDATE SET status=excluded.status, recorded_by=excluded.recorded_by",
            (student_adm_no, date, status, staff_no),
        )
        self.conn.commit()

    def attendance_summary(self, student_adm_no: str) -> Dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS c FROM attendance WHERE student_adm_no=? GROUP BY status",
            (student_adm_no,),
        ).fetchall()
        summary = {"present": 0, "absent": 0, "late": 0}
        for r in rows:
            summary[r["status"]] = r["c"]
        return summary

    def _can_manage_class(self, staff_no: str, form_number: int, stream_code: str) -> bool:
        class_code = f"{form_number}{stream_code}"
        scope = self.teacher_scope(staff_no)
        return (class_code in scope["class_teacher_of"] or form_number in scope["supervises_forms"]
                or self.has_full_oversight(staff_no))

    # ------------------------------------------------------------------
    # timetable
    # ------------------------------------------------------------------
    def teacher_initials(self, staff_no: str) -> str:
        row = self.conn.execute("SELECT first_name, last_name FROM teachers WHERE staff_no=?", (staff_no,)).fetchone()
        if not row:
            return "?"
        return f"{row['first_name'][0]}{row['last_name'][0]}".upper()

    def add_timetable_entry(self, form_number: int, stream_name_or_code: str, day_of_week: str,
                             period: int, subject: str, staff_no: str, allow_conflict: bool = False):
        """Conflict-free scheduling: refuses to double-book a teacher (same
        day+period, different class) or double-book a class slot with two
        subjects, unless allow_conflict=True is explicitly passed."""
        stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
        code = subject_code(subject)
        self._require_teacher(staff_no)

        if not allow_conflict:
            teacher_clash = self.conn.execute(
                "SELECT form_number, stream_code FROM timetable WHERE staff_no=? AND day_of_week=? "
                "AND period=? AND NOT (form_number=? AND stream_code=?)",
                (staff_no, day_of_week, period, form_number, stream_code),
            ).fetchone()
            if teacher_clash:
                raise ValueError(
                    f"Scheduling conflict: {staff_no} is already teaching "
                    f"{teacher_clash['form_number']}{teacher_clash['stream_code']} on {day_of_week} "
                    f"period {period}. Pass allow_conflict=True to override."
                )

        self.conn.execute(
            "INSERT OR REPLACE INTO timetable(form_number, stream_code, day_of_week, period, "
            "subject_code, staff_no) VALUES (?,?,?,?,?,?)",
            (form_number, stream_code, day_of_week, period, code, staff_no),
        )
        self.conn.commit()

    def get_timetable(self, form_number: int, stream_name_or_code: str) -> List[sqlite3.Row]:
        stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
        return self.conn.execute(
            """SELECT tt.*, sub.name AS subject_name, t.first_name, t.last_name FROM timetable tt
               JOIN subjects sub ON sub.code = tt.subject_code
               JOIN teachers t ON t.staff_no = tt.staff_no
               WHERE tt.form_number=? AND tt.stream_code=?
               ORDER BY CASE day_of_week WHEN 'Mon' THEN 1 WHEN 'Tue' THEN 2 WHEN 'Wed' THEN 3
                    WHEN 'Thu' THEN 4 WHEN 'Fri' THEN 5 ELSE 6 END, period""",
            (form_number, stream_code),
        ).fetchall()

    def get_timetable_grid(self, form_number: int, stream_name_or_code: str,
                            days: Optional[List[str]] = None, periods: int = 8) -> Dict[str, Any]:
        """Same data as get_timetable(), reshaped into a day x period grid so the
        UI can render an actual timetable box layout, each cell carrying the
        subject name and the teacher's initials for the bottom of the box."""
        days = days or ["Mon", "Tue", "Wed", "Thu", "Fri"]
        rows = self.get_timetable(form_number, stream_name_or_code)
        grid = {d: {p: None for p in range(1, periods + 1)} for d in days}
        for r in rows:
            if r["day_of_week"] in grid and r["period"] in grid[r["day_of_week"]]:
                grid[r["day_of_week"]][r["period"]] = {
                    "subject": r["subject_name"],
                    "teacher_initials": self.teacher_initials(r["staff_no"]),
                    "teacher_name": f"{r['first_name']} {r['last_name']}",
                }
        return {"days": days, "periods": list(range(1, periods + 1)), "grid": grid}

    # ------------------------------------------------------------------
    # discipline
    # ------------------------------------------------------------------
    def add_discipline_record(self, staff_no: str, student_adm_no: str, date: str,
                               description: str, severity: str = "minor"):
        self.conn.execute(
            "INSERT INTO discipline_records(student_adm_no, date, description, severity, recorded_by) "
            "VALUES (?,?,?,?,?)",
            (student_adm_no, date, description, severity, staff_no),
        )
        self.conn.commit()
        self._audit("discipline", student_adm_no, "add", staff_no, description)

    def get_discipline_records(self, student_adm_no: str) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM discipline_records WHERE student_adm_no=? ORDER BY date DESC",
            (student_adm_no,),
        ).fetchall()

    # ------------------------------------------------------------------
    # fees
    # ------------------------------------------------------------------
    def add_fee_invoice(self, student_adm_no: str, term: int, year: int, amount_billed: float):
        self.conn.execute(
            "INSERT OR REPLACE INTO fee_invoices(student_adm_no, term, year, amount_billed) "
            "VALUES (?,?,?,?)",
            (student_adm_no, term, year, amount_billed),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # parents / guardians
    # ------------------------------------------------------------------
    def add_parent(self, first_name: str, last_name: str, email: str,
                    phone: Optional[str] = None, password: Optional[str] = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO parents(first_name, last_name, phone, email) VALUES (?,?,?,?)",
            (first_name.strip(), last_name.strip(), phone, email.strip().lower()),
        )
        self.conn.commit()
        parent_id = cur.lastrowid
        if password:
            self.set_parent_password(parent_id, password)
        return parent_id

    def link_parent_to_student(self, parent_id: int, student_adm_no: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO parent_students(parent_id, student_adm_no) VALUES (?,?)",
            (parent_id, student_adm_no),
        )
        self.conn.commit()

    def set_parent_password(self, parent_id: int, password: str):
        salt = secrets.token_hex(16)
        pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000).hex()
        self.conn.execute(
            "UPDATE parents SET password_hash=?, password_salt=? WHERE id=?",
            (pw_hash, salt, parent_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # forgot password (token-based reset, works for teacher AND parent accounts)
    # ------------------------------------------------------------------
    def request_password_reset(self, account_type: str, identifier: str) -> str:
        """Generates a one-time reset token (valid 30 minutes) and queues a
        notification containing it. Returns the raw token.

        NOTE: in a real deployment the raw token would only ever be emailed/
        texted to the account holder via a real provider -- it would NOT be
        returned to the caller. It's returned here purely because no real
        email/SMS provider is wired in yet (see queue_notification)."""
        if account_type == "teacher":
            staff_no = self._resolve_login_identifier(identifier)
            if staff_no is None:
                raise NotFound("No teacher account matches that staff number or email.")
            account_id = staff_no
            row = self.conn.execute("SELECT email FROM teachers WHERE staff_no=?", (staff_no,)).fetchone()
            contact_ok = bool(row and row["email"])
        elif account_type == "parent":
            row = self.conn.execute(
                "SELECT id FROM parents WHERE email=?", (identifier.strip().lower(),)
            ).fetchone()
            if row is None:
                raise NotFound("No parent account matches that email.")
            account_id = str(row["id"])
            contact_ok = True
        else:
            raise ValueError("account_type must be 'teacher' or 'parent'.")

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO password_resets(account_type, account_id, token_hash, expires_at, created_at) "
            "VALUES (?,?,?,?,?)",
            (account_type, account_id, token_hash, expires_at, _now()),
        )
        self.conn.commit()
        if contact_ok:
            self.queue_notification(
                account_type, account_id, "email",
                f"Password reset requested. Your reset token (valid 30 min): {token}",
            )
        self._audit(account_type, account_id, "password_reset_requested", "self-service", "")
        return token

    def reset_password_with_token(self, token: str, new_password: str):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = self.conn.execute(
            "SELECT * FROM password_resets WHERE token_hash=? AND used=0 ORDER BY id DESC LIMIT 1",
            (token_hash,),
        ).fetchone()
        if row is None:
            raise AuthError("Invalid or already-used reset token.")
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            raise AuthError("This reset token has expired. Please request a new one.")

        if row["account_type"] == "teacher":
            self.set_password(row["account_id"], new_password, force_reset=False)
        else:
            self.set_parent_password(int(row["account_id"]), new_password)

        self.conn.execute("UPDATE password_resets SET used=1 WHERE id=?", (row["id"],))
        self.conn.commit()
        self._audit(row["account_type"], row["account_id"], "password_reset_completed", "self-service", "")

    def parent_login(self, email: str, password: str) -> int:
        row = self.conn.execute("SELECT * FROM parents WHERE email=?", (email.strip().lower(),)).fetchone()
        if row is None or not row["password_hash"]:
            raise AuthError("Invalid email or password.")
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(row["password_salt"]), 100_000
        ).hex()
        if not secrets.compare_digest(candidate, row["password_hash"]):
            raise AuthError("Invalid email or password.")
        return row["id"]

    def parent_children(self, parent_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            """SELECT s.* FROM parent_students ps JOIN students s ON s.adm_no = ps.student_adm_no
               WHERE ps.parent_id=?""",
            (parent_id,),
        ).fetchall()

    def parent_can_view(self, parent_id: int, student_adm_no: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM parent_students WHERE parent_id=? AND student_adm_no=?",
            (parent_id, student_adm_no),
        ).fetchone() is not None

    def parent_view_report(self, parent_id: int, student_adm_no: str, exam_id: int) -> Dict[str, Any]:
        """A parent may only see PUBLISHED results for their own linked child."""
        if not self.parent_can_view(parent_id, student_adm_no):
            raise PermissionDenied("This parent is not linked to that student.")
        if not self.is_exam_published(exam_id):
            raise PermissionDenied("Results for this exam have not been published yet.")
        return self.build_report_data(student_adm_no, exam_id)

    # ------------------------------------------------------------------
    # notifications (stub -- queues a message, does not actually dispatch;
    # wire in a real SMS/email provider here in production)
    # ------------------------------------------------------------------
    def queue_notification(self, recipient_type: str, recipient_id: str, channel: str, message: str):
        self.conn.execute(
            "INSERT INTO notifications(recipient_type, recipient_id, channel, message, status, created_at) "
            "VALUES (?,?,?,?,'queued',?)",
            (recipient_type, recipient_id, channel, message, _now()),
        )
        self.conn.commit()

    def get_notifications(self, recipient_id: str) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM notifications WHERE recipient_id=? ORDER BY created_at DESC", (recipient_id,)
        ).fetchall()

    # ------------------------------------------------------------------
    # class lists & form performance
    # ------------------------------------------------------------------
    def form_performance(self, form_number: int, exam_id: int) -> Dict[str, Any]:
        """Per-stream performance within a form: each stream's mean score and its
        rank against the other streams in the same form, for one exam."""
        streams = self.conn.execute(
            "SELECT code, name FROM streams WHERE form_number=? ORDER BY code", (form_number,)
        ).fetchall()

        stream_stats = []
        for st in streams:
            students = self.conn.execute(
                "SELECT adm_no FROM students WHERE form_number=? AND stream_code=?",
                (form_number, st["code"]),
            ).fetchall()
            student_means = []
            for s in students:
                marks = self.get_marks_for_student(s["adm_no"], exam_id)
                if marks:
                    student_means.append(sum(m["score"] for m in marks) / len(marks))
            stream_mean = round(sum(student_means) / len(student_means), 2) if student_means else None
            stream_stats.append({
                "class_code": f"{form_number}{st['code']}",
                "stream_name": st["name"],
                "num_students": len(students),
                "mean_score": stream_mean,
            })

        ranked = sorted([s for s in stream_stats if s["mean_score"] is not None],
                         key=lambda x: -x["mean_score"])
        rank_by_class = {s["class_code"]: i + 1 for i, s in enumerate(ranked)}
        for s in stream_stats:
            s["rank"] = rank_by_class.get(s["class_code"])

        scored = [s["mean_score"] for s in stream_stats if s["mean_score"] is not None]
        form_mean = round(sum(scored) / len(scored), 2) if scored else None

        return {"form_number": form_number, "form_mean": form_mean,
                "num_streams_ranked": len(ranked), "streams": stream_stats}

    def form_ranking_with_teachers(self, form_number: int, exam_id: int) -> Dict[str, Any]:
        """Every stream in a form, ranked by performance, with its class teacher's
        name right beside it -- and the Class Principal/Supervisor at the bottom,
        since they oversee the whole form rather than one stream."""
        perf = self.form_performance(form_number, exam_id)
        for s in perf["streams"]:
            stream_code = s["class_code"][len(str(form_number)):]
            row = self.conn.execute(
                "SELECT class_teacher_staff_no FROM streams WHERE form_number=? AND code=?",
                (form_number, stream_code),
            ).fetchone()
            ct_name = "Unassigned"
            if row and row["class_teacher_staff_no"]:
                ct = self.conn.execute(
                    "SELECT first_name, last_name FROM teachers WHERE staff_no=?",
                    (row["class_teacher_staff_no"],),
                ).fetchone()
                if ct:
                    ct_name = f"{ct['first_name']} {ct['last_name']}"
            s["class_teacher"] = ct_name

        # Ranked streams first (best to worst), unranked (no marks yet) after
        perf["streams"].sort(key=lambda s: (s["rank"] is None, s["rank"] if s["rank"] is not None else 0))

        sup = self.conn.execute(
            """SELECT t.* FROM form_supervisors fs JOIN teachers t ON t.staff_no = fs.staff_no
               WHERE fs.form_number=?""",
            (form_number,),
        ).fetchone()
        perf["supervisor_name"] = f"{sup['first_name']} {sup['last_name']}" if sup else "Unassigned"
        perf["supervisor_class"] = f"Form {form_number}"
        return perf

    def class_list_data(self, form_number: int, stream_name_or_code: str,
                         exam_id: Optional[int] = None) -> Dict[str, Any]:
        """Everything needed to print a class list: roster, class teacher, class
        supervisor, and (if an exam is given) this class's standing within its form."""
        stream_code = self._resolve_stream_code(form_number, stream_name_or_code)
        stream_row = self.conn.execute(
            "SELECT * FROM streams WHERE form_number=? AND code=?", (form_number, stream_code)
        ).fetchone()

        class_teacher = None
        if stream_row["class_teacher_staff_no"]:
            class_teacher = self.conn.execute(
                "SELECT * FROM teachers WHERE staff_no=?", (stream_row["class_teacher_staff_no"],)
            ).fetchone()

        supervisor = self.conn.execute(
            """SELECT t.* FROM form_supervisors fs JOIN teachers t ON t.staff_no = fs.staff_no
               WHERE fs.form_number=?""",
            (form_number,),
        ).fetchone()

        students = self.conn.execute(
            "SELECT * FROM students WHERE form_number=? AND stream_code=? ORDER BY last_name",
            (form_number, stream_code),
        ).fetchall()

        roster = []
        for s in students:
            entry = {"adm_no": s["adm_no"], "name": f"{s['first_name']} {s['last_name']}"}
            if exam_id is not None:
                marks = self.get_marks_for_student(s["adm_no"], exam_id)
                entry["mean_score"] = round(sum(m["score"] for m in marks) / len(marks), 2) if marks else None
            roster.append(entry)

        result = {
            "class_code": f"{form_number}{stream_code}",
            "stream_name": stream_row["name"],
            "form_number": form_number,
            "class_teacher": f"{class_teacher['first_name']} {class_teacher['last_name']}" if class_teacher else "Unassigned",
            "supervisor": f"{supervisor['first_name']} {supervisor['last_name']}" if supervisor else "Unassigned",
            "students": roster,
        }
        if exam_id is not None:
            result["form_performance"] = self.form_performance(form_number, exam_id)
        return result
