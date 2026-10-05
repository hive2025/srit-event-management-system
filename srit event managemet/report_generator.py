import os
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, Image, KeepTogether, HRFlowable,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas
from PIL import Image as PILImage
from xml.sax.saxutils import escape

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ASSETS = os.path.join(BASE_DIR, "static", "default_assets")

# ── Default logo configuration (club / professional chapter / unit logos) ───
DEFAULT_LOGOS = [
    {"slot": "hive",  "filename": "logo_hive.png",  "label": "HIVE",   "visible": True, "width_pt": 48, "order": 0},
    {"slot": "iic",   "filename": "logo_iic.png",   "label": "IIC",    "visible": True, "width_pt": 48, "order": 1},
    {"slot": "ecell", "filename": "logo_ecell.png", "label": "E-Cell", "visible": True, "width_pt": 48, "order": 2},
    {"slot": "edc",   "filename": "logo_edc.png",   "label": "EDC",    "visible": True, "width_pt": 48, "order": 3},
    {"slot": "sish",  "filename": "logo_sish.png",  "label": "SISH",   "visible": True, "width_pt": 48, "order": 4},
]

DEFAULT_LETTERHEAD = os.path.join(DEFAULT_ASSETS, "srit_letterhead_default.png")


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor('#687784'))
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(self._pagesize[0] - 40, 20, page_text)

        # Running footer
        self.drawString(40, 20, f"SRIT HIVE Event Portal | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.setStrokeColor(colors.HexColor('#edf1f3'))
        self.setLineWidth(0.5)
        self.line(40, 30, self._pagesize[0] - 40, 30)
        self.restoreState()


def _sop_text(value):
    """Return safe, visible text for fields that may not have source data."""
    if value is None or not str(value).strip():
        return "Not Available"
    return escape(str(value))


def _sop_status(available, complete_text="Completed"):
    return complete_text if available else "Not Available"


def _image_or_none(path, max_w, max_h=None):
    """
    Return a ReportLab Image fitted within max_w (and optionally max_h).
    Returns None if the file is missing or unreadable.
    """
    if not path or not os.path.isfile(path):
        return None
    try:
        with PILImage.open(path) as img:
            orig_w, orig_h = img.size
        if orig_w <= 0 or orig_h <= 0:
            return None
        ratio = orig_h / orig_w
        fit_w = float(max_w)
        fit_h = fit_w * ratio
        if max_h and fit_h > max_h:
            fit_h = float(max_h)
            fit_w = fit_h / ratio
        return Image(path, width=fit_w, height=fit_h)
    except Exception:
        return None


def _resolve_logo_list(sop_template, has_letterhead=True):
    """
    Return an ordered list of logo dicts that are visible, with resolved 'path'.
    If has_letterhead is True, avoids duplicating institutional SNR and SRIT logos
    that are already integrated inside the official letterhead image.
    """
    logos_cfg = None
    if sop_template and sop_template.get("logos"):
        logos_cfg = sop_template["logos"]

    if not logos_cfg:
        logos_cfg = DEFAULT_LOGOS

    # Resolve paths
    resolved = []
    for lg in logos_cfg:
        if not lg.get("visible", True):
            continue
        
        slot_name = str(lg.get("slot", "")).lower()
        fn = lg.get("filename", "")
        # If letterhead is displayed, don't duplicate default SNR/SRIT logos
        if has_letterhead and (slot_name in ("snr", "srit") or fn in ("logo_snr.png", "logo_srit.png")):
            # If no custom replacement image was uploaded, skip it
            if not lg.get("path") or lg.get("path") == os.path.join(DEFAULT_ASSETS, fn):
                continue

        path = lg.get("path") or ""
        if not path or not os.path.isfile(path):
            default_path = os.path.join(DEFAULT_ASSETS, fn)
            path = default_path if fn and os.path.isfile(default_path) else ""
        if not path:
            continue
        resolved.append({
            "path": path,
            "label": lg.get("label", ""),
            "width_pt": float(lg.get("width_pt") or 48),
            "order": int(lg.get("order") or 0),
        })

    # Sort by order
    resolved.sort(key=lambda x: x["order"])
    return resolved


def generate_sop_pdf_report(pdf_path, event_data, user_inputs, attendance_stats,
                            feedback_stats, transcript_text, photo_paths,
                            sop_template=None):
    """
    Reproduce the official SRIT 'Application for Organizing Programme' template.
    15 numbered items, letterhead image (or text fallback), logo bar,
    and 3-column signature footer.

    Key guarantees:
    - rowHeights=None  — fully dynamic, never hard-coded
    - repeatRows=1     — header row repeats on every page
    - KeepTogether     — signature section stays together
    - Paragraph cells  — all variable-length content wraps naturally
    """

    PAGE_W, PAGE_H = A4          # 595.27 × 841.89 pts
    MARGIN = 36
    CONTENT_W = PAGE_W - 2 * MARGIN   # ~523 pts

    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        rightMargin=MARGIN, leftMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=50,
    )
    styles = getSampleStyleSheet()

    # ── Style helpers ──────────────────────────────────────────────────────
    def _style(name, **kw):
        base = kw.pop("parent", styles["Normal"])
        return ParagraphStyle(name, parent=base, **kw)

    hdr_inst = _style("HdrInst", fontName="Helvetica-Bold", fontSize=13,
                      leading=16, alignment=1, textColor=colors.HexColor("#0a2240"))
    hdr_sub  = _style("HdrSub",  fontName="Helvetica",       fontSize=8,
                      leading=10, alignment=1, textColor=colors.HexColor("#333333"))
    hdr_appr = _style("HdrAppr", fontName="Helvetica-Oblique", fontSize=7,
                      leading=9,  alignment=1, textColor=colors.HexColor("#555555"))
    hdr_addr = _style("HdrAddr", fontName="Helvetica",        fontSize=7,
                      leading=9,  alignment=1, textColor=colors.HexColor("#555555"))

    form_title = _style("FormTitle", fontName="Helvetica-Bold", fontSize=14,
                        leading=18, alignment=1, textColor=colors.HexColor("#0a2240"),
                        spaceBefore=10, spaceAfter=2)
    date_style = _style("DateStyle", fontName="Helvetica",      fontSize=9,
                        leading=11, alignment=2)   # right-align

    # Table header row style
    tbl_hdr    = _style("TblHdr", fontName="Helvetica-Bold",  fontSize=8.5, leading=12,
                        textColor=colors.white, alignment=1)
    num_style  = _style("Num",   fontName="Helvetica-Bold",  fontSize=9,  leading=12,
                        textColor=colors.HexColor("#0a2240"))
    lbl_style  = _style("Lbl",   fontName="Helvetica-Bold",  fontSize=8.5, leading=12,
                        textColor=colors.HexColor("#18232d"))
    lbl_small  = _style("LblSm", fontName="Helvetica",       fontSize=7.5, leading=10,
                        textColor=colors.HexColor("#555555"))
    val_style  = _style("Val",   fontName="Helvetica",       fontSize=8.5, leading=12,
                        textColor=colors.HexColor("#2c3e50"))
    q_style    = _style("Q",     fontName="Helvetica-Bold",  fontSize=8.5, leading=13,
                        textColor=colors.HexColor("#0a2240"), spaceAfter=4)
    ans_style  = _style("Ans",   fontName="Helvetica",       fontSize=8.5, leading=13,
                        textColor=colors.HexColor("#2c3e50"))

    sig_lbl    = _style("SigLbl", fontName="Helvetica",      fontSize=9,   leading=12,
                        alignment=1)
    sig_val    = _style("SigVal", fontName="Helvetica-Bold", fontSize=9,   leading=12,
                        textColor=colors.HexColor("#0a2240"), alignment=1)

    # ── Helper: safe paragraph ─────────────────────────────────────────────
    def P(text, st=val_style):
        return Paragraph(_sop_text(text), st)

    # ── Gather data from event + form inputs ───────────────────────────────
    ev = event_data

    dept_assoc   = user_inputs.get("dept_assoc_club",
                       f"{ev.get('organizing_details') or 'HIVE'}, "
                       f"DEPARTMENT OF {(ev.get('program_name') or 'ENGINEERING').upper()}")
    nature_prog  = user_inputs.get("nature_of_programme",
                       ev.get("program_name") or "Workshop")
    title_prog   = ev.get("title") or "N/A"
    coordinators = user_inputs.get("coordinator",
                       ev.get("organizing_details") or "N/A")

    # Date & Day (field 5)
    raw_date = ev.get("event_date") or ""
    try:
        dt_obj   = datetime.strptime(raw_date, "%Y-%m-%d")
        date_day = dt_obj.strftime("%d/%m/%Y %A")
    except Exception:
        date_day = raw_date

    # Time (field 6)
    t_start  = ev.get("start_time") or ""
    t_end    = ev.get("end_time")   or ""
    time_str = f"{t_start} – {t_end}" if (t_start or t_end) else "N/A"

    venue       = ev.get("venue") or "N/A"
    participants= user_inputs.get("participants", "Students (All Years)")
    audience    = user_inputs.get("total_audience",
                      str(ev.get("capacity") or "N/A"))

    # Resource person (field 10) — multi-line
    rp_name  = user_inputs.get("resource_person_name") or ev.get("speaker_name") or "N/A"
    rp_desg  = user_inputs.get("resource_person_designation") or ev.get("speaker_designation") or ""
    rp_org   = user_inputs.get("resource_person_org") or ev.get("speaker_organization") or ""
    rp_addr  = user_inputs.get("resource_person_address", "")
    rp_phone = user_inputs.get("resource_person_phone") or ev.get("speaker_contact") or ""
    rp_email = user_inputs.get("resource_person_email") or ev.get("speaker_email") or ""
    rp_parts = [l for l in [rp_name, rp_desg, rp_org, rp_addr,
                              rp_phone and f"Ph: {rp_phone}",
                              rp_email and f"E: {rp_email}"] if l]
    rp_text  = "<br/>".join(escape(p) for p in rp_parts) if rp_parts else "N/A"

    expenditure  = user_inputs.get("estimated_expenditure",
                       f"Rs {ev.get('estimated_expenditure') or 'N/A'}")
    fund_source  = user_inputs.get("fund_source", "HOD's Fund")

    objective    = (ev.get("objectives") or
                    user_inputs.get("objective") or "N/A")
    stu_contrib  = (user_inputs.get("student_contribution") or
                    ev.get("benefits") or "N/A")
    inst_contrib = (user_inputs.get("institution_contribution") or
                    ev.get("spc_mapping") or "N/A")

    sop_date     = user_inputs.get("sop_date",
                       datetime.now().strftime("%d.%m.%Y"))

    hod_name     = user_inputs.get("hod",      "Head of Department")
    principal    = user_inputs.get("principal", "Principal")

    # ── STORY ──────────────────────────────────────────────────────────────
    story = []

    # ── Extract custom logos if provided ───────────────────────────────────
    custom_logos = {}
    if isinstance(sop_template, dict):
        custom_logos = sop_template.get("custom_logos") or sop_template.get("logos") or {}
        if isinstance(custom_logos, list):
            custom_logos = {
                item.get("slot"): (item.get("path") or (os.path.join(DEFAULT_ASSETS, item.get("filename", "")) if item.get("filename") else ""))
                for item in custom_logos if isinstance(item, dict) and item.get("slot")
            }
    elif isinstance(user_inputs, dict) and user_inputs.get("custom_logos"):
        custom_logos = user_inputs.get("custom_logos") or {}

    # ── Parse included logos set (defaults to all 7) ──────────────────────
    ALL_7_LOGOS = {"snr", "srit", "hive", "ecell", "idealab", "iic", "sish"}
    included_logos_raw = None
    if isinstance(sop_template, dict):
        included_logos_raw = sop_template.get("included_logos") or sop_template.get("selected_logos")
    if included_logos_raw is None and isinstance(user_inputs, dict):
        included_logos_raw = user_inputs.get("included_logos") or user_inputs.get("selected_logos")

    if included_logos_raw is not None:
        if isinstance(included_logos_raw, str):
            try:
                import json as _json
                included_set = set(_json.loads(included_logos_raw))
            except Exception:
                included_set = {k.strip() for k in included_logos_raw.split(",") if k.strip()}
        else:
            included_set = set(included_logos_raw)
    else:
        included_set = set(ALL_7_LOGOS)

    # ── 1. INSTITUTIONAL TOP HEADER (SNR & SRIT - Optional) ────────────────
    left_logo = None
    if "snr" in included_set:
        snr_custom = custom_logos.get("snr")
        snr_logo_path = snr_custom if (snr_custom and os.path.isfile(snr_custom)) else os.path.join(DEFAULT_ASSETS, "logo_snr.png")
        left_logo = _image_or_none(snr_logo_path, max_w=45, max_h=52)

    right_logo = None
    if "srit" in included_set:
        srit_custom = custom_logos.get("srit")
        srit_logo_path = srit_custom if (srit_custom and os.path.isfile(srit_custom)) else os.path.join(DEFAULT_ASSETS, "logo_srit.png")
        right_logo = _image_or_none(srit_logo_path, max_w=48, max_h=52)

    logo_col = 52
    text_col = CONTENT_W - 2 * logo_col

    hdr_inst = _style("HdrInst", fontName="Helvetica-Bold", fontSize=12.5,
                      leading=15, alignment=1, textColor=colors.HexColor("#0a2240"))
    hdr_sub1 = _style("HdrSub1", fontName="Helvetica", fontSize=8,
                      leading=10, alignment=1, textColor=colors.HexColor("#222222"))
    hdr_sub2 = _style("HdrSub2", fontName="Helvetica", fontSize=7.5,
                      leading=9.5, alignment=1, textColor=colors.HexColor("#333333"))
    hdr_appr = _style("HdrAppr", fontName="Helvetica-Oblique", fontSize=7,
                      leading=9, alignment=1, textColor=colors.HexColor("#555555"))
    hdr_addr = _style("HdrAddr", fontName="Helvetica", fontSize=7.5,
                      leading=9.5, alignment=1, textColor=colors.HexColor("#444444"))

    hdr_text_table = Table([
        [Paragraph("SRI RAMAKRISHNA INSTITUTE OF TECHNOLOGY", hdr_inst)],
        [Paragraph("[Educational Service : SNR Sons Charitable Trust]", hdr_sub1)],
        [Paragraph("[Autonomous Institution, Reaccredited by NAAC with 'A' Grade, ISO 21001:2018 Certified]", hdr_sub2)],
        [Paragraph("[Approved by AICTE New Delhi, Permanently Affiliated to Anna University, Chennai]", hdr_appr)],
        [Paragraph("Coimbatore - 641 010.  www.srit.org", hdr_addr)],
    ], colWidths=[text_col])
    hdr_text_table.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 0, colors.white),
        ("INNERGRID",     (0, 0), (-1, -1), 0, colors.white),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 0.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
    ]))

    hdr_outer = Table(
        [[left_logo or Paragraph("", styles["Normal"]), hdr_text_table, right_logo or Paragraph("", styles["Normal"])]],
        colWidths=[logo_col, text_col, logo_col],
    )
    hdr_outer.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("BOX",           (0, 0), (-1, -1), 0, colors.white),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
    ]))
    story.append(hdr_outer)

    # ── 2. INITIATIVE LOGOS ROW (HIVE, E-Cell, Idea Lab, IIC, SISH - OPTIONAL) ──
    INITIATIVE_LOGOS_CONFIG = [
        ("hive",    "logo_hive.png",    "HIVE",     40, 24.0),
        ("ecell",   "logo_ecell.png",   "E-Cell",   40, 24.0),
        ("idealab", "logo_idealab.png", "Idea Lab", 40, 24.0),
        ("iic",     "logo_iic.png",     "IIC",      40, 24.0),
        ("sish",    "logo_sish.png",    "SISH",     36, 24.0),
    ]

    init_cells = []
    for slot_key, default_fn, label, max_w, max_h in INITIATIVE_LOGOS_CONFIG:
        # Skip unselected logos
        if slot_key not in included_set:
            continue

        custom_p = custom_logos.get(slot_key)
        if slot_key == "idealab" and not custom_p:
            custom_p = custom_logos.get("edc")

        if custom_p and os.path.isfile(custom_p):
            logo_path = custom_p
        else:
            logo_path = os.path.join(DEFAULT_ASSETS, default_fn)
            if not os.path.isfile(logo_path) and slot_key == "idealab":
                logo_path = os.path.join(DEFAULT_ASSETS, "logo_edc.png")

        img = _image_or_none(logo_path, max_w=max_w, max_h=max_h)
        if img:
            init_cells.append(img)
        else:
            init_cells.append(Paragraph(label, _style(f"Init_{slot_key}", fontName="Helvetica-Bold", fontSize=7, leading=9, alignment=1, textColor=colors.HexColor("#333333"))))

    if init_cells:
        story.append(Spacer(1, 3))
        col_w = CONTENT_W / float(len(init_cells))
        init_bar = Table([init_cells], colWidths=[col_w] * len(init_cells))
        init_bar.setStyle(TableStyle([
            ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ]))
        story.append(init_bar)

    # ── 3. APPLICATION FOR ORGANIZING PROGRAMME + DATE ─────────────────────
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#0a2240"), spaceBefore=1, spaceAfter=4))

    title_date_style_title = _style("FormTitleClean", fontName="Helvetica-Bold", fontSize=12,
                                    leading=15, alignment=1, textColor=colors.HexColor("#0a2240"))
    title_date_style_date  = _style("DateClean", fontName="Helvetica-Bold", fontSize=8.5,
                                    leading=11, alignment=2, textColor=colors.HexColor("#333333"))

    title_date = Table(
        [[Paragraph("Application for Organizing Programme", title_date_style_title),
          Paragraph(f"Date: {_sop_text(sop_date)}", title_date_style_date)]],
        colWidths=[CONTENT_W * 0.74, CONTENT_W * 0.26],
    )
    title_date.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 0, colors.white),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",         (0, 0), (0, 0),   "CENTER"),
        ("ALIGN",         (1, 0), (1, 0),   "RIGHT"),
        ("TOPPADDING",    (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
    ]))
    story.append(title_date)
    story.append(Spacer(1, 4))

    # ── 4. MAIN 15-ITEM TABLE ─────────────────────────────────────────────
    # Column widths: num | label | value
    C_NUM = 22
    C_LBL = 160
    C_VAL = CONTENT_W - C_NUM - C_LBL

    # ── Header row (repeated on every page via repeatRows=1) ──────────────
    header_row = [
        P("#",           tbl_hdr),
        P("Description", tbl_hdr),
        P("Details",     tbl_hdr),
    ]

    # ── Row builders ──────────────────────────────────────────────────────
    def row(num, label, value, sublabel=""):
        """Build one numbered table row. Returns row_data list."""
        label_parts = escape(str(label)).replace("\n", "<br/>")
        if sublabel:
            escaped_sub = escape(str(sublabel)).replace("\n", "<br/>")
            label_parts += f"<br/><font size='7.5' color='#555555'>{escaped_sub}</font>"
        
        if isinstance(value, str):
            if "<br/>" in value or "<font" in value or "<b>" in value:
                val_content = Paragraph(value if value.strip() else "Not Available", val_style)
            else:
                escaped_val = escape(value).replace("\n", "<br/>")
                val_content = Paragraph(escaped_val if escaped_val.strip() else "Not Available", val_style)
        else:
            val_content = value

        return [
            P(str(num), num_style),
            Paragraph(label_parts, lbl_style),
            val_content,
        ]

    def question_row(num, question, answer):
        """
        Full-width question+answer row.
        Col 0 = number, cols 1+2 span with question (bold) then answer (normal).
        Returns row_data.
        """
        escaped_q = escape(str(question)).replace("\n", "<br/>")
        escaped_ans = escape(str(answer)).replace("\n", "<br/>")
        q_para   = Paragraph(escaped_q, q_style)
        ans_para = Paragraph(escaped_ans if escaped_ans.strip() else "Not Available", ans_style)
        # Use a borderless inner table so question and answer stack vertically
        inner = Table(
            [[q_para], [ans_para]],
            colWidths=[C_LBL + C_VAL],
        )
        inner.setStyle(TableStyle([
            ("BOX",           (0, 0), (-1, -1), 0, colors.white),
            ("INNERGRID",     (0, 0), (-1, -1), 0, colors.white),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ]))
        return [P(str(num), num_style), inner, Paragraph("", val_style)]

    # ── Assemble rows ─────────────────────────────────────────────────────
    all_data = [header_row]   # row 0 = header (repeated)

    # Rows 1–12 (label | value)
    all_data.append(row(1,  "Department, Association, Club", dept_assoc))
    all_data.append(row(2,  "Nature of Programme",           nature_prog))
    all_data.append(row(3,  "Title of the Programme",        title_prog))
    all_data.append(row(4,  "Name of the Faculty\nCoordinator(s)", coordinators))
    all_data.append(row(5,  "Date and Day",                  date_day))
    all_data.append(row(6,  "Time",                          time_str))
    all_data.append(row(7,  "Venue",                         venue))
    all_data.append(row(8,  "Participants",                  participants))
    all_data.append(row(9,  "Total Audience expected within\nand outside the Institute", audience))
    all_data.append(row(10, "Details of Resource Person:",   rp_text,
                      sublabel="(Name, Designation,\nOrganization, Address, Phone No., E-mail ID)"))
    all_data.append(row(11, "Estimated Expenditure",         expenditure))
    all_data.append(row(12, "Sources & Application of Fund", fund_source,
                      sublabel="(Budget to be given as Annexure)"))

    # Rows 13–15 (question spans label+value cols)
    q13_row = question_row(13, "What is the objective of conducting the programme?", objective)
    q14_row = question_row(14, "How will it contribute to student development?", stu_contrib)
    q15_row = question_row(15,
                           "How will it contribute to Institution Development/Brand Building?",
                           inst_contrib)
    all_data.append(q13_row)
    all_data.append(q14_row)
    all_data.append(q15_row)

    # ── Optional custom rows (safely appended without breaking structure) ──
    import json as _json
    raw_custom = (sop_template.get("custom_rows") if sop_template else None) or user_inputs.get("custom_rows") or []
    if isinstance(raw_custom, str):
        try:
            custom_rows_list = _json.loads(raw_custom)
        except Exception:
            custom_rows_list = []
    elif isinstance(raw_custom, list):
        custom_rows_list = raw_custom
    else:
        custom_rows_list = []

    custom_spans = []
    custom_bg = []
    curr_num = 16
    for cr in custom_rows_list:
        if not isinstance(cr, dict):
            continue
        c_label = cr.get("label") or cr.get("name") or "Additional Detail"
        c_val = cr.get("value") or cr.get("content") or ""
        c_is_q = bool(cr.get("is_question", False))
        c_num = cr.get("num") or curr_num
        curr_num += 1

        r_idx = len(all_data)
        if c_is_q:
            all_data.append(question_row(c_num, c_label, c_val))
            custom_spans.append(r_idx)
        else:
            all_data.append(row(c_num, c_label, c_val, sublabel=cr.get("sublabel", "")))
            custom_bg.append(r_idx)

    # ── Table styles ──────────────────────────────────────────────────────
    ts = [
        ("BOX",           (0, 0), (-1, -1), 0.6, colors.HexColor("#444444")),
        ("INNERGRID",     (0, 0), (-1, -1), 0.4, colors.HexColor("#888888")),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),

        # Header row style
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#0a2240")),
        ("ALIGN",         (0, 0), (-1, 0), "CENTER"),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),

        # Shade number column (all data rows)
        ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#e8eef4")),
        # Shade label column (simple rows 1–12, i.e. rows 1..12)
        ("BACKGROUND", (1, 1), (1, 12), colors.HexColor("#f5f8fb")),
    ]

    # Span label+value cols for question rows 13–15 (data rows 13, 14, 15 in all_data)
    for qi in range(3):
        r = 13 + qi   # all_data indices 13, 14, 15
        ts.append(("SPAN",       (1, r), (2, r)))
        ts.append(("BACKGROUND", (1, r), (2, r), colors.HexColor("#fffef8")))

    # Apply styles for any custom rows
    for r in custom_spans:
        ts.append(("SPAN",       (1, r), (2, r)))
        ts.append(("BACKGROUND", (1, r), (2, r), colors.HexColor("#fffef8")))
    for r in custom_bg:
        ts.append(("BACKGROUND", (1, r), (1, r), colors.HexColor("#f5f8fb")))

    # ── Build table with repeatRows=1, rowHeights=None ────────────────────
    main_table = Table(
        all_data,
        colWidths=[C_NUM, C_LBL, C_VAL],
        rowHeights=None,          # ← fully dynamic; ReportLab measures each row
        repeatRows=1,             # ← header row repeats on every page
        splitByRow=True,          # ← long rows flow naturally to next page
    )
    main_table.setStyle(TableStyle(ts))
    story.append(main_table)
    story.append(Spacer(1, 20))

    # ── 5. SIGNATURE FOOTER (kept together) ──────────────────────────────
    sig_col = CONTENT_W / 3.0
    sig_data = [
        # Line 1: labels
        [P("Signature of Faculty Coordinator:", sig_lbl),
         P("Recommendation of HOD:", sig_lbl),
         P("Approval of Principal:", sig_lbl)],
        # Line 2: blank space for handwritten signature
        [Spacer(1, 24), Spacer(1, 24), Spacer(1, 24)],
        # Line 3: printed names
        [P(f"({coordinators})", sig_val),
         P("Recommended / Not Recommended", sig_val),
         P("Permitted / Not Permitted", sig_val)],
    ]
    sig_table = Table(sig_data, colWidths=[sig_col, sig_col, sig_col])
    sig_table.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 0.6, colors.HexColor("#444444")),
        ("INNERGRID",     (0, 0), (-1, -1), 0.4, colors.HexColor("#aaaaaa")),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEABOVE",     (0, 2), (-1, 2), 0.5, colors.HexColor("#444444")),
    ]))
    story.append(KeepTogether(sig_table))

    doc.build(story, canvasmaker=NumberedCanvas)


def generate_event_pdf_report(pdf_path, report_type, event_data, user_inputs, attendance_stats, feedback_stats, transcript_text, photo_paths):
    if report_type == 'sop_report':
        generate_sop_pdf_report(
            pdf_path, event_data, user_inputs, attendance_stats,
            feedback_stats, transcript_text, photo_paths
        )
        return

    # A4 dimensions: 595.27 x 841.89 points
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=45
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#083b5c'),  # Navy
        alignment=1,  # Center
        spaceAfter=5
    )

    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=12,
        textColor=colors.HexColor('#0c8b8b'),  # Teal
        alignment=1,  # Center
        spaceAfter=15
    )

    section_heading_style = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor('#083b5c'),
        spaceBefore=12,
        spaceAfter=6,
        keepWithNext=True
    )

    label_style = ParagraphStyle(
        'TableLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11,
        textColor=colors.HexColor('#18232d')
    )

    val_style = ParagraphStyle(
        'TableVal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=11,
        textColor=colors.HexColor('#4c5b66')
    )

    body_style = ParagraphStyle(
        'BodyTextCustom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor('#18232d'),
        spaceAfter=6
    )

    comment_style = ParagraphStyle(
        'FeedbackComment',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#4c5b66'),
        spaceAfter=4
    )

    story = []

    # 1. Header Section
    inst_name = user_inputs.get('institute_name', 'SRI RAMAKRISHNA INSTITUTE OF TECHNOLOGY').upper()
    story.append(Paragraph(inst_name, title_style))

    dept_name = user_inputs.get('department', 'DEPARTMENT OF ELECTRICAL AND ELECTRONICS ENGINEERING').upper()
    story.append(Paragraph(f"{dept_name} • HIVE INNOVATION HUB", subtitle_style))

    # Report Title
    report_title = "EVENT COMPLETION REPORT (SOP)" if report_type == 'sop_report' else "EVENT ACTIVITY SUMMARY REPORT"
    title_banner_data = [[Paragraph(f"<b>{report_title}</b>", ParagraphStyle('BannerText', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, textColor=colors.white, alignment=1))]]

    banner_table = Table(title_banner_data, colWidths=[515])
    banner_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#083b5c')),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(banner_table)
    story.append(Spacer(1, 15))

    speaker_text = user_inputs.get('speaker_name') or event_data.get('speaker_name') or 'Not Specified'
    if speaker_text != 'Not Specified':
        speaker_extra = ", ".join(filter(None, [event_data.get('speaker_designation'), event_data.get('speaker_organization')]))
        if speaker_extra and speaker_extra not in speaker_text:
            speaker_text = f"{speaker_text} ({speaker_extra})"

    # 2. Event Metadata Table
    meta_rows = [
        [Paragraph("Event Title", label_style), Paragraph(event_data.get('title', ''), val_style),
         Paragraph("Event Date", label_style), Paragraph(event_data.get('event_date', ''), val_style)],
        [Paragraph("Program Type", label_style), Paragraph(user_inputs.get('program_name', event_data.get('program_name', '')), val_style),
         Paragraph("Venue / Room", label_style), Paragraph(event_data.get('venue', ''), val_style)],
        [Paragraph("Timings", label_style), Paragraph(f"{event_data.get('start_time', '')} - {event_data.get('end_time', '')}", val_style),
         Paragraph("Academic Year", label_style), Paragraph(event_data.get('academic_year', ''), val_style)],
        [Paragraph("Speaker / Resource Person", label_style), Paragraph(speaker_text, val_style),
         Paragraph("Faculty Coordinator", label_style), Paragraph(user_inputs.get('coordinator', 'Not Specified'), val_style)],
        [Paragraph("HOD", label_style), Paragraph(user_inputs.get('hod', 'Not Specified'), val_style),
         Paragraph("Principal", label_style), Paragraph(user_inputs.get('principal', 'Not Specified'), val_style)]
    ]

    meta_table = Table(meta_rows, colWidths=[110, 147, 100, 158])
    meta_table.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#edf1f3')),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#f7fafb')),
        ('BACKGROUND', (2,0), (2,-1), colors.HexColor('#f7fafb')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 15))

    # 3. Objectives & Benefits
    story.append(Paragraph("1. Event Objectives", section_heading_style))
    story.append(Paragraph(event_data.get('objectives', 'No objectives specified.'), body_style))

    story.append(Paragraph("2. Expected Benefits", section_heading_style))
    story.append(Paragraph(event_data.get('benefits', 'No benefits specified.'), body_style))

    story.append(Paragraph("3. Event Summary", section_heading_style))
    story.append(Paragraph(user_inputs.get('summary', 'No summary provided.'), body_style))
    story.append(Spacer(1, 10))

    # 4. Attendance Statistics
    story.append(Paragraph("4. Attendance Summary", section_heading_style))
    total_reg = attendance_stats.get('registered', 0)
    total_pres = attendance_stats.get('present', 0)
    total_abs = attendance_stats.get('absent', 0)
    pct = attendance_stats.get('percentage', 0.0)

    att_data = [
        [Paragraph("Total Registrations", label_style), Paragraph(str(total_reg), val_style)],
        [Paragraph("Students Present", label_style), Paragraph(str(total_pres), val_style)],
        [Paragraph("Students Absent", label_style), Paragraph(str(total_abs), val_style)],
        [Paragraph("Attendance Percentage", label_style), Paragraph(f"{pct:.2f}%", val_style)]
    ]
    att_table = Table(att_data, colWidths=[150, 365])
    att_table.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#edf1f3')),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#f7fafb')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(att_table)

    # Page Break for clean formatting
    story.append(PageBreak())

    # 5. Feedback Analysis
    story.append(Paragraph("5. Feedback Analysis", section_heading_style))
    fb_total = feedback_stats.get('total', 0)
    fb_avg = feedback_stats.get('avg_rating', 0.0)

    fb_summary_data = [
        [Paragraph("Total Feedback Submitted", label_style), Paragraph(str(fb_total), val_style)],
        [Paragraph("Average Rating", label_style), Paragraph(f"{fb_avg:.2f} / 5.0", val_style)]
    ]
    fb_table = Table(fb_summary_data, colWidths=[150, 365])
    fb_table.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#edf1f3')),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#f7fafb')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(fb_table)
    story.append(Spacer(1, 10))

    # Rating Distribution Table
    dist = feedback_stats.get('distribution', {})
    dist_headers = [Paragraph("<b>Rating</b>", label_style)] + [Paragraph(f"<b>{i} Star</b>", label_style) for i in range(5, 0, -1)]
    dist_values = [Paragraph("<b>Count</b>", label_style)] + [Paragraph(str(dist.get(i, 0)), val_style) for i in range(5, 0, -1)]

    dist_table = Table([dist_headers, dist_values], colWidths=[100, 83, 83, 83, 83, 83])
    dist_table.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#edf1f3')),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f7fafb')),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(Paragraph("<b>Rating Distribution:</b>", ParagraphStyle('SubLbl', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9.5, spaceBefore=5, spaceAfter=4)))
    story.append(dist_table)
    story.append(Spacer(1, 10))

    # Feedback Comments
    story.append(Paragraph("<b>Sample Comments:</b>", ParagraphStyle('SubLbl2', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9.5, spaceBefore=5, spaceAfter=4)))
    comments = feedback_stats.get('comments', [])
    if comments:
        for c in comments[:8]:  # Limit to top 8 feedback comments to avoid overflowing
            story.append(Paragraph(f"• \"{c}\"", comment_style))
    else:
        story.append(Paragraph("No feedback comments submitted.", val_style))
    story.append(Spacer(1, 15))

    # 6. Speaker Transcript
    story.append(Paragraph("6. Speaker Transcript Summary", section_heading_style))
    if transcript_text and transcript_text.strip():
        # Clean transcript text and print the first 800 characters as a summary if it's very long
        display_transcript = transcript_text
        if len(display_transcript) > 1500:
            display_transcript = display_transcript[:1500] + "\n\n[... Transcript Truncated for PDF Summary ...]"

        t_paras = display_transcript.strip().split('\n')
        for tp in t_paras:
            if tp.strip():
                story.append(Paragraph(tp.strip(), body_style))
    else:
        story.append(Paragraph("No transcript available.", val_style))
    story.append(Spacer(1, 15))

    # 7. Geo Photographs
    story.append(Paragraph("7. Event Photographs (Geo-Tagged)", section_heading_style))
    if photo_paths:
        photo_cells = []
        for path in photo_paths:
            if os.path.exists(path):
                try:
                    # Let's resize it proportionally to fit in a table cell (approx 230x160)
                    img = PILImage.open(path)
                    w, h = img.size
                    ratio = w / h
                    target_w = 230
                    target_h = int(target_w / ratio)
                    if target_h > 160:
                        target_h = 160
                        target_w = int(target_h * ratio)
                    photo_cells.append(Image(path, width=target_w, height=target_h))
                except Exception as e:
                    photo_cells.append(Paragraph(f"Image load error: {str(e)}", val_style))

        # Structure images in pairs (2 per row)
        img_table_data = []
        for i in range(0, len(photo_cells), 2):
            row_imgs = photo_cells[i:i+2]
            if len(row_imgs) < 2:
                row_imgs.append(Paragraph("", val_style))
            img_table_data.append(row_imgs)

        if img_table_data:
            img_table = Table(img_table_data, colWidths=[257, 258])
            img_table.setStyle(TableStyle([
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 8),
                ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ]))
            story.append(img_table)
        else:
            story.append(Paragraph("No photographs available.", val_style))
    else:
        story.append(Paragraph("No photographs available.", val_style))

    story.append(Spacer(1, 20))

    # 8. Signatures Block
    sig_data = [
        [Paragraph("<b>Prepared By:</b>", label_style), Paragraph("<b>Verified By:</b>", label_style), Paragraph("<b>Approved By:</b>", label_style)],
        [Spacer(1, 25), Spacer(1, 25), Spacer(1, 25)],
        [Paragraph(user_inputs.get('coordinator', 'Faculty Coordinator'), val_style),
         Paragraph("Head of Department (HOD)", val_style),
         Paragraph("Principal", val_style)]
    ]
    sig_table = Table(sig_data, colWidths=[171, 172, 172])
    sig_table.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'BOTTOM'),
        ('LINEABOVE', (0,2), (-1,2), 0.5, colors.HexColor('#687784')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))

    # Try to keep the signatures block together with its header
    story.append(Spacer(1, 15))
    story.append(sig_table)

    # Build PDF using NumberedCanvas
    doc.build(story, canvasmaker=NumberedCanvas)
