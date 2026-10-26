"""
merit_list.py
Generates a printable, form-wide Merit List (PDF): every student in a form,
across all its streams, individually ranked by mean score for one exam.
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

from db import SchoolDB


def generate_merit_list(db: SchoolDB, form_number: int, exam_id: int, output_path: str,
                         school_name: str = "EduLink"):
    entries = db.form_merit_list(form_number, exam_id)
    exam = db.conn.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("SchoolTitle", parent=styles["Title"], fontSize=16, spaceAfter=2)
    sub_style = ParagraphStyle("SubTitle", parent=styles["Normal"], fontSize=11,
                                alignment=1, textColor=colors.HexColor("#444444"))

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )
    story = []

    story.append(Paragraph(school_name, title_style))
    story.append(Paragraph(
        f"Form {form_number} Merit List &mdash; {exam['name']} (Term {exam['term']}, {exam['year']})",
        sub_style,
    ))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#888888")))
    story.append(Spacer(1, 12))

    header = ["Position", "Adm No", "Name", "Class", "Total", "Mean", "Grade"]
    rows = [header]
    for e in entries:
        rows.append([
            str(e["position"]), e["adm_no"], e["name"], e["class_code"],
            f"{e['total']:.1f}", f"{e['mean']:.2f}", e["mean_grade"],
        ])
    if not entries:
        rows.append(["-", "-", "No marks recorded for this form/exam yet.", "-", "-", "-", "-"])

    table = Table(rows, colWidths=[20 * mm, 25 * mm, 60 * mm, 20 * mm, 20 * mm, 20 * mm, 18 * mm])
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
    # Highlight top 3 positions
    medal_colors = {1: colors.HexColor("#fff2c2"), 2: colors.HexColor("#eaeaea"), 3: colors.HexColor("#f3ddc4")}
    for i, e in enumerate(entries, start=1):
        if e["position"] in medal_colors:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), medal_colors[e["position"]]))
            style_cmds.append(("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    doc.build(story)
    return output_path
