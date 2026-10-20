"""
class_list.py
Generates a printable Class List (PDF) for one class (form + stream):
- the class teacher's name
- the form's performance standing (this class ranked against the other
  streams in the same form, for a given exam)
- the roster of students in the class
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)

from db import SchoolDB


def generate_class_list(db: SchoolDB, form_number: int, stream_name_or_code: str,
                         output_path: str, exam_id: int = None,
                         school_name: str = "EduLink"):
    data = db.class_list_data(form_number, stream_name_or_code, exam_id)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("SchoolTitle", parent=styles["Title"], fontSize=16, spaceAfter=2)
    sub_style = ParagraphStyle("SubTitle", parent=styles["Normal"], fontSize=11,
                                alignment=1, textColor=colors.HexColor("#444444"))
    label_style = ParagraphStyle("Label", parent=styles["Normal"], fontSize=10)
    section_style = ParagraphStyle("Section", parent=styles["Heading3"], fontSize=12, spaceBefore=10)

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )
    story = []

    story.append(Paragraph(school_name, title_style))
    story.append(Paragraph(f"Class List &mdash; {data['class_code']} ({data['stream_name']})", sub_style))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#888888")))
    story.append(Spacer(1, 10))

    # Class teacher: specific to THIS class, so it sits right up top
    story.append(Paragraph(f"<b>Class Teacher:</b> {data['class_teacher']}", label_style))
    story.append(Spacer(1, 6))

    # Form performance section (only if an exam was supplied)
    if exam_id is not None:
        perf = data["form_performance"]
        story.append(Paragraph(
            f"Form {perf['form_number']} Performance &mdash; "
            f"Form Mean: {perf['form_mean'] if perf['form_mean'] is not None else 'N/A'}",
            section_style,
        ))

        header = ["Class", "Stream", "Students", "Mean Score", "Rank in Form"]
        rows = [header]
        for s in perf["streams"]:
            is_this_class = s["class_code"] == data["class_code"]
            rows.append([
                s["class_code"] + (" *" if is_this_class else ""),
                s["stream_name"],
                str(s["num_students"]),
                f"{s['mean_score']:.2f}" if s["mean_score"] is not None else "-",
                str(s["rank"]) if s["rank"] is not None else "-",
            ])
        perf_table = Table(rows, colWidths=[25 * mm, 35 * mm, 25 * mm, 30 * mm, 30 * mm])
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b3a55")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (2, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6fa")]),
        ]
        # highlight this class's row
        for i, s in enumerate(perf["streams"], start=1):
            if s["class_code"] == data["class_code"]:
                style_cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#dfe9ff")))
                style_cmds.append(("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"))
        perf_table.setStyle(TableStyle(style_cmds))
        story.append(perf_table)

    story.append(Spacer(1, 14))

    # Roster
    story.append(Paragraph(f"Student Roster ({len(data['students'])} students)", section_style))
    if exam_id is not None:
        header = ["#", "Adm No", "Name", "Mean Score"]
        rows = [header] + [
            [str(i + 1), s["adm_no"], s["name"], f"{s['mean_score']:.2f}" if s["mean_score"] is not None else "-"]
            for i, s in enumerate(data["students"])
        ]
        col_widths = [10 * mm, 30 * mm, 90 * mm, 30 * mm]
    else:
        header = ["#", "Adm No", "Name"]
        rows = [header] + [
            [str(i + 1), s["adm_no"], s["name"]] for i, s in enumerate(data["students"])
        ]
        col_widths = [10 * mm, 35 * mm, 105 * mm]

    if not data["students"]:
        rows.append(["-", "No students in this class yet.", "", ""][:len(header)])

    roster_table = Table(rows, colWidths=col_widths)
    roster_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b3a55")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (-1, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6fa")]),
    ]))
    story.append(roster_table)

    doc.build(story)
    return output_path
