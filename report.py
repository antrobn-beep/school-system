"""
report.py
Generates a printable report form (PDF) for one student, for one exam,
pulling data straight out of the SQLite database (db.py).
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)

from db import SchoolDB


def generate_report_card(db: SchoolDB, adm_no: str, exam_id: int, output_path: str,
                          school_name: str = "EduLink"):
    data = db.build_report_data(adm_no, exam_id)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("SchoolTitle", parent=styles["Title"], fontSize=16, spaceAfter=2)
    sub_style = ParagraphStyle("SubTitle", parent=styles["Normal"], fontSize=11,
                                alignment=1, textColor=colors.HexColor("#444444"))
    label_style = ParagraphStyle("Label", parent=styles["Normal"], fontSize=10)

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )
    story = []

    story.append(Paragraph(school_name, title_style))
    story.append(Paragraph("Student Report Form", sub_style))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#888888")))
    story.append(Spacer(1, 10))

    # Student info block
    info_table = Table(
        [
            ["Name:", data["student_name"], "Adm No:", data["adm_no"]],
            ["Class:", data["class_code"], "Exam:", data["exam_name"]],
            ["Term:", str(data["term"]), "Year:", str(data["year"])],
        ],
        colWidths=[25 * mm, 60 * mm, 25 * mm, 60 * mm],
    )
    info_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 14))

    # Marks table
    header = ["Subject", "Score (%)", "Grade", "Status"]
    rows = [header]
    for s in data["subjects"]:
        rows.append([s["subject"], f"{s['score']:.1f}", s["grade"], s["status"].capitalize()])
    if not data["subjects"]:
        rows.append(["No marks recorded yet.", "-", "-", "-"])

    marks_table = Table(rows, colWidths=[70 * mm, 35 * mm, 30 * mm, 35 * mm])
    marks_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b3a55")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6fa")]),
    ]))
    story.append(marks_table)
    story.append(Spacer(1, 10))

    # Totals
    totals_table = Table(
        [["Total Marks", "Mean Score", "Mean Grade"],
         [f"{data['total']:.1f}", f"{data['mean']:.2f}", data["mean_grade"]]],
        colWidths=[45 * mm, 45 * mm, 45 * mm],
    )
    totals_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8ebf2")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 24))

    # Remarks & signatures
    story.append(Paragraph(f"Class Teacher: {data['class_teacher']}", label_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph("Remarks: " + "_" * 70, label_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph("Signature: " + "_" * 40 + "    Date: " + "_" * 20, label_style))
    story.append(Spacer(1, 16))

    story.append(Paragraph(f"Class Supervisor / Principal: {data['supervisor']}", label_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph("Remarks: " + "_" * 70, label_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph("Signature: " + "_" * 40 + "    Date: " + "_" * 20, label_style))

    doc.build(story)
    return output_path
