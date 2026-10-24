"""
app.py
The full web app on top of the school system. Every section (students,
staff, departments, marks, attendance, discipline, fees, exams, alumni,
promotion, reports) is a real page with real buttons wired to the actual
database -- nothing here is a placeholder.

Permission enforcement happens server-side (in db.py / auth.py): a button
is shown to everyone, but the action itself will be rejected with a flashed
error if the logged-in account isn't authorized. The 'admin' account
(see create_admin.py) has full oversight and can do everything.

Run with:
    python3 app.py
Then open http://localhost:5000
"""

import os
from datetime import timedelta
from html import escape as esc
import tempfile
from flask import Flask, request, session, redirect, url_for, render_template_string, send_file, flash

from db import SchoolDB, PermissionDenied, AuthError, NotFound
from auth import login as db_login, TeacherSession

app = Flask(__name__)
app.secret_key = os.environ.get("SCHOOL_APP_SECRET", "dev-secret-change-me")
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_COOKIE_HTTPONLY=True,          # page scripts can't read the session cookie
    SESSION_COOKIE_SAMESITE="Lax",         # not sent on cross-site POSTs
    SESSION_COOKIE_SECURE=os.environ.get("SCHOOL_APP_HTTPS") == "1",  # set to 1 once behind HTTPS
)

# Where the data lives. Locally these default to the project folder; on a host with a
# persistent disk (e.g. Render) point them at the disk with SCHOOL_DB_PATH / SCHOOL_UPLOAD_DIR.
DB_PATH = os.environ.get("SCHOOL_DB_PATH", "school.db")
UPLOAD_DIR = os.environ.get("SCHOOL_UPLOAD_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "uploads", "receipts")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)


def get_db():
    return SchoolDB(DB_PATH)


# ----------------------------------------------------------------------
# layout helper -- every page shares the same nav + flash rendering
# ----------------------------------------------------------------------
STYLE = """
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@500&display=swap');

  :root {
    --ink: #1B2333;
    --ink-muted: #6B7686;
    --navy: #1F2A44;
    --navy-2: #2B3A55;
    --gold: #C9A227;
    --gold-soft: #F5EBC7;
    --paper: #F4F6F9;
    --card: #FFFFFF;
    --border: #E2E6ED;
    --danger: #B3261E;
    --danger-soft: #FBEAEA;
    --radius: 10px;
  }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }

  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--paper); color: var(--ink);
    font-family: 'Inter', system-ui, sans-serif; font-size: 14px; line-height: 1.5;
  }
  h1, h2, h3, h4 { font-family: 'Space Grotesk', sans-serif; color: var(--navy); margin: 0 0 12px; }
  h2 { font-size: 22px; font-weight: 700; letter-spacing: -0.01em; }
  h3 { font-size: 15px; font-weight: 600; color: var(--navy-2); margin-top: 28px; }
  a { color: var(--navy-2); }
  code, .mono { font-family: 'IBM Plex Mono', monospace; }

  /* ---- shell layout ---- */
  .app-shell { display: flex; min-height: 100vh; }
  .sidebar {
    width: 232px; flex-shrink: 0; background: var(--navy);
    padding: 20px 0; position: sticky; top: 0; height: 100vh; overflow-y: auto;
  }
  .brand {
    font-family: 'Space Grotesk', sans-serif; color: #fff; font-weight: 700;
    font-size: 15px; padding: 0 20px 18px; border-bottom: 1px solid rgba(255,255,255,0.12);
    margin-bottom: 10px; letter-spacing: -0.01em;
  }
  .brand small { display: block; color: #9BA8C4; font-weight: 500; font-size: 11px; margin-top: 2px; }
  .navtab {
    display: flex; align-items: center; gap: 10px; padding: 9px 20px;
    color: #C7D0E3; text-decoration: none; font-size: 13px; font-weight: 500;
    border-left: 3px solid transparent; transition: all .15s ease;
  }
  .navtab:hover { background: rgba(255,255,255,0.06); color: #fff; }
  .navtab.active { background: rgba(201,162,39,0.14); border-left-color: var(--gold); color: #fff; }
  .navtab .tag {
    font-family: 'IBM Plex Mono', monospace; font-size: 9px; font-weight: 500;
    width: 22px; height: 22px; border-radius: 5px; display: flex; align-items: center;
    justify-content: center; background: rgba(255,255,255,0.08); flex-shrink: 0;
  }
  .navtab.active .tag { background: var(--gold); color: var(--navy); }
  .sidebar .logout-tab { margin-top: 14px; border-top: 1px solid rgba(255,255,255,0.12); padding-top: 14px; }

  .main { flex: 1; min-width: 0; }
  .topbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 16px 32px; background: var(--card); border-bottom: 1px solid var(--border);
  }
  .topbar h2 { margin: 0; }
  .who { display: flex; align-items: center; gap: 10px; font-size: 12px; color: var(--ink-muted); }
  .badge {
    font-family: 'IBM Plex Mono', monospace; font-size: 10px; font-weight: 500;
    padding: 4px 9px; border-radius: 20px; background: var(--navy-2); color: #fff;
  }
  .badge.oversight { background: var(--gold); color: var(--navy); }
  .content { padding: 24px 32px 60px; max-width: 1080px; }

  /* ---- flash ---- */
  .flash {
    background: var(--danger-soft); color: var(--danger); border: 1px solid #F0C4C0;
    border-radius: var(--radius); padding: 10px 16px; font-size: 13px; font-weight: 500;
    margin-bottom: 18px;
  }

  /* ---- forms as cards ---- */
  form {
    background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 16px 18px; margin-bottom: 16px; display: flex; flex-wrap: wrap;
    align-items: center; gap: 10px 14px;
  }
  form input[type=text], form input[type=password], form input:not([type]),
  form select, form textarea {
    font-family: 'Inter', sans-serif; font-size: 13px; padding: 7px 10px;
    border: 1px solid var(--border); border-radius: 7px; background: #fff; color: var(--ink);
  }
  form input:focus, form select:focus, form textarea:focus {
    outline: 2px solid var(--gold); outline-offset: 1px; border-color: var(--gold);
  }
  form button, button {
    font-family: 'Inter', sans-serif; font-size: 13px; font-weight: 600;
    background: var(--navy); color: #fff; border: none; padding: 8px 16px;
    border-radius: 7px; cursor: pointer; transition: background .15s ease;
  }
  form button:hover, button:hover { background: var(--navy-2); }

  /* ---- tables ---- */
  table {
    width: 100%; border-collapse: collapse; background: var(--card);
    border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden;
    margin-bottom: 18px; display: block; overflow-x: auto; white-space: nowrap;
  }
  table thead, table tr { display: table; width: 100%; table-layout: fixed; }
  th {
    background: var(--navy-2); color: #fff; font-family: 'Space Grotesk', sans-serif;
    font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;
    text-align: left; padding: 10px 12px;
  }
  td { padding: 9px 12px; border-top: 1px solid var(--border); font-size: 13px; }
  tr:nth-child(even) td { background: #FAFBFD; }

  /* ---- auth screens: clean light layout ---- */
  .auth-wrap { min-height: 100vh; display: flex; align-items: flex-start; justify-content: center; background: #fff; padding-top: 6vh; }
  .auth-card { background: transparent; padding: 0 18px 40px; width: 500px; max-width: 94vw; box-shadow: none; font-family: 'Inter', sans-serif; }
  .auth-card .brand-row { display: flex; align-items: center; justify-content: center; gap: 12px; margin-bottom: 46px;
    font-weight: 700; font-size: 30px; color: #111827; letter-spacing: -0.01em; }
  .auth-card .brand-mark { width: 50px; height: 50px; border-radius: 12px; background: var(--gold); display: flex;
    align-items: center; justify-content: center; font-weight: 800; color: var(--navy); font-size: 18px; margin: 0; }
  .auth-card h2 { text-align: center; font-size: 42px; font-weight: 800; color: #150a52; margin: 0 0 6px; letter-spacing: -0.02em; }
  .auth-card p.sub { text-align: center; color: #4b5563; font-size: 19px; margin: 0 0 40px; }
  .auth-card form { flex-direction: column; align-items: stretch; border: none; padding: 0; background: transparent; }
  .auth-card label.fl { display: block; font-weight: 700; font-size: 17px; margin: 22px 0 9px; color: #374151; }
  .auth-card .req { color: #e11d48; }
  .auth-card form input[type=text], .auth-card form input[type=password], .auth-card form input[type=email],
  .auth-card form input:not([type]) { width: 100%; height: 54px; padding: 0 16px; border-radius: 10px;
    border: 1px solid #d1d5db; font-size: 17px; font-weight: 500; color: #111827; background: #fff;
    box-shadow: 0 1px 2px rgba(16,24,40,0.05); }
  .auth-card form input::placeholder { color: #6b7280; font-weight: 600; }
  .auth-card form input:focus { outline: none; border-color: #15b04a; box-shadow: 0 0 0 3px rgba(21,176,74,0.18); }
  .auth-card .pw-wrap { position: relative; }
  .auth-card .pw-wrap input { padding-right: 170px !important; }
  .auth-card .pw-toggle { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); width: auto !important;
    background: none !important; color: #6b7280 !important; padding: 4px 6px !important; font-size: 15px; font-weight: 600;
    border: none; cursor: pointer; display: flex; align-items: center; gap: 8px; }
  .auth-card .row-opts { display: flex; justify-content: space-between; align-items: center; margin-top: 24px; font-size: 17px; }
  .auth-card .row-opts label { display: flex; align-items: center; gap: 14px; font-weight: 700; color: #374151; cursor: pointer; }
  .auth-card .row-opts input[type=checkbox] { width: 22px; height: 22px; accent-color: #14b8a6; margin: 0; }
  .auth-card a { color: #0a58ca; font-weight: 700; text-decoration: none; }
  .auth-card a:hover { text-decoration: underline; }
  .auth-card form button.btn-green { width: 100%; height: 56px; background: #15b04a !important; border-radius: 999px !important;
    font-size: 19px !important; font-weight: 700; margin-top: 28px; padding: 0 !important; }
  .auth-card form button.btn-green:hover { background: #12993f !important; }
  .auth-card .divider { display: flex; align-items: center; gap: 18px; margin: 34px 0 22px; color: #6b7280;
    font-weight: 600; font-size: 17px; }
  .auth-card .divider::before, .auth-card .divider::after { content: ""; flex: 1; height: 1px; background: #e5e7eb; }
  .auth-card .alt-pill { display: flex; align-items: center; justify-content: center; gap: 12px; width: fit-content;
    margin: 0 auto; background: #e8f7ee; color: #1f2937 !important; border-radius: 999px; padding: 14px 26px; font-size: 17px; }
  .auth-card .alt-pill:hover { text-decoration: none; background: #d9f1e2; }
  .auth-card .alt-pill svg { flex: none; }
  .auth-card .foot { text-align: center; margin-top: 30px; font-size: 16px; color: #4b5563; }
  .auth-card .foot a { margin-left: 4px; }
  .auth-card .links { margin-top: 22px; font-size: 15px; display: flex; flex-direction: column; gap: 10px; align-items: center; }

  /* ---- app-launcher layout: no long sidebar, a slim top bar + tile grid ---- */
  .app-shell { display: block; }
  .sidebar { display: none; }
  .main { width: 100%; }
  .topbar.launcher { gap: 16px; }
  .topbar.launcher .left { display: flex; align-items: center; gap: 14px; }
  .brand-chip { display: flex; align-items: center; gap: 8px; text-decoration: none; color: var(--navy);
    font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 15px; }
  .brand-chip .mark { width: 30px; height: 30px; border-radius: 8px; background: var(--gold); color: var(--navy);
    display: flex; align-items: center; justify-content: center; font-size: 12px; }
  .apps-btn { display: inline-flex; align-items: center; gap: 6px; background: var(--paper); color: var(--navy);
    border: 1px solid var(--border); border-radius: 8px; padding: 6px 12px; font-size: 13px; font-weight: 600;
    text-decoration: none; }
  .apps-btn:hover { border-color: var(--gold); }
  .topbar.launcher h2 { font-size: 17px; }
  .content { margin: 0 auto; max-width: 1180px; }
  .app-group { font-family: 'Space Grotesk', sans-serif; font-size: 12px; font-weight: 600; letter-spacing: .06em;
    text-transform: uppercase; color: var(--ink-muted); margin: 26px 0 10px; }
  .app-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 14px; }
  .app-tile { position: relative; display: flex; flex-direction: column; gap: 6px; background: var(--card);
    border: 1px solid var(--border); border-radius: 16px; padding: 18px; text-decoration: none; color: var(--ink);
    transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease; }
  .app-tile:hover { transform: translateY(-3px); box-shadow: 0 10px 24px rgba(31,42,68,.12); border-color: var(--gold); }
  .app-icon { width: 52px; height: 52px; border-radius: 14px; display: flex; align-items: center;
    justify-content: center; font-size: 26px; margin-bottom: 6px; }
  .app-name { font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 15px; color: var(--navy); }
  .app-desc { font-size: 12px; color: var(--ink-muted); line-height: 1.4; }
  .tabs { display:flex; flex-wrap:wrap; gap:4px; margin:0 0 18px; border-bottom:1px solid var(--border); }
  .tabs a { padding:9px 16px; text-decoration:none; color:var(--ink-muted); font-weight:600; font-size:13px;
    border-bottom:2px solid transparent; margin-bottom:-1px; }
  .tabs a:hover { color:var(--navy); }
  .tabs a.active { color:var(--navy); border-bottom-color:var(--gold); }
  .hint { font-size:12px; color:var(--ink-muted); margin:0 0 12px; }
  .chip-row { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px; }
  .chip { display:inline-flex; gap:6px; align-items:center; background:var(--card); border:1px solid var(--border);
    border-radius:20px; padding:5px 12px; font-size:13px; }
  .split { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:14px; }
  .split form { margin:0; }
  .toggle-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:12px; }
  .toggle-card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:14px;
    display:flex; flex-direction:column; gap:8px; }
  .toggle-card .state { font-size:12px; font-weight:600; }
  .toggle-card form { margin:0; padding:0; border:none; background:transparent; }
  .grid-wrap { max-height:64vh; overflow:auto; border:1px solid var(--border); border-radius:10px; background:var(--card); }
  .grid-wrap table, .grid-wrap thead, .grid-wrap tbody, .grid-wrap tr { display:revert; width:auto; table-layout:auto; }
  .grid-wrap table { width:100%; margin:0; border:none; white-space:nowrap; }
  .grid-wrap th { position:sticky; top:0; z-index:2; }
  .grid-wrap td { padding:3px 6px; }
  .grid-wrap td.wrap { white-space:normal; min-width:110px; max-width:230px; font-size:12px; }
  .grid-wrap form { display:inline-flex; padding:0; border:none; background:transparent; margin:0 4px 0 0; }
  .grid-form { display:block !important; padding:0 !important; border:none !important;
    background:transparent !important; margin:0 0 14px !important; }
  .grid-wrap input.cell, .grid-wrap select.cell { width:100%; min-width:120px; border:1px solid transparent;
    background:transparent; padding:5px 7px; border-radius:6px; font:inherit; }
  .grid-wrap input.cell:hover, .grid-wrap select.cell:hover { border-color:var(--border); background:#fff; }
  .grid-wrap input.cell:focus, .grid-wrap select.cell:focus { border-color:var(--gold); background:#fff; outline:none; }
  .grid-wrap input.narrow { min-width:0; width:46px; text-align:center; }
  .grid-actions { margin-top:10px; }
  .btn-danger { background:var(--danger) !important; padding:5px 10px !important; font-size:12px !important; }
  .app-badge { position: absolute; top: 12px; right: 12px; background: var(--danger); color: #fff; font-size: 11px;
    font-weight: 700; border-radius: 20px; padding: 2px 8px; }
</style>
"""

NAV_ITEMS = [
    ("dashboard", "Dashboard", "DB", "all"),
    ("students_view", "Students", "ST", "all"),
    ("staff_view", "Staff", "SF", "staffing"),
    ("departments_view", "Departments", "DP", "hod"),
    ("marks_view", "Marks", "MK", "all"),
    ("attendance_view", "Attendance", "AT", "all"),
    ("timetable_view", "Timetable", "TT", "all"),
    ("calendar_view", "Calendar", "CA", "all"),
    ("fees_view", "Fees", "FE", "oversight"),
    ("exams_view", "Exams", "EX", "all"),
    ("analytics_view", "Analytics", "AN", "all"),
    ("messages_view", "Messages", "MS", "all"),
    ("alumni_view", "Alumni", "AL", "oversight"),
    ("academic_setup_view", "Academic Setup", "AS", "oversight"),
    ("notifications_view", "Notifications", "NO", "all"),
    ("audit_log_view", "Audit Log", "AU", "oversight"),
    ("promotion_view", "Promotion", "PR", "oversight"),
    ("reports_view", "Reports", "RP", "all"),
]

_NAV_VISIBILITY_VAR = {"all": None, "oversight": "oversight_flag", "staffing": "staffing_flag", "hod": "hod_flag"}

NAV = """
<div class="brand">EduLink<small>School Administration System</small></div>
""" + "".join(
    (
        f'{{% if {_NAV_VISIBILITY_VAR[visibility]} %}}'
        f'<a class="navtab{{% if request.endpoint == \'{endpoint}\' %}} active{{% endif %}}" '
        f'href="{{{{ url_for(\'{endpoint}\') }}}}"><span class="tag">{tag}</span>{label}</a>'
        f'{{% endif %}}'
        if visibility != "all" else
        f'<a class="navtab{{% if request.endpoint == \'{endpoint}\' %}} active{{% endif %}}" '
        f'href="{{{{ url_for(\'{endpoint}\') }}}}"><span class="tag">{tag}</span>{label}</a>'
    )
    for endpoint, label, tag, visibility in NAV_ITEMS
) + """
<div class="logout-tab">
  <a class="navtab" href="{{ url_for('logout') }}"><span class="tag">&larr;</span>Logout</a>
</div>
"""


def page(title, body, nav=True):
    if not nav:
        # auth screens (login, forgot password, change password) get the centered card treatment
        template = f"""<!doctype html><html><head><title>EduLink &mdash; {title}</title>{STYLE}</head>
        <body><div class="auth-wrap"><div class="auth-card">
        <div class="brand-row"><div class="brand-mark">EL</div>EduLink</div>
        <h2>{title}</h2>
        <p class="sub">All school services</p>
        {{% with messages = get_flashed_messages() %}}
          {{% if messages %}}<p class="flash">{{{{ messages[0] }}}}</p>{{% endif %}}
        {{% endwith %}}
        {body}
        </div></div></body></html>"""
        return render_template_string(template)

    oversight_flag = False
    staffing_flag = False
    hod_flag = False
    if "staff_no" in session:
        db = get_db()
        try:
            oversight_flag = db.has_full_oversight(session["staff_no"])
            staffing_flag = oversight_flag or bool(db.staffing_manager_forms(session["staff_no"]))
            hod_flag = oversight_flag or any(
                r["role"] == "hod" for r in db.get_roles(session["staff_no"])
            )
        finally:
            db.close()

    template = f"""<!doctype html><html><head><title>EduLink &mdash; {title}</title>{STYLE}</head>
    <body><div class="app-shell">
      <main class="main">
        <div class="topbar launcher">
          <div class="left">
            <a class="brand-chip" href="{{{{ url_for('dashboard') }}}}"><span class="mark">EL</span>EduLink</a>
            <a class="apps-btn" href="{{{{ url_for('dashboard') }}}}">&#9638; Apps</a>
            <h2>{title}</h2>
          </div>
          <div class="who">
            <span class="mono">{{{{ session.get('staff_no','') }}}}</span>
            {{% if oversight_flag %}}
              <span class="badge oversight">FULL OVERSIGHT</span>
            {{% else %}}
              <span class="badge">STAFF</span>
            {{% endif %}}
            <a class="apps-btn" href="{{{{ url_for('logout') }}}}">Logout</a>
          </div>
        </div>
        <div class="content">
          {{% with messages = get_flashed_messages() %}}
            {{% if messages %}}<p class="flash">{{{{ messages[0] }}}}</p>{{% endif %}}
          {{% endwith %}}
          {body}
        </div>
      </main>
    </div></body></html>"""
    return render_template_string(template, oversight_flag=oversight_flag,
                                   staffing_flag=staffing_flag, hod_flag=hod_flag)


# ----------------------------------------------------------------------
# auth
# ----------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    return redirect(url_for("dashboard") if "staff_no" in session else url_for("login_view"))


@app.route("/login", methods=["GET", "POST"])
def login_view():
    if request.method == "POST":
        identifier = request.form["identifier"].strip()
        password = request.form["password"]
        db = get_db()
        try:
            LOCK_MSG = "Error, please try again later"
            # Locked accounts get the same generic error whether or not the password is right,
            # so the lock never reveals that a password was correct.
            target = db._resolve_login_identifier(identifier)
            if target and db.is_account_locked(target) and not db.has_full_oversight(target):
                flash(LOCK_MSG)
                return redirect(url_for("login_view"))
            sess = db_login(db, identifier, password)
            if db.is_system_locked() and not db.has_full_oversight(sess.staff_no):
                flash(LOCK_MSG)
                return redirect(url_for("login_view"))
            session["staff_no"] = sess.staff_no
            session.permanent = bool(request.form.get("remember"))
            if sess.must_reset_password:
                session["must_reset_password"] = True
                return redirect(url_for("change_password_view"))
            return redirect(url_for("dashboard"))
        except AuthError as e:
            flash(str(e))
        finally:
            db.close()
    body = """
    <form method="post">
      <label class="fl">Staff number or email address <span class="req">*</span></label>
      <input name="identifier" placeholder="Enter your staff number or email" autocomplete="username" required
             value="{{ request.form.get('identifier','') }}">
      <label class="fl">Password <span class="req">*</span></label>
      <div class="pw-wrap">
        <input id="pw" name="password" type="password" placeholder="Password" autocomplete="current-password" required>
        <button type="button" class="pw-toggle" onclick="var i=document.getElementById('pw');var s=i.type==='password';i.type=s?'text':'password';this.firstChild.textContent=s?'Hide Password':'Show Password'"><span>Show Password</span><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#4b5563" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg></button>
      </div>
      <div class="row-opts">
        <label><input type="checkbox" name="remember" value="1"> Remember for 30 days</label>
        <a href="{{ url_for('forgot_password_view') }}">Forgot Password</a>
      </div>
      <button type="submit" class="btn-green">Sign In</button>
    </form>
    <div class="divider">Or continue with</div>
    <a class="alt-pill" href="{{ url_for('parent_login_view') }}"><svg width="34" height="34" viewBox="0 0 34 34"><circle cx="17" cy="17" r="17" fill="#15b04a"/><circle cx="17" cy="13" r="4.6" fill="#fff"/><path d="M8.5 25.5c1.2-4.3 4.8-6.3 8.5-6.3s7.3 2 8.5 6.3" fill="#fff"/></svg><b>Sign in to the Parent portal</b></a>
    <div class="links"><a href="{{ url_for('reset_password_view') }}">Have a one-time code (OTP)? Enter it here</a></div>
    <div class="foot">Need an account? Ask your school administrator.</div>
    """
    return page("Login", body, nav=False)


@app.route("/change-password", methods=["GET", "POST"])
def change_password_view():
    if "staff_no" not in session:
        return redirect(url_for("login_view"))
    if request.method == "POST":
        db = get_db()
        try:
            db.set_password(session["staff_no"], request.form["new_password"], force_reset=False)
            session.pop("must_reset_password", None)
            return redirect(url_for("dashboard"))
        finally:
            db.close()
    body = """
    <p>You must set a new password before continuing.</p>
    <form method="post">
      New password: <input name="new_password" type="password"><br><br>
      <button type="submit">Set password</button>
    </form>
    """
    return page("Change Password", body, nav=False)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password_view():
    if request.method == "POST":
        db = get_db()
        try:
            db.request_password_reset("teacher", request.form["identifier"].strip())
        except Exception:
            pass
        finally:
            db.close()
        flash("This system sends no email or SMS. Ask an administrator for a one-time code, "
              "then enter it on the reset page.")
    body = """
    <p style="font-size:13px;color:var(--ink-muted)">This system runs offline and does not send
    email or SMS. Ask an administrator to generate a one-time code (OTP) for you, then use the
    link below to enter it.</p>
    <form method="post">
      Staff No or Email: <input name="identifier"><br><br>
      <button type="submit">Request a code</button>
    </form>
    <p><a href="{{ url_for('reset_password_view') }}">I already have a code &rarr; reset my password</a></p>
    <p><a href="{{ url_for('login_view') }}">Back to login</a></p>
    """
    return page("Forgot Password", body, nav=False)


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password_view():
    if request.method == "POST":
        db = get_db()
        try:
            db.reset_password_with_token(request.form["token"], request.form["new_password"])
            flash("Password reset. You can log in with your new password now.")
            return redirect(url_for("login_view"))
        except AuthError as e:
            flash(str(e))
        finally:
            db.close()
    body = """
    <form method="post">
      One-time code (OTP): <input name="token" size="20" autocomplete="off" value="{{ request.args.get('token','') }}"><br><br>
      New password: <input name="new_password" type="password"><br><br>
      <button type="submit">Reset password</button>
    </form>
    <p style="font-size:12px;color:var(--ink-muted)">The code is 6 digits, works once, and expires after 15 minutes.</p>
    <p><a href="{{ url_for('login_view') }}">Back to login</a></p>
    """
    return page("Reset Password", body, nav=False)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_view"))


def _require_login():
    if "staff_no" not in session or session.get("must_reset_password"):
        return None
    return get_db(), session["staff_no"]


def _guard():
    return _require_login()


_ENDPOINT_TO_FEATURE_NAME = {endpoint: endpoint.replace("_view", "") for endpoint, _l, _t, _v in NAV_ITEMS
                             if endpoint.replace("_view", "") in SchoolDB.TOGGLEABLE_FEATURES}


@app.before_request
def _enforce_system_controls():
    """Centrally enforces: (1) a disabled component is unreachable by anyone
    non-executive even via a direct URL, not just hidden from nav, and (2) an
    existing session for a now-locked account or a now-locked system is cut
    off on its very next request, not just at the next login."""
    endpoint = request.endpoint
    if endpoint in ("login_view", "logout", "forgot_password_view", "reset_password_view",
                     "change_password_view", "parent_login_view", "forgot_password_view_parent",
                     "parent_logout", "parent_dashboard", "parent_child_view", "static"):
        return None
    if "staff_no" not in session:
        return None

    db = get_db()
    try:
        staff_no = session["staff_no"]
        oversight = db.has_full_oversight(staff_no)

        if not oversight and (db.is_account_locked(staff_no) or db.is_system_locked()):
            session.clear()
            flash("Error, please try again later")
            return redirect(url_for("login_view"))

        feature_name = _ENDPOINT_TO_FEATURE_NAME.get(endpoint)
        if feature_name and not oversight and not db.is_feature_enabled(feature_name):
            flash(f"This component has been temporarily disabled by an administrator.")
            return redirect(url_for("dashboard"))
    finally:
        db.close()
    return None


def stat_cards(data: dict) -> str:
    """Renders a flat dict of label->value as a row of small stat cards
    instead of a raw Python dict dump."""
    cards = "".join(
        f'<div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);'
        f'padding:12px 16px;min-width:110px">'
        f'<div style="font-size:11px;color:var(--ink-muted);text-transform:uppercase;letter-spacing:.04em">'
        f'{str(k).replace("_"," ")}</div>'
        f'<div class="mono" style="font-size:18px;font-weight:600;color:var(--navy)">{v}</div></div>'
        for k, v in data.items()
    )
    return f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px">{cards}</div>'


# ----------------------------------------------------------------------
# parent portal (a separate, view-only login for parents/guardians)
# ----------------------------------------------------------------------
@app.route("/parent/login", methods=["GET", "POST"])
def parent_login_view():
    if request.method == "POST":
        db = get_db()
        try:
            parent_id = db.parent_login(request.form["email"].strip(), request.form["password"])
            session["parent_id"] = parent_id
            session.permanent = bool(request.form.get("remember"))
            return redirect(url_for("parent_dashboard"))
        except AuthError as e:
            flash(str(e))
        finally:
            db.close()
    body = """
    <form method="post">
      <label class="fl">Email address <span class="req">*</span></label>
      <input name="email" type="email" placeholder="Enter your email address" autocomplete="username" required>
      <label class="fl">Password <span class="req">*</span></label>
      <div class="pw-wrap">
        <input id="pw" name="password" type="password" placeholder="Password" autocomplete="current-password" required>
        <button type="button" class="pw-toggle" onclick="var i=document.getElementById('pw');var s=i.type==='password';i.type=s?'text':'password';this.firstChild.textContent=s?'Hide Password':'Show Password'"><span>Show Password</span><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#4b5563" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg></button>
      </div>
      <div class="row-opts">
        <label><input type="checkbox" name="remember" value="1"> Remember for 30 days</label>
        <a href="{{ url_for('forgot_password_view_parent') }}">Forgot Password</a>
      </div>
      <button type="submit" class="btn-green">Sign In</button>
    </form>
    <div class="divider">Or continue with</div>
    <a class="alt-pill" href="{{ url_for('login_view') }}"><svg width="34" height="34" viewBox="0 0 34 34"><circle cx="17" cy="17" r="17" fill="#15b04a"/><circle cx="17" cy="13" r="4.6" fill="#fff"/><path d="M8.5 25.5c1.2-4.3 4.8-6.3 8.5-6.3s7.3 2 8.5 6.3" fill="#fff"/></svg><b>Sign in as school staff</b></a>
    """
    return page("Parent Login", body, nav=False)


@app.route("/parent/forgot-password", methods=["GET", "POST"])
def forgot_password_view_parent():
    if request.method == "POST":
        db = get_db()
        try:
            db.request_password_reset("parent", request.form["email"].strip())
        except Exception:
            pass
        finally:
            db.close()
        flash("If that account exists, a reset token has been sent.")
    body = """
    <form method="post">
      Email: <input name="email"><br><br>
      <button type="submit">Send reset token</button>
    </form>
    <p><a href="{{ url_for('reset_password_view') }}">Have a token already? Reset here.</a></p>
    """
    return page("Parent: Forgot Password", body, nav=False)


@app.route("/parent/logout")
def parent_logout():
    session.pop("parent_id", None)
    return redirect(url_for("parent_login_view"))


def _require_parent_login():
    if "parent_id" not in session:
        return None
    return get_db(), session["parent_id"]


def parent_page(title, body):
    template = f"""<!doctype html><html><head><title>EduLink &mdash; {title}</title>{STYLE}</head>
    <body style="background: var(--paper);">
      <div class="topbar" style="padding: 16px 40px;">
        <div class="brand" style="background:none;border:none;padding:0;margin:0;color:var(--navy);">
          EduLink<small style="color:var(--ink-muted)">Parent Portal</small>
        </div>
        <div class="who">
          <a class="navtab" style="color:var(--navy-2);border:1px solid var(--border);border-radius:7px;padding:6px 14px;"
             href="{{{{ url_for('parent_dashboard') }}}}">My Children</a>
          <a class="navtab" style="color:var(--navy-2);border:1px solid var(--border);border-radius:7px;padding:6px 14px;"
             href="{{{{ url_for('parent_logout') }}}}">Logout</a>
        </div>
      </div>
      <div class="content" style="max-width: 900px; margin: 0 auto;">
        <h2>{title}</h2>
        {{% with messages = get_flashed_messages() %}}
          {{% if messages %}}<p class="flash">{{{{ messages[0] }}}}</p>{{% endif %}}
        {{% endwith %}}
        {body}
      </div>
    </body></html>"""
    return render_template_string(template)


@app.route("/parent/dashboard")
def parent_dashboard():
    ctx = _require_parent_login()
    if ctx is None:
        return redirect(url_for("parent_login_view"))
    db, parent_id = ctx
    children = db.parent_children(parent_id)
    db.close()
    rows = "".join(
        f"<tr><td>{c['adm_no']}</td><td>{c['first_name']} {c['last_name']}</td>"
        f"<td>{c['form_number']}{c['stream_code']}</td>"
        f"<td><a href='{url_for('parent_child_view', adm_no=c['adm_no'])}'>View</a></td></tr>"
        for c in children
    )
    body = f"""
    <table border="1" cellpadding="4"><tr><th>Adm No</th><th>Name</th><th>Class</th><th></th></tr>{rows}</table>
    """
    return parent_page("My Children", body)


@app.route("/parent/child/<adm_no>", methods=["GET"])
def parent_child_view(adm_no):
    ctx = _require_parent_login()
    if ctx is None:
        return redirect(url_for("parent_login_view"))
    db, parent_id = ctx

    if not db.parent_can_view(parent_id, adm_no):
        db.close()
        flash("You are not linked to that student.")
        return redirect(url_for("parent_dashboard"))

    term = request.args.get("term", "1")
    year = request.args.get("year", "2026")
    exam_id = request.args.get("exam_id", "1")

    profile = db.student_profile(adm_no, term=int(term), year=int(year))

    report = None
    report_error = None
    try:
        report = db.parent_view_report(parent_id, adm_no, int(exam_id))
    except PermissionDenied as e:
        report_error = str(e)
    db.close()

    tt_rows = "".join(
        f"<tr><td>{t['day_of_week']}</td><td>{t['period']}</td><td>{t['subject_name']}</td>"
        f"<td>{t['first_name']} {t['last_name']}</td></tr>"
        for t in profile["timetable"]
    )
    report_html = (
        f"<p>Mean: {report['mean']} | Grade: {report['mean_grade']}</p>"
        f"<table border='1' cellpadding='4'><tr><th>Subject</th><th>Score</th><th>Grade</th></tr>" +
        "".join(f"<tr><td>{s['subject']}</td><td>{s['score']}</td><td>{s['grade']}</td></tr>"
                for s in report["subjects"]) + "</table>"
    ) if report else f"<p style='color:#a00'>{report_error}</p>"

    body = f"""
    <p>Class: {profile['class_code']} | Attendance rate: {profile['attendance_rate']}%</p>
    {stat_cards(profile['attendance'])}

    <h3>Report card</h3>
    <form method="get">
      Exam ID: <input name="exam_id" size="4" value="{exam_id}">
      <button type="submit">View report</button>
    </form>
    {report_html}
    <p style="font-size:0.9em;color:#666">Only PUBLISHED results are visible here.</p>

    <h3>Fee balance</h3>
    <form method="get">
      Term: <input name="term" size="2" value="{term}"> Year: <input name="year" size="4" value="{year}">
      <button type="submit">View</button>
    </form>
    {stat_cards(profile.get('fee_balance', {}))}

    <h3>Timetable</h3>
    <table border="1" cellpadding="4"><tr><th>Day</th><th>Period</th><th>Subject</th><th>Teacher</th></tr>{tt_rows}</table>
    """
    return parent_page(f"{profile['name']} ({adm_no})", body)


# ----------------------------------------------------------------------
# dashboard
# ----------------------------------------------------------------------

# ---- app-launcher metadata: icon, one-line description, group, pastel tint ----
APP_META = {
    "students_view":       ("\U0001F393", "Students",       "Search, enrol and manage learners",         "People",         "#E4EEFF"),
    "staff_view":          ("\U0001F465", "Staff",          "Teachers, roles, assignments and HR",       "People",         "#EFE6FF"),
    "departments_view":    ("\U0001F3DB", "Departments",    "Departments and heads of department",       "People",         "#E6F4EA"),
    "alumni_view":         ("\U0001F3C5", "Alumni",         "Graduates and their contacts",              "People",         "#FFF1D6"),
    "marks_view":          ("\u270F\uFE0F", "Marks",        "Enter, correct and submit marks",           "Teaching",       "#FFE9E0"),
    "attendance_view":     ("\U0001F4CB", "Attendance",     "Class attendance reports",                  "Teaching",       "#E0F5F5"),
    "timetable_view":      ("\U0001F5D3", "Timetable",      "Weekly lesson grids per class",             "Teaching",       "#E4EEFF"),
    "exams_view":          ("\U0001F4DD", "Exams",          "Exams, grade breakdown and publishing",     "Teaching",       "#FFE9E0"),
    "analytics_view":      ("\U0001F4CA", "Analytics",      "Improvement, remedial and risk lists",      "Teaching",       "#EFE6FF"),
    "reports_view":        ("\U0001F5A8", "Reports",        "Class lists, merit lists, statements",      "Teaching",       "#E6F4EA"),
    "calendar_view":       ("\U0001F4C5", "Calendar",       "School events and key dates",               "School life",    "#FFF1D6"),
    "messages_view":       ("\U0001F4AC", "Messages",       "Staff inbox and compose",                   "School life",    "#E0F5F5"),
    "notifications_view":  ("\U0001F514", "Notifications",  "Queued alerts and reminders",               "School life",    "#FFE9E0"),
    "fees_view":           ("\U0001F4B0", "Fees",           "Invoices, payments, arrears, expenses",     "Administration", "#E6F4EA"),
    "academic_setup_view": ("\u2699\uFE0F", "Academic Setup", "Subjects, streams and system controls",  "Administration", "#EFE6FF"),
    "audit_log_view":      ("\U0001F50D", "Audit Log",      "Who changed what, and when",                "Administration", "#E4EEFF"),
    "promotion_view":      ("\u2B06\uFE0F", "Promotion",    "End-of-year promotion and graduation",      "Administration", "#FFF1D6"),
}
APP_GROUP_ORDER = ["Teaching", "People", "School life", "Administration"]


def build_app_tiles(db, staff_no, unread=0):
    """Only the apps this person can actually use, grouped. Components an
    executive has switched off are left out for everyone else."""
    oversight = db.has_full_oversight(staff_no)
    flags = {
        "all": True, "oversight": oversight,
        "staffing": oversight or bool(db.staffing_manager_forms(staff_no)),
        "hod": oversight or any(r["role"] == "hod" for r in db.get_roles(staff_no)),
    }
    groups = {g: [] for g in APP_GROUP_ORDER}
    for endpoint, _label, _tag, visibility in NAV_ITEMS:
        if endpoint not in APP_META or not flags[visibility]:
            continue
        feature = _ENDPOINT_TO_FEATURE_NAME.get(endpoint)
        if feature and not oversight and not db.is_feature_enabled(feature):
            continue
        icon, name, desc, group, tint = APP_META[endpoint]
        badge = f'<span class="app-badge">{unread}</span>' if endpoint == "messages_view" and unread else ""
        groups[group].append(
            f'<a class="app-tile" href="{url_for(endpoint)}">{badge}'
            f'<div class="app-icon" style="background:{tint}">{icon}</div>'
            f'<div class="app-name">{name}</div><div class="app-desc">{desc}</div></a>'
        )
    return "".join(
        f'<div class="app-group">{g}</div><div class="app-grid">{"".join(tiles)}</div>'
        for g, tiles in groups.items() if tiles
    )

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx

    if request.method == "POST" and request.form.get("action") == "request_leave":
        db.request_leave(staff_no, request.form["leave_type"], request.form["start_date"],
                          request.form["end_date"], request.form.get("notes", ""))
        flash("Leave request submitted.")

    roles = [dict(r) for r in db.get_roles(staff_no)]
    classes = db.visible_classes(staff_no)
    oversight = db.has_full_oversight(staff_no)
    scope = db.teacher_scope(staff_no)
    my_leave = db.list_leave(staff_no)
    unread = db.unread_count(staff_no)

    stats_html = ""
    if oversight:
        sc = db.school_counts()
        stats_html += stat_cards({"Total Students": sc["total_students"], "Total Teachers": sc["total_teachers"]})
        form_rows = "".join(
            f"<tr><td>Form {f['form_number']}</td><td>{f['total_students']}</td><td>{f['teacher_count']}</td>"
            f"<td>{', '.join(s['code'] + ':' + str(s['count']) for s in f['streams'] if s['count'] > 0) or '-'}</td></tr>"
            for f in sc["forms"]
        )
        stats_html += (
            "<h3>School-wide breakdown</h3>"
            "<table border='1' cellpadding='4'><tr><th>Form</th><th>Students</th><th>Teachers</th>"
            "<th>Streams (non-empty)</th></tr>" + form_rows + "</table>"
        )
    else:
        my_forms = set(scope["supervises_forms"])
        for cc in scope["class_teacher_of"]:
            my_forms.add(int(cc[0]))
        if my_forms:
            for fn in sorted(my_forms):
                fc = db.form_counts(fn)
                stats_html += f"<h3>Form {fn}</h3>" + stat_cards(
                    {"Students": fc["total_students"], "Teachers": fc["teacher_count"]}
                )

    my_class_link = ""
    if scope["class_teacher_of"]:
        cc = scope["class_teacher_of"][0]
        fn, sc_code = int(cc[0]), cc[1:]
        my_class_link = (
            f'<a href="{url_for("class_list_pdf")}?form_number={fn}&stream={sc_code}" '
            f'style="display:inline-block;background:var(--navy);color:#fff;padding:8px 16px;'
            f'border-radius:7px;text-decoration:none;font-weight:600;font-size:13px">'
            f'Print my class list ({cc})</a>'
        )

    apps_html = build_app_tiles(db, staff_no, unread)
    db.close()

    role_pills = "".join(
        f'<span class="badge oversight" style="margin-right:6px">{r["role"].replace("_"," ").upper()}'
        f'{" &middot; " + r["scope"] if r["scope"] != "school" else ""}</span>'
        for r in roles
    ) or '<span class="badge">No roles assigned</span>'

    class_chips = "".join(
        f'<span class="mono" style="display:inline-block;background:#fff;border:1px solid var(--border);'
        f'border-radius:6px;padding:3px 8px;margin:2px;font-size:12px">{c}</span>'
        for c in classes
    )

    def _leave_color(status):
        return {"approved": "green", "rejected": "var(--danger)"}.get(status, "inherit")

    leave_rows = "".join(
        f"<tr><td>{l['leave_type']}</td><td>{l['start_date']} to {l['end_date']}</td>"
        f"<td style='color:{_leave_color(l['status'])}'>{l['status']}</td></tr>"
        for l in my_leave
    )

    body = f"""
    {apps_html}
    <div class="app-group">Overview</div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-bottom:16px">
      <h3 style="margin-top:0">Your roles</h3>
      {role_pills}
      {f'<div style="margin-top:14px">{my_class_link}</div>' if my_class_link else ''}
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-bottom:16px">
      <h3 style="margin-top:0">Classes you can see ({len(classes)})</h3>
      <div>{class_chips}</div>
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-bottom:16px">
      <h3 style="margin-top:0">Enrolment &amp; staffing</h3>
      {stats_html or "<p style='color:var(--ink-muted)'>No form-level access yet.</p>"}
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-bottom:16px">
      <h3 style="margin-top:0">My leave</h3>
      <table border="1" cellpadding="4"><tr><th>Type</th><th>Dates</th><th>Status</th></tr>
        {leave_rows or "<tr><td colspan='3'>No leave requests yet.</td></tr>"}
      </table>
      <form method="post">
        <input type="hidden" name="action" value="request_leave">
        Type: <select name="leave_type"><option>Annual</option><option>Sick</option><option>Compassionate</option>
          <option>Maternity/Paternity</option></select>
        Start: <input name="start_date" placeholder="2026-03-01">
        End: <input name="end_date" placeholder="2026-03-05">
        Notes: <input name="notes" size="20">
        <button type="submit">Request leave</button>
      </form>
    </div>
    <p style="color:var(--ink-muted)">You have <b>{unread}</b> unread <a href="{url_for('messages_view')}">message(s)</a>.
    Open an app above to get started.</p>
    """
    return page("Dashboard", body)


# ----------------------------------------------------------------------
# students
# ----------------------------------------------------------------------
@app.route("/students", methods=["GET", "POST"])
def students_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    oversight = db.has_full_oversight(staff_no)
    results = []
    if request.method == "POST" and request.form.get("action") == "search":
        results = db.search_students(request.form["query"], include_alumni=bool(request.form.get("include_alumni")))
    if request.method == "POST" and request.form.get("action") == "add":
        if not oversight:
            flash("Only an executive (Principal/Deputy/Dean) may add new students.")
        else:
            try:
                adm_no = db.add_student(request.form["first_name"], request.form["last_name"],
                                         int(request.form["form_number"]), request.form["stream"],
                                         gender=request.form.get("gender") or None)
                flash(f"Added student {adm_no}.")
            except (NotFound, ValueError) as e:
                flash(str(e))
    if request.method == "POST" and request.form.get("action") == "remove_student":
        try:
            db.remove_student_permanently(request.form["adm_no"], staff_no)
            flash(f"Student {request.form['adm_no']} permanently removed.")
        except (PermissionDenied, NotFound) as e:
            flash(str(e))

    if request.method == "POST" and request.form.get("action") == "save_student_grid":
        if not oversight:
            flash("Only an executive may edit student details.")
        elif request.form.get("remove"):
            try:
                db.remove_student_permanently(request.form["remove"], staff_no)
                flash("Student permanently removed.")
            except (PermissionDenied, NotFound) as e:
                flash(str(e))
        else:
            changed, errors, i = 0, [], 0
            while f"orig_{i}" in request.form:
                orig = request.form[f"orig_{i}"]
                fn = request.form.get(f"fn_{i}", "").strip()
                ln = request.form.get(f"ln_{i}", "").strip()
                fm = request.form.get(f"fm_{i}", "").strip()
                st = request.form.get(f"st_{i}", "").strip()
                gd = request.form.get(f"gd_{i}", "")
                i += 1
                row = db.conn.execute(
                    "SELECT first_name, last_name, form_number, stream_code, gender FROM students WHERE adm_no=?",
                    (orig,)).fetchone()
                if not row or not fn or not ln:
                    continue
                class_changed = (fm, st.upper()) != (str(row["form_number"]), row["stream_code"])
                if (fn, ln, gd) == (row["first_name"], row["last_name"], row["gender"] or "") and not class_changed:
                    continue
                try:
                    db.update_student(orig, first_name=fn, last_name=ln,
                                      form_number=int(fm) if class_changed else None,
                                      stream_name_or_code=st if class_changed else None,
                                      gender=gd if gd != (row["gender"] or "") else None)
                    changed += 1
                except (NotFound, ValueError) as e:
                    errors.append(f"{orig}: {e}")
            msg = f"Saved {changed} student change(s)."
            if errors:
                msg += " Not saved -- " + "; ".join(errors)
            flash(msg)

    grid_form = request.values.get("grid_form", "")
    grid = db.student_grid(int(grid_form)) if grid_form else db.student_grid()
    db.close()

    result_rows = "".join(
        f"<tr><td>{r['adm_no']}</td><td>{esc(r['name'])}</td><td>{r['class_code']}</td><td>{r['type']}</td>"
        f"<td><a href='{url_for('student_detail', adm_no=r['adm_no'])}'>View</a></td></tr>"
        for r in results
    )
    results_html = (
        "<div class='grid-wrap' style='max-height:240px;margin-bottom:16px'><table><thead><tr><th>Adm No</th>"
        "<th>Name</th><th>Class</th><th>Type</th><th></th></tr></thead><tbody>" + result_rows + "</tbody></table></div>"
        if results else ""
    )

    def _student_row(i, s):
        view = f"<a href='{url_for('student_detail', adm_no=s['adm_no'])}'>View</a>"
        if not oversight:
            return (f"<tr><td class='mono'>{s['adm_no']}</td><td>{esc(s['first_name'])} {esc(s['last_name'])}</td>"
                    f"<td>{s['form_number']}{s['stream_code']}</td><td>{s['gender'] or '-'}</td><td>{view}</td></tr>")
        g = s["gender"] or ""
        opts = "".join(
            "<option value='" + v + "'" + (" selected" if v == g else "") + ">" + (v or "-") + "</option>"
            for v in ("", "M", "F"))
        return (
            "<tr>"
            f"<td><input type='hidden' name='orig_{i}' value='{esc(s['adm_no'])}'><span class='mono'>{esc(s['adm_no'])}</span></td>"
            f"<td><input class='cell' name='fn_{i}' value='{esc(s['first_name'])}'></td>"
            f"<td><input class='cell' name='ln_{i}' value='{esc(s['last_name'])}'></td>"
            f"<td><input class='cell narrow' name='fm_{i}' value='{s['form_number']}'></td>"
            f"<td><input class='cell narrow' name='st_{i}' value='{esc(s['stream_code'])}'></td>"
            f"<td><select class='cell' name='gd_{i}'>{opts}</select></td>"
            f"<td>{view}</td>"
            f"<td><button type='submit' name='remove' value='{esc(s['adm_no'])}' class='btn-danger' "
            "onclick=\"return confirm('Permanently remove this student and all their records? This cannot be undone.')\">Remove</button></td>"
            "</tr>"
        )

    rows_html = "".join(_student_row(i, s) for i, s in enumerate(grid)) or "<tr><td colspan='8'>No students yet.</td></tr>"
    if oversight:
        heads = "<th>Adm No</th><th>First name</th><th>Last name</th><th>Form</th><th>Stream</th><th>Gender</th><th></th><th></th>"
        grid_html = (
            f"<form method='post' class='grid-form' action='?grid_form={esc(grid_form)}'>"
            "<input type='hidden' name='action' value='save_student_grid'>"
            "<button type='submit' style='position:absolute;left:-9999px' tabindex='-1' aria-hidden='true'>Save</button>"
            f"<div class='grid-wrap'><table><thead><tr>{heads}</tr></thead><tbody>{rows_html}</tbody></table></div>"
            "<div class='grid-actions'><button type='submit'>Save changes</button></div></form>"
        )
    else:
        heads = "<th>Adm No</th><th>Name</th><th>Class</th><th>Gender</th><th></th>"
        grid_html = f"<div class='grid-wrap'><table><thead><tr>{heads}</tr></thead><tbody>{rows_html}</tbody></table></div>"

    add_section = """
    <h3>Add student</h3>
    <form method="post">
      <input type="hidden" name="action" value="add">
      First name: <input name="first_name"> Last name: <input name="last_name">
      Form: <input name="form_number" size="2" value="1"> Stream: <input name="stream" size="8" value="White">
      Gender: <select name="gender"><option value="">-</option><option value="M">M</option><option value="F">F</option></select>
      <button type="submit">Add</button>
    </form>
    """ if oversight else """
    <div class="flash" style="background:#FFF6E0;color:#8a6d1a;border-color:#F0DFA6">
      Adding new students is available to the Principal, a Deputy Principal, or the Dean of Studies only.
    </div>
    """

    body = f"""
    <form method="post">
      <input type="hidden" name="action" value="search">
      Search: <input name="query" placeholder="name or admission number">
      <label style="font-size:12px"><input type="checkbox" name="include_alumni"> include alumni</label>
      <button type="submit">Search</button>
      <span style="margin-left:auto"></span>
    </form>
    {results_html}

    <h3>All students</h3>
    {"<p class='hint'>Edit any cell directly, then press Save changes.</p>" if oversight else ""}
    <form method="get" style="margin-bottom:10px">
      Show form: <input name="grid_form" size="2" value="{esc(grid_form)}"> <button type="submit">Filter</button>
    </form>
    {grid_html}
    {add_section}
    """
    return page("Students", body)

@app.route("/students/<adm_no>", methods=["GET", "POST"])
def student_detail(adm_no):
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx

    student_row = db.conn.execute(
        "SELECT form_number, stream_code FROM students WHERE adm_no=?", (adm_no,)
    ).fetchone()
    can_view_finance = db.has_full_oversight(staff_no) or (
        student_row and db._can_manage_class(staff_no, student_row["form_number"], student_row["stream_code"])
    )

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "attendance":
                db.record_attendance(staff_no, adm_no, request.form["date"], request.form["status"])
                flash("Attendance recorded.")
            elif action == "discipline":
                db.add_discipline_record(staff_no, adm_no, request.form["date"],
                                          request.form["description"], request.form.get("severity", "minor"))
                flash("Discipline record added.")
            elif action == "payment":
                if not can_view_finance:
                    flash("Only this student's class teacher, form supervisor, or an executive may record payments.")
                else:
                    db.record_payment(adm_no, float(request.form["amount"]), request.form["date"],
                                       int(request.form["term"]), int(request.form["year"]),
                                       request.form.get("method", "cash"), recorded_by=staff_no)
                    flash("Payment recorded -- a receipt is available below.")
            elif action == "update_profile":
                db.update_student(adm_no, first_name=request.form.get("first_name") or None,
                                   last_name=request.form.get("last_name") or None,
                                   form_number=int(request.form["new_form_number"]) if request.form.get("new_form_number") else None,
                                   stream_name_or_code=request.form.get("new_stream") or None)
                flash("Profile updated.")
            elif action == "send_results":
                db.send_results_notification(adm_no, int(request.form["exam_id"]), request.form.get("channel", "sms"))
                flash("Results notification queued for the parent.")
        except (PermissionDenied, NotFound, ValueError) as e:
            flash(str(e))

    try:
        class_code = db.class_code_of(adm_no)
    except NotFound:
        class_code = "N/A (alumnus or unknown)"

    student_row = db.conn.execute(
        "SELECT first_name, last_name, form_number, stream_code FROM students WHERE adm_no=?", (adm_no,)
    ).fetchone()

    term = request.values.get("term", "1")
    year = request.values.get("year", "2026")
    exam_id = request.values.get("exam_id", "1")

    try:
        profile = db.student_profile(adm_no, term=int(term), year=int(year))
    except NotFound as e:
        profile = {"attendance": {}, "attendance_rate": None, "discipline": [], "timetable": [],
                   "fee_balance": {"billed": 0, "paid": 0, "balance": 0}}
        flash(str(e))

    marks = []
    try:
        marks = db.get_marks_for_student(adm_no, int(exam_id))
    except Exception:
        pass

    target_grade = request.values.get("target_grade", "")
    projection_html = ""
    if target_grade:
        try:
            proj = db.target_grade_projection(adm_no, int(exam_id), target_grade)

            def _proj_row(s):
                color = "green" if s["achieved"] else "var(--danger)"
                gap_text = "Achieved" if s["achieved"] else f"+{s['gap']:.1f} needed"
                return (f"<tr><td>{s['subject']}</td><td>{s['current_score']}</td><td>{s['current_grade']}</td>"
                        f"<td style='color:{color}'>{gap_text}</td></tr>")

            proj_rows = "".join(_proj_row(s) for s in proj["subjects"])
            projection_html = f"""
            <table border="1" cellpadding="4"><tr><th>Subject</th><th>Current</th><th>Grade</th><th>Gap to {target_grade}</th></tr>
              {proj_rows or "<tr><td colspan='4'>No marks recorded for this exam yet.</td></tr>"}
            </table>
            <p>Current mean: {proj['current_mean']} | Mean gap to {target_grade}: {proj['mean_gap']}</p>
            """
        except ValueError as e:
            flash(str(e))

    payments = []
    if can_view_finance:
        payments = db.conn.execute(
            "SELECT * FROM fee_payments WHERE student_adm_no=? ORDER BY date DESC", (adm_no,)
        ).fetchall()

    db.close()

    disc_rows = "".join(f"<tr><td>{d['date']}</td><td>{d['description']}</td><td>{d['severity']}</td></tr>"
                         for d in profile["discipline"])
    tt_rows = "".join(
        f"<tr><td>{t['day_of_week']}</td><td>{t['period']}</td><td>{t['subject_name']}</td>"
        f"<td>{t['first_name']} {t['last_name']}</td></tr>"
        for t in profile["timetable"]
    )
    mark_rows = "".join(f"<tr><td>{m['subject_name']}</td><td>{m['score']}</td><td>{m['status']}</td></tr>"
                         for m in marks)

    if can_view_finance:
        payment_rows = "".join(
            f"<tr><td>{p['date']}</td><td>{p['amount']}</td><td>{p['method']}</td>"
            f"<td>{p['channel_code'] or 'manual'}</td>"
            f"<td><a href='{url_for('receipt_pdf', payment_id=p['id'])}'>Download receipt</a></td></tr>"
            for p in payments
        )
        finance_section = f"""
        <h3>Fee balance (term/year selector)</h3>
        <form method="get">
          Term: <input name="term" size="2" value="{term}"> Year: <input name="year" size="4" value="{year}">
          <button type="submit">View balance</button>
        </form>
        {stat_cards(profile.get('fee_balance', {}))}

        <h3>Payment history &amp; receipts</h3>
        <table border="1" cellpadding="4">
          <tr><th>Date</th><th>Amount</th><th>Method</th><th>Channel</th><th></th></tr>{payment_rows}
        </table>

        <h3>Record fee payment</h3>
        <form method="post">
          <input type="hidden" name="action" value="payment">
          Amount: <input name="amount" size="8">
          Date: <input name="date" placeholder="2026-01-15">
          Term: <input name="term" size="2" value="1">
          Year: <input name="year" size="4" value="2026">
          Method: <input name="method" value="cash">
          <button type="submit">Record</button>
        </form>
        """
    else:
        finance_section = """
        <div class="flash" style="background:#FFF6E0;color:#8a6d1a;border-color:#F0DFA6">
          Financial details for this student are only visible to their class teacher, form
          supervisor, or an executive.
        </div>
        """

    body = f"""
    <p>Class: {class_code} | Attendance rate: {profile['attendance_rate']}% </p>
    {stat_cards(profile['attendance'])}

    <h3>Marks (exam ID selector)</h3>
    <form method="get">
      Exam ID: <input name="exam_id" size="4" value="{exam_id}">
      <button type="submit">View marks</button>
    </form>
    <table border="1" cellpadding="4"><tr><th>Subject</th><th>Score</th><th>Status</th></tr>{mark_rows}</table>

    <h3>Target grade projection</h3>
    <form method="get">
      <input type="hidden" name="exam_id" value="{exam_id}">
      Target grade: <input name="target_grade" value="{target_grade}" placeholder="B+" size="4">
      <button type="submit">Project</button>
    </form>
    {projection_html}

    <h3>Send results to parent</h3>
    <form method="post">
      <input type="hidden" name="action" value="send_results">
      Exam ID: <input name="exam_id" size="4" value="{exam_id}">
      Channel: <select name="channel"><option>sms</option><option>email</option></select>
      <button type="submit">Send</button>
    </form>
    <p style="font-size:12px;color:var(--ink-muted)">Only works once results are published; queues a
    notification (no live SMS/email provider connected yet).</p>

    {finance_section}

    <h3>Class timetable</h3>
    <table border="1" cellpadding="4"><tr><th>Day</th><th>Period</th><th>Subject</th><th>Teacher</th></tr>{tt_rows}</table>

    <h3>Update profile</h3>
    <form method="post">
      <input type="hidden" name="action" value="update_profile">
      First name: <input name="first_name" value="{student_row['first_name'] if student_row else ''}">
      Last name: <input name="last_name" value="{student_row['last_name'] if student_row else ''}">
      Move to Form: <input name="new_form_number" size="2" value="{student_row['form_number'] if student_row else ''}">
      Stream: <input name="new_stream" size="8" value="{student_row['stream_code'] if student_row else ''}">
      <button type="submit">Update</button>
    </form>

    <h3>Record attendance</h3>
    <form method="post">
      <input type="hidden" name="action" value="attendance">
      Date: <input name="date" placeholder="2026-05-01">
      Status:
      <select name="status"><option>present</option><option>absent</option><option>late</option></select>
      <button type="submit">Record</button>
    </form>

    <h3>Add discipline record</h3>
    <form method="post">
      <input type="hidden" name="action" value="discipline">
      Date: <input name="date" placeholder="2026-05-01">
      Description: <input name="description" size="40">
      Severity: <select name="severity"><option>minor</option><option>moderate</option><option>major</option></select>
      <button type="submit">Add</button>
    </form>
    <table border="1" cellpadding="4"><tr><th>Date</th><th>Description</th><th>Severity</th></tr>{disc_rows}</table>
    """
    return page(f"Student {adm_no}", body)


@app.route("/receipts/<int:payment_id>")
def receipt_pdf(payment_id):
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    payment = db.conn.execute("SELECT student_adm_no FROM fee_payments WHERE id=?", (payment_id,)).fetchone()
    if payment is None:
        db.close()
        flash("No such payment.")
        return redirect(url_for("students_view"))
    student = db.conn.execute(
        "SELECT form_number, stream_code FROM students WHERE adm_no=?", (payment["student_adm_no"],)
    ).fetchone()
    allowed = db.has_full_oversight(staff_no) or (
        student and db._can_manage_class(staff_no, student["form_number"], student["stream_code"])
    )
    if not allowed:
        db.close()
        flash("Only that student's class teacher, form supervisor, or an executive may view this receipt.")
        return redirect(url_for("students_view"))
    from receipt import generate_receipt
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        generate_receipt(db, payment_id, tmp.name)
        return send_file(tmp.name, as_attachment=True)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("students_view"))
    finally:
        db.close()


# ----------------------------------------------------------------------
# staff
# ----------------------------------------------------------------------
@app.route("/staff", methods=["GET", "POST"])
def staff_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    oversight = db.has_full_oversight(staff_no)
    my_staffing_forms = [] if oversight else db.staffing_manager_forms(staff_no)
    has_staffing_power = oversight or bool(my_staffing_forms)

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add_teacher":
                if not oversight:
                    flash("Only an executive (Principal/Deputy/Dean) may add new teacher accounts.")
                else:
                    new_no = db.add_teacher(request.form["first_name"], request.form["last_name"],
                                             email=request.form.get("email") or None)
                    temp_pw = db.generate_temp_password_for(new_no)
                    flash(f"Added teacher {new_no}, temp password: {temp_pw}")
            elif action == "grant_role":
                if not oversight:
                    flash("Only an executive (Principal/Deputy/Dean) may grant roles.")
                else:
                    db.grant_role(request.form["target_staff_no"], request.form["role"],
                                   request.form.get("scope", "school"))
                    flash("Role granted.")
            elif action == "class_teacher":
                db.set_class_teacher(int(request.form["form_number"]), request.form["stream"],
                                      request.form["target_staff_no"], requested_by=staff_no)
                flash("Class teacher assigned.")
            elif action == "remove_class_teacher":
                db.remove_class_teacher(int(request.form["form_number"]), request.form["stream"],
                                         requested_by=staff_no)
                flash("Class teacher removed -- class is now Unassigned.")
            elif action == "supervisor":
                db.set_supervisor(int(request.form["form_number"]), request.form["target_staff_no"],
                                   requested_by=staff_no)
                flash("Supervisor assigned.")
            elif action == "remove_supervisor":
                db.remove_supervisor(int(request.form["form_number"]), requested_by=staff_no)
                flash("Supervisor removed -- form is now Unassigned.")
            elif action == "subject_assignment":
                db.assign_subject_teacher(request.form["target_staff_no"], request.form["subject"],
                                           int(request.form["form_number"]), request.form["stream"],
                                           requested_by=staff_no)
                flash("Subject teacher assigned to class.")
            elif action == "unassign_subject":
                db.unassign_subject_teacher(request.form["target_staff_no"], request.form["subject"],
                                             int(request.form["form_number"]), request.form["stream"],
                                             requested_by=staff_no)
                flash("Subject teacher unassigned from that class.")
            elif action == "delegate_staffing":
                if not oversight:
                    flash("Only an executive may delegate staffing management for a form.")
                else:
                    db.grant_staffing_manager(int(request.form["form_number"]), request.form["target_staff_no"])
                    flash("Staffing manager delegated for that form.")
            elif action == "revoke_staffing":
                if not oversight:
                    flash("Only an executive may revoke a staffing delegation.")
                else:
                    db.revoke_staffing_manager(int(request.form["form_number"]), request.form["target_staff_no"])
                    flash("Staffing delegation revoked.")
            elif action == "revoke_role":
                if not oversight:
                    flash("Only an executive (Principal/Deputy/Dean) may revoke roles.")
                else:
                    db.revoke_role(request.form["target_staff_no"], request.form["role"],
                                    request.form.get("scope", "school"))
                    flash("Role revoked.")
            elif action == "set_id_criteria":
                if not oversight:
                    flash("Only an executive may configure admission/staff numbering.")
                else:
                    kind = request.form["kind"]
                    db.set_id_criteria(
                        kind,
                        prefix=request.form.get("prefix") or None,
                        padding=int(request.form["padding"]) if request.form.get("padding") else None,
                        next_number=int(request.form["next_number"]) if request.form.get("next_number") else None,
                    )
                    flash(f"{kind.title()} numbering criteria updated.")
            elif action == "add_contract":
                if not oversight:
                    flash("Only an executive may add staff contracts.")
                else:
                    db.add_contract(request.form["target_staff_no"], request.form["contract_type"],
                                     request.form["start_date"], request.form.get("end_date") or None,
                                     request.form.get("notes", ""))
                    flash("Contract added.")
            elif action == "decide_leave":
                db.decide_leave(int(request.form["leave_id"]), staff_no,
                                 approve=request.form["decision"] == "approve")
                flash("Leave request updated.")
            elif action == "save_teacher_grid":
                if not oversight:
                    flash("Only an executive may edit staff details.")
                elif request.form.get("remove"):
                    db.remove_teacher_permanently(request.form["remove"], staff_no)
                    flash(f"Teacher {request.form['remove']} permanently removed.")
                else:
                    changed, errors, i = 0, [], 0
                    while f"orig_{i}" in request.form:
                        orig = request.form[f"orig_{i}"]
                        sn = request.form.get(f"sn_{i}", orig).strip() or orig
                        fn = request.form.get(f"fn_{i}", "").strip()
                        ln = request.form.get(f"ln_{i}", "").strip()
                        i += 1
                        row = db.conn.execute("SELECT first_name, last_name FROM teachers WHERE staff_no=?",
                                              (orig,)).fetchone()
                        if row and fn and ln and (sn, fn, ln) != (orig, row["first_name"], row["last_name"]):
                            try:
                                new_no = db.update_teacher(orig, new_staff_no=sn if sn != orig else None,
                                                           first_name=fn, last_name=ln)
                                if orig == session.get("staff_no"):
                                    session["staff_no"] = new_no
                                    staff_no = new_no
                                changed += 1
                            except (NotFound, ValueError) as e:
                                errors.append(f"{orig}: {e}")
                    msg = f"Saved {changed} staff change(s)."
                    if errors:
                        msg += " Not saved -- " + "; ".join(errors)
                    flash(msg)
            elif action == "edit_teacher":
                if not oversight:
                    flash("Only an executive may edit a teacher's name or staff number.")
                else:
                    new_no = db.update_teacher(
                        request.form["target_staff_no"],
                        new_staff_no=request.form.get("new_staff_no") or None,
                        first_name=request.form.get("first_name") or None,
                        last_name=request.form.get("last_name") or None,
                    )
                    flash(f"Teacher updated (staff no: {new_no}).")
            elif action == "remove_teacher":
                try:
                    db.remove_teacher_permanently(request.form["target_staff_no"], staff_no)
                    flash(f"Teacher {request.form['target_staff_no']} permanently removed.")
                except (PermissionDenied, NotFound) as e:
                    flash(str(e))
            elif action == "create_group":
                db.create_teacher_group(request.form["group_name"])
                flash("Teacher group created.")
            elif action == "add_to_group":
                db.add_to_group(int(request.form["group_id"]), request.form["target_staff_no"])
                flash("Added to group.")
            elif action == "remove_from_group":
                db.remove_from_group(int(request.form["group_id"]), request.form["target_staff_no"])
                flash("Removed from group.")
        except (PermissionDenied, NotFound, ValueError) as e:
            flash(str(e))

    # ---- Tier 1: full oversight sees everything, including everyone's roles ----
    if oversight:
        grid = db.teacher_grid()
        cur = {r["staff_no"]: r for r in db.conn.execute("SELECT staff_no, first_name, last_name FROM teachers")}

        def _staff_row(i, t):
            c = cur[t["staff_no"]]
            return (
                "<tr>"
                f"<td><input type='hidden' name='orig_{i}' value='{esc(t['staff_no'])}'>"
                f"<input class='cell mono' name='sn_{i}' value='{esc(t['staff_no'])}'></td>"
                f"<td><input class='cell' name='fn_{i}' value='{esc(c['first_name'])}'></td>"
                f"<td><input class='cell' name='ln_{i}' value='{esc(c['last_name'])}'></td>"
                f"<td class='wrap'>{', '.join(r.replace('_', ' ') for r in t['roles']) or '-'}</td>"
                f"<td class='wrap'>{', '.join(t['class_teacher_of']) or '-'}</td>"
                f"<td class='wrap'>{', '.join(t['supervises_forms']) or '-'}</td>"
                f"<td class='wrap'>{', '.join(t['teaching']) or '-'}</td>"
                f"<td><button type='submit' name='remove' value='{esc(t['staff_no'])}' class='btn-danger' "
                "onclick=\"return confirm('Permanently remove this teacher? This cannot be undone.')\">Remove</button></td>"
                "</tr>"
            )

        roster_section = (
            "<h3>Staff</h3><p class='hint'>Edit names or staff numbers directly in the table, then press "
            "Save changes.</p>"
            "<form method='post' class='grid-form'><input type='hidden' name='action' value='save_teacher_grid'>"
            "<button type='submit' style='position:absolute;left:-9999px' tabindex='-1' aria-hidden='true'>Save</button>"
            "<div class='grid-wrap'><table><thead><tr><th>Staff No</th><th>First name</th><th>Last name</th>"
            "<th>Roles</th><th>Class teacher of</th><th>Supervises</th><th>Teaching</th><th></th></tr></thead>"
            "<tbody>" + "".join(_staff_row(i, t) for i, t in enumerate(grid)) + "</tbody></table></div>"
            "<div class='grid-actions'><button type='submit'>Save changes</button></div></form>"
        )
        adm_criteria = db.get_id_criteria("admission")
        staff_criteria = db.get_id_criteria("staff")
        id_settings_section = f"""
        <h3>Admission &amp; staff numbering criteria</h3>
        <p style="font-size:12px;color:var(--ink-muted)">Controls how admission numbers and staff
        numbers are auto-generated when you don't type one in yourself.</p>
        <form method="post">
          <input type="hidden" name="action" value="set_id_criteria">
          <input type="hidden" name="kind" value="admission">
          Admission &mdash; Prefix: <input name="prefix" value="{adm_criteria['prefix']}" size="8">
          Padding (digits): <input name="padding" value="{adm_criteria['padding']}" size="3">
          Next number: <input name="next_number" value="{adm_criteria['next']}" size="6">
          <button type="submit">Update</button>
        </form>
        <form method="post">
          <input type="hidden" name="action" value="set_id_criteria">
          <input type="hidden" name="kind" value="staff">
          Staff &mdash; Prefix: <input name="prefix" value="{staff_criteria['prefix']}" size="8">
          Padding (digits): <input name="padding" value="{staff_criteria['padding']}" size="3">
          Next number: <input name="next_number" value="{staff_criteria['next']}" size="6">
          <button type="submit">Update</button>
        </form>
        <p style="font-size:12px;color:var(--ink-muted)">Example: prefix "2026", padding 3, next 1
        would generate admission numbers 2026001, 2026002, ...</p>
        """

        pending_leave = [l for l in db.list_leave() if l["status"] == "pending"]
        leave_rows = "".join(
            f"<tr><td class='mono'>{l['staff_no']}</td><td>{l['leave_type']}</td>"
            f"<td>{l['start_date']} to {l['end_date']}</td><td>{l['notes'] or ''}</td>"
            f"<td><form method='post' style='display:inline;margin-right:4px'>"
            f"<input type='hidden' name='action' value='decide_leave'>"
            f"<input type='hidden' name='leave_id' value='{l['id']}'>"
            f"<input type='hidden' name='decision' value='approve'>"
            f"<button type='submit'>Approve</button></form>"
            f"<form method='post' style='display:inline'>"
            f"<input type='hidden' name='action' value='decide_leave'>"
            f"<input type='hidden' name='leave_id' value='{l['id']}'>"
            f"<input type='hidden' name='decision' value='reject'>"
            f"<button type='submit'>Reject</button></form></td></tr>"
            for l in pending_leave
        )
        hr_section = f"""
        <h3>HR: staff contracts</h3>
        <form method="post">
          <input type="hidden" name="action" value="add_contract">
          Staff No: <input name="target_staff_no">
          Contract type: <select name="contract_type"><option>Permanent</option><option>Contract</option>
            <option>TSC</option><option>Probation</option></select>
          Start date: <input name="start_date" placeholder="2026-01-01">
          End date (optional): <input name="end_date" placeholder="2027-01-01">
          Notes: <input name="notes" size="20">
          <button type="submit">Add contract</button>
        </form>

        <h3>HR: pending leave requests ({len(pending_leave)})</h3>
        <table border="1" cellpadding="4">
          <tr><th>Staff No</th><th>Type</th><th>Dates</th><th>Notes</th><th></th></tr>
          {leave_rows or "<tr><td colspan='5'>No pending requests.</td></tr>"}
        </table>
        """
        add_teacher_section = f"""
        <h3>Add teacher (issues a temp password)</h3>
        <form method="post">
          <input type="hidden" name="action" value="add_teacher">
          First name: <input name="first_name"> Last name: <input name="last_name">
          Email: <input name="email">
          <button type="submit">Add</button>
        </form>
        """
        role_section = f"""
        <h3>Grant a role</h3>
        <form method="post">
          <input type="hidden" name="action" value="grant_role">
          Staff No: <input name="target_staff_no">
          Role:
          <select name="role">
            <option>principal</option><option>deputy_principal_academics</option>
            <option>deputy_principal_admin</option><option>dean_of_studies</option><option>hod</option>
          </select>
          Scope (dept code if HOD): <input name="scope" value="school">
          <button type="submit">Grant</button>
        </form>

        <h3>Revoke a role</h3>
        <form method="post">
          <input type="hidden" name="action" value="revoke_role">
          Staff No: <input name="target_staff_no">
          Role:
          <select name="role">
            <option>principal</option><option>deputy_principal_academics</option>
            <option>deputy_principal_admin</option><option>dean_of_studies</option><option>hod</option>
          </select>
          Scope (dept code if HOD): <input name="scope" value="school">
          <button type="submit">Revoke</button>
        </form>

        <h3>Roles &amp; permissions reference</h3>
        <table border="1" cellpadding="4">
          <tr><th>Role</th><th>Can do</th></tr>
          <tr><td>Principal</td><td>Everything -- full oversight of the whole school</td></tr>
          <tr><td>Deputy Principal (Academics)</td><td>Full oversight: all classes, publish results, promotion</td></tr>
          <tr><td>Deputy Principal (Admin)</td><td>Full oversight: all classes, staffing, promotion (cannot publish exam results)</td></tr>
          <tr><td>Dean of Studies</td><td>Full oversight: all classes, publish results, promotion</td></tr>
          <tr><td>HOD (scoped to a department)</td><td>Views/moderates marks for their department's subjects, school-wide</td></tr>
          <tr><td>Staffing manager (delegated, per form)</td><td>Assign/remove class teachers, supervisors, subject teachers -- for that one form only</td></tr>
          <tr><td>Class supervisor / principal</td><td>Full access to their one form: all streams, marks, class lists</td></tr>
          <tr><td>Class teacher</td><td>Full access to their one class: marks, attendance, discipline, fees, receipts</td></tr>
          <tr><td>Subject teacher</td><td>Marks entry only, only for the subject(s)/class(es) they're assigned to</td></tr>
        </table>
        <p style="font-size:12px;color:var(--ink-muted)">"Full oversight" means: sees every class,
        every department, every student's finances, can publish results, and can run end-of-year
        promotion. Only the four executive roles above have it.</p>
        """
        staffing_delegation_section = """
        <h3>Delegate staffing manager for a form</h3>
        <form method="post">
          <input type="hidden" name="action" value="delegate_staffing">
          Staff No: <input name="target_staff_no"> Form: <input name="form_number" size="2" value="1">
          <button type="submit">Delegate</button>
        </form>

        <h3>Revoke a staffing delegation</h3>
        <form method="post">
          <input type="hidden" name="action" value="revoke_staffing">
          Staff No: <input name="target_staff_no"> Form: <input name="form_number" size="2" value="1">
          <button type="submit">Revoke</button>
        </form>
        <p style="font-size:12px;color:var(--ink-muted)">A delegated staffing manager can assign class
        teachers, supervisors, and subject teachers for that one form, without needing an executive role.</p>
        """
    else:
        roster_section = ""
        id_settings_section = ""
        add_teacher_section = ""
        role_section = ""
        staffing_delegation_section = ""
        hr_section = ""

    # ---- Tier 2/3: assignment forms only shown if you can actually use them ----
    if has_staffing_power:
        scope_note = ("all forms" if oversight else f"Form {', '.join(map(str, my_staffing_forms))}")
        default_form = my_staffing_forms[0] if my_staffing_forms else 1
        assignment_section = f"""
        <h3>Assign / remove class teacher</h3>
        <p style="font-size:12px;color:var(--ink-muted)">You may assign staff for: {scope_note}.</p>
        <form method="post">
          <input type="hidden" name="action" value="class_teacher">
          Staff No: <input name="target_staff_no">
          Form: <input name="form_number" size="2" value="{default_form}">
          Stream: <input name="stream" value="White">
          <button type="submit">Assign</button>
        </form>
        <form method="post">
          <input type="hidden" name="action" value="remove_class_teacher">
          Form: <input name="form_number" size="2" value="{default_form}">
          Stream: <input name="stream" value="White">
          <button type="submit">Remove current class teacher</button>
        </form>

        <h3>Assign / remove form supervisor</h3>
        <form method="post">
          <input type="hidden" name="action" value="supervisor">
          Staff No: <input name="target_staff_no">
          Form: <input name="form_number" size="2" value="{default_form}">
          <button type="submit">Assign</button>
        </form>
        <form method="post">
          <input type="hidden" name="action" value="remove_supervisor">
          Form: <input name="form_number" size="2" value="{default_form}">
          <button type="submit">Remove current supervisor</button>
        </form>

        <h3>Assign / unassign subject teacher</h3>
        <form method="post">
          <input type="hidden" name="action" value="subject_assignment">
          Staff No: <input name="target_staff_no"> Subject: <input name="subject" value="Maths">
          Form: <input name="form_number" size="2" value="{default_form}">
          Stream: <input name="stream" value="White">
          <button type="submit">Assign</button>
        </form>
        <form method="post">
          <input type="hidden" name="action" value="unassign_subject">
          Staff No: <input name="target_staff_no"> Subject: <input name="subject" value="Maths">
          Form: <input name="form_number" size="2" value="{default_form}">
          Stream: <input name="stream" value="White">
          <button type="submit">Unassign</button>
        </form>
        <p style="font-size:12px;color:var(--ink-muted)">To reassign someone, just assign the new
        person directly -- it overwrites whoever was there before. Use "remove"/"unassign" when you
        want the slot to go back to Unassigned instead of being replaced.</p>
        """
    else:
        assignment_section = """
        <div class="flash" style="background:#FFF6E0;color:#8a6d1a;border-color:#F0DFA6">
          You don't currently have staff-assignment permissions for any form. If you need a class
          teacher, supervisor, or subject teacher changed, contact the Principal, a Deputy Principal,
          or the Dean of Studies.
        </div>
        """

    # ---- Teacher groups: organizational, not a "power" reveal -- open to everyone logged in ----
    groups = db.list_groups()
    group_rows = "".join(
        f"<tr><td>{g['id']}</td><td>{g['name']}</td><td>{', '.join(g['members']) or '-'}</td></tr>"
        for g in groups
    )
    db.close()

    body = f"""
    {roster_section}
    {add_teacher_section}
    {role_section}
    {staffing_delegation_section}
    {id_settings_section}
    {hr_section}
    {assignment_section}

    <h3>Teacher groups</h3>
    <table border="1" cellpadding="4"><tr><th>ID</th><th>Name</th><th>Members</th></tr>{group_rows}</table>
    <form method="post">
      <input type="hidden" name="action" value="create_group">
      Group name: <input name="group_name"> <button type="submit">Create group</button>
    </form>
    <form method="post">
      <input type="hidden" name="action" value="add_to_group">
      Group ID: <input name="group_id" size="4"> Staff No: <input name="target_staff_no">
      <button type="submit">Add member</button>
    </form>
    <form method="post">
      <input type="hidden" name="action" value="remove_from_group">
      Group ID: <input name="group_id" size="4"> Staff No: <input name="target_staff_no">
      <button type="submit">Remove member</button>
    </form>
    """
    return page("Staff", body)


# ----------------------------------------------------------------------
# departments
# ----------------------------------------------------------------------
@app.route("/departments", methods=["GET", "POST"])
def departments_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add":
                subjects = [s.strip() for s in request.form["subjects"].split(",") if s.strip()]
                code = db.add_department(request.form["name"], subjects=subjects)
                flash(f"Added department {code}.")
            elif action == "assign_hod":
                db.assign_hod(request.form["department_code"], request.form["target_staff_no"])
                flash("HOD assigned.")
        except (PermissionDenied, NotFound, ValueError) as e:
            flash(str(e))

    depts = db.conn.execute("SELECT * FROM departments").fetchall()
    row_htmls = []
    for d in depts:
        hod = db.hod_of(d["code"])
        hod_name = f"{hod['first_name']} {hod['last_name']}" if hod else "Unassigned"
        row_htmls.append(
            f"<tr><td>{d['code']}</td><td>{d['name']}</td>"
            f"<td>{db.department_subjects_list(d['code'])}</td><td>{hod_name}</td></tr>"
        )
    rows = "".join(row_htmls)
    db.close()
    body = f"""
    <table border="1" cellpadding="4"><tr><th>Code</th><th>Name</th><th>Subjects</th><th>HOD</th></tr>{rows}</table>

    <h3>Add department</h3>
    <form method="post">
      <input type="hidden" name="action" value="add">
      Name: <input name="name"> Subjects (comma-separated): <input name="subjects" size="30" placeholder="Biology, Chemistry, Physics">
      <button type="submit">Add</button>
    </form>

    <h3>Assign HOD</h3>
    <form method="post">
      <input type="hidden" name="action" value="assign_hod">
      Department code: <input name="department_code"> Staff No: <input name="target_staff_no">
      <button type="submit">Assign</button>
    </form>
    """
    return page("Departments", body)


# ----------------------------------------------------------------------
# marks
# ----------------------------------------------------------------------
@app.route("/marks", methods=["GET", "POST"])
def marks_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx

    form_number = request.values.get("form_number", "1")
    stream = request.values.get("stream", "White")
    exam_id = request.values.get("exam_id", "1")
    subject = request.values.get("subject", "Maths")

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "bulk_save":
                scores = {}
                for key, val in request.form.items():
                    if key.startswith("score_"):
                        adm = key[len("score_"):]
                        scores[adm] = float(val) if val.strip() != "" else None
                result = db.save_class_marks_bulk(staff_no, request.form["grid_subject"],
                                                   int(request.form["grid_exam_id"]),
                                                   int(request.form["grid_form"]), request.form["grid_stream"],
                                                   scores)
                msg = f"Saved {result['saved']} score(s) as drafts."
                if result["errors"]:
                    msg += f" {len(result['errors'])} row(s) failed: " + \
                           "; ".join(f"{e['adm_no']}: {e['error']}" for e in result["errors"][:3])
                flash(msg)
            elif action == "bulk_submit":
                n = db.submit_class_marks(staff_no, request.form["grid_subject"], int(request.form["grid_exam_id"]),
                                           int(request.form["grid_form"]), request.form["grid_stream"])
                flash(f"Submitted {n} draft score(s) for this subject/class.")
            elif action == "import_csv":
                file = request.files.get("csv_file")
                if not file or not file.filename:
                    flash("Choose a CSV file first.")
                else:
                    import csv, io
                    text = file.read().decode("utf-8-sig")
                    reader = csv.DictReader(io.StringIO(text))
                    scores = {}
                    for row in reader:
                        adm = (row.get("Adm No") or row.get("adm_no") or "").strip()
                        score_str = (row.get("Score") or row.get("score") or "").strip()
                        if adm:
                            scores[adm] = float(score_str) if score_str else None
                    result = db.save_class_marks_bulk(staff_no, request.form["grid_subject"],
                                                       int(request.form["grid_exam_id"]),
                                                       int(request.form["grid_form"]), request.form["grid_stream"],
                                                       scores)
                    flash(f"Imported CSV: saved {result['saved']} score(s) as drafts, "
                          f"{len(result['errors'])} error(s).")
            elif action == "edit_submitted":
                db.edit_submitted_mark(staff_no, request.form["adm_no"], request.form["subject"],
                                        int(request.form["exam_id"]), float(request.form["new_score"]))
                flash("Submitted mark corrected.")
        except (PermissionDenied, NotFound, ValueError) as e:
            flash(str(e))

    fn = int(form_number)
    stream_code = None
    try:
        stream_code = db._resolve_stream_code(fn, stream)
    except NotFound as e:
        flash(str(e))

    # ---- permission-aware context for this class, computed once ----
    is_class_manager = False
    class_teacher_name = "the class teacher"
    my_assigned_subjects: List[str] = []
    if stream_code:
        stream_row = db.conn.execute(
            "SELECT class_teacher_staff_no FROM streams WHERE form_number=? AND code=?", (fn, stream_code)
        ).fetchone()
        if stream_row and stream_row["class_teacher_staff_no"]:
            ct = db.conn.execute(
                "SELECT first_name, last_name, staff_no FROM teachers WHERE staff_no=?",
                (stream_row["class_teacher_staff_no"],),
            ).fetchone()
            if ct:
                class_teacher_name = f"{ct['first_name']} {ct['last_name']} ({ct['staff_no']})"
        is_class_manager = db._can_manage_class(staff_no, fn, stream_code)
        scope = db.teacher_scope(staff_no)
        my_assigned_subjects = [s for s, cc in scope["subject_assignments"] if cc == f"{fn}{stream_code}"]

    can_enter = is_class_manager or bool(my_assigned_subjects)

    # ---- the spreadsheet-style grid: one row per student, editable score cell ----
    grid_html = ""
    if can_enter and stream_code:
        students = db.conn.execute(
            "SELECT adm_no, first_name, last_name FROM students WHERE form_number=? AND stream_code=? "
            "ORDER BY last_name", (fn, stream_code),
        ).fetchall()
        code_for_lookup = subject_code_lookup = None
        from db import subject_code as _subject_code
        subj_code = _subject_code(subject)
        grid_rows = ""
        for s in students:
            mark = db.conn.execute(
                "SELECT score, status FROM marks WHERE student_adm_no=? AND subject_code=? AND exam_id=?",
                (s["adm_no"], subj_code, int(exam_id)),
            ).fetchone()
            value = mark["score"] if mark else ""
            status = mark["status"] if mark else "empty"
            status_color = {"draft": "#EFE6C8", "submitted": "#D9E8DE", "empty": "#fff"}[status]
            locked = mark and mark["status"] == "submitted" and not is_class_manager
            adm_no_val = s["adm_no"]
            cell_style = (
                "width:70px;text-align:center;background:#eee;color:#888;cursor:not-allowed"
                if locked else "width:70px;text-align:center"
            )
            status_label = f"{status} (locked -- contact {class_teacher_name})" if locked else status
            tooltip = "Locked: only the class teacher can edit a submitted mark." if locked else ""
            grid_rows += (
                f"<tr><td class='mono'>{s['adm_no']}</td><td>{s['first_name']} {s['last_name']}</td>"
                f"<td style='background:{status_color}' title='{tooltip}'>"
                f"<input name='score_{adm_no_val}' value='{value}' size='5' "
                f"{'readonly disabled' if locked else ''} style='{cell_style}'></td>"
                f"<td style='font-size:11px;color:var(--ink-muted)'>{status_label}</td></tr>"
            )
        grid_html = f"""
        <h3>Marks entry &amp; correction grid &mdash; {subject} ({form_number}{stream_code}), Exam {exam_id}</h3>
        <p style="font-size:12px;color:var(--ink-muted)">
          {"Type a score for each student and Save All. Submitted rows (green) can also be corrected here since you're the class teacher/supervisor." if is_class_manager else "Type a score for each student and Save All. Submitted rows (green) are locked -- contact " + class_teacher_name + " to request a correction."}
          Blank cells are left untouched when you save.
        </p>
        <form method="post">
          <input type="hidden" name="grid_form" value="{form_number}">
          <input type="hidden" name="grid_stream" value="{stream}">
          <input type="hidden" name="grid_subject" value="{subject}">
          <input type="hidden" name="grid_exam_id" value="{exam_id}">
          <div style="max-height:480px;overflow-y:auto;border:1px solid var(--border);border-radius:8px">
            <table style="margin-bottom:0">
              <thead><tr><th>Adm No</th><th>Name</th><th>Score</th><th>Status</th></tr></thead>
              <tbody>{grid_rows}</tbody>
            </table>
          </div>
          <div style="margin-top:10px">
            <button type="submit" name="action" value="bulk_save">Save All</button>
            <button type="submit" name="action" value="bulk_submit">Submit All Drafts</button>
          </div>
        </form>

        <h4 style="font-family:'Space Grotesk',sans-serif;color:var(--navy-2);font-size:13px">
          Import / export like Excel</h4>
        <a href="{url_for('marks_csv_export')}?form_number={form_number}&stream={stream}&subject={subject}&exam_id={exam_id}"
           style="display:inline-block;background:var(--navy);color:#fff;padding:7px 14px;border-radius:7px;
           text-decoration:none;font-weight:600;font-size:12px;margin-right:10px">
           Download CSV template</a>
        <form method="post" enctype="multipart/form-data" style="display:inline-flex">
          <input type="hidden" name="action" value="import_csv">
          <input type="hidden" name="grid_form" value="{form_number}">
          <input type="hidden" name="grid_stream" value="{stream}">
          <input type="hidden" name="grid_subject" value="{subject}">
          <input type="hidden" name="grid_exam_id" value="{exam_id}">
          <input type="file" name="csv_file" accept=".csv">
          <button type="submit">Import CSV</button>
        </form>
        <p style="font-size:12px;color:var(--ink-muted)">Download the template, edit scores in Excel,
        save as CSV, then import it back here -- column headers "Adm No" and "Score" are required.</p>
        """
    elif not can_enter:
        grid_html = f"""
        <div class="flash" style="background:#FFF6E0;color:#8a6d1a;border-color:#F0DFA6">
          You are not assigned to teach any subject in class {form_number}{stream_code or stream}.
          Ask an executive or that form's staffing manager to assign you before you can enter marks here.
        </div>
        """

    db.close()

    body = f"""
    <form method="get">
      Form: <input name="form_number" size="2" value="{form_number}">
      Stream: <input name="stream" value="{stream}">
      Subject: <input name="subject" value="{subject}">
      Exam ID: <input name="exam_id" size="4" value="{exam_id}">
      <button type="submit">Load grid</button>
    </form>

    {grid_html}
    """
    return page("Marks", body)


@app.route("/marks/export.csv")
def marks_csv_export():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    import csv, io
    fn = int(request.args["form_number"])
    stream = request.args["stream"]
    subject = request.args["subject"]
    exam_id = int(request.args["exam_id"])
    from db import subject_code as _subject_code
    subj_code = _subject_code(subject)
    stream_code = db._resolve_stream_code(fn, stream)
    students = db.conn.execute(
        "SELECT adm_no, first_name, last_name FROM students WHERE form_number=? AND stream_code=? "
        "ORDER BY last_name", (fn, stream_code),
    ).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Adm No", "Name", "Score"])
    for s in students:
        mark = db.conn.execute(
            "SELECT score FROM marks WHERE student_adm_no=? AND subject_code=? AND exam_id=?",
            (s["adm_no"], subj_code, exam_id),
        ).fetchone()
        writer.writerow([s["adm_no"], f"{s['first_name']} {s['last_name']}", mark["score"] if mark else ""])
    db.close()
    from flask import Response
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={subject}_{fn}{stream_code}_exam{exam_id}.csv"},
    )


# ----------------------------------------------------------------------
# attendance
# ----------------------------------------------------------------------
@app.route("/attendance", methods=["GET"])
def attendance_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    form_number = request.args.get("form_number", "1")
    stream = request.args.get("stream", "White")
    report = []
    try:
        report = db.class_attendance_report(int(form_number), stream)
    except NotFound as e:
        flash(str(e))
    db.close()
    rows = "".join(f"<tr><td>{r['adm_no']}</td><td>{r['name']}</td><td>{r['attendance_rate']}</td></tr>"
                    for r in report)
    body = f"""
    <form method="get">
      Form: <input name="form_number" size="2" value="{form_number}">
      Stream: <input name="stream" value="{stream}">
      <button type="submit">View report</button>
    </form>
    <table border="1" cellpadding="4"><tr><th>Adm No</th><th>Name</th><th>Attendance %</th></tr>{rows}</table>
    <p>Record attendance from a student's own page (Students &rarr; View).</p>
    """
    return page("Attendance", body)


# ----------------------------------------------------------------------
# timetable
# ----------------------------------------------------------------------
@app.route("/timetable", methods=["GET", "POST"])
def timetable_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx

    if request.method == "POST":
        try:
            db.add_timetable_entry(int(request.form["form_number"]), request.form["stream"],
                                    request.form["day_of_week"], int(request.form["period"]),
                                    request.form["subject"], request.form["target_staff_no"])
            flash("Timetable entry saved.")
        except ValueError as e:
            flash(f"Could not save: {e}")
        except NotFound as e:
            flash(str(e))

    form_number = request.values.get("form_number", "1")
    stream = request.values.get("stream", "White")
    grid_data = None
    try:
        grid_data = db.get_timetable_grid(int(form_number), stream)
    except NotFound as e:
        flash(str(e))
    db.close()

    grid_html = ""
    if grid_data:
        header_cells = "".join(f"<th>{d}</th>" for d in grid_data["days"])
        body_rows = ""
        for p in grid_data["periods"]:
            cells = ""
            for d in grid_data["days"]:
                cell = grid_data["grid"][d][p]
                if cell:
                    cells += (
                        f'<td style="vertical-align:top;padding:6px">'
                        f'<div style="background:var(--gold-soft);border-radius:6px;padding:6px 8px;min-height:44px">'
                        f'<div style="font-weight:600;font-size:12px;color:var(--navy)">{cell["subject"]}</div>'
                        f'<div class="mono" style="font-size:10px;color:var(--ink-muted);margin-top:10px;'
                        f'text-align:right" title="{cell["teacher_name"]}">{cell["teacher_initials"]}</div>'
                        f'</div></td>'
                    )
                else:
                    cells += '<td style="padding:6px"><div style="min-height:44px;border:1px dashed var(--border);border-radius:6px"></div></td>'
            body_rows += f"<tr><td style='font-weight:600;color:var(--ink-muted);padding:6px'>P{p}</td>{cells}</tr>"
        grid_html = (
            f'<table style="display:table;white-space:normal"><thead><tr><th></th>{header_cells}</tr></thead>'
            f'<tbody>{body_rows}</tbody></table>'
        )

    body = f"""
    <form method="get">
      Form: <input name="form_number" size="2" value="{form_number}">
      Stream: <input name="stream" value="{stream}">
      <button type="submit">View</button>
    </form>
    {grid_html}
    <p style="font-size:12px;color:var(--ink-muted)">Each box shows the subject with the teacher's
    initials in the bottom-right corner.</p>

    <h3>Add timetable entry</h3>
    <form method="post">
      Form: <input name="form_number" size="2" value="{form_number}">
      Stream: <input name="stream" value="{stream}">
      Day:
      <select name="day_of_week">
        <option>Mon</option><option>Tue</option><option>Wed</option><option>Thu</option><option>Fri</option>
      </select>
      Period: <input name="period" size="2" value="1">
      Subject: <input name="subject" value="Maths">
      Teacher Staff No: <input name="target_staff_no">
      <button type="submit">Save</button>
    </form>
    <p style="font-size:12px;color:var(--ink-muted)">Scheduling refuses to double-book a teacher into
    two classes at the same day/period.</p>
    """
    return page("Timetable", body)


# ----------------------------------------------------------------------
# school calendar
# ----------------------------------------------------------------------
@app.route("/calendar", methods=["GET", "POST"])
def calendar_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add":
                form_no = request.form.get("form_number") or None
                db.add_event(request.form["title"], request.form["date"], staff_no,
                             description=request.form.get("description", ""),
                             category=request.form.get("category", "general"),
                             end_date=request.form.get("end_date") or None,
                             form_number=int(form_no) if form_no else None)
                flash("Event added.")
            elif action == "delete":
                db.delete_event(int(request.form["event_id"]), staff_no)
                flash("Event deleted.")
        except (PermissionDenied, NotFound, ValueError) as e:
            flash(str(e))

    start = request.args.get("start")
    end = request.args.get("end")
    events = db.list_events(start_date=start, end_date=end)
    db.close()

    rows = "".join(
        f"<tr><td>{e['date']}</td><td>{e['end_date'] or ''}</td><td>{e['category']}</td>"
        f"<td>{e['title']}</td><td>{e['description'] or ''}</td>"
        f"<td>{'Form ' + str(e['form_number']) if e['form_number'] else 'Whole school'}</td>"
        f"<td><form method='post' style='display:inline'>"
        f"<input type='hidden' name='action' value='delete'>"
        f"<input type='hidden' name='event_id' value='{e['id']}'>"
        f"<button type='submit'>Delete</button></form></td></tr>"
        for e in events
    )
    body = f"""
    <form method="get">
      From: <input name="start" placeholder="2026-01-01">
      To: <input name="end" placeholder="2026-12-31">
      <button type="submit">Filter</button>
    </form>
    <table border="1" cellpadding="4">
      <tr><th>Date</th><th>End Date</th><th>Category</th><th>Title</th><th>Description</th><th>Audience</th><th></th></tr>
      {rows}
    </table>

    <h3>Add event</h3>
    <form method="post">
      <input type="hidden" name="action" value="add">
      Title: <input name="title">
      Date: <input name="date" placeholder="2026-05-01">
      End date (optional): <input name="end_date" placeholder="2026-05-03">
      Category:
      <select name="category">
        <option>general</option><option>holiday</option><option>exam</option>
        <option>meeting</option><option>activity</option>
      </select>
      Form (optional, blank = whole school): <input name="form_number" size="2">
      Description: <input name="description" size="30">
      <button type="submit">Add</button>
    </form>
    """
    return page("School Calendar", body)


# ----------------------------------------------------------------------
# fees
# ----------------------------------------------------------------------
@app.route("/fees", methods=["GET", "POST"])
def fees_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx

    if not db.has_full_oversight(staff_no):
        db.close()
        body = """
        <div class="flash" style="background:#FFF6E0;color:#8a6d1a;border-color:#F0DFA6">
          The financial system is only visible to the Principal, a Deputy Principal, or the
          Dean of Studies. If a parent needs to know a balance, ask that student's class teacher
          to look it up from the student's own profile page.
        </div>
        """
        return page("Fees", body)

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "set_structure":
                db.set_fee_structure(int(request.form["form_number"]), int(request.form["term"]),
                                      int(request.form["year"]), float(request.form["amount"]))
                flash("Fee structure set.")
            elif action == "generate_invoices":
                n = db.generate_invoices_for_form(int(request.form["form_number"]), int(request.form["term"]),
                                                   int(request.form["year"]))
                flash(f"Generated {n} invoices.")
            elif action == "add_expense":
                receipt_path = None
                file = request.files.get("receipt_file")
                if file and file.filename:
                    import uuid
                    safe_name = f"{uuid.uuid4().hex}_{os.path.basename(file.filename)}"
                    file.save(os.path.join(UPLOAD_DIR, safe_name))
                    receipt_path = safe_name
                db.add_expense(request.form["category"], float(request.form["amount"]), request.form["date"],
                                request.form.get("description", ""), recorded_by=staff_no,
                                receipt_path=receipt_path)
                flash("Expense recorded" + (" with receipt." if receipt_path else "."))
            elif action == "add_channel":
                db.add_payment_channel(request.form["code"], request.form["name"], request.form.get("kind", "bank"))
                flash("Payment channel added.")
            elif action == "simulate_payment":
                result = db.record_payment_via_channel(
                    request.form["channel_code"], request.form["reference"], request.form["adm_no"],
                    float(request.form["amount"]), request.form["date"],
                    int(request.form["term"]), int(request.form["year"]),
                )
                if result["status"] == "duplicate_ignored":
                    flash(f"Reference already processed -- no double-credit applied. Live balance: {result['balance']}")
                else:
                    flash(f"Payment auto-detected and recorded. Live balance: {result['balance']}")
            elif action == "add_pledge":
                db.add_pledge(request.form["adm_no"], float(request.form["amount"]),
                              request.form.get("expected_date") or None, request.form.get("notes", ""))
                flash("Pledge recorded.")
        except (PermissionDenied, NotFound, ValueError) as e:
            flash(str(e))

    term = request.values.get("term", "1")
    year = request.values.get("year", "2026")
    summary = db.financial_summary(int(term), int(year))
    arrears = db.arrears_report(int(term), int(year))
    channels = db.list_payment_channels()
    expenses = db.list_expenses(int(year))
    db.close()

    balance_rows = "".join(
        f"<tr><td class='mono'>{o['adm_no']}</td><td>{o['name']}</td><td>{o['class_code']}</td>"
        f"<td>{o['billed']}</td><td>{o['paid']}</td><td>{o['balance']}</td>"
        f"<td>{o['pledged']}</td><td>{o['unpledged_balance']}</td></tr>"
        for o in arrears
    )
    channel_rows = "".join(f"<tr><td class='mono'>{c['code']}</td><td>{c['name']}</td><td>{c['kind']}</td></tr>"
                            for c in channels)
    expense_rows = "".join(
        f"<tr><td>{e['date']}</td><td>{e['category']}</td><td>{e['amount']}</td><td>{e['description'] or ''}</td>"
        f"<td>{'<a href=' + chr(39) + url_for('receipt_file', filename=e['receipt_path']) + chr(39) + '>View</a>' if e['receipt_path'] else '-'}</td></tr>"
        for e in expenses
    )

    body = f"""
    <h3>Financial summary</h3>
    <form method="get">
      Term: <input name="term" size="2" value="{term}"> Year: <input name="year" size="4" value="{year}">
      <button type="submit">View</button>
    </form>
    {stat_cards(summary)}
    <a href="{url_for('financial_statement_pdf')}?term={term}&year={year}"
       style="display:inline-block;background:var(--navy);color:#fff;padding:8px 16px;border-radius:7px;
       text-decoration:none;font-weight:600;font-size:13px;margin-bottom:16px">
       Download audit-ready financial statement (PDF)</a>

    <h3>Arrears &amp; pledges ({len(arrears)} students owing)</h3>
    <table border="1" cellpadding="4">
      <tr><th>Adm No</th><th>Name</th><th>Class</th><th>Billed</th><th>Paid</th><th>Balance</th>
          <th>Pledged</th><th>Unpledged</th></tr>
      {balance_rows}
    </table>

    <h3>Payment channels (bank, paybill, till...)</h3>
    <table border="1" cellpadding="4"><tr><th>Code</th><th>Name</th><th>Kind</th></tr>{channel_rows}</table>
    <form method="post">
      <input type="hidden" name="action" value="add_channel">
      Code: <input name="code" size="8" placeholder="EQTY"> Name: <input name="name" placeholder="Equity Bank">
      Kind: <select name="kind"><option>bank</option><option>paybill</option><option>till</option></select>
      <button type="submit">Add channel</button>
    </form>

    <h3>Simulate an incoming payment (auto-detection)</h3>
    <p style="font-size:12px;color:var(--ink-muted)">
      This mimics what a real bank/paybill webhook would POST when a parent pays directly.
      The balance updates immediately, and re-sending the same reference is safely ignored
      (no double-crediting).
    </p>
    <form method="post">
      <input type="hidden" name="action" value="simulate_payment">
      Channel code: <input name="channel_code" size="8"> Reference: <input name="reference" size="14">
      Adm No: <input name="adm_no"> Amount: <input name="amount" size="8">
      Date: <input name="date" placeholder="2026-01-15"> Term: <input name="term" size="2" value="{term}">
      Year: <input name="year" size="4" value="{year}">
      <button type="submit">Simulate payment received</button>
    </form>

    <h3>Record a pledge</h3>
    <form method="post">
      <input type="hidden" name="action" value="add_pledge">
      Adm No: <input name="adm_no"> Amount: <input name="amount" size="8">
      Expected date: <input name="expected_date" placeholder="2026-02-01"> Notes: <input name="notes" size="24">
      <button type="submit">Record pledge</button>
    </form>

    <h3>Set fee structure</h3>
    <form method="post">
      <input type="hidden" name="action" value="set_structure">
      Form: <input name="form_number" size="2" value="1"> Term: <input name="term" size="2" value="1">
      Year: <input name="year" size="4" value="2026"> Amount: <input name="amount" size="8" value="45000">
      <button type="submit">Set</button>
    </form>

    <h3>Generate invoices for a form</h3>
    <form method="post">
      <input type="hidden" name="action" value="generate_invoices">
      Form: <input name="form_number" size="2" value="1"> Term: <input name="term" size="2" value="1">
      Year: <input name="year" size="4" value="2026">
      <button type="submit">Generate</button>
    </form>

    <h3>Record an expense</h3>
    <form method="post" enctype="multipart/form-data">
      <input type="hidden" name="action" value="add_expense">
      Category: <input name="category"> Amount: <input name="amount" size="8">
      Date: <input name="date" placeholder="2026-01-20"> Description: <input name="description" size="30">
      Receipt (optional): <input type="file" name="receipt_file">
      <button type="submit">Record</button>
    </form>

    <h3>Expense ledger ({year})</h3>
    <table border="1" cellpadding="4">
      <tr><th>Date</th><th>Category</th><th>Amount</th><th>Description</th><th>Receipt</th></tr>
      {expense_rows or '<tr><td colspan="5">No expenses recorded yet.</td></tr>'}
    </table>
    <p style="font-size:12px;color:var(--ink-muted)">Manual payments and receipts are recorded from a
    student's own page (Students &rarr; View).</p>
    """
    return page("Fees", body)


@app.route("/uploads/receipts/<path:filename>")
def receipt_file(filename):
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    oversight = db.has_full_oversight(staff_no)
    db.close()
    if not oversight:
        flash("Only an executive may view uploaded receipts.")
        return redirect(url_for("dashboard"))
    from flask import send_from_directory
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)


@app.route("/reports/financial_statement")
def financial_statement_pdf():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    if not db.has_full_oversight(staff_no):
        db.close()
        flash("The financial statement is only available to the Principal, a Deputy Principal, or the Dean of Studies.")
        return redirect(url_for("reports_view"))
    term, year = int(request.args["term"]), int(request.args["year"])
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    from school_reports import generate_financial_statement
    generate_financial_statement(db, term, year, tmp.name)
    db.close()
    return send_file(tmp.name, as_attachment=True)


# ----------------------------------------------------------------------
# exams
# ----------------------------------------------------------------------
@app.route("/exams", methods=["GET", "POST"])
def exams_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add_exam":
                if not db.has_full_oversight(staff_no):
                    flash("Only an executive (Principal/Deputy/Dean) may create a new exam.")
                else:
                    exam_id = db.add_exam(request.form["name"], int(request.form["term"]), int(request.form["year"]),
                                           request.form.get("category", "General"),
                                           float(request.form.get("weight", 1.0)))
                    flash(f"Exam created, id={exam_id}.")
            elif action == "schedule_paper":
                if not db.has_full_oversight(staff_no):
                    flash("Only an executive (Principal/Deputy/Dean) may schedule an exam paper.")
                else:
                    db.schedule_exam_paper(int(request.form["exam_id"]), request.form["subject"],
                                            request.form["date"], request.form.get("start_time"),
                                            request.form.get("end_time"))
                    flash("Paper scheduled.")
            elif action == "publish_exam":
                db.publish_exam_results(staff_no, int(request.form["exam_id"]))
                flash("Results published. Marks for this exam are now locked -- even class teachers "
                      "cannot edit them until an executive unpublishes.")
            elif action == "unpublish_exam":
                db.unpublish_exam_results(staff_no, int(request.form["exam_id"]))
                flash("Results unpublished. Class teachers can edit marks for this exam again.")
        except (PermissionDenied, NotFound, ValueError) as e:
            flash(str(e))

    is_oversight = db.has_full_oversight(staff_no)
    exams = db.conn.execute("SELECT * FROM exams ORDER BY year DESC, term, id").fetchall()
    db_published = {e["id"]: db.is_exam_published(e["id"]) for e in exams}

    breakdown_html = ""
    gb_form = request.values.get("gb_form", "1")
    gb_subject = request.values.get("gb_subject", "Maths")
    gb_exam = request.values.get("gb_exam", "1")
    if request.values.get("gb_go"):
        try:
            gb = db.subject_grade_breakdown(int(gb_form), gb_subject, int(gb_exam))
            grade_headers = "".join(f"<th>{g}</th>" for g in gb["grade_letters"])

            def _row(label, s, is_total=False):
                grade_cells = "".join(f"<td>{s['grade_counts'][g]}</td>" for g in gb["grade_letters"])
                dev_marks = s.get("mean_marks_deviation")
                dev_points = s.get("mean_points_deviation")
                dev_marks_str = f"{dev_marks:+.2f}" if dev_marks is not None else "-"
                dev_points_str = f"{dev_points:+.2f}" if dev_points is not None else "-"
                teacher_cells = "" if is_total else (
                    f"<td>{s['subject_teacher']}"
                    f"{' (' + s['subject_teacher_staff_no'] + ')' if s['subject_teacher_staff_no'] != '-' else ''}</td>"
                )
                row_style = " style='font-weight:600;background:#EAF2EC'" if is_total else ""
                return (
                    f"<tr{row_style}><td>{label}</td>{grade_cells}<td>{s['entries']}</td>"
                    f"<td>{s['mean_marks'] if s['mean_marks'] is not None else '-'}</td>"
                    f"<td>{dev_marks_str}</td>"
                    f"<td>{s['mean_points'] if s['mean_points'] is not None else '-'}</td>"
                    f"<td>{dev_points_str}</td>"
                    f"<td>{s['mean_grade']}</td>{teacher_cells}</tr>"
                )

            body_rows = "".join(_row(s["class_code"], s) for s in gb["streams"])
            total_row = _row(f"Form {gb['form_number']} Total", gb["totals"], is_total=True)

            breakdown_html = f"""
            <div id="grade-breakdown-wrap" style="overflow-x:auto">
              <style>
                #grade-breakdown-wrap table {{ display: revert; width: auto; min-width: 100%; table-layout: auto; white-space: normal; }}
                #grade-breakdown-wrap table thead, #grade-breakdown-wrap table tr {{ display: revert; width: auto; table-layout: auto; }}
                #grade-breakdown-wrap th, #grade-breakdown-wrap td {{ white-space: nowrap; padding: 6px 8px; }}
              </style>
              <table border="1" cellpadding="4">
                <tr><th>Form</th>{grade_headers}<th>Entries</th><th>Mean</th>
                    <th>Dev.</th><th>Pts</th><th>Pts Dev.</th>
                    <th>Grade</th><th>Subject Teacher</th></tr>
                {body_rows}
                {total_row}
              </table>
            </div>
            """
        except (NotFound, ValueError) as e:
            flash(str(e))

    db.close()
    rows = "".join(
        f"<tr><td>{e['id']}</td><td>{e['name']}</td><td>{e['term']}</td><td>{e['year']}</td>"
        f"<td>{e['category']}</td><td>{e['weight']}</td>"
        f"<td>{'<span class=' + chr(39) + 'badge oversight' + chr(39) + '>PUBLISHED</span>' if db_published.get(e['id']) else '-'}</td></tr>"
        for e in exams
    )
    creation_section = """
    <h3>Add exam</h3>
    <form method="post">
      <input type="hidden" name="action" value="add_exam">
      Name: <input name="name"> Term: <input name="term" size="2" value="1">
      Year: <input name="year" size="4" value="2026"> Category: <input name="category" value="General">
      Weight: <input name="weight" size="4" value="1.0">
      <button type="submit">Create</button>
    </form>

    <h3>Schedule an exam paper</h3>
    <form method="post">
      <input type="hidden" name="action" value="schedule_paper">
      Exam ID: <input name="exam_id" size="4"> Subject: <input name="subject" value="Maths">
      Date: <input name="date" placeholder="2026-04-20"> Start: <input name="start_time" placeholder="08:00">
      End: <input name="end_time" placeholder="10:00">
      <button type="submit">Schedule</button>
    </form>

    <h3>Publish / unpublish results</h3>
    <p style="font-size:12px;color:var(--ink-muted)">Publishing locks marks for that exam completely --
    even the class teacher can no longer edit them, and parents can see the report card. Unpublish to
    make corrections, then re-publish when done.</p>
    <form method="post">
      <input type="hidden" name="action" value="publish_exam">
      Exam ID: <input name="exam_id" size="4">
      <button type="submit">Publish results</button>
    </form>
    <form method="post">
      <input type="hidden" name="action" value="unpublish_exam">
      Exam ID: <input name="exam_id" size="4">
      <button type="submit">Unpublish results</button>
    </form>
    """ if is_oversight else """
    <div class="flash" style="background:#FFF6E0;color:#8a6d1a;border-color:#F0DFA6">
      Creating exams, scheduling papers, and publishing/unpublishing results are available to the
      Principal, a Deputy Principal, or the Dean of Studies only.
    </div>
    """
    body = f"""
    <table border="1" cellpadding="4"><tr><th>ID</th><th>Name</th><th>Term</th><th>Year</th><th>Category</th><th>Weight</th><th>Status</th></tr>{rows}</table>

    {creation_section}

    <h3>Grade breakdown</h3>
    <p style="font-size:12px;color:var(--ink-muted)">Per-stream grade distribution for one subject,
    with each stream's deviation from the form average and who teaches it there.</p>
    <form method="get">
      <input type="hidden" name="gb_go" value="1">
      Form: <input name="gb_form" size="2" value="{gb_form}">
      Subject: <input name="gb_subject" value="{gb_subject}">
      Exam ID: <input name="gb_exam" size="4" value="{gb_exam}">
      <button type="submit">View breakdown</button>
    </form>
    {breakdown_html}
    """
    return page("Exams", body)


# ----------------------------------------------------------------------
# analytics: most improved, remedial flagging, performance chart
# ----------------------------------------------------------------------
@app.route("/analytics", methods=["GET", "POST"])
def analytics_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    oversight = db.has_full_oversight(staff_no)

    if request.method == "POST" and request.form.get("action") == "send_reminder":
        if not oversight:
            flash("Only an executive may send missing-marks reminders.")
        else:
            db.send_missing_marks_reminder(request.form["target_staff_no"], request.form["subject"],
                                            request.form["class_code"], int(request.form["missing_count"]))
            flash("Reminder queued.")

    form_number = int(request.args.get("form_number", 1))
    exam_before = request.args.get("exam_before", "")
    exam_after = request.args.get("exam_after", "")
    threshold = request.args.get("threshold", "50")
    exam_for_threshold = request.args.get("exam_for_threshold", "")
    mm_exam = request.args.get("mm_exam", "")
    risk_threshold = request.args.get("risk_threshold", "5")

    improved_rows = ""
    if exam_before and exam_after:
        improved = db.most_improved(form_number, int(exam_before), int(exam_after))
        improved_rows = "".join(
            f"<tr><td class='mono'>{r['adm_no']}</td><td>{r['name']}</td><td>{r['class_code']}</td>"
            f"<td>{r['mean_before']}</td><td>{r['mean_after']}</td>"
            f"<td style='color:{'green' if r['improvement']>=0 else 'var(--danger)'}'>{r['improvement']:+.2f}</td></tr>"
            for r in improved
        ) or "<tr><td colspan='6'>No data for both exams yet.</td></tr>"

    remedial_rows = ""
    if exam_for_threshold:
        flagged = db.students_below_threshold(form_number, int(exam_for_threshold), float(threshold))
        remedial_rows = "".join(
            f"<tr><td class='mono'>{r['adm_no']}</td><td>{r['name']}</td><td>{r['class_code']}</td><td>{r['score']}</td></tr>"
            for r in flagged
        ) or "<tr><td colspan='4'>No students below that threshold.</td></tr>"

    # ---- Missing marks dashboard (oversight only) ----
    missing_marks_html = ""
    if oversight:
        mm_rows = ""
        if mm_exam:
            dash = db.missing_marks_dashboard(int(mm_exam))
            for d in dash:
                mm_rows += (
                    f"<tr><td>{d['class_code']}</td><td>{d['subject']}</td><td>{d['teacher_name']}</td>"
                    f"<td>{d['missing_count']}</td>"
                    f"<td><form method='post' style='margin:0'>"
                    f"<input type='hidden' name='action' value='send_reminder'>"
                    f"<input type='hidden' name='target_staff_no' value='{d['staff_no']}'>"
                    f"<input type='hidden' name='subject' value='{d['subject']}'>"
                    f"<input type='hidden' name='class_code' value='{d['class_code']}'>"
                    f"<input type='hidden' name='missing_count' value='{d['missing_count']}'>"
                    f"<button type='submit'>Send reminder</button></form></td></tr>"
                )
            mm_rows = mm_rows or "<tr><td colspan='5'>Nothing missing -- every assigned class/subject is fully entered.</td></tr>"
        missing_marks_html = f"""
        <h3>Missing marks dashboard</h3>
        <form method="get">
          Exam ID: <input name="mm_exam" size="4" value="{mm_exam}">
          <button type="submit">Check</button>
        </form>
        {"<table border='1' cellpadding='4'><tr><th>Class</th><th>Subject</th><th>Teacher</th><th>Missing</th><th></th></tr>" + mm_rows + "</table>" if mm_exam else ""}
        """

    # ---- At-risk students by demerit points (oversight only) ----
    at_risk_html = ""
    if oversight:
        risk_list = db.at_risk_students(form_number, int(risk_threshold))
        risk_rows = "".join(
            f"<tr><td class='mono'>{r['adm_no']}</td><td>{r['name']}</td><td>{r['class_code']}</td>"
            f"<td>{r['demerit_points']}</td></tr>"
            for r in risk_list
        ) or "<tr><td colspan='4'>No students at or above that threshold.</td></tr>"
        at_risk_html = f"""
        <h3>At-risk students (demerit points)</h3>
        <form method="get">
          Form: <input name="form_number" size="2" value="{form_number}">
          At or above: <input name="risk_threshold" size="4" value="{risk_threshold}"> points
          <button type="submit">Check</button>
        </form>
        <table border="1" cellpadding="4"><tr><th>Adm No</th><th>Name</th><th>Class</th><th>Points</th></tr>{risk_rows}</table>
        """

    db.close()

    body = f"""
    <h3>Most improved students</h3>
    <form method="get">
      <input type="hidden" name="threshold" value="{threshold}">
      Form: <input name="form_number" size="2" value="{form_number}">
      Exam before: <input name="exam_before" size="4" value="{exam_before}">
      Exam after: <input name="exam_after" size="4" value="{exam_after}">
      <button type="submit">Compare</button>
    </form>
    {"<table border='1' cellpadding='4'><tr><th>Adm No</th><th>Name</th><th>Class</th><th>Mean Before</th><th>Mean After</th><th>Change</th></tr>" + improved_rows + "</table>" if improved_rows else ""}

    <h3>Remedial flagging (below a score threshold)</h3>
    <form method="get">
      <input type="hidden" name="exam_before" value="{exam_before}">
      <input type="hidden" name="exam_after" value="{exam_after}">
      Form: <input name="form_number" size="2" value="{form_number}">
      Exam: <input name="exam_for_threshold" size="4" value="{exam_for_threshold}">
      Below score: <input name="threshold" size="4" value="{threshold}">
      <button type="submit">Flag</button>
    </form>
    {"<table border='1' cellpadding='4'><tr><th>Adm No</th><th>Name</th><th>Class</th><th>Score</th></tr>" + remedial_rows + "</table>" if remedial_rows else ""}

    <h3>Performance chart: student vs class average</h3>
    <form method="get" action="{url_for('performance_chart')}" target="_blank">
      Adm No: <input name="adm_no"> Exam ID: <input name="exam_id" size="4">
      <button type="submit">View chart</button>
    </form>

    {missing_marks_html}
    {at_risk_html}
    """
    return page("Analytics", body)


@app.route("/analytics/chart")
def performance_chart():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    adm_no = request.args["adm_no"]
    exam_id = int(request.args["exam_id"])

    marks = db.get_marks_for_student(adm_no, exam_id)
    student = db.conn.execute("SELECT form_number, stream_code FROM students WHERE adm_no=?", (adm_no,)).fetchone()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import io

    subjects, scores, class_avgs = [], [], []
    if student:
        for m in marks:
            subjects.append(m["subject_name"])
            scores.append(m["score"])
            class_scores = db.conn.execute(
                """SELECT AVG(m2.score) AS avg FROM marks m2 JOIN students s2 ON s2.adm_no = m2.student_adm_no
                   WHERE m2.subject_code=? AND m2.exam_id=? AND s2.form_number=? AND s2.stream_code=?""",
                (m["subject_code"], exam_id, student["form_number"], student["stream_code"]),
            ).fetchone()["avg"]
            class_avgs.append(round(class_scores, 2) if class_scores else 0)
    db.close()

    fig, ax = plt.subplots(figsize=(7, 4))
    x = range(len(subjects))
    width = 0.35
    ax.bar([i - width / 2 for i in x], scores, width, label="Student", color="#2B3A55")
    ax.bar([i + width / 2 for i in x], class_avgs, width, label="Class Average", color="#C9A227")
    ax.set_xticks(list(x))
    ax.set_xticklabels(subjects, rotation=30, ha="right")
    ax.set_ylabel("Score (%)")
    ax.set_title(f"{adm_no}: Performance vs Class Average")
    ax.legend()
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# ----------------------------------------------------------------------
# in-app staff messaging
# ----------------------------------------------------------------------
@app.route("/messages", methods=["GET", "POST"])
def messages_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "compose":
                db.send_message(staff_no, request.form["recipient"], request.form["subject"],
                                 request.form["body"])
                flash("Message sent.")
            elif action == "mark_read":
                db.mark_message_read(int(request.form["message_id"]), staff_no)
        except NotFound as e:
            flash(str(e))

    tab = request.values.get("tab", "inbox")
    if tab == "sent":
        messages = db.sent_messages(staff_no)
        col_label = "To"
    else:
        messages = db.inbox(staff_no)
        col_label = "From"
    unread = db.unread_count(staff_no)
    db.close()

    rows = "".join(
        f"<tr style='{'font-weight:600' if tab=='inbox' and not m['read_at'] else ''}'>"
        f"<td>{m['sent_at'][:16]}</td><td>{m['first_name']} {m['last_name']}</td>"
        f"<td>{m['subject']}</td><td>{m['body']}</td>"
        + (f"<td><form method='post' style='margin:0'><input type='hidden' name='action' value='mark_read'>"
           f"<input type='hidden' name='message_id' value='{m['id']}'>"
           f"<button type='submit'>{'Mark read' if not m['read_at'] else 'Read'}</button></form></td>"
           if tab == "inbox" else "<td></td>")
        + "</tr>"
        for m in messages
    )

    body = f"""
    <p>Unread messages: <b>{unread}</b></p>
    <p>
      <a href="{url_for('messages_view')}?tab=inbox" style="margin-right:12px">Inbox</a>
      <a href="{url_for('messages_view')}?tab=sent">Sent</a>
    </p>
    <table border="1" cellpadding="4">
      <tr><th>When</th><th>{col_label}</th><th>Subject</th><th>Message</th><th></th></tr>
      {rows or '<tr><td colspan="5">Nothing here yet.</td></tr>'}
    </table>

    <h3>Compose</h3>
    <form method="post">
      <input type="hidden" name="action" value="compose">
      To (Staff No): <input name="recipient"> Subject: <input name="subject" size="20">
      Message: <input name="body" size="40">
      <button type="submit">Send</button>
    </form>
    """
    return page("Messages", body)


# ----------------------------------------------------------------------
# alumni
# ----------------------------------------------------------------------
@app.route("/alumni", methods=["GET", "POST"])
def alumni_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx

    if request.method == "POST" and request.form.get("action") == "update_contact":
        try:
            db.update_alumni_contact(request.form["adm_no"], email=request.form.get("email") or None,
                                      phone=request.form.get("phone") or None)
            flash("Contact updated.")
        except NotFound as e:
            flash(str(e))

    year = request.args.get("year")
    alumni = db.get_alumni(int(year)) if year else db.get_alumni()
    db.close()
    rows = "".join(
        f"<tr><td>{a['adm_no']}</td><td>{a['first_name']} {a['last_name']}</td><td>{a['graduation_year']}</td>"
        f"<td>{a['final_class']}</td><td>{a['contact_email'] or ''}</td><td>{a['contact_phone'] or ''}</td></tr>"
        for a in alumni
    )
    body = f"""
    <form method="get">
      Graduation year (optional): <input name="year" size="4">
      <button type="submit">Filter</button>
    </form>
    <table border="1" cellpadding="4">
      <tr><th>Adm No</th><th>Name</th><th>Year</th><th>Final Class</th><th>Email</th><th>Phone</th></tr>{rows}
    </table>

    <h3>Update alumni contact</h3>
    <form method="post">
      <input type="hidden" name="action" value="update_contact">
      Adm No: <input name="adm_no"> Email: <input name="email"> Phone: <input name="phone">
      <button type="submit">Update</button>
    </form>
    """
    return page("Alumni", body)


# ----------------------------------------------------------------------
# academic setup (subjects & streams)
# ----------------------------------------------------------------------
@app.route("/academic-setup", methods=["GET", "POST"])
def academic_setup_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add_subject":
                code = db.add_subject(request.form["name"])
                flash(f"Added subject, code={code}.")
            elif action == "remove_subject":
                db.remove_subject(request.form["name_or_code"])
                flash("Subject removed.")
            elif action == "add_stream":
                db.add_stream(int(request.form["form_number"]), request.form["name"])
                flash("Stream added.")
            elif action == "remove_stream":
                reassign = request.form.get("reassign_to") or None
                db.remove_stream(int(request.form["form_number"]), request.form["name_or_code"],
                                  reassign_to=reassign)
                flash("Stream removed.")
            elif action == "toggle_feature":
                db.set_feature_enabled(request.form["feature"], request.form["enabled"] == "1", staff_no)
                flash(f"{request.form['feature']} is now {'enabled' if request.form['enabled']=='1' else 'disabled'}.")
            elif action == "toggle_system_lock":
                db.set_system_locked(request.form["locked"] == "1", staff_no)
                flash("System " + ("locked." if request.form["locked"] == "1" else "unlocked."))
            elif action == "toggle_account_lock":
                db.set_account_locked(request.form["target_staff_no"], request.form["locked"] == "1", staff_no)
                flash("Account " + ("locked." if request.form["locked"] == "1" else "unlocked."))
            elif action == "generate_otp":
                otp = db.generate_otp_for(request.form["target_staff_no"], staff_no)
                flash(f"One-time code for {request.form['target_staff_no']}: {otp} "
                      f"(valid 15 minutes, single use -- enter it at /reset-password)")
            elif action == "bulk_renumber_students":
                form_no = request.form.get("form_number") or None
                mapping = db.bulk_renumber_students(
                    staff_no, request.form["prefix"], int(request.form["padding"]), int(request.form["start"]),
                    form_number=int(form_no) if form_no else None,
                )
                flash(f"Renumbered {len(mapping)} student(s).")
            elif action == "bulk_renumber_staff":
                mapping = db.bulk_renumber_staff(
                    staff_no, request.form["prefix"], int(request.form["padding"]), int(request.form["start"]),
                )
                flash(f"Renumbered {len(mapping)} staff member(s).")
        except (NotFound, ValueError, PermissionDenied) as e:
            flash(str(e))

    subjects = db.list_subjects()
    forms_streams = {
        n: db.conn.execute("SELECT code, name FROM streams WHERE form_number=? ORDER BY code", (n,)).fetchall()
        for n in (1, 2, 3, 4)
    }
    feature_states = db.all_feature_states()
    system_locked = db.is_system_locked()
    teacher_list = db.conn.execute(
        "SELECT staff_no, first_name, last_name, account_locked FROM teachers ORDER BY staff_no").fetchall()
    db.close()

    tab_defs = [("subjects", "Subjects"), ("streams", "Streams"), ("components", "Components"),
                ("access", "Access &amp; OTP"), ("numbering", "Renumbering")]
    tab = request.values.get("tab", "subjects")
    if tab not in [k for k, _ in tab_defs]:
        tab = "subjects"

    def chip(code, name):
        return "<span class='chip'><b class='mono'>" + esc(code) + "</b> " + esc(name) + "</span>"

    def form_btn(action, fields, label, extra_style=""):
        hidden = "".join("<input type='hidden' name='" + k + "' value='" + esc(str(v)) + "'>" for k, v in fields.items())
        return ("<form method='post'" + extra_style + "><input type='hidden' name='action' value='" + action + "'>"
                + hidden + "<button type='submit'>" + label + "</button></form>")

    if tab == "subjects":
        content = (
            "<div class='chip-row'>" + "".join(chip(r["code"], r["name"]) for r in subjects) + "</div>"
            "<div class='split'>"
            "<form method='post'><input type='hidden' name='action' value='add_subject'>"
            "Name: <input name='name'> <button type='submit'>Add subject</button></form>"
            "<form method='post'><input type='hidden' name='action' value='remove_subject'>"
            "Name or code: <input name='name_or_code'> <button type='submit'>Remove subject</button></form>"
            "</div>")
    elif tab == "streams":
        rows = "".join(
            "<tr><td><b>Form " + str(num) + "</b></td><td><div class='chip-row' style='margin:0'>"
            + "".join(chip(r["code"], r["name"]) for r in rs) + "</div></td></tr>"
            for num, rs in forms_streams.items())
        content = (
            "<div class='grid-wrap'><table><thead><tr><th>Form</th><th>Streams</th></tr></thead><tbody>"
            + rows + "</tbody></table></div><br><div class='split'>"
            "<form method='post'><input type='hidden' name='action' value='add_stream'>"
            "Form: <input name='form_number' size='2' value='1'> Stream name: <input name='name'> "
            "<button type='submit'>Add stream</button></form>"
            "<form method='post'><input type='hidden' name='action' value='remove_stream'>"
            "Form: <input name='form_number' size='2' value='1'> Stream: <input name='name_or_code'> "
            "Move students to: <input name='reassign_to' size='8'> <button type='submit'>Remove stream</button></form>"
            "</div>")
    elif tab == "components":
        cards = ""
        for name, enabled in feature_states.items():
            cards += (
                "<div class='toggle-card'><b>" + name.replace("_", " ").title() + "</b>"
                "<span class='state' style='color:" + ("green" if enabled else "var(--danger)") + "'>"
                + ("Enabled" if enabled else "Disabled") + "</span>"
                + form_btn("toggle_feature", {"feature": name, "enabled": "0" if enabled else "1"},
                           "Disable" if enabled else "Enable") + "</div>")
        content = ("<p class='hint'>Disabling a component hides it from every non-executive account. "
                   "You can always see and re-enable it.</p><div class='toggle-grid'>" + cards + "</div>")
    elif tab == "access":
        acct = "".join(
            "<tr><td class='mono'>" + esc(t["staff_no"]) + "</td><td>" + esc(t["first_name"]) + " " + esc(t["last_name"])
            + "</td><td>" + ("<b style='color:var(--danger)'>LOCKED</b>" if t["account_locked"] else "Active") + "</td><td>"
            + form_btn("toggle_account_lock", {"target_staff_no": t["staff_no"],
                                               "locked": "0" if t["account_locked"] else "1"},
                       "Unlock" if t["account_locked"] else "Lock")
            + form_btn("generate_otp", {"target_staff_no": t["staff_no"]}, "Generate OTP")
            + "</td></tr>"
            for t in teacher_list)
        content = (
            "<div class='toggle-card' style='max-width:380px;margin-bottom:18px'><b>System lockout</b>"
            "<span class='state' style='color:" + ("var(--danger)" if system_locked else "green") + "'>"
            + ("LOCKED" if system_locked else "Open") + "</span>"
            "<p class='hint' style='margin:0'>Blocks every non-executive login (maintenance, holidays, staff "
            "leave). Executives can still log in to lift it.</p>"
            + form_btn("toggle_system_lock", {"locked": "0" if system_locked else "1"},
                       "Unlock system" if system_locked else "Lock system") + "</div>"
            "<p class='hint'>Lock one person's login, or generate a 15-minute, single-use 6-digit code to read out "
            "to them (they enter it on the reset page). No password is ever shown.</p>"
            "<div class='grid-wrap'><table><thead><tr><th>Staff No</th><th>Name</th><th>Status</th><th></th></tr></thead>"
            "<tbody>" + acct + "</tbody></table></div>")
    else:  # numbering
        content = (
            "<p class='hint'>Renumbers EVERY existing record in one pass and cascades to everything that "
            "references it (marks, attendance, fees, ...), so nothing goes stale.</p><div class='split'>"
            "<form method='post'><b>Students</b><input type='hidden' name='action' value='bulk_renumber_students'>"
            "Prefix: <input name='prefix' size='8' value='2027'> Padding: <input name='padding' size='3' value='3'> "
            "Start: <input name='start' size='6' value='1'> Form (blank = all): <input name='form_number' size='2'> "
            "<button type='submit'>Renumber students</button></form>"
            "<form method='post'><b>Staff</b><input type='hidden' name='action' value='bulk_renumber_staff'>"
            "Prefix: <input name='prefix' size='8' value='EMP'> Padding: <input name='padding' size='3' value='3'> "
            "Start: <input name='start' size='6' value='1'> <button type='submit'>Renumber staff</button></form>"
            "</div>")

    # keep the person on the same tab after any action
    content = content.replace("<form method='post'", "<form method='post' action='?tab=" + tab + "'")
    bar = "<div class='tabs'>" + "".join(
        "<a href='?tab=" + k + "' class='" + ("active" if k == tab else "") + "'>" + v + "</a>"
        for k, v in tab_defs) + "</div>"
    return page("Academic Setup", bar + content)


# ----------------------------------------------------------------------
# notifications
# ----------------------------------------------------------------------
@app.route("/notifications", methods=["GET"])
def notifications_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    recipient_id = request.args.get("recipient_id", staff_no)
    notes = db.get_notifications(recipient_id)
    db.close()
    rows = "".join(
        f"<tr><td>{n['created_at']}</td><td>{n['recipient_type']}</td><td>{n['channel']}</td>"
        f"<td>{n['status']}</td><td>{n['message']}</td></tr>"
        for n in notes
    )
    body = f"""
    <form method="get">
      Recipient ID (staff_no or parent id): <input name="recipient_id" value="{recipient_id}">
      <button type="submit">View</button>
    </form>
    <table border="1" cellpadding="4">
      <tr><th>When</th><th>Recipient Type</th><th>Channel</th><th>Status</th><th>Message</th></tr>{rows}
    </table>
    <p style="font-size:0.9em;color:#666">
      Notifications are queued here but not actually dispatched -- no real SMS/email
      provider is connected yet.
    </p>
    """
    return page("Notifications", body)


# ----------------------------------------------------------------------
# audit log (oversight only)
# ----------------------------------------------------------------------
@app.route("/audit-log", methods=["GET"])
def audit_log_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    if not db.has_full_oversight(staff_no):
        db.close()
        flash("Only the Principal, a Deputy Principal, or the Dean of Studies may view the audit log.")
        return redirect(url_for("dashboard"))
    entity = request.args.get("entity") or None
    entity_id = request.args.get("entity_id") or None
    rows = db.get_audit_log(entity=entity, entity_id=entity_id)[:200]
    db.close()
    table_rows = "".join(
        f"<tr><td>{r['created_at']}</td><td>{r['entity']}</td><td>{r['entity_id']}</td>"
        f"<td>{r['action']}</td><td>{r['actor']}</td><td>{r['details']}</td></tr>"
        for r in rows
    )
    body = f"""
    <form method="get">
      Entity (optional): <input name="entity" placeholder="marks, fees, students...">
      Entity ID (optional): <input name="entity_id">
      <button type="submit">Filter</button>
    </form>
    <table border="1" cellpadding="4">
      <tr><th>When</th><th>Entity</th><th>Entity ID</th><th>Action</th><th>Actor</th><th>Details</th></tr>
      {table_rows}
    </table>
    <p>Showing most recent {len(rows)} entries.</p>
    """
    return page("Audit Log", body)


# ----------------------------------------------------------------------
# promotion (end-of-year rollover)
# ----------------------------------------------------------------------
@app.route("/promotion", methods=["GET", "POST"])
def promotion_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx

    result = None
    step2 = request.args.get("step") == "2" or request.method == "POST"
    if request.method == "POST":
        if request.form.get("confirm_text", "").strip().upper() != "PROMOTE":
            flash('Type PROMOTE exactly (all caps) in the confirmation box to proceed. Nothing was run.')
        else:
            try:
                year = request.form.get("graduation_year")
                result = db.promote_all(staff_no, graduation_year=int(year) if year else None)
                flash(f"Promotion complete: {result}")
                step2 = False
            except PermissionDenied as e:
                flash(str(e))
    db.close()

    if step2 and result is None:
        body = """
        <div class="flash" style="background:#FBEAEA;color:#B3261E;border-color:#F0C4C0">
          <b>This is irreversible from within the app.</b> It graduates every Form 4 student to
          alumni and moves every other form up one level (3&rarr;4, 2&rarr;3, 1&rarr;2).
          Back up school.db first if you haven't already.
        </div>
        <form method="post">
          Type <b>PROMOTE</b> to confirm: <input name="confirm_text" autocomplete="off">
          Graduation year (optional): <input name="graduation_year" size="4">
          <button type="submit">Confirm and run promotion</button>
        </form>
        <p><a href="{{ url_for('promotion_view') }}">Cancel, go back</a></p>
        """
    else:
        body = """
        <p><b>Warning:</b> this graduates every Form 4 student to alumni and moves every other
        form up one level (3&rarr;4, 2&rarr;3, 1&rarr;2). This cannot be undone from here.
        Only the Principal, a Deputy Principal, or the Dean of Studies may run this.</p>
        <a href="{{ url_for('promotion_view') }}?step=2"
           style="display:inline-block;background:var(--navy);color:#fff;padding:8px 16px;
           border-radius:7px;text-decoration:none;font-weight:600;font-size:13px">
           Start end-of-year promotion</a>
        """
    return page("Promotion", body)


# ----------------------------------------------------------------------
# reports (PDF downloads)
# ----------------------------------------------------------------------
def _pdf_response(path):
    return send_file(path, as_attachment=True)


@app.route("/reports", methods=["GET"])
def reports_view():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    can_school_merit = db.can_print_school_merit_list(staff_no)
    scope = db.teacher_scope(staff_no)
    can_form_merit = bool(scope["supervises_forms"]) or can_school_merit
    is_hod_anywhere = any(r["role"] == "hod" for r in db.get_roles(staff_no)) or db.has_full_oversight(staff_no)
    is_oversight = db.has_full_oversight(staff_no)

    gender_result_html = ""
    if request.values.get("gender_go") and can_form_merit:
        gf = int(request.values.get("gender_form", 1))
        ge = int(request.values.get("gender_exam", 1))
        gsm = db.gender_stratified_merit_list(gf, ge)

        def _gender_table(label, entries):
            rows = "".join(
                f"<tr><td>{e['gender_position']}</td><td class='mono'>{e['adm_no']}</td><td>{e['name']}</td>"
                f"<td>{e['class_code']}</td><td>{e['mean']}</td><td>{e['mean_grade']}</td></tr>"
                for e in entries
            )
            return (
                f"<h4 style='font-family:\"Space Grotesk\",sans-serif;color:var(--navy-2);font-size:13px'>{label}</h4>"
                f"<table border='1' cellpadding='4'><tr><th>Pos</th><th>Adm No</th><th>Name</th>"
                f"<th>Class</th><th>Mean</th><th>Grade</th></tr>{rows or '<tr><td colspan=6>No students.</td></tr>'}</table>"
            )

        gender_result_html = _gender_table("Boys", gsm["male"]) + _gender_table("Girls", gsm["female"])
        if gsm["unspecified"]:
            gender_result_html += (
                f"<p style='font-size:12px;color:var(--ink-muted)'>{len(gsm['unspecified'])} "
                f"student(s) have no gender recorded and aren't shown above.</p>"
            )

    db.close()

    class_list_section = """
    <form method="get" action="{{ url_for('class_list_pdf') }}">
      Class list &mdash; Form: <input name="form_number" size="2" value="1">
      Stream: <input name="stream" size="8" value="White">
      Exam ID (optional): <input name="exam_id" size="4">
      <button type="submit">Download PDF</button>
    </form>
    <p style="font-size:12px;color:var(--ink-muted)">Works for classes you're the class teacher of,
    or any class in a form you supervise/oversee.</p>
    """

    form_merit_section = """
    <form method="get" action="{{ url_for('merit_list_pdf') }}">
      Form merit list &mdash; Form: <input name="form_number" size="2" value="1">
      Exam ID: <input name="exam_id" size="4" value="1">
      <button type="submit">Download PDF</button>
    </form>
    """ if can_form_merit else """
    <div class="flash" style="background:#FFF6E0;color:#8a6d1a;border-color:#F0DFA6">
      Form merit lists are available to that form's supervisor, the Dean of Studies, or an executive.
    </div>
    """

    school_merit_section = """
    <form method="get" action="{{ url_for('school_merit_list_pdf') }}">
      School-wide merit list &mdash; Exam ID: <input name="exam_id" size="4" value="1">
      <button type="submit">Download PDF</button>
    </form>
    """ if can_school_merit else """
    <div class="flash" style="background:#FFF6E0;color:#8a6d1a;border-color:#F0DFA6">
      The school-wide merit list is available to the Dean of Studies or an executive.
    </div>
    """

    dept_section = """
    <form method="get" action="{{ url_for('department_performance_pdf') }}">
      Department performance &mdash; Code: <input name="department_code" size="6">
      Exam ID: <input name="exam_id" size="4" value="1">
      <button type="submit">Download PDF</button>
    </form>
    """ if is_hod_anywhere else """
    <div class="flash" style="background:#FFF6E0;color:#8a6d1a;border-color:#F0DFA6">
      Department performance reports are available to that department's HOD or an executive.
    </div>
    """

    financial_section = f"""
    <form method="get" action="{url_for('financial_statement_pdf')}">
      Term: <input name="term" size="2" value="1"> Year: <input name="year" size="4" value="2026">
      <button type="submit">Download PDF</button>
    </form>
    """ if is_oversight else """
    <div class="flash" style="background:#FFF6E0;color:#8a6d1a;border-color:#F0DFA6">
      The financial statement is available to the Principal, a Deputy Principal, or the Dean of Studies.
    </div>
    """

    ranking_section = f"""
    <form method="get" action="{url_for('form_ranking_pdf')}">
      Form ranking with class teachers &mdash; Form: <input name="form_number" size="2" value="1">
      Exam ID: <input name="exam_id" size="4" value="1">
      <button type="submit">Download PDF</button>
    </form>
    """ if can_form_merit else """
    <div class="flash" style="background:#FFF6E0;color:#8a6d1a;border-color:#F0DFA6">
      Form-wide stream rankings are available to that form's supervisor, the Dean of Studies, or an executive.
    </div>
    """

    gender_section = f"""
    <form method="get">
      <input type="hidden" name="gender_go" value="1">
      Gender-stratified merit list &mdash; Form: <input name="gender_form" size="2" value="{request.values.get('gender_form','1')}">
      Exam ID: <input name="gender_exam" size="4" value="{request.values.get('gender_exam','1')}">
      <button type="submit">View</button>
    </form>
    """ if can_form_merit else """
    <div class="flash" style="background:#FFF6E0;color:#8a6d1a;border-color:#F0DFA6">
      Gender-stratified merit lists are available to that form's supervisor, the Dean of Studies, or an executive.
    </div>
    """

    body = f"""
    <h3>Class List</h3>{class_list_section}
    <h3>Form Merit List</h3>{form_merit_section}
    <h3>Form Stream Ranking (with Class Teachers)</h3>{ranking_section}
    <h3>School-Wide Merit List</h3>{school_merit_section}
    <h3>Gender-Stratified Merit List</h3>{gender_section}{gender_result_html}
    <h3>Department Performance</h3>{dept_section}
    <h3>Financial Statement</h3>{financial_section}
    """
    return page("Reports", body)


@app.route("/reports/form_ranking")
def form_ranking_pdf():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    form_number = int(request.args["form_number"])
    if not db.can_print_form_merit_list(staff_no, form_number):
        db.close()
        flash("Only that form's supervisor, the Dean of Studies, or an executive may print this ranking.")
        return redirect(url_for("reports_view"))
    exam_id = int(request.args["exam_id"])
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    from form_ranking import generate_form_ranking
    generate_form_ranking(db, form_number, exam_id, tmp.name)
    db.close()
    return send_file(tmp.name, as_attachment=True)


@app.route("/reports/class_list")
def class_list_pdf():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    ts = TeacherSession(db, staff_no)
    exam_id = request.args.get("exam_id") or None
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        ts.print_class_list(int(request.args["form_number"]), request.args["stream"], tmp.name,
                             exam_id=int(exam_id) if exam_id else None)
        return _pdf_response(tmp.name)
    except (PermissionDenied, NotFound) as e:
        flash(str(e))
        return redirect(url_for("reports_view"))
    finally:
        db.close()


@app.route("/reports/merit_list")
def merit_list_pdf():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    ts = TeacherSession(db, staff_no)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        ts.print_merit_list(int(request.args["form_number"]), int(request.args["exam_id"]), tmp.name)
        return _pdf_response(tmp.name)
    except PermissionDenied as e:
        flash(str(e))
        return redirect(url_for("reports_view"))
    finally:
        db.close()


@app.route("/reports/school_merit_list")
def school_merit_list_pdf():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    ts = TeacherSession(db, staff_no)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        ts.print_school_merit_list(int(request.args["exam_id"]), tmp.name)
        return _pdf_response(tmp.name)
    except PermissionDenied as e:
        flash(str(e))
        return redirect(url_for("reports_view"))
    finally:
        db.close()


@app.route("/reports/department_performance")
def department_performance_pdf():
    ctx = _guard()
    if ctx is None:
        return redirect(url_for("login_view"))
    db, staff_no = ctx
    ts = TeacherSession(db, staff_no)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        ts.print_department_performance(request.args["department_code"], int(request.args["exam_id"]), tmp.name)
        return _pdf_response(tmp.name)
    except (PermissionDenied, NotFound) as e:
        flash(str(e))
        return redirect(url_for("reports_view"))
    finally:
        db.close()


# ----------------------------------------------------------------------
# hosted deployment (Render, or anything setting SCHOOL_APP_PRODUCTION=1)
# Runs under gunicorn, which imports this module, so the safety checks live here
# rather than in the __main__ block below.
# ----------------------------------------------------------------------
IS_PRODUCTION = bool(os.environ.get("RENDER")) or os.environ.get("SCHOOL_APP_PRODUCTION") == "1"

if IS_PRODUCTION:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # trust the host's HTTPS proxy
    app.config["SESSION_COOKIE_SECURE"] = True

    # First start on an empty disk: create the admin from SCHOOL_ADMIN_PASSWORD (never 'admin').
    _pw = os.environ.get("SCHOOL_ADMIN_PASSWORD")
    _db = get_db()
    try:
        if _pw and not _db.conn.execute("SELECT 1 FROM teachers WHERE staff_no='admin'").fetchone():
            _db.add_teacher("System", "Administrator", staff_no="admin", password=_pw)
            for _role in ("principal", "deputy_principal_academics", "deputy_principal_admin", "dean_of_studies"):
                _db.grant_role("admin", _role)
        _problems = []
        if app.secret_key == "dev-secret-change-me":
            _problems.append("SCHOOL_APP_SECRET is not set")
        if not _db.conn.execute("SELECT 1 FROM staff_roles WHERE role IN "
                                "('principal','deputy_principal_academics','deputy_principal_admin','dean_of_studies')").fetchone():
            _problems.append("there is no executive account yet (set SCHOOL_ADMIN_PASSWORD for the first start)")
        if _db.verify_password("admin", "admin"):
            _problems.append("the admin account uses the default password 'admin' "
                             "(set SCHOOL_ADMIN_PASSWORD before the first start)")
    finally:
        _db.close()
    if _problems:
        raise RuntimeError("Refusing to start: " + "; ".join(_problems))


if __name__ == "__main__":
    import sys
    host = os.environ.get("SCHOOL_APP_HOST", "127.0.0.1")
    port = int(os.environ.get("SCHOOL_APP_PORT", "5000"))
    exposed = host not in ("127.0.0.1", "localhost", "::1")
    debug = os.environ.get("SCHOOL_APP_DEBUG", "1") == "1"

    if exposed:
        # Reachable from other machines: refuse to start in an unsafe state.
        problems = []
        if app.secret_key == "dev-secret-change-me":
            problems.append("SCHOOL_APP_SECRET is not set (sessions could be forged). "
                            "Set it to a long random string.")
        _db = get_db()
        try:
            if _db.verify_password("admin", "admin"):
                problems.append("The admin account still uses the default password 'admin'. "
                                "Change it first (see create_admin.py / set_password).")
        finally:
            _db.close()
        if problems:
            print("\nRefusing to start on " + host + " -- the system would be reachable by others:")
            for item in problems:
                print("  - " + item)
            sys.exit(1)
        if debug:
            print("Debug mode switched OFF automatically: it lets anyone who can reach the "
                  "server run code on this machine.")
            debug = False

    app.run(host=host, port=port, debug=debug)
