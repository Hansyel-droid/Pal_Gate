from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.units import inch
from django.utils import timezone
import io

def generate_incident_report_pdf(log):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    # Title
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=18, textColor=colors.HexColor('#8B5A00'))
    story.append(Paragraph(f"Incident Report #{log.id}", title_style))
    story.append(Spacer(1, 0.2 * inch))

    # Status banner
    status_colors = {'entry': '#10B981', 'exit': '#F2994A', 'denied': '#EF4444'}
    status_text = log.get_action_display().upper()
    banner_style = ParagraphStyle('Banner', parent=styles['Normal'], fontSize=12, textColor=colors.white, backColor=colors.HexColor(status_colors.get(log.action, '#333')), alignment=1, spaceAfter=0.2*inch)
    story.append(Paragraph(status_text, banner_style))
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph(f"Timestamp: {log.timestamp.strftime('%b %d, %Y - %I:%M:%S %p')}", styles['Normal']))
    story.append(Spacer(1, 0.3 * inch))

    # Vehicle Info
    story.append(Paragraph("VEHICLE INFORMATION", styles['Heading2']))
    vehicle_data = [
        ['Plate Number', log.plate_number or 'N/A'],
        ['Model', log.vehicle_model or 'N/A'],
        ['Color', log.vehicle_color or 'N/A'],
    ]
    t = Table(vehicle_data, colWidths=[1.5*inch, 3*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F3F4F6')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.black),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.3 * inch))

    # Driver Info
    story.append(Paragraph("DRIVER INFORMATION", styles['Heading2']))
    driver_data = [['Name', log.driver_name or 'Unknown']]
    if log.rfid_tag and log.rfid_tag.sticker_application:
        applicant = log.rfid_tag.sticker_application.applicant
        driver_data.append(['Affiliation', applicant.get_classification_display() or 'N/A'])
        driver_data.append(['Contact', applicant.contact_number or 'N/A'])
    t2 = Table(driver_data, colWidths=[1.5*inch, 3*inch])
    t2.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F3F4F6')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.black),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
    ]))
    story.append(t2)

    doc.build(story)
    buffer.seek(0)
    return buffer