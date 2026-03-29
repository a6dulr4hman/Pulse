import os
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
import datetime

def generate_morning_pdf(team_name: str, team_id: int, summary: str, logs: list, conflicts: list) -> str:
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    
    # Store dynamic reports by team folder logic
    folder_path = f"reports/team_{team_id}"
    os.makedirs(folder_path, exist_ok=True)
    filename = os.path.join(folder_path, f"report_{date_str}.pdf")
    
    doc = SimpleDocTemplate(filename, pagesize=letter)
    styles = getSampleStyleSheet()
    
    # Custom Styles
    title_style = ParagraphStyle(
        name="TitleStyle",
        parent=styles['Heading1'],
        alignment=TA_CENTER,
        spaceAfter=20
    )
    
    heading_style = styles['Heading2']
    normal_style = styles['Normal']
    
    elements = []
    
    # Title
    elements.append(Paragraph(f"Pulse Morning Sync: {team_name}", title_style))
    elements.append(Paragraph(f"Date: {date_str}", normal_style))
    elements.append(Spacer(1, 20))
    
    # AI Executive Summary
    elements.append(Paragraph("Executive Summary", heading_style))
    elements.append(Paragraph(summary, normal_style))
    elements.append(Spacer(1, 20))
    
    # Active Conflicts
    elements.append(Paragraph("Active Architectural Conflicts", heading_style))
    if conflicts:
        for c in conflicts:
            elements.append(Paragraph(f"• {c.description}", normal_style))
    else:
        elements.append(Paragraph("No active conflicts detected. All clear!", normal_style))
    elements.append(Spacer(1, 20))
    
    # Recent Activity
    elements.append(Paragraph("Recent Activity (Last 24h)", heading_style))
    if logs:
        for log in logs:
            elements.append(Paragraph(f"• {log.developer_name}: {log.action_type} - {log.raw_data}", normal_style))
    else:
        elements.append(Paragraph("No recent activity.", normal_style))

    doc.build(elements)
    
    return os.path.abspath(filename)