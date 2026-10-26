"""
school_reports.py
Two school-level printable reports:
- School-wide merit list (every student, every form, ranked together)
- Department performance (a department's subjects, aggregated across the
  whole school -- the HOD's view)
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

from db import SchoolDB


def _doc(output_path):
    return SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )


def generate_school_merit_list(db: SchoolDB, exam_id: int, output_path: str,
                                school_name: str = "EduLink"):
    entries = db.school_merit_list(exam_id)
    exam = db.conn.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Title"], fontSize=16, spaceAfter=2)
    sub_style = ParagraphStyle("S", parent=styles["Normal"], fontSize=11, alignment=1,
                                textColor=colors.HexColor("#444444"))

    story = [
        Paragraph(school_name, title_style),
        Paragraph(f"School-Wide Merit List &mdash; {exam['name']} (Term {exam['term']}, {exam['year']})", sub_style),
        Spacer(1, 6), HRFlowable(width="100%", color=colors.HexColor("#888888")), Spacer(1, 12),
    ]

    header = ["Position", "Adm No", "Name", "Class", "Total", "Mean", "Grade"]
    rows = [header] + [
        [str(e["position"]), e["adm_no"], e["name"], e["class_code"],
         f"{e['total']:.1f}", f"{e['mean']:.2f}", e["mean_grade"]]
        for e in entries
    ] or [header + ["No marks recorded yet."]]

    table = Table(rows, colWidths=[20 * mm, 25 * mm, 55 * mm, 20 * mm, 20 * mm, 20 * mm, 18 * mm])
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14724b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (3, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3faf6")]),
    ]
    medals = {1: colors.HexColor("#fff2c2"), 2: colors.HexColor("#eaeaea"), 3: colors.HexColor("#f3ddc4")}
    for e in entries:
        if e["position"] in medals:
            style_cmds.append(("BACKGROUND", (0, e["position"]), (-1, e["position"]), medals[e["position"]]))
            style_cmds.append(("FONTNAME", (0, e["position"]), (-1, e["position"]), "Helvetica-Bold"))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    _doc(output_path).build(story)
    return output_path


def generate_financial_statement(db: SchoolDB, term: int, year: int, output_path: str,
                                  school_name: str = "EduLink"):
    """An audit-ready financial statement: summary, outstanding balances, and
    pledges vs unpledged arrears, for one term."""
    summary = db.financial_summary(term, year)
    arrears = db.arrears_report(term, year)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Title"], fontSize=16, spaceAfter=2)
    sub_style = ParagraphStyle("S", parent=styles["Normal"], fontSize=11, alignment=1,
                                textColor=colors.HexColor("#444444"))
    section_style = ParagraphStyle("Sec", parent=styles["Heading3"], fontSize=12, spaceBefore=14)

    story = [
        Paragraph(school_name, title_style),
        Paragraph(f"Financial Statement &mdash; Term {term}, {year}", sub_style),
        Spacer(1, 6), HRFlowable(width="100%", color=colors.HexColor("#888888")), Spacer(1, 12),
    ]

    summary_rows = [["Metric", "Amount (KES)"]] + [
        [k.replace("_", " ").title(), f"{v:,.2f}"] for k, v in summary.items()
    ]
    summary_table = Table(summary_rows, colWidths=[70 * mm, 60 * mm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14724b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3faf6")]),
    ]))
    story.append(summary_table)

    story.append(Paragraph("Arrears &amp; Pledges", section_style))
    header = ["Adm No", "Name", "Class", "Balance", "Pledged", "Unpledged"]
    rows = [header] + [
        [a["adm_no"], a["name"], a["class_code"], f"{a['balance']:,.2f}",
         f"{a['pledged']:,.2f}", f"{a['unpledged_balance']:,.2f}"]
        for a in arrears
    ]
    if not arrears:
        rows.append(["-", "No outstanding balances.", "-", "-", "-", "-"])
    arrears_table = Table(rows, colWidths=[22 * mm, 42 * mm, 18 * mm, 24 * mm, 24 * mm, 24 * mm])
    arrears_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14724b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3faf6")]),
    ]))
    story.append(arrears_table)
    story.append(Spacer(1, 10))
    story.append(Paragraph("Generated for audit purposes. All figures reflect live system balances at time of print.",
                            ParagraphStyle("note", parent=styles["Normal"], fontSize=8, textColor=colors.grey)))

    _doc(output_path).build(story)
    return output_path


def generate_department_performance(db: SchoolDB, department_code: str, exam_id: int, output_path: str,
                                      school_name: str = "EduLink"):
    data = db.department_performance(department_code, exam_id)
    dept = db.conn.execute("SELECT name FROM departments WHERE code=?", (department_code,)).fetchone()
    hod = db.hod_of(department_code)
    exam = db.conn.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Title"], fontSize=16, spaceAfter=2)
    sub_style = ParagraphStyle("S", parent=styles["Normal"], fontSize=11, alignment=1,
                                textColor=colors.HexColor("#444444"))
    label_style = ParagraphStyle("L", parent=styles["Normal"], fontSize=10)

    story = [
        Paragraph(school_name, title_style),
        Paragraph(f"{dept['name']} Department Performance &mdash; "
                  f"{exam['name']} (Term {exam['term']}, {exam['year']})", sub_style),
        Spacer(1, 6), HRFlowable(width="100%", color=colors.HexColor("#888888")), Spacer(1, 10),
        Paragraph(f"Head of Department: {hod['first_name'] + ' ' + hod['last_name'] if hod else 'Unassigned'}",
                  label_style),
        Spacer(1, 10),
    ]

    header = ["Subject", "Mean Score (School-wide)", "Entries"]
    rows = [header] + [
        [s["name"], f"{s['mean_score']:.2f}", str(s["n"])] for s in data["subjects"]
    ]
    if not data["subjects"]:
        rows.append(["No marks recorded yet.", "-", "-"])
    table = Table(rows, colWidths=[70 * mm, 55 * mm, 35 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14724b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3faf6")]),
    ]))
    story.append(table)
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f"Department Mean: {data['department_mean'] if data['department_mean'] is not None else 'N/A'}",
        ParagraphStyle("DM", parent=styles["Normal"], fontSize=11, fontName="Helvetica-Bold"),
    ))

    _doc(output_path).build(story)
    return output_path
