"""
form_staff_list.py
Generates a printable Form Staffing List (PDF):
- every stream in the form, with its class teacher's name right beside it
- the class principal / supervisor listed at the bottom, with their "class"
  shown as the whole form (e.g. "Form 1") rather than any single stream
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

from db import SchoolDB


def generate_form_staff_list(db: SchoolDB, form_number: int, output_path: str,
                              school_name: str = "EduLink"):
    data = db.form_staffing_data(form_number)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("SchoolTitle", parent=styles["Title"], fontSize=16, spaceAfter=2)
    sub_style = ParagraphStyle("SubTitle", parent=styles["Normal"], fontSize=11,
                                alignment=1, textColor=colors.HexColor("#444444"))
    section_style = ParagraphStyle("Section", parent=styles["Heading3"], fontSize=12, spaceBefore=14)
    footer_label_style = ParagraphStyle("FooterLabel", parent=styles["Normal"], fontSize=11)

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )
    story = []

    story.append(Paragraph(school_name, title_style))
    story.append(Paragraph(f"Form {form_number} Staffing List", sub_style))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#888888")))
    story.append(Spacer(1, 12))

    # Streams, each with its class teacher listed right beside it
    story.append(Paragraph("Streams &amp; Class Teachers", section_style))
    header = ["Class", "Stream", "Class Teacher"]
    rows = [header] + [
        [s["class_code"], s["stream_name"], s["class_teacher"]] for s in data["streams"]
    ]
    # Class principal/supervisor goes in the SAME grid, as the final row,
    # with "Class" shown as the whole form rather than a stream.
    supervisor_row_index = len(rows)  # index it will occupy (0-based, header is row 0)
    rows.append([data["supervisor_class"], "", data["supervisor_name"]])

    stream_table = Table(rows, colWidths=[25 * mm, 45 * mm, 80 * mm])
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b3a55")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f4f6fa")]),
        # highlight the supervisor row so it reads as a different kind of entry
        ("BACKGROUND", (0, supervisor_row_index), (-1, supervisor_row_index), colors.HexColor("#dfe9ff")),
        ("FONTNAME", (0, supervisor_row_index), (-1, supervisor_row_index), "Helvetica-Bold"),
        ("LINEABOVE", (0, supervisor_row_index), (-1, supervisor_row_index), 1, colors.HexColor("#2b3a55")),
    ]
    stream_table.setStyle(TableStyle(style_cmds))
    story.append(stream_table)

    doc.build(story)
    return output_path
