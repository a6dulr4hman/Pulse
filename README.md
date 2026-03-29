# Pulse AI
## Made for the Global Build Challenge (Powered by Notch)

Pulse AI is an automated, AI-driven technical lead agent built for enterprise development teams. It monitors incoming webhooks and activities from Git and project management platforms, passing the data through Google's Gemini LLMs to detect and prevent architectural overlaps, dependency collisions, and major code regressions before they occur. 

## Key Features

* Automated Dependency Radar
  Real-time analysis of code commits and Jira movements to proactively identify conflicts across disparate engineering teams.
  
* Role-Based Access Control
  Secure boundaries providing tiered dashboards:
  - Admin (System configuration, company-wide management)
  - Team Leader (Team configuration, Git/Jira/Discord token generation)
  - Member (View-only operational metrics)

* System Integrations
  - GitHub Webhooks (Commit monitoring)
  - Jira Webhooks (Workflow transitions and issue assignments)
  - Discord Alert Webhook (Instant alerts on critical architectural breaks)

* Auto-Generated Reporting
  Generates daily standup executive summaries and exports them as clean PDF reports formatted specifically for engineering leadership.

* Modern Glassmorphism UI
  A strictly tailored dark-theme dashboard powered by TailwindCSS and Jinja2 templates, offering a frictionless developer experience.

## Technology Stack

* Backend: FastAPI (Starlette), Uvicorn for asynchronous server processing
* Database: SQLite powered by SQLAlchemy ORM
* Frontend: HTML5, TailwindCSS (CDN), Jinja2 Templating
* AI/LLM: Google Generative AI (Gemini 2.5) module
* Utilities: ReportLab (PDF Generation), PyBcrypt (Security)

## Setup and Installation

1. Clone the repository and navigate into the root directory.

2. Create a virtual environment and activate it:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Start the application:
   ```bash
   uvicorn main:app --reload
   ```

5. Access the Platform:
   Open your browser and navigate to `http://127.0.0.1:8000`
   
   - Note: The database automatically scaffolds on the first load if it does not exist.

## Architectural Notes

Pulse AI intercepts webhooks entirely asynchronously. When a commit or Jira payload is received:
1. The payload is standardized and stored in the ActivityLog.
2. A Starlette BackgroundTask spins up a context-aware prompt parsing the current payload against the team's last 24 hours of logs.
3. If an architectural risk is identified, a "Conflict" entity is generated, flagging red in the Dependency Radar, and immediately pushing an alert sequence to the specified Discord Webhook URL.
