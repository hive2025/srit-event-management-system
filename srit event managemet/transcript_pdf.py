import os
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from datetime import datetime

def generate_transcript_pdf(path, institute_name, event_name, event_date, transcript_text, timestamp):
    doc = SimpleDocTemplate(
        path,
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
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
        spaceAfter=10
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=colors.HexColor('#0c8b8b'),  # Teal
        alignment=1,  # Center
        spaceAfter=20
    )
    
    label_style = ParagraphStyle(
        'MetaLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=12,
        textColor=colors.HexColor('#18232d')
    )
    
    val_style = ParagraphStyle(
        'MetaVal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=12,
        textColor=colors.HexColor('#687784')
    )
    
    body_heading_style = ParagraphStyle(
        'BodyHeading',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.HexColor('#083b5c'),
        spaceBefore=15,
        spaceAfter=10
    )
    
    body_style = ParagraphStyle(
        'BodyTextCustom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=15,
        textColor=colors.HexColor('#18232d'),
        spaceAfter=10
    )
    
    footer_style = ParagraphStyle(
        'FooterCustom',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#687784'),
        alignment=1
    )

    story = []

    # 1. Header (Institute & Department Details)
    story.append(Paragraph(institute_name.upper(), title_style))
    story.append(Paragraph("HIVE • HUB FOR INNOVATION AND ENTREPRENEURSHIP", subtitle_style))
    story.append(Spacer(1, 10))

    # 2. Metadata Table
    formatted_date = event_date
    try:
        formatted_date = datetime.strptime(event_date, "%Y-%m-%d").strftime("%d %B %Y")
    except Exception:
        pass
        
    formatted_ts = timestamp
    try:
        if 'T' in timestamp:
            dt = datetime.fromisoformat(timestamp)
            formatted_ts = dt.strftime("%d %B %Y, %I:%M %p")
    except Exception:
        pass

    meta_data = [
        [Paragraph("Event Name:", label_style), Paragraph(event_name, val_style)],
        [Paragraph("Event Date:", label_style), Paragraph(formatted_date, val_style)],
        [Paragraph("Transcript Date:", label_style), Paragraph(formatted_ts, val_style)]
    ]
    
    # Calculate widths for A4: 595 - 80 = 515 total width
    t = Table(meta_data, colWidths=[110, 405])
    t.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor('#edf1f3')),
    ]))
    
    story.append(t)
    story.append(Spacer(1, 20))

    # 3. Transcript Body
    story.append(Paragraph("SPEAKER TRANSCRIPT", body_heading_style))
    
    # Split text into paragraphs by newline
    paragraphs = transcript_text.strip().split('\n')
    for p_text in paragraphs:
        p_text_clean = p_text.strip()
        if p_text_clean:
            story.append(Paragraph(p_text_clean, body_style))
            
    story.append(Spacer(1, 30))
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"Generated via SRIT HIVE Event Portal on {datetime.now().strftime('%d %B %Y, %I:%M %p')}", footer_style))

    # Build the document
    doc.build(story)
