import os
import sys

# Ensure the root directory is in the Python path so direct execution doesn't fail to find 'database' or 'models'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import logging
import google.generativeai as genai
from datetime import datetime, timedelta

from database import SessionLocal
from models import ActivityLog, Company, Team, User
from services.discord_service import send_discord_alert

logger = logging.getLogger(__name__)

# Helper to get the correct reporting time window
def get_report_start_time():
    now = datetime.utcnow()
    if now.hour < 12:
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)

def generate_fallback_markdown(context_str: str) -> str:
    lines = context_str.split('\n')
    bullets = "\n".join([f"- {line}" for line in lines if line.strip()])
    return f"""# ⚠️ Fallback Report Activated
> **Note:** The AI integration is currently offline natively or API connection failed. This report provides a raw, chronological history of commits and team activities.

## 1. Executive Summary
(AI Connection Disabled - Automated Fallback Summarization)

## 2. Priority: Major Commits & Risks
(AI Analysis Offline - Raw Commit Data Provided)

## 3. Member Activity Report
{bullets}

## 4. Pending / Rollover Tasks
(Rollover analysis offline - pending tasks not aggregated)
"""

async def generate_daily_executive_summary(context_str: str, api_key: str = None) -> str:
    """Generate a daily sync summary for the morning PDF."""
    if not api_key:
        return generate_fallback_markdown(context_str)
        
    try:
        genai.configure(api_key=api_key.strip())
        prompt = f"""
        You are an Elite Tech Lead preparing for a daily async standup. 
        Analyze the following team activity (commits, blockers, JIRA syncs if any) and provide a structured text summary.
        
        CRITICAL FORMATTING INSTRUCTIONS:
        - Output strictly in beautifully formatted Markdown.
        - Create visually appealing colored boxes using HTML or Blockquotes. (e.g., `<div style="background-color: #ffebee; border-left: 4px solid #f44336; padding: 10px; margin: 10px 0; color: #b71c1c;"><strong>⚠️ Alert:</strong> YOUR ALERT HERE</div>` or `> ⚠️ **ALERT**`)
        - Use emojis for metrics and status.
        
        Structure your report EXACTLY with these 4 sections:
        
        ## 1. Executive Summary
        (2-3 sentences max on team progress, formatted cleanly)
        
        ## 2. Priority: Major Commits & Risks
        (Bullet points. List major code movements. Highlight active bottlenecks or critical risks using the styled red/yellow alert boxes described above.)
        
        ## 3. Member Activity Report
        (Summarize specifically what each unique user built/pushed in bullet points grouped by their names)

        ## 4. Pending / Rollover Tasks
        (If there are Jira tasks that have been in progress for multiple updates without moving to 'Done', list them here gently to keep them visible for alignment, without penalizing the developer).

        Data Log for the Day:
        {context_str}
        """
        
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Error generating summary: {e}")
        return generate_fallback_markdown(context_str)