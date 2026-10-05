"""
certificate_generator.py
Generates a prestigious, high-impact landscape A4 PDF certificate using ReportLab.
Faithfully follows the reference certificate composition:
- Balanced, full-page layout without compressed text or wasted vertical space.
- Emerald-green geometric panel on the LEFT, visually proportional and elegant.
- Gold laurel medallion on the left, positioned around the vertical center.
- Top logo container with larger, evenly spaced, vertically aligned logos.
- Large, dominant "CERTIFICATE" title with letter-spaced sub-type directly below.
- "Proudly presented to" in elegant serif italics.
- Substantially larger recipient student name in rich gold serif, centered.
- Clean gold divider line with center diamond accent directly below student name.
- Higher, larger, and prominent certificate statement:
  Register No. • Department
  participation statement
  EVENT TITLE (prominent with automatic multiline wrapping)
  Date • Venue
- Dedicated, clearly separated SIGNATURE SECTION near bottom (3–4 evenly spaced columns):
  signature image, horizontal baseline, signatory name, designation.
  Preserves aspect ratio, identical visual height, no overlap with body.
- Dark green bottom footer bar strictly for institutional motto/details.
- Generous margins and consistent vertical spacing throughout (LOGOS -> TITLE -> RECIPIENT -> STATEMENT -> SIGNATURES -> FOOTER).
- Full compatibility with existing application logic, schemas, and template parameters.
"""

import os
import math
from datetime import datetime
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import stringWidth

# Default Brand Values
_DEFAULT_INSTITUTION = "SRI RAMAKRISHNA INSTITUTE OF TECHNOLOGY"
_DEFAULT_TAGLINE     = "HIVE \u2022 HUB FOR INNOVATION AND ENTREPRENEURSHIP"
_DEFAULT_BODY_TEXT   = "for successfully participating in / contributing to"
_DEFAULT_FOOTER1     = "Hub for Innovation and Entrepreneurship (HIVE)"
_DEFAULT_FOOTER2     = "Sri Ramakrishna Institute of Technology \u2022 Coimbatore"

# Primary Luxury Palette
COLOR_EMERALD_DARK   = colors.HexColor("#063826")
COLOR_EMERALD_MID    = colors.HexColor("#0B5338")
COLOR_EMERALD_LIGHT  = colors.HexColor("#14724E")
COLOR_GOLD_DEEP      = colors.HexColor("#B8860B")
COLOR_GOLD_PRIMARY   = colors.HexColor("#D4AF37")
COLOR_GOLD_LIGHT     = colors.HexColor("#EEDC82")
COLOR_CREAM_BG       = colors.HexColor("#FCFCFA")
COLOR_CARD_BORDER    = colors.HexColor("#E2D5AC")
COLOR_INK_DARK       = colors.HexColor("#152820")
COLOR_INK_MUTED      = colors.HexColor("#475950")
COLOR_LINE_GRAY      = colors.HexColor("#CBD5E1")


def _safe_draw_image(c, path, x, y, width, height, anchor='c'):
    """Draw an image safely inside bounded box preserving aspect ratio."""
    if not path:
        return False
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        return False
    ext = os.path.splitext(abs_path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        return False
    try:
        c.drawImage(
            abs_path,
            x,
            y,
            width=width,
            height=height,
            preserveAspectRatio=True,
            anchor=anchor,
            mask="auto"
        )
        return True
    except Exception:
        return False


def _draw_star(c, cx, cy, r_out, r_in=None, fill_color=colors.white):
    """Draw a 5-pointed vector star centered at (cx, cy)."""
    if r_in is None:
        r_in = r_out * 0.42
    p = c.beginPath()
    angle_offset = math.pi / 2
    for i in range(10):
        angle = angle_offset + i * (math.pi / 5)
        r = r_out if i % 2 == 0 else r_in
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        if i == 0:
            p.moveTo(x, y)
        else:
            p.lineTo(x, y)
    p.close()
    c.setFillColor(fill_color)
    c.drawPath(p, fill=1, stroke=0)


def _draw_laurel_leaf(c, cx, cy, size=4.5, angle_deg=0):
    """Draw a stylized single laurel leaf rotated by angle_deg."""
    c.saveState()
    c.translate(cx, cy)
    c.rotate(angle_deg)
    p = c.beginPath()
    p.moveTo(0, -size * 0.4)
    p.curveTo(size * 0.8, -size * 0.2, size * 0.8, size * 0.2, 0, size * 0.9)
    p.curveTo(-size * 0.8, size * 0.2, -size * 0.8, -size * 0.2, 0, -size * 0.4)
    p.close()
    c.setFillColor(colors.HexColor("#FDF7DC"))
    c.setStrokeColor(COLOR_GOLD_PRIMARY)
    c.setLineWidth(0.4)
    c.drawPath(p, fill=1, stroke=1)
    c.restoreState()


def _draw_gold_medallion(c, cx, cy, radius=52):
    """Renders a radiant gold seal medallion with decorative laurels and star."""
    c.saveState()

    # Drop Shadow
    c.setFillColor(colors.HexColor("#000000"), alpha=0.14)
    c.circle(cx + 3, cy - 3, radius + 2, fill=1, stroke=0)

    # Outer Ribbon / Gear Notch Rim
    teeth = 36
    c.setFillColor(COLOR_GOLD_DEEP)
    c.setStrokeColor(COLOR_GOLD_PRIMARY)
    c.setLineWidth(1)
    ribbon = c.beginPath()
    for i in range(teeth * 2):
        angle = i * (math.pi / teeth)
        r = (radius + 4) if i % 2 == 0 else (radius + 0.5)
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        if i == 0:
            ribbon.moveTo(x, y)
        else:
            ribbon.lineTo(x, y)
    ribbon.close()
    c.drawPath(ribbon, fill=1, stroke=1)

    # Gold Base Layers
    c.setFillColor(COLOR_GOLD_PRIMARY)
    c.circle(cx, cy, radius, fill=1, stroke=0)

    c.setFillColor(COLOR_GOLD_LIGHT)
    c.circle(cx - 1, cy + 1, radius - 4, fill=1, stroke=0)

    c.setFillColor(COLOR_GOLD_PRIMARY)
    c.circle(cx, cy, radius - 7, fill=1, stroke=0)

    # Concentric rings
    c.setStrokeColor(colors.HexColor("#996515"))
    c.setLineWidth(1.2)
    c.circle(cx, cy, radius - 9, fill=0, stroke=1)

    c.setStrokeColor(colors.HexColor("#F8E8A2"))
    c.setLineWidth(0.8)
    c.circle(cx, cy, radius - 11, fill=0, stroke=1)

    # Inner Core Disc
    c.setFillColor(colors.HexColor("#C28B14"))
    c.circle(cx, cy, radius - 13, fill=1, stroke=0)

    # Laurel Wreath Arch
    wreath_r = radius - 22
    for deg in range(120, 260, 24):
        rad = math.radians(deg)
        lx = cx + wreath_r * math.cos(rad)
        ly = cy + wreath_r * math.sin(rad)
        _draw_laurel_leaf(c, lx, ly, size=5.2, angle_deg=deg + 90)

    for deg in range(-80, 60, 24):
        rad = math.radians(deg)
        lx = cx + wreath_r * math.cos(rad)
        ly = cy + wreath_r * math.sin(rad)
        _draw_laurel_leaf(c, lx, ly, size=5.2, angle_deg=deg - 90)

    # Center Star
    _draw_star(c, cx, cy, r_out=14, r_in=6.5, fill_color=colors.HexColor("#FFFFFF"))

    c.restoreState()


def _draw_diamond_accent(c, cx, cy, size=6, line_len=185):
    """Draw a classic gold divider rule with a center diamond."""
    c.saveState()
    c.setStrokeColor(COLOR_GOLD_PRIMARY)
    c.setLineWidth(1.2)
    c.line(cx - line_len, cy, cx - size - 4, cy)
    c.line(cx + size + 4, cy, cx + line_len, cy)

    p = c.beginPath()
    p.moveTo(cx, cy + size)
    p.lineTo(cx + size, cy)
    p.lineTo(cx, cy - size)
    p.lineTo(cx - size, cy)
    p.close()
    c.setFillColor(COLOR_GOLD_PRIMARY)
    c.setStrokeColor(COLOR_GOLD_DEEP)
    c.setLineWidth(0.8)
    c.drawPath(p, fill=1, stroke=1)

    s_in = size * 0.45
    p2 = c.beginPath()
    p2.moveTo(cx, cy + s_in)
    p2.lineTo(cx + s_in, cy)
    p2.lineTo(cx, cy - s_in)
    p2.lineTo(cx - s_in, cy)
    p2.close()
    c.setFillColor(colors.HexColor("#FDF7DC"))
    c.drawPath(p2, fill=1, stroke=0)

    c.restoreState()


def _wrap_text_into_lines(text, font_name, font_size, max_width):
    """Splits text into multiple lines such that no line exceeds max_width."""
    words = text.split()
    if not words:
        return []
    lines = []
    curr = words[0]
    for w in words[1:]:
        cand = curr + " " + w
        if stringWidth(cand, font_name, font_size) <= max_width:
            curr = cand
        else:
            lines.append(curr)
            curr = w
    if curr:
        lines.append(curr)
    return lines


def _render_top_logos(c, logos, content_center_x, page_h):
    """
    Renders top row logos with generous size and even spacing inside a rounded container.
    Returns the container bottom Y.
    """
    visible = sorted(
        [lg for lg in (logos or []) if lg.get("show") and lg.get("path")],
        key=lambda x: int(x.get("order", 99))
    )

    box_w = 560
    box_h = 56
    box_x = content_center_x - (box_w / 2)
    box_y = page_h - 26 - box_h

    c.saveState()
    c.setFillColor(colors.HexColor("#FFFFFF"), alpha=0.98)
    c.setStrokeColor(COLOR_CARD_BORDER)
    c.setLineWidth(1.1)
    c.roundRect(box_x, box_y, box_w, box_h, 8, fill=1, stroke=1)
    c.restoreState()

    if not visible:
        c.setFont("Helvetica-Bold", 10.5)
        c.setFillColor(COLOR_INK_DARK)
        c.drawCentredString(content_center_x, box_y + 28, _DEFAULT_INSTITUTION)
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(COLOR_GOLD_DEEP)
        c.drawCentredString(content_center_x, box_y + 13, _DEFAULT_TAGLINE)
        return box_y

    n = len(visible)
    slot_w = (box_w - 24) / n

    for i, lg in enumerate(visible):
        cx = box_x + 12 + (i + 0.5) * slot_w
        cy = box_y + box_h / 2
        max_w = min(115, slot_w - 14)
        max_h = 44
        _safe_draw_image(c, lg["path"], cx - max_w / 2, cy - max_h / 2, max_w, max_h, anchor='c')

    return box_y


def _render_signature_blocks(c, signatures, content_center_x, baseline_y=100, max_width=550):
    """
    Renders cleanly aligned signature columns near the bottom.
    Accommodates 1 to 4 signatures with image, baseline rule, signatory name, and designation.
    Preserves aspect ratios and maintains identical visual baseline and height.
    """
    visible = sorted(
        [sg for sg in (signatures or []) if sg.get("show")],
        key=lambda x: int(x.get("order", 99))
    )

    if not visible:
        visible = [
            {"name": "FACULTY COORDINATOR", "designation": "HIVE Coordinator, SRIT", "path": ""},
            {"name": "DR. M. PAULRAJ", "designation": "Principal, SRIT", "path": ""},
        ]

    n = min(len(visible), 4)
    if n == 1:
        block_w = 170
        gap = 0
        start_x = content_center_x - block_w / 2
    elif n == 2:
        block_w = 160
        gap = max_width - 2 * block_w
        start_x = content_center_x - max_width / 2
    elif n == 3:
        block_w = 145
        gap = (max_width - 3 * block_w) / 2
        start_x = content_center_x - max_width / 2
    else:
        block_w = 125
        gap = (max_width - 4 * block_w) / 3
        start_x = content_center_x - max_width / 2

    sig_img_h = 48

    for i in range(n):
        sg = visible[i]
        bx = start_x + i * (block_w + gap)
        by = baseline_y

        # 1. Signature image (anchored at bottom right onto baseline)
        if sg.get("path"):
            _safe_draw_image(c, sg["path"], bx + 5, by + 3, block_w - 10, sig_img_h, anchor='s')

        # 2. Horizontal baseline rule
        c.setStrokeColor(COLOR_LINE_GRAY)
        c.setLineWidth(1.1)
        c.line(bx, by, bx + block_w, by)

        # 3. Signatory Name (Bold, clear)
        name_str = (sg.get("name") or "Signatory").strip()
        c.setFont("Helvetica-Bold", 9.5)
        c.setFillColor(COLOR_INK_DARK)
        if stringWidth(name_str, "Helvetica-Bold", 9.5) > block_w + 10:
            name_str = name_str[:22] + "..."
        c.drawCentredString(bx + block_w / 2, by - 14, name_str)

        # 4. Signatory Designation (Supporting text)
        desig_str = (sg.get("designation") or "").strip()
        if desig_str:
            c.setFont("Helvetica", 8)
            c.setFillColor(COLOR_INK_MUTED)
            if stringWidth(desig_str, "Helvetica", 8) > block_w + 10:
                desig_str = desig_str[:25] + "..."
            c.drawCentredString(bx + block_w / 2, by - 25, desig_str)


def generate_certificate(path, student_name, reg_no, department, event_title,
                         event_date, certificate_type="Participation", venue="",
                         template=None):
    """
    Generates a world-class, premium landscape A4 certificate matching reference style.
    Compatible with existing call sites and optional event-level template dict.
    """
    t = template or {}

    # Extract template fields or fall back to defaults
    institution = t.get("institution_name", _DEFAULT_INSTITUTION).strip() or _DEFAULT_INSTITUTION
    tagline     = t.get("tagline", _DEFAULT_TAGLINE).strip() or _DEFAULT_TAGLINE
    body_text   = t.get("body_text", _DEFAULT_BODY_TEXT).strip() or _DEFAULT_BODY_TEXT
    footer1     = t.get("footer_line1", _DEFAULT_FOOTER1).strip() or _DEFAULT_FOOTER1
    footer2     = t.get("footer_line2", _DEFAULT_FOOTER2).strip() or _DEFAULT_FOOTER2
    logos       = t.get("logos") or []
    signatures  = t.get("signatures") or []

    # Format Date
    date_display = event_date
    try:
        date_display = datetime.strptime(str(event_date).strip(), "%Y-%m-%d").strftime("%d %B %Y")
    except Exception:
        pass

    page_w, page_h = landscape(A4)  # 841.89 x 595.27
    c = canvas.Canvas(path, pagesize=(page_w, page_h))

    # ─────────────────────────────────────────────────────────────────────────
    # 1. LUXURY BACKGROUND & BASE CANVAS
    # ─────────────────────────────────────────────────────────────────────────
    c.setFillColor(COLOR_CREAM_BG)
    c.rect(0, 0, page_w, page_h, fill=1, stroke=0)

    # ─────────────────────────────────────────────────────────────────────────
    # 2. LEFT GEOMETRIC ANGLED PANELS (Proportional, Elegant, Vertically Balanced)
    # ─────────────────────────────────────────────────────────────────────────
    p_dark = c.beginPath()
    p_dark.moveTo(0, 0)
    p_dark.lineTo(0, page_h)
    p_dark.lineTo(135, page_h)
    p_dark.lineTo(162, 385)
    p_dark.lineTo(174, 298)
    p_dark.lineTo(162, 210)
    p_dark.lineTo(130, 0)
    p_dark.close()
    c.setFillColor(COLOR_EMERALD_DARK)
    c.drawPath(p_dark, fill=1, stroke=0)

    p_mid = c.beginPath()
    p_mid.moveTo(0, 0)
    p_mid.lineTo(0, page_h)
    p_mid.lineTo(92, page_h)
    p_mid.lineTo(122, 298)
    p_mid.lineTo(84, 0)
    p_mid.close()
    c.setFillColor(COLOR_EMERALD_MID)
    c.drawPath(p_mid, fill=1, stroke=0)

    p_accent = c.beginPath()
    p_accent.moveTo(0, page_h)
    p_accent.lineTo(135, page_h)
    p_accent.lineTo(75, 460)
    p_accent.lineTo(0, 500)
    p_accent.close()
    c.setFillColor(COLOR_EMERALD_LIGHT)
    c.drawPath(p_accent, fill=1, stroke=0)

    # Gold pinstripes tracing emerald outer contour
    c.setStrokeColor(COLOR_GOLD_PRIMARY)
    c.setLineWidth(2.2)
    p_gold = c.beginPath()
    p_gold.moveTo(135, page_h)
    p_gold.lineTo(162, 385)
    p_gold.lineTo(174, 298)
    p_gold.lineTo(162, 210)
    p_gold.lineTo(130, 0)
    c.drawPath(p_gold, fill=0, stroke=1)

    c.setStrokeColor(COLOR_GOLD_LIGHT)
    c.setLineWidth(0.7)
    p_gold_inner = c.beginPath()
    p_gold_inner.moveTo(130, page_h)
    p_gold_inner.lineTo(157, 385)
    p_gold_inner.lineTo(169, 298)
    p_gold_inner.lineTo(157, 210)
    p_gold_inner.lineTo(125, 0)
    c.drawPath(p_gold_inner, fill=0, stroke=1)

    # ─────────────────────────────────────────────────────────────────────────
    # 3. BOTTOM ACCENT FOOTER BAR & INSTITUTIONAL MOTTO
    # ─────────────────────────────────────────────────────────────────────────
    p_bot = c.beginPath()
    p_bot.moveTo(130, 0)
    p_bot.lineTo(page_w, 0)
    p_bot.lineTo(page_w, 24)
    p_bot.lineTo(133.7, 24)
    p_bot.close()
    c.setFillColor(COLOR_EMERALD_DARK)
    c.drawPath(p_bot, fill=1, stroke=0)

    c.setStrokeColor(COLOR_GOLD_PRIMARY)
    c.setLineWidth(1.4)
    c.line(133.7, 24, page_w, 24)

    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(COLOR_GOLD_LIGHT)
    c.drawRightString(page_w - 28, 8, f"{footer1.upper()}   \u2022   {footer2.upper()}")

    # ─────────────────────────────────────────────────────────────────────────
    # 4. GOLD MEDALLION ON LEFT PANEL (At Vertical Center)
    # ─────────────────────────────────────────────────────────────────────────
    medallion_x = 96
    medallion_y = 297.64
    _draw_gold_medallion(c, medallion_x, medallion_y, radius=52)

    # ─────────────────────────────────────────────────────────────────────────
    # 5. ELEGANT GOLD PERIMETER FRAME
    # ─────────────────────────────────────────────────────────────────────────
    frame_left = 186
    frame_right = page_w - 22
    frame_top = page_h - 18
    frame_bottom = 30

    c.setStrokeColor(COLOR_GOLD_PRIMARY)
    c.setLineWidth(0.8)
    c.rect(frame_left, frame_bottom, frame_right - frame_left, frame_top - frame_bottom, fill=0, stroke=1)

    # Corner decorative accents
    c.setStrokeColor(COLOR_GOLD_DEEP)
    c.setLineWidth(1.5)
    for corner in [(frame_left, frame_top), (frame_right, frame_top), (frame_left, frame_bottom), (frame_right, frame_bottom)]:
        cx, cy = corner
        dx = 20 if cx == frame_left else -20
        dy = -20 if cy == frame_top else 20
        c.line(cx, cy, cx + dx, cy)
        c.line(cx, cy, cx, cy + dy)

    # Visual center of content zone
    content_center_x = (frame_left + frame_right) / 2  # ~ 503

    # ─────────────────────────────────────────────────────────────────────────
    # 6. TOP LOGO CONTAINER (Larger, evenly spaced, vertically aligned)
    # ─────────────────────────────────────────────────────────────────────────
    logo_box_bottom = _render_top_logos(c, logos, content_center_x, page_h)

    # ─────────────────────────────────────────────────────────────────────────
    # 7. MAIN CERTIFICATE TITLES & RECIPIENT
    # ─────────────────────────────────────────────────────────────────────────
    # Title: "CERTIFICATE" (Large & dominant)
    cert_title_y = logo_box_bottom - 42
    c.setFont("Times-Bold", 44)
    c.setFillColor(COLOR_EMERALD_DARK)
    c.drawCentredString(content_center_x, cert_title_y, "CERTIFICATE")

    # Sub-type: "OF PARTICIPATION / APPRECIATION / MERIT"
    sub_type = certificate_type.upper().strip()
    cert_sub_y = cert_title_y - 24
    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(COLOR_EMERALD_MID)
    tracked_type = "  ".join(list(f"OF {sub_type}"))
    c.drawCentredString(content_center_x, cert_sub_y, tracked_type)

    # Lead-in: "Proudly presented to"
    pres_y = cert_sub_y - 27
    c.setFont("Times-Italic", 14)
    c.setFillColor(COLOR_INK_MUTED)
    c.drawCentredString(content_center_x, pres_y, "Proudly presented to")

    # Recipient Student Name (Significantly larger & centered)
    name_display = (student_name or "STUDENT NAME").strip()
    name_size = 42
    max_name_w = 530
    if stringWidth(name_display, "Times-Bold", name_size) > max_name_w:
        name_size = 36
    if stringWidth(name_display, "Times-Bold", name_size) > max_name_w:
        name_size = 30
    if stringWidth(name_display, "Times-Bold", name_size) > max_name_w:
        name_size = 24

    name_y = pres_y - 48
    c.setFont("Times-Bold", name_size)
    c.setFillColor(COLOR_GOLD_DEEP)
    c.drawCentredString(content_center_x, name_y, name_display)

    # Clean Gold Divider immediately below student name
    div_y = name_y - 20
    _draw_diamond_accent(c, content_center_x, div_y, size=6, line_len=185)

    # ─────────────────────────────────────────────────────────────────────────
    # 8. MAIN CERTIFICATE STATEMENT (Elevated, Prominent, Autowrapped)
    # ─────────────────────────────────────────────────────────────────────────
    curr_y = div_y - 30

    # Line A: Register No. • Department
    meta_parts = []
    if reg_no:
        meta_parts.append(f"Reg. No: {reg_no}")
    if department:
        meta_parts.append(f"Department: {department}")
    meta_line = "   \u2022   ".join(meta_parts) if meta_parts else institution

    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(colors.HexColor("#2D3748"))
    c.drawCentredString(content_center_x, curr_y, meta_line)

    # Line B: Participation Statement
    curr_y -= 26
    c.setFont("Helvetica", 11.5)
    c.setFillColor(colors.HexColor("#4A5568"))
    c.drawCentredString(content_center_x, curr_y, body_text)

    # Line C: Event Title (Bold Emerald, Prominent, Autowrapped)
    title_str = (event_title or "Event").strip()
    title_font_size = 18
    text_max_w = 550
    lines = _wrap_text_into_lines(title_str, "Times-Bold", title_font_size, text_max_w)
    if len(lines) > 2:
        title_font_size = 15
        lines = _wrap_text_into_lines(title_str, "Times-Bold", title_font_size, text_max_w)

    c.setFont("Times-Bold", title_font_size)
    c.setFillColor(COLOR_EMERALD_DARK)

    if len(lines) == 1:
        curr_y -= 30
        c.drawCentredString(content_center_x, curr_y, lines[0])
        curr_y -= 26
    elif len(lines) == 2:
        curr_y -= 28
        c.drawCentredString(content_center_x, curr_y, lines[0])
        curr_y -= 22
        c.drawCentredString(content_center_x, curr_y, lines[1])
        curr_y -= 24
    else:
        curr_y -= 26
        for el in lines[:3]:
            c.drawCentredString(content_center_x, curr_y, el)
            curr_y -= 19
        curr_y -= 20

    # Line D: Date & Venue
    dv_parts = []
    if date_display:
        dv_parts.append(f"Held on: {date_display}")
    if venue:
        dv_parts.append(f"Venue: {venue}")
    dv_line = "   |   ".join(dv_parts)

    c.setFont("Helvetica-Oblique", 11)
    c.setFillColor(colors.HexColor("#4A5568"))
    c.drawCentredString(content_center_x, curr_y, dv_line)

    # ─────────────────────────────────────────────────────────────────────────
    # 9. DEDICATED SIGNATURE SECTION NEAR BOTTOM (No Overlap with Body)
    # ─────────────────────────────────────────────────────────────────────────
    sig_baseline = 100
    _render_signature_blocks(
        c,
        signatures,
        content_center_x=content_center_x,
        baseline_y=sig_baseline,
        max_width=550
    )

    # ─────────────────────────────────────────────────────────────────────────
    # 10. SAVE CANVAS
    # ─────────────────────────────────────────────────────────────────────────
    c.save()
