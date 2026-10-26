"""
form_ranking.py
Generates a printable Form Stream Ranking (PDF): every stream in a form,
ranked by performance for one exam, with its class teacher's name right
beside it -- and the Class Principal/Supervisor at the very bottom, since
they oversee the whole form rather than any single stream (same convention
as the Form Staffing List).
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

from db import SchoolDB


def generate_form_ranking(db: SchoolDB, form_number: int, exam_id: int, output_path: str,
                           school_name: str = "EduLink"):
    data = db.form_ranking_with_teachers(form_number, exam_id)
    exam = db.conn.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Title"], fontSize=16, spaceAfter=2)
    sub_style = ParagraphStyle("S", parent=styles["Normal"], fontSize=11, alignment=1,
                                textColor=colors.HexColor("#444444"))

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )
    story = [
        Paragraph(school_name, title_style),
        Paragraph(
            f"Form {form_number} Stream Ranking &mdash; {exam['name']} (Term {exam['term']}, {exam['year']})",
            sub_style,
        ),
        Spacer(1, 6), HRFlowable(width="100%", color=colors.HexColor("#888888")), Spacer(1, 10),
        Paragraph(f"Form Mean: {data['form_mean'] if data['form_mean'] is not None else 'N/A'}",
                  ParagraphStyle("fm", parent=styles["Normal"], fontSize=11, fontName="Helvetica-Bold")),
        Spacer(1, 10),
    ]

    header = ["Rank", "Class", "Stream", "Class Teacher", "Students", "Mean Score"]
    rows = [header]
    for s in data["streams"]:
        rows.append([
            str(s["rank"]) if s["rank"] is not None else "-",
            s["class_code"], s["stream_name"], s["class_teacher"], str(s["num_students"]),
            f"{s['mean_score']:.2f}" if s["mean_score"] is not None else "-",
        ])
    # Class Principal / Supervisor goes in the SAME grid, as the final row,
    # with "Class" shown as the whole form rather than a stream.
    supervisor_row_index = len(rows)
    rows.append(["", data["supervisor_class"], "", data["supervisor_name"], "", ""])

    table = Table(rows, colWidths=[16 * mm, 20 * mm, 32 * mm, 55 * mm, 22 * mm, 28 * mm])
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14724b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (4, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, supervisor_row_index - 1), [colors.white, colors.HexColor("#f3faf6")]),
        ("BACKGROUND", (0, supervisor_row_index), (-1, supervisor_row_index), colors.HexColor("#dcfce7")),
        ("FONTNAME", (0, supervisor_row_index), (-1, supervisor_row_index), "Helvetica-Bold"),
        ("LINEABOVE", (0, supervisor_row_index), (-1, supervisor_row_index), 1, colors.HexColor("#14724b")),
    ]
    # Highlight rank #1
    for i, s in enumerate(data["streams"], start=1):
        if s["rank"] == 1:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fff2c2")))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    doc.build(story)
    return output_path
