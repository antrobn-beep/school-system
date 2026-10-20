"""
receipt.py
Generates an instant PDF receipt for a single fee payment -- the "digital
fee collections & receipting" piece: every payment, manual or auto-detected
via a payment channel, can produce an error-free receipt immediately.
"""

from reportlab.lib.pagesizes import A5
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

from db import SchoolDB


def generate_receipt(db: SchoolDB, payment_id: int, output_path: str,
                      school_name: str = "EduLink"):
    payment = db.conn.execute("SELECT * FROM fee_payments WHERE id=?", (payment_id,)).fetchone()
    if not payment:
        raise ValueError(f"No such payment id {payment_id}.")
    student = db.conn.execute(
        "SELECT * FROM students WHERE adm_no=?", (payment["student_adm_no"],)
    ).fetchone()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Title"], fontSize=15, spaceAfter=2)
    sub_style = ParagraphStyle("S", parent=styles["Normal"], fontSize=10, alignment=1,
                                textColor=colors.HexColor("#444444"))
    label_style = ParagraphStyle("L", parent=styles["Normal"], fontSize=10)

    doc = SimpleDocTemplate(output_path, pagesize=A5, topMargin=14 * mm, bottomMargin=14 * mm,
                             leftMargin=14 * mm, rightMargin=14 * mm)
    story = [
        Paragraph(school_name, title_style),
        Paragraph("Official Fee Payment Receipt", sub_style),
        Spacer(1, 6), HRFlowable(width="100%", color=colors.HexColor("#888888")), Spacer(1, 10),
    ]

    info = Table(
        [
            ["Receipt No:", f"RCPT{payment['id']:06d}", "Date:", payment["date"]],
            ["Student:", f"{student['first_name']} {student['last_name']}", "Adm No:", student["adm_no"]],
            ["Class:", f"{student['form_number']}{student['stream_code']}", "Term/Year:",
             f"{payment['term']}/{payment['year']}"],
            ["Method:", payment["method"] or "-", "Channel:", payment["channel_code"] or "manual"],
        ],
        colWidths=[26 * mm, 45 * mm, 22 * mm, 40 * mm],
    )
    info.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(info)
    story.append(Spacer(1, 14))

    amount_table = Table([["Amount Paid", f"KES {payment['amount']:,.2f}"]], colWidths=[60 * mm, 60 * mm])
    amount_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#2b3a55")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 13),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(amount_table)
    story.append(Spacer(1, 16))

    balance = db.fee_balance(payment["student_adm_no"], payment["term"], payment["year"])
    story.append(Paragraph(
        f"Remaining balance for Term {payment['term']}, {payment['year']}: KES {balance['balance']:,.2f}",
        label_style,
    ))
    story.append(Spacer(1, 16))
    if payment["reference"]:
        story.append(Paragraph(f"Reference: {payment['reference']}",
                                ParagraphStyle("ref", parent=styles["Normal"], fontSize=8, textColor=colors.grey)))
    story.append(Paragraph("This is a system-generated receipt.",
                            ParagraphStyle("note", parent=styles["Normal"], fontSize=8, textColor=colors.grey)))

    doc.build(story)
    return output_path
