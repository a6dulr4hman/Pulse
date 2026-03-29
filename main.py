import os
import secrets
import logging
import hmac
import hashlib
from fastapi import FastAPI, Request, Form, Depends, HTTPException, BackgroundTasks, status
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import bcrypt
from starlette.middleware.sessions import SessionMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from database import engine, Base, get_db
from models import User, Company, Team, ActivityLog, Conflict
from services.ai_service import analyze_commit_for_collisions, generate_daily_executive_summary
from services.discord_service import send_discord_alert
from datetime import datetime, timedelta

# Setup Basic Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Create DB Tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Pulse AI - Auto Tech Lead")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get('/favicon.svg', include_in_schema=False)
async def favicon():
    return FileResponse('static/favicon.svg')


templates = Jinja2Templates(directory="templates")

# Configure SessionMiddleware for Login Cookies
SECRET_KEY = "pulse_ai_enterprise_super_secret_session_key_123!@#"
app.add_middleware(
    SessionMiddleware, 
    secret_key=SECRET_KEY, 
    session_cookie="pulse_session", 
    max_age=86400 * 30, # Save for 30 days
    same_site="lax", 
    https_only=False
)


def form_redirect(url: str):
    response = RedirectResponse(url=url, status_code=303)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def form_redirect(url: str):
    response = RedirectResponse(url=url, status_code=303)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

def browser_redirect(url: str):


    response = RedirectResponse(url=url, status_code=303)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def active_dashboard_redirect(user):
    if user.role == "Admin":
        return browser_redirect("/admin/dashboard")
    elif user.role == "Team Leader":
        return browser_redirect("/leader/dashboard")
    else:
        return browser_redirect("/member/dashboard")


def get_current_user(request: Request, db: Session = Depends(get_db)):
    """Middleware-like dependence to fetch currently logged in User via session ID."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


class NotAuthenticatedException(Exception):
    pass

def login_required(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise NotAuthenticatedException("Not authenticated")
    return user

@app.exception_handler(NotAuthenticatedException)
async def auth_exception_handler(request: Request, exc: NotAuthenticatedException):
    return browser_redirect(f"/login?next={request.url.path}")


def get_report_start_time():
    now = datetime.utcnow()
    if now.hour < 12:
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)

async def send_daily_summaries_job():
    """APScheduler Cron Job: Runs at 22:00 Server Time. Reads activity and generates a summary via Gemini."""
    db = next(get_db())
    company = db.query(Company).first()
    if not company or not company.gemini_api_key:
        print("CRON: No Company setup or API key missing.")
        return

    teams = db.query(Team).all()
    start_time = get_report_start_time()
    
    for team in teams:
        if not team.discord_webhook:
            continue
            
        logs = db.query(ActivityLog).filter(
            ActivityLog.team_id == team.id,
            ActivityLog.timestamp >= start_time
        ).all()
        
        if not logs:
            await send_discord_alert(team.discord_webhook, f"🔔 **Daily Pulse Sync (Team: {team.name})**\nNo activity logged today.")
            continue
            
        context_str = "\n".join([f"[{l.timestamp}] {l.developer_name} ({l.action_type}): {l.raw_data}" for l in logs])
        
        summary = await generate_daily_executive_summary(context_str, company.gemini_api_key)
        
        report = f"🔔 **Daily Pulse Sync Report: {team.name}**\n\n{summary}"
        await send_discord_alert(team.discord_webhook, report)

    db.close()

# Start the APScheduler
scheduler = AsyncIOScheduler()
scheduler.add_job(send_daily_summaries_job, CronTrigger(hour=22, minute=0))

@app.on_event("startup")
async def startup_event():
    scheduler.start()
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Pulse Started. Background Job Scheduled for 22:00 Server Time. (Current Server Time: {current_time})")


# --- Webhooks (GitHub / Jira passive ingestion) --- #

@app.post("/webhook/github/{team_id}")
async def github_webhook(team_id: str, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Ingest GitHub events passively."""
    logger.info(f"Received webhook for team_id: {team_id}")
    if not team_id.isdigit():
        logger.warning(f"Invalid team_id format: {team_id}")
        return {"status": "ignored", "detail": "Invalid team_id"}
        
    team = db.query(Team).filter(Team.id == int(team_id)).first()
    if not team:
        logger.warning(f"Team ID not found: {team_id}")
        return {"status": "ignored", "detail": "Team Not Found"}
    
    try:
        body = await request.body()
        payload = await request.json()
        logger.info(f"Webhook payload received. Keys: {list(payload.keys())}")
    except Exception as e:
        logger.error(f"Failed to parse JSON payload. Error: {e}")
        return {"status": "error", "detail": "invalid json"}
        
    if team.github_secret:
        signature_header = request.headers.get("x-hub-signature-256")
        if not signature_header:
            logger.warning("Missing GitHub signature.")
            raise HTTPException(status_code=403, detail="Missing signature")
            
        expected_signature = "sha256=" + hmac.new(team.github_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_signature, signature_header):
            logger.warning("Invalid GitHub signature.")
            raise HTTPException(status_code=403, detail="Invalid signature")

    if payload.get("zen"): # Ping event
        logger.info("GitHub ping event received")
        return {"status": "ping received"}
        
    if "commits" in payload:
        pusher = payload.get("pusher", {}).get("name", "Unknown")
        repo_name = payload.get("repository", {}).get("full_name", "Unknown Repo")
        logger.info(f"Processing commits from pusher: {pusher} on repo: {repo_name}")
        
        user_match = db.query(User).filter(User.github_username == pusher).first()
        dev_name = user_match.username if user_match else pusher
        
        for commit in payload.get("commits", []):
            msg = commit.get("message", "")
            logger.info(f"Commit msg: {msg}")
            
            # Log it for the 22:00 Job
            log = ActivityLog(
                team_id=team.id,
                developer_name=dev_name,
                action_type="GITHUB_COMMIT",
                raw_data=f"Pushed to {repo_name}: {msg}"
            )
            db.add(log)
            db.commit()
            
            # Passively review architectural impact
            background_tasks.add_task(
                analyze_commit_for_collisions, 
                team.id, 
                dev_name, 
                msg, 
                team.discord_webhook
            )
            logger.info("Added passive collision analysis to background tasks.")
            
    return {"status": "processed"}


# --- UI Routes & Authentication --- #

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    logger.info("Accessing index route (/)")
    user = get_current_user(request, db)
    if user:
        logger.info(f"User {user.username} already logged in, role: {user.role}. Redirecting to dashboard.")
        if user.role == "Admin":
            return browser_redirect("/admin/dashboard")
        elif user.role == "Team Leader":
            return browser_redirect("/leader/dashboard")
        else:
            return browser_redirect("/member/dashboard")
            
    company = db.query(Company).first()
    if not company:
        logger.info("No company found in DB. Redirecting to /setup.")
        return browser_redirect("/setup")
        
    logger.info("Company exists but no user session. Redirecting to /login.")
    return browser_redirect("/login")


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, db: Session = Depends(get_db)):
    logger.info("Accessing /setup page via GET")
    company = db.query(Company).first()
    if company:
         logger.info("Company already setup. Redirecting away from /setup to /login.")
         return browser_redirect("/login")
    logger.info("Rendering setup.html")
    return templates.TemplateResponse(request=request, name="setup.html")


@app.post("/setup")
async def setup_post(request: Request, company_name: str = Form(...), gemini_key: str = Form(...), admin_user: str = Form(...), admin_full_name: str = Form(default=""), admin_pass: str = Form(...), db: Session = Depends(get_db)):
    logger.info(f"Processing /setup POST. Company Name: {company_name}")
    try:
        company = Company(name=company_name, gemini_api_key=gemini_key)
        db.add(company)
        db.flush() 
        logger.info(f"Company {company_name} created successfully.")
        
        hashed = bcrypt.hashpw(admin_pass[:72].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        admin = User(username=admin_user, full_name=admin_full_name, password_hash=hashed, role="Admin", company_id=company.id)
        db.add(admin)
        db.commit()
        logger.info(f"Admin user {admin_user} created successfully. Redirecting to /login.")
        
        return browser_redirect("/login")
    except Exception as e:
        db.rollback()
        logger.error(f"Error during setup POST: {str(e)}")
        raise e


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, next: str = ""):
    logger.info("Accessing /login page via GET. Rendering login.html")
    return templates.TemplateResponse(request=request, name="login.html", context={"next": next})

@app.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form(default=""), db: Session = Depends(get_db)):
    logger.info(f"Processing /login POST for username: {username}")
    user = db.query(User).filter(User.username == username).first()
    
    if not user or not bcrypt.checkpw(password[:72].encode('utf-8'), user.password_hash.encode('utf-8')):
        logger.warning(f"Login failed for username: {username}")
        return templates.TemplateResponse(request=request, name="login.html", context={"error": "Invalid credentials", "next": next})
        
    logger.info(f"Login successful for {username}. Setting session ID.")
    request.session["user_id"] = user.id
    
    if next and next.startswith("/") and not next.startswith("//"):
        return form_redirect(next)

    # RBAC Routing
    logger.info(f"Routing user {username} based on role: {user.role}")
    if user.role == "Admin":
        return browser_redirect("/admin/dashboard")
    elif user.role == "Team Leader":
        return browser_redirect("/leader/dashboard")
    else:
        return browser_redirect("/member/dashboard")

@app.get("/logout")
async def logout(request: Request):
    logger.info("Processing /logout. Clearing session.")
    request.session.clear()
    return browser_redirect("/login")


# --- RBAC Dashboards --- #

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    logger.info("Accessing /admin/dashboard")
    user = login_required(request, db)
    if user.role != "Admin":
         logger.warning(f"Access denied to /admin/dashboard for user {user.username} with role {user.role}")
         return active_dashboard_redirect(user)
    
    teams = db.query(Team).all()
    users = db.query(User).all()
    company = db.query(Company).first()
    logger.info(f"Rendering admin_dashboard.html explicitly for {user.username}")
    return templates.TemplateResponse(request=request, name="admin_dashboard.html", context={"user": user, "teams": teams, "users": users, "company": company})

@app.get("/leader/dashboard", response_class=HTMLResponse)
async def leader_dashboard(request: Request, db: Session = Depends(get_db)):
    logger.info("Accessing /leader/dashboard")
    user = login_required(request, db)
    if user.role != "Team Leader":
         logger.warning(f"Access denied to /leader/dashboard for user {user.username} with role {user.role}")
         return active_dashboard_redirect(user)
         
    team = db.query(Team).filter(Team.id == user.team_id).first()
    logs = db.query(ActivityLog).filter(ActivityLog.team_id == user.team_id).order_by(ActivityLog.timestamp.desc()).limit(20).all() if team else []
    conflicts = db.query(Conflict).filter(Conflict.team_id == user.team_id).all() if team else []
    team_members = db.query(User).filter(User.team_id == user.team_id).all() if team else []
    
    logger.info(f"Rendering leader_dashboard.html explicitly for {user.username}")
    return templates.TemplateResponse(request=request, name="leader_dashboard.html", context={"user": user, "team": team, "logs": logs, "conflicts": conflicts, "team_members": team_members})


@app.get("/member/dashboard", response_class=HTMLResponse)
async def member_dashboard(request: Request, db: Session = Depends(get_db)):
    logger.info("Accessing /member/dashboard")
    user = login_required(request, db)
    team = db.query(Team).filter(Team.id == user.team_id).first()
    logs = db.query(ActivityLog).filter(ActivityLog.developer_name == user.username).order_by(ActivityLog.timestamp.desc()).limit(10).all()
    
    logger.info(f"Rendering member_dashboard.html explicitly for {user.username}")
    return templates.TemplateResponse(request=request, name="member_dashboard.html", context={"user": user, "team": team, "logs": logs})

# Additional API Routes for Admnins to create users/teams omitted for brevity, logic belongs in the views.

@app.post("/admin/team")
async def create_team(request: Request, name: str = Form(...), discord_webhook: str = Form(...), db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Admin":
         return active_dashboard_redirect(user)
    
    g_secret = secrets.token_urlsafe(32)
    team = Team(name=name, discord_webhook=discord_webhook, github_secret=g_secret)
    db.add(team)
    db.commit()
    logger.info(f"Team {name} created with specific secret: {g_secret}")
    return form_redirect("/admin/dashboard")

@app.post("/admin/user")
async def create_user(request: Request, username: str = Form(...), full_name: str = Form(default=""), password: str = Form(...), email: str = Form(default=""), role: str = Form(...), team_id: str = Form(default=""), db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Admin":
         return active_dashboard_redirect(user)
    
    parsed_team_id = int(team_id) if team_id and team_id.isdigit() else None
    hashed = bcrypt.hashpw(password[:72].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    new_user = User(username=username, full_name=full_name, email=email, password_hash=hashed, role=role, team_id=parsed_team_id)
    db.add(new_user)
    db.commit()
    return form_redirect("/admin/dashboard")

@app.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request, db: Session = Depends(get_db)):
    user = login_required(request, db)
    return templates.TemplateResponse(request=request, name="settings.html", context={"user": user})

@app.post("/settings/password")
async def settings_password(request: Request, current_password: str = Form(...), new_password: str = Form(...), db: Session = Depends(get_db)):
    user = login_required(request, db)
    if not bcrypt.checkpw(current_password[:72].encode('utf-8'), user.password_hash.encode('utf-8')):
        return templates.TemplateResponse(request=request, name="settings.html", context={"user": user, "error": "Incorrect current password."})
    
    user.password_hash = bcrypt.hashpw(new_password[:72].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    db.commit()
    return templates.TemplateResponse(request=request, name="settings.html", context={"user": user, "success": "Password changed successfully."})

@app.post("/admin/company/key")
async def update_company_key(request: Request, gemini_api_key: str = Form(...), db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Admin":
        return active_dashboard_redirect(user)
    
    company = db.query(Company).first()
    if company:
        company.gemini_api_key = gemini_api_key.strip()
        db.commit()
    return form_redirect("/admin/dashboard")

@app.post("/admin/team/delete/{team_id}")
async def admin_delete_team(request: Request, team_id: int, db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Admin":
         return active_dashboard_redirect(user)
    
    team = db.query(Team).filter(Team.id == team_id).first()
    if team:
        db.query(User).filter(User.team_id == team_id).update({"team_id": None})
        db.query(ActivityLog).filter(ActivityLog.team_id == team_id).delete()
        db.query(Conflict).filter(Conflict.team_id == team_id).delete()
        db.delete(team)
        db.commit()
    return form_redirect("/admin/dashboard")

@app.post("/admin/user/delete/{user_id}")
async def admin_delete_user(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Admin":
         return active_dashboard_redirect(user)
    
    target_user = db.query(User).filter(User.id == user_id).first()
    if target_user and target_user.id != user.id:
        db.delete(target_user)
        db.commit()
    return form_redirect("/admin/dashboard")

@app.post("/leader/user/add")
async def leader_add_user(request: Request, username: str = Form(...), full_name: str = Form(default=""), password: str = Form(...), email: str = Form(default=""), db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Team Leader" or not user.team_id:
        return active_dashboard_redirect(user)
        
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        return form_redirect("/leader/dashboard?error=exists")
        
    new_user = User(
        username=username,
        full_name=full_name,
        email=email,
        password_hash=bcrypt.hashpw(password[:72].encode('utf-8'), bcrypt.gensalt()).decode('utf-8'),
        role="Member",
        team_id=user.team_id
    )
    db.add(new_user)
    db.commit()
    return form_redirect("/leader/dashboard")

@app.post("/leader/user/remove/{user_id}")
async def leader_remove_user(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Team Leader" or not user.team_id:
        return active_dashboard_redirect(user)
        
    target = db.query(User).filter(User.id == user_id, User.team_id == user.team_id, User.role != "Team Leader").first()
    if target:
        target.team_id = None
        db.commit()
    return form_redirect("/leader/dashboard")


from services.ai_service import generate_daily_executive_summary, analyze_commit_for_collisions
from services.discord_service import send_discord_alert
from models import TeamReport, ActivityLog, Company
from datetime import timedelta

@app.post("/demo/simulate/{team_id}")
async def simulate_demo_conflict(request: Request, team_id: int, db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Team Leader" or user.team_id != team_id:
        return active_dashboard_redirect(user)
        
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        return form_redirect("/leader/dashboard")
        
    # Inject fake logs
    log1 = ActivityLog(
        team_id=team.id,
        developer_name="Alice (Demo)",
        action_type="GITHUB_COMMIT",
        raw_data="Dropped the legacy users table in DB to modernize the schema"
    )
    db.add(log1)
    db.commit()
    
    # Immediately trigger the background collision check for the second action
    await analyze_commit_for_collisions(
        team.id, 
        "Bob (Demo)", 
        "Updated frontend API to brutally query the legacy users table across all marketing pages", 
        team.discord_webhook
    )
    
    return form_redirect("/leader/dashboard")

@app.post("/leader/conflict/resolve/{conflict_id}")
async def resolve_conflict(request: Request, conflict_id: int, db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Team Leader" or not user.team_id:
        return active_dashboard_redirect(user)
        
    conflict = db.query(Conflict).filter(Conflict.id == conflict_id, Conflict.team_id == user.team_id).first()
    if conflict:
        # Instead of just marking it resolved, let's delete it so it vanishes from the dashboard
        db.delete(conflict)
        db.commit()
        
    return form_redirect("/leader/dashboard")

@app.post("/leader/trigger_report")
async def leader_trigger_report(request: Request, db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Team Leader" or not user.team_id:
        return active_dashboard_redirect(user)
    
    team = user.team
    company = db.query(Company).first()
    
    start_time = get_report_start_time()
    logs = db.query(ActivityLog).filter(
        ActivityLog.team_id == team.id,
        ActivityLog.timestamp >= start_time
    ).all()
    
    if not logs:
        summary = "No activity logged today."
    else:
        context_str = "\\n".join([f"[{l.timestamp}] {l.developer_name} ({l.action_type}): {l.raw_data}" for l in logs])
        api_key = company.gemini_api_key if company else None
        summary = await generate_daily_executive_summary(context_str, api_key)
        
    report = TeamReport(team_id=team.id, summary=summary)
    db.add(report)
    db.commit()
    db.refresh(report)
    
    if team.discord_webhook:
        host = request.headers.get("x-forwarded-host", request.url.hostname)
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        port = request.headers.get("x-forwarded-port", request.url.port)
        
        base_url = f"{scheme}://{host}"
        if port and port not in ["80", "443"] and ":" not in host:
            base_url += f":{port}"
            
        report_url = f"{base_url}/report/{report.id}"
        await send_discord_alert(team.discord_webhook, f"📊 **New On-Demand Pulse Report!**\nA team leader ({user.username}) triggered a report.\nView it here: {report_url}")
        
    return form_redirect(f"/report/{report.id}")

@app.get("/report/{report_id}", response_class=HTMLResponse)
async def view_report(request: Request, report_id: int, db: Session = Depends(get_db)):
    user = login_required(request, db) # Raises NotAuthenticatedException if not logged in
    
    report = db.query(TeamReport).filter(TeamReport.id == report_id).first()
    if not report:
        return HTMLResponse("Report not found", status_code=404)
        
    if user.role != "Admin" and user.team_id != report.team_id:
        return active_dashboard_redirect(user)
        
    return templates.TemplateResponse(request=request, name="report.html", context={"report": report, "user": user, "hide_nav": True})


from fastapi import HTTPException

@app.post("/leader/discord/update")
async def update_discord_webhook(request: Request, discord_webhook: str = Form(...), db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Team Leader" or not user.team_id:
        return active_dashboard_redirect(user)
    
    team = user.team
    team.discord_webhook = discord_webhook.strip()
    db.commit()
    
    return form_redirect("/leader/dashboard")

@app.post("/leader/discord/update")
async def update_discord_webhook(request: Request, discord_webhook: str = Form(...), db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Team Leader" or not user.team_id:
        return active_dashboard_redirect(user)
    
    team = user.team
    team.discord_webhook = discord_webhook.strip()
    db.commit()
    
    return form_redirect("/leader/dashboard")

@app.post("/leader/jira/setup")
async def setup_jira(request: Request, db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Team Leader" or not user.team_id:
        return active_dashboard_redirect(user)
    
    team = user.team
    if not team.jira_connection:
        import secrets
        team.jira_connection = secrets.token_urlsafe(32)
        db.commit()
        
    return form_redirect("/leader/dashboard")

@app.post("/webhook/jira/{team_id}/{secret}")
async def jira_webhook(
    request: Request,
    team_id: int,
    secret: str,
    db: Session = Depends(get_db)
):
    team = db.query(Team).filter(Team.id == team_id, Team.jira_connection == secret).first()
    if not team:
        raise HTTPException(status_code=403, detail="Invalid team or secret")
    
    try:
        payload = await request.json()
        with open("last_jira_payload.json", "w") as f:
            import json
            json.dump(payload, f, indent=2)
        print(f"JIRA PAYLOAD saved to last_jira_payload.json")
    except Exception as e:
        print(f"Error parsing jira payload: {e}")
        payload = {}
        
    event = payload.get("webhookEvent")
    print(f"Jira event: {event}")
    if event == "jira:issue_updated":
        issue = payload.get("issue", {})
        user_info = payload.get("user", {})
        changelog = payload.get("changelog", {}).get("items", [])
        print(f"Jira changelog: {changelog}")
        
        key = issue.get("key", "UNKNOWN")
        summary = issue.get("fields", {}).get("summary", "No Title")
        developer_name = user_info.get("displayName", "Jira User")
        
        for item in changelog:
            field_name = str(item.get("field", "")).lower()
            field_id = str(item.get("fieldId", "")).lower()
            if field_name == "status" or field_id == "status":
                from_status = item.get("fromString", "Unknown")
                to_status = item.get("toString", "Unknown")
                
                log_msg = f"Task moved: [{key}] {summary} (from '{from_status}' to '{to_status}')"
                print(f"Jira log msg: {log_msg}")
                log_entry = ActivityLog(
                    team_id=team.id,
                    developer_name=developer_name,
                    action_type="JIRA_STATUS_UPDATE",
                    raw_data=log_msg
                )
                db.add(log_entry)
        db.commit()
        
    return {"status": "success"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, proxy_headers=True, forwarded_allow_ips="*")
