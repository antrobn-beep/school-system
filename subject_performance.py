"""
subject_performance.py
Generates a printable Subject Performance by Stream matrix (PDF): for one
form's exam, each subject's mean score in every stream, with the
top-performing stream highlighted per subject.
"""

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

from db import SchoolDB


def generate_subject_performance(db: SchoolDB, form_number: int, exam_id: int, output_path: str,
                                  school_name: str = "EduLink"):
    data = db.stream_subject_performance(form_number, exam_id)
    exam = db.conn.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("SchoolTitle", parent=styles["Title"], fontSize=16, spaceAfter=2)
    sub_style = ParagraphStyle("SubTitle", parent=styles["Normal"], fontSize=11,
                                alignment=1, textColor=colors.HexColor("#444444"))
    note_style = ParagraphStyle("Note", parent=styles["Normal"], fontSize=8, textColor=colors.grey)

    doc = SimpleDocTemplate(
        output_path, pagesize=landscape(A4),
        topMargin=15 * mm, bottomMargin=15 * mm, leftMargin=15 * mm, rightMargin=15 * mm,
    )
    story = []

    story.append(Paragraph(school_name, title_style))
    story.append(Paragraph(
        f"Form {form_number} Subject Performance by Stream &mdash; "
        f"{exam['name']} (Term {exam['term']}, {exam['year']})",
        sub_style,
    ))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#888888")))
    story.append(Spacer(1, 12))

    streams = data["streams"]
    subjects = data["subjects"]

    if not subjects or not streams:
        story.append(Paragraph("No marks recorded yet for this form and exam.", styles["Normal"]))
        doc.build(story)
        return output_path

    header = ["Subject"] + [s["class_code"] for s in streams] + ["Subject Mean"]
    rows = [header]
    best_cells = []  # (row_index, col_index) to highlight

    for r_idx, subj in enumerate(subjects, start=1):
        row = [subj["name"]]
        subj_matrix = data["matrix"].get(subj["code"], {})
        stream_means = []
        best_rank1_col = None
        for c_idx, st in enumerate(streams, start=1):
            entry = subj_matrix.get(st["code"])
            if entry:
                row.append(f"{entry['mean']:.2f}")
                stream_means.append(entry["mean"])
                if entry["rank"] == 1:
                    best_rank1_col = c_idx
            else:
                row.append("-")
        if best_rank1_col is not None:
            best_cells.append((r_idx, best_rank1_col))
        subj_mean = round(sum(stream_means) / len(stream_means), 2) if stream_means else None
        row.append(f"{subj_mean:.2f}" if subj_mean is not None else "-")
        rows.append(row)

    n_stream_cols = len(streams)
    col_widths = [40 * mm] + [22 * mm] * n_stream_cols + [26 * mm]

    table = Table(rows, colWidths=col_widths)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14724b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3faf6")]),
        ("FONTNAME", (-1, 1), (-1, -1), "Helvetica-Bold"),
    ]
    for r_idx, c_idx in best_cells:
        style_cmds.append(("BACKGROUND", (c_idx, r_idx), (c_idx, r_idx), colors.HexColor("#dcfce7")))
        style_cmds.append(("FONTNAME", (c_idx, r_idx), (c_idx, r_idx), "Helvetica-Bold"))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    doc.build(story)
    return output_path
