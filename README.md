# Pulse AI
## Made for the Global Build Challenge (Powered by Notch)

Pulse AI is an automated, AI-driven technical lead agent built for enterprise development teams. It monitors incoming webhooks and activities from Git and project management platforms, passing the data through Google's Gemini LLMs to generate reports.

## Key Features

* Modular Extension Architecture
  Fully decoupled integration system allowing drop-in plugins for various platforms:
  - Code Integrations (GitHub, GitLab via generic VCS extensions)
  - Task Management (Jira, Trello, YouTrack via PM extensions)
  - Chat Alerts (Discord, Slack, MS Teams via Chat extensions)

* Next-Generation Security
  - Passkey Support: Device-native biometric authentication (TouchID, FaceID, YubiKey) securely built-in.
  - Multi-Factor Authentication: Standard 6-digit rolling authenticator app (TOTP) workflows.
  - Granular Role-Based Access Control (Admin, Leader, Member).

* Advanced Admin Console
  - Centralized, unified dynamic "Teams" manager with global inline search and filtering.
  - Rapid Data Import: Custom drag-and-drop zones for bulk onboarding users and teams via CSV and JSON payloads.
  - Real-time webhook connection testing before saving infrastructure configurations.

* Auto-Generated Reporting
  Generates daily standup executive summaries and exports them as clean PDF reports formatted specifically for engineering leadership.

* Beautiful Animated UI
  A strictly tailored dark-theme single-page-feel dashboard featuring:
  - Smooth page fades, seamless modal transitions, and dynamic glowing inputs.
  - Intuitive 2-Step interactive Setup Wizard.
  - Interactive profile and security setting layouts leveraging sleek navigation.

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