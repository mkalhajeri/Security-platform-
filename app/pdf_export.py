"""Renders a stored incident back into a PDF that mirrors the original
paper "Security Incident Report" form — same section numbers and titles,
so a printed copy looks like the paper process it replaced.
"""

import base64
import binascii
from datetime import date, datetime, time
from io import BytesIO

from PIL import Image as PILImage, UnidentifiedImageError
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Exact section numbers/titles from the organization's paper form, so the
# generated PDF reads as the same document, not a reinterpretation of it.
SECTION_TITLES = {
    1: "SECTION 1: REPORTING DETAILS",
    2: "SECTION 2: INVOLVED PERSON(S)",
    3: "SECTION 3: INCIDENT DETAILS",
    4: "SECTION 4: TYPE OF INCIDENT (Select all that apply)",
    5: "SECTION 5: INCIDENT BACKGROUND",
    6: "SECTION 6: IMMEDIATE ACTION TAKEN",
    7: "SECTION 7: ROOT CAUSE (If known)",
    8: "SECTION 8: RECOMMENDATIONS TO PREVENT RECURRENCE",
    9: "SECTION 9: INCIDENT PICTURES",
    10: "SECTION 10: LOCAL AUTHORITIES INVOLVEMENT / RESPONSE",
    11: "SECTION 11: SUPPORTING DOCUMENTS",
    12: "SECTION 12: APPROVALS",
}

# [value, label] pairs — numbering matches Section 4 of the paper form.
# Kept in sync by hand with app/static/app.js's INCIDENT_CATEGORIES; both
# are static presentation lists over the same IncidentCategory enum values.
INCIDENT_CATEGORY_LABELS = {
    "vehicle_accident": "01 Vehicle Accident",
    "injury": "02 Injury / Personal Injury",
    "illness_medical": "03 Illness / Medical Case",
    "fire_explosion": "04 Fire / Explosion",
    "theft": "05 Theft",
    "attempted_theft": "06 Attempted Theft",
    "vandalism_damage": "07 Vandalism / Damage",
    "property_damage": "08 Property Damage",
    "security_breach": "09 Security Breach",
    "harassment": "10 Harassment",
    "verbal_abuse": "11 Verbal Abuse",
    "physical_assault": "12 Physical Assault",
    "drug_alcohol": "13 Drug / Alcohol Related",
    "traffic_violation": "14 Traffic Violation",
    "environmental": "15 Environmental",
    "fall_from_height": "16 Fall from Height",
    "electrical": "17 Electrical",
    "chemical_spill": "18 Chemical Spill",
    "equipment_failure": "19 Equipment Failure",
    "other": "20 Others",
}

SUPPORTING_DOCUMENT_LABELS = {
    "photos": "Photos",
    "cctv_footage": "CCTV Footage",
    "medical_report": "Medical Report",
    "witness_statement": "Witness Statement",
    "police_report": "Police Report",
    "vehicle_report": "Vehicle Report",
    "maintenance_report": "Maintenance Report",
    "other": "Other",
}

MAX_EMBEDDED_PICTURES = 6  # keep the PDF a reasonable size; list the rest by name
MAX_IMAGE_WIDTH = 150 * mm


def _humanize(value: str | None) -> str:
    return (value or "").replace("_", " ").strip()


def _fmt(value) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


def _decode_signature_image(data_uri: str | None) -> bytes | None:
    if not data_uri or "," not in data_uri:
        return None
    try:
        return base64.b64decode(data_uri.split(",", 1)[1])
    except (binascii.Error, ValueError):
        return None


def _load_flowable_image(data: bytes, max_width: float) -> Image | None:
    """Fully decode `data` via Pillow and re-encode it before handing it to
    ReportLab. ReportLab's own image loader defers pixel decoding until the
    document is actually rendered (deep inside doc.build()), well past any
    try/except around flowable construction — so a corrupted or unusual
    upload would otherwise crash PDF generation for the whole incident.
    Decoding fully here, up front, means a bad image is just skipped."""
    try:
        with PILImage.open(BytesIO(data)) as im:
            im.load()
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            iw, ih = im.size
            if iw <= 0 or ih <= 0:
                return None
            width = min(max_width, iw)
            height = width * ih / iw
            clean = BytesIO()
            im.save(clean, format="PNG")
            clean.seek(0)
            return Image(clean, width=width, height=height)
    except (UnidentifiedImageError, OSError, ValueError):
        return None


class _Styles:
    def __init__(self):
        base = getSampleStyleSheet()
        self.title = ParagraphStyle(
            "SPTitle", parent=base["Title"], fontSize=16, spaceAfter=2, textColor=colors.HexColor("#1c2029")
        )
        self.subtitle = ParagraphStyle(
            "SPSubtitle", parent=base["Normal"], fontSize=9.5, textColor=colors.HexColor("#5b6270"), spaceAfter=10
        )
        self.section = ParagraphStyle(
            "SPSection",
            parent=base["Heading3"],
            fontSize=10,
            leading=13,
            textColor=colors.white,
            backColor=colors.HexColor("#2954d6"),
            borderPadding=(4, 6, 4, 6),
            spaceBefore=10,
            spaceAfter=6,
        )
        self.label = ParagraphStyle(
            "SPLabel", parent=base["Normal"], fontSize=7.5, textColor=colors.HexColor("#5b6270"), leading=9
        )
        self.value = ParagraphStyle("SPValue", parent=base["Normal"], fontSize=9.5, leading=12)
        self.body = ParagraphStyle("SPBody", parent=base["Normal"], fontSize=9.5, leading=14)
        self.hint = ParagraphStyle("SPHint", parent=base["Normal"], fontSize=8.5, textColor=colors.HexColor("#8a8f9c"))
        self.table_header = ParagraphStyle(
            "SPTableHeader", parent=base["Normal"], fontSize=7.5, textColor=colors.white, leading=9
        )
        self.table_cell = ParagraphStyle("SPTableCell", parent=base["Normal"], fontSize=8.5, leading=11)


def _key_value_grid(styles, pairs: list[tuple[str, str]], columns: int = 3) -> Table:
    """Section 1/3-style grid: label above value, several per row."""
    rows = []
    for i in range(0, len(pairs), columns):
        chunk = pairs[i : i + columns]
        row = []
        for label, value in chunk:
            cell = [Paragraph(label.upper(), styles.label), Paragraph(_fmt(value), styles.value)]
            row.append(cell)
        while len(row) < columns:
            row.append("")
        rows.append(row)
    col_width = (A4[0] - 30 * mm) / columns
    table = Table(rows, colWidths=[col_width] * columns)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#dfe2e8")),
            ]
        )
    )
    return table


def _section_header(styles, number: int) -> Paragraph:
    return Paragraph(SECTION_TITLES[number], styles.section)


def _persons_table(styles, persons: list[dict]) -> Table:
    headers = ["Name", "Designation", "Company", "ID / Labour Card", "Nationality", "Contact No.", "Gender"]
    rows = [[Paragraph(h, styles.table_header) for h in headers]]
    if not persons:
        rows.append([Paragraph("None recorded.", styles.table_cell)] + [""] * (len(headers) - 1))
    else:
        for p in persons:
            rows.append(
                [
                    Paragraph(_fmt(p.get("name")), styles.table_cell),
                    Paragraph(_fmt(p.get("designation")), styles.table_cell),
                    Paragraph(_fmt(p.get("company")), styles.table_cell),
                    Paragraph(_fmt(p.get("id_number")), styles.table_cell),
                    Paragraph(_fmt(p.get("nationality")), styles.table_cell),
                    Paragraph(_fmt(p.get("contact_no")), styles.table_cell),
                    Paragraph(_fmt(p.get("gender")), styles.table_cell),
                ]
            )
    table = Table(rows, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2954d6")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c7cbd4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _checkbox_list(styles, all_labels: dict, checked: list[str], other: str | None) -> Paragraph:
    # Helvetica (ReportLab's default) has no glyph for ☑/☐ — both fall back
    # to the same substitution box, so a checked item becomes visually
    # indistinguishable from an unchecked one. "[X]"/"[ ]" needs no special
    # glyph and reads unambiguously either way.
    marks = []
    for value, label in all_labels.items():
        if value in checked:
            marks.append(f'<font color="#1c2029"><b>[X] {label}</b></font>')
        else:
            marks.append(f'<font color="#8a8f9c">[&nbsp;&nbsp;] {label}</font>')
    text = "&nbsp;&nbsp;&nbsp;".join(marks)
    if other:
        text += f'&nbsp;&nbsp;&nbsp;<b>Other:</b> {_fmt(other)}'
    return Paragraph(text, styles.value)


def _approval_block(styles, label: str, role_label: str, value: dict | None) -> list:
    value = value or {}
    rows = [
        [Paragraph(f"<b>{label}</b> ({role_label})", styles.label)],
        [Paragraph(f"Name: {_fmt(value.get('name'))}", styles.table_cell)],
        [Paragraph(f"Position: {_fmt(value.get('position'))}", styles.table_cell)],
        [Paragraph(f"Date: {_fmt(value.get('signed_date'))}", styles.table_cell)],
    ]
    sig_bytes = _decode_signature_image(value.get("signature_image"))
    flowable_image = _load_flowable_image(sig_bytes, 50 * mm) if sig_bytes else None
    if flowable_image:
        rows.append([flowable_image])
    elif value.get("signature"):
        rows.append([Paragraph(f"Signature: {_fmt(value.get('signature'))}", styles.table_cell)])
    else:
        rows.append([Paragraph("Not yet signed.", styles.hint)])
    return rows


def build_incident_pdf(incident: dict, picture_bytes: dict[str, bytes]) -> bytes:
    """`incident` is the same dict the API returns (IncidentResponse-shaped).
    `picture_bytes` maps attachment id -> raw file bytes, for entries in
    incident["incident_pictures"] that are images and should be embedded."""

    styles = _Styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        title=f"Security Incident Report {incident.get('incident_number', '')} — {incident.get('site_location', '')}",
    )

    story = []
    story.append(Paragraph("SECURITY INCIDENT REPORT", styles.title))
    story.append(
        Paragraph(
            f"No. {_fmt(incident.get('incident_number'))} · {_fmt(incident.get('site_location'))} · "
            f"Filed {_fmt(incident.get('report_date'))} · Status: {_humanize(incident.get('status')).title()}",
            styles.subtitle,
        )
    )

    # Section 1
    story.append(_section_header(styles, 1))
    story.append(
        _key_value_grid(
            styles,
            [
                ("Incident No.", incident.get("incident_number")),
                ("Site / Location", incident.get("site_location")),
                ("Department / Area", incident.get("department_area")),
                ("Report Date", incident.get("report_date")),
                ("Report Time", incident.get("report_time")),
                ("Reported By", incident.get("reported_by")),
                ("Job Title", incident.get("reported_by_job_title")),
                ("ID No.", incident.get("reported_by_id_no")),
                ("Reported Via", _humanize(incident.get("reported_via")) + (f" ({incident['reported_via_other']})" if incident.get("reported_via_other") else "")),
                ("Nature of Report", _humanize(incident.get("nature_of_report")) + (f" ({incident['nature_of_report_other']})" if incident.get("nature_of_report_other") else "")),
            ],
        )
    )

    # Section 2
    story.append(_section_header(styles, 2))
    story.append(_persons_table(styles, incident.get("involved_persons") or []))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"<b>Witness(es):</b> {_fmt(incident.get('witnesses'))}", styles.value))

    # Section 3
    story.append(_section_header(styles, 3))
    story.append(
        _key_value_grid(
            styles,
            [
                ("Type of Incident", incident.get("incident_type_summary")),
                ("Exact Location", incident.get("exact_location")),
                ("Date of Incident", incident.get("incident_date")),
                ("Time of Incident", incident.get("incident_time")),
            ],
        )
    )

    # Section 4
    story.append(_section_header(styles, 4))
    story.append(
        _checkbox_list(
            styles,
            INCIDENT_CATEGORY_LABELS,
            incident.get("incident_categories") or [],
            incident.get("incident_category_other"),
        )
    )

    # Sections 5-8, 10 — narrative
    for num, field in ((5, "incident_background"), (6, "immediate_action_taken"), (7, "root_cause"), (8, "recommendations")):
        story.append(_section_header(styles, num))
        story.append(Paragraph(_fmt(incident.get(field)).replace("\n", "<br/>"), styles.body))

    # Section 9 — pictures
    story.append(_section_header(styles, 9))
    pictures = incident.get("incident_pictures") or []
    if not pictures:
        story.append(Paragraph("None attached.", styles.hint))
    else:
        embedded = 0
        listed = []
        for pic in pictures:
            data = picture_bytes.get(pic["id"])
            flowable_image = _load_flowable_image(data, MAX_IMAGE_WIDTH) if data and embedded < MAX_EMBEDDED_PICTURES else None
            if flowable_image:
                story.append(flowable_image)
                caption = pic.get("filename", "")
                if pic.get("description"):
                    caption += f" — {pic['description']}"
                story.append(Paragraph(caption, styles.hint))
                story.append(Spacer(1, 6))
                embedded += 1
                continue
            listed.append(pic.get("filename", "(unnamed file)"))
        if listed:
            story.append(Paragraph("Also referenced: " + ", ".join(listed), styles.hint))

    # Section 10
    story.append(_section_header(styles, 10))
    story.append(Paragraph(_fmt(incident.get("local_authorities_involvement")).replace("\n", "<br/>"), styles.body))

    # Section 11
    story.append(_section_header(styles, 11))
    story.append(
        _checkbox_list(
            styles,
            SUPPORTING_DOCUMENT_LABELS,
            incident.get("supporting_documents") or [],
            incident.get("supporting_documents_other"),
        )
    )
    doc_files = incident.get("supporting_document_files") or []
    if doc_files:
        story.append(Spacer(1, 4))
        story.append(Paragraph("Files: " + ", ".join(f.get("filename", "") for f in doc_files), styles.hint))

    # Section 12 — approvals, three columns
    story.append(_section_header(styles, 12))
    approval_cols = [
        _approval_block(styles, "Prepared By", "Security Officer", incident.get("prepared_by")),
        _approval_block(styles, "Reviewed By", "Security Supervisor", incident.get("reviewed_by")),
        _approval_block(styles, "Approved By", "Management", incident.get("approved_by")),
    ]
    max_rows = max(len(c) for c in approval_cols)
    for col in approval_cols:
        while len(col) < max_rows:
            col.append(Paragraph("", styles.table_cell))
    approval_rows = list(zip(*approval_cols))
    col_width = (A4[0] - 30 * mm) / 3
    approval_table = Table(approval_rows, colWidths=[col_width] * 3)
    approval_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (0, -1), 0.4, colors.HexColor("#c7cbd4")),
                ("BOX", (1, 0), (1, -1), 0.4, colors.HexColor("#c7cbd4")),
                ("BOX", (2, 0), (2, -1), 0.4, colors.HexColor("#c7cbd4")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(KeepTogether([approval_table]))

    story.append(Spacer(1, 10))
    story.append(
        Paragraph(
            f"Generated from the Security Platform incident console — record ID {incident.get('id', '')}.",
            styles.hint,
        )
    )

    doc.build(story)
    return buf.getvalue()
