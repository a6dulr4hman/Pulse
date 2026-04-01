import os
import secrets
import logging
import hmac
import hashlib
import json
import csv
import io
from fastapi import FastAPI, Request, Form, Depends, HTTPException, BackgroundTasks, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import bcrypt
from starlette.middleware.sessions import SessionMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from database import engine, Base, get_db
from models import User, Company, Team, ActivityLog, Passkey
import base64
from webauthn import generate_registration_options, verify_registration_response, generate_authentication_options, verify_authentication_response, options_to_json
import pyotp
import qrcode


from services.ai_service import generate_daily_executive_summary
from services.alert_service import send_chat_alert
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

# Setup Basic Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Create DB Tables
Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    scheduler.start()
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Pulse Started. Background Job Scheduled for 22:00 Server Time. (Current Server Time: {current_time})")
    
    yield
    
    # Shutdown actions
    scheduler.shutdown()


# WEBAUTHN CONFIG
RP_ID = "localhost" # or appropriate domain
RP_NAME = "PulseAI"
EXPECTED_ORIGIN = "http://localhost:8000" # fallback


# WEBAUTHN CONFIG
RP_ID = "localhost" # or appropriate domain
RP_NAME = "PulseAI"
EXPECTED_ORIGIN = "http://localhost:8000" # fallback

app = FastAPI(title="Pulse AI - Auto Tech Lead", lifespan=lifespan)
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
    elif user.role == "Leader":
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
        if not team.chat_webhook_url:
            continue
            
        logs = db.query(ActivityLog).filter(
            ActivityLog.team_id == team.id,
            ActivityLog.timestamp >= start_time
        ).all()
        
        if not logs:
            await send_chat_alert(team.chat_provider, team.chat_webhook_url, f"🔔 **Daily Pulse Sync (Team: {team.name})**\nNo activity logged today.")
            continue
            
        context_str = "\n".join([f"[{l.timestamp}] {l.developer_name} ({l.action_type}): {l.raw_data}" for l in logs])
        
        summary = await generate_daily_executive_summary(context_str, company.gemini_api_key)
        
        report = f"🔔 **Daily Pulse Sync Report: {team.name}**\n\n{summary}"
        await send_chat_alert(team.chat_provider, team.chat_webhook_url, report)

    db.close()

# Start the APScheduler
scheduler = AsyncIOScheduler()
scheduler.add_job(send_daily_summaries_job, CronTrigger(hour=22, minute=0))

# --- Webhooks (GitHub / Jira passive ingestion) --- #

@app.post("/webhook/vcs/{vcs_provider}/{team_id}")
async def vcs_webhook(vcs_provider: str, team_id: str, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
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
        
    if team.vcs_secret:
        signature_header = request.headers.get("x-hub-signature-256")
        if not signature_header:
            logger.warning("Missing GitHub signature.")
            raise HTTPException(status_code=403, detail="Missing signature")
            
        expected_signature = "sha256=" + hmac.new(team.vcs_secret.encode(), body, hashlib.sha256).hexdigest()
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
        
        user_match = db.query(User).filter(User.vcs_username == pusher).first()
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
        elif user.role == "Leader":
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
async def setup_page(request: Request, error: str = "", db: Session = Depends(get_db)):
    logger.info("Accessing /setup page via GET")
    company = db.query(Company).first()
    if company:
         logger.info("Company already setup. Redirecting away from /setup to /login.")
         return browser_redirect("/login")
    logger.info("Rendering setup.html")
    return templates.TemplateResponse(request=request, name="setup.html", context={"error": error})


@app.post("/setup")
async def setup_post(request: Request, company_name: str = Form(...), gemini_key: str = Form(...), admin_user: str = Form(...), admin_full_name: str = Form(...), admin_pass: str = Form(...), admin_confirm_pass: str = Form(...), db: Session = Depends(get_db)):
    logger.info(f"Processing /setup POST. Company Name: {company_name}")
    
    if not company_name.strip() or not gemini_key.strip() or not admin_user.strip() or not admin_full_name.strip() or not admin_pass.strip():
        return form_redirect("/setup?error=All+fields+are+required")
    
    if admin_pass != admin_confirm_pass:
        return form_redirect("/setup?error=Passwords+do+not+match")

    try:
        company = Company(name=company_name, gemini_api_key=gemini_key)
        db.add(company)
        db.flush() 
        logger.info(f"Company {company_name} created successfully.")
        
        hashed = bcrypt.hashpw(admin_pass[:72].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        admin = User(username=admin_user, email=admin_user, full_name=admin_full_name, password_hash=hashed, role="Admin", company_id=company.id)
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
    logger.info(f"Processing /login POST for username/email: {username}")
    user = db.query(User).filter((User.username == username) | (User.email == username)).first()
    
    if not user or not bcrypt.checkpw(password[:72].encode('utf-8'), user.password_hash.encode('utf-8')):
        logger.warning(f"Login failed for username/email: {username}")
        return templates.TemplateResponse(request=request, name="login.html", context={"error": "Invalid credentials", "next": next})
        
    # Enforce strictly email-only login for Admins
    if user.role == "Admin" and user.username == username and user.email != username:
        logger.warning(f"Admin attempt to log in with username {username}. Rejected.")
        return templates.TemplateResponse(request=request, name="login.html", context={"error": "Admins must log in using an email address", "next": next})

    logger.info(f"Login password successful for {username}.")
    if user.totp_enabled:
        request.session["pending_user_id"] = user.id
        # We need a GET request for 2FA next.
        return browser_redirect(f"/login/2fa?next={next}")

    logger.info(f"Setting session ID for {username}.")
    request.session["user_id"] = user.id
    
    if next and next.startswith("/") and not next.startswith("//"):
        return form_redirect(next)

    # RBAC Routing
    logger.info(f"Routing user {username} based on role: {user.role}")
    if user.role == "Admin":
        return browser_redirect("/admin/dashboard")
    elif user.role == "Leader":
        return browser_redirect("/leader/dashboard")
    else:
        return browser_redirect("/member/dashboard")

@app.get("/login/2fa", response_class=HTMLResponse)
async def login_2fa_get(request: Request, next: str = ""):
    if "pending_user_id" not in request.session:
        return browser_redirect("/login")
    return templates.TemplateResponse(request=request, name="login_2fa.html", context={"next": next})

@app.post("/login/2fa")
async def login_2fa_post(request: Request, code: str = Form(...), next: str = Form(default=""), db: Session = Depends(get_db)):
    pending_user_id = request.session.get("pending_user_id")
    if not pending_user_id:
        return browser_redirect("/login")
    
    user = db.query(User).filter(User.id == pending_user_id).first()
    if not user or not user.totp_enabled:
        return browser_redirect("/login")
        
    import pyotp
    totp = pyotp.TOTP(user.totp_secret)
    if totp.verify(code):
        request.session["user_id"] = user.id
        del request.session["pending_user_id"]
        
        if next and next.startswith("/") and not next.startswith("//"):
            return form_redirect(next)
            
        if user.role == "Admin":
            return browser_redirect("/admin/dashboard")
        elif user.role == "Leader":
            return browser_redirect("/leader/dashboard")
        else:
            return browser_redirect("/member/dashboard")
    else:
        return templates.TemplateResponse(request=request, name="login_2fa.html", context={"error": "Invalid 2FA code", "next": next})

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
    if user.role != "Leader":
         logger.warning(f"Access denied to /leader/dashboard for user {user.username} with role {user.role}")
         return active_dashboard_redirect(user)
         
    team = db.query(Team).filter(Team.id == user.team_id).first()
    logs = db.query(ActivityLog).filter(ActivityLog.team_id == user.team_id).order_by(ActivityLog.timestamp.desc()).limit(20).all() if team else []
    team_members = db.query(User).filter(User.team_id == user.team_id).all() if team else []
    
    logger.info(f"Rendering leader_dashboard.html explicitly for {user.username}")
    return templates.TemplateResponse(request=request, name="leader_dashboard.html", context={"user": user, "team": team, "logs": logs, "team_members": team_members})


@app.get("/member/dashboard", response_class=HTMLResponse)
async def member_dashboard(request: Request, db: Session = Depends(get_db)):
    logger.info("Accessing /member/dashboard")
    user = login_required(request, db)
    team = db.query(Team).filter(Team.id == user.team_id).first()
    logs = db.query(ActivityLog).filter(ActivityLog.developer_name == user.username).order_by(ActivityLog.timestamp.desc()).limit(10).all()
    
    logger.info(f"Rendering member_dashboard.html explicitly for {user.username}")
    return templates.TemplateResponse(request=request, name="member_dashboard.html", context={"user": user, "team": team, "logs": logs})

# Additional API Routes for Admnins to create users/teams omitted for brevity, logic belongs in the views.

@app.post("/admin/team/test-webhook")
async def test_team_webhook(request: Request, db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Admin":
         raise HTTPException(status_code=403, detail="Unauthorized")
    
    data = await request.json()
    webhook_url = data.get("webhook_url")
    provider = data.get("provider", "discord")
    if not webhook_url:
        raise HTTPException(status_code=400, detail="Webhook URL required")
        
    success = await send_chat_alert(provider, webhook_url, "🤖 **PulseAI Setup**\nThis is a test notification to verify your webhook works!")
    if success:
        return {"success": True}
    else:
        return {"success": False, "error": "Failed to send message or received non-200 response."}

@app.post("/admin/team")
async def create_team(request: Request, name: str = Form(...), chat_provider: str = Form("discord"), chat_webhook_url: str = Form(""), vcs_provider: str = Form("github"), pm_provider: str = Form("jira"), db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Admin":
         return active_dashboard_redirect(user)
    
    g_secret = secrets.token_urlsafe(32)
    team = Team(name=name, chat_provider=chat_provider, chat_webhook_url=chat_webhook_url, vcs_provider=vcs_provider, vcs_secret=g_secret, pm_provider=pm_provider, pm_secret=secrets.token_urlsafe(32))
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

@app.post("/admin/user/update/{user_id}")
async def admin_update_user(request: Request, user_id: int, username: str = Form(default=""), email: str = Form(default=""), password: str = Form(default=""), db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Admin":
         return active_dashboard_redirect(user)
    
    target_user = db.query(User).filter(User.id == user_id).first()
    if target_user:
        if username: target_user.username = username
        if email: target_user.email = email
        if password: target_user.password_hash = bcrypt.hashpw(password[:72].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        db.commit()
    return form_redirect("/admin/dashboard")



@app.post("/admin/import")
async def import_data(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Admin":
        return active_dashboard_redirect(user)
    
    company = db.query(Company).first()
    
    if not company:
        return RedirectResponse(url="/setup", status_code=302)

    content = await file.read()
    
    try:
        if file.filename.endswith(".json"):
            data = json.loads(content)
            teams = data.get("teams", [])
            for t_data in teams:
                team = db.query(Team).filter_by(name=t_data["name"], company_id=company.id).first()
                if not team:
                    team = Team(
                        name=t_data.get("name", "Imported Team"),
                        company_id=company.id,
                        chat_provider=t_data.get("chat_provider", "discord"),
                        chat_webhook_url=t_data.get("chat_webhook_url", ""),
                        vcs_provider=t_data.get("vcs_provider", "github"),
                        vcs_secret=t_data.get("vcs_secret") or secrets.token_urlsafe(32),
                        pm_provider=t_data.get("pm_provider", "jira")
                    )
                    db.add(team)
                    db.flush()
                
                for member in t_data.get("members", []):
                    existing = db.query(User).filter_by(username=member["username"]).first()
                    if not existing:
                        hashed = bcrypt.hashpw(member.get("password", "change_me")[:72].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                        new_user = User(
                            company_id=company.id,
                            team_id=team.id,
                            username=member["username"],
                            email=member.get("email", ""),
                            full_name=member.get("full_name", ""),
                            role=member.get("role", "Member").capitalize(),
                            password_hash=hashed
                        )
                        db.add(new_user)
            db.commit()
        
        elif file.filename.endswith(".csv"):
            text_data = content.decode("utf-8")
            reader = csv.DictReader(io.StringIO(text_data))
            for row in reader:
                team_name = row.get("team_name", "").strip()
                if not team_name: continue
                
                team = db.query(Team).filter_by(name=team_name, company_id=company.id).first()
                if not team:
                    team = Team(
                        name=team_name,
                        company_id=company.id,
                        chat_provider=row.get("chat_provider", "discord"),
                        chat_webhook_url=row.get("chat_webhook_url", ""),
                        vcs_provider=row.get("vcs_provider", "github"),
                        vcs_secret=row.get("vcs_secret") or secrets.token_urlsafe(32),
                        pm_provider=row.get("pm_provider", "jira")
                    )
                    db.add(team)
                    db.flush()
                
                username = row.get("username", "").strip()
                if username:
                    existing = db.query(User).filter_by(username=username).first()
                    if not existing:
                        hashed = bcrypt.hashpw(row.get("password", "change_me")[:72].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                        new_user = User(
                            company_id=company.id,
                            team_id=team.id,
                            username=username,
                            email=row.get("email", ""),
                            full_name=row.get("full_name", ""),
                            role=row.get("role", "Member").capitalize(),
                            password_hash=hashed
                        )
                        db.add(new_user)
            db.commit()
    except Exception as e:
        print(f"Import error: {e}")
        
    return RedirectResponse(url="/admin/dashboard", status_code=302)

@app.post("/admin/import")
async def import_data(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Admin":
        return active_dashboard_redirect(user)
    
    company = db.query(Company).first()
    if not company:
        return RedirectResponse(url="/setup", status_code=302)

    content = await file.read()
    try:
        if file.filename.endswith(".json"):
            data = json.loads(content)
            teams = data.get("teams", [])
            for t_data in teams:
                team = db.query(Team).filter_by(name=t_data["name"], company_id=company.id).first()
                if not team:
                    team = Team(
                        name=t_data.get("name", "Imported Team"),
                        company_id=company.id,
                        chat_provider=t_data.get("chat_provider", "discord"),
                        chat_webhook_url=t_data.get("chat_webhook_url", ""),
                        vcs_provider=t_data.get("vcs_provider", "github"),
                        vcs_secret=t_data.get("vcs_secret") or secrets.token_urlsafe(32),
                        pm_provider=t_data.get("pm_provider", "jira")
                    )
                    db.add(team)
                    db.flush()
                
                for member in t_data.get("members", []):
                    existing = db.query(User).filter_by(username=member["username"]).first()
                    if not existing:
                        hashed = bcrypt.hashpw(member.get("password", "change_me")[:72].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                        new_user = User(
                            company_id=company.id,
                            team_id=team.id,
                            username=member["username"],
                            email=member.get("email", ""),
                            full_name=member.get("full_name", ""),
                            role=member.get("role", "Member").capitalize(),
                            password_hash=hashed
                        )
                        db.add(new_user)
            db.commit()
        
        elif file.filename.endswith(".csv"):
            text_data = content.decode("utf-8")
            reader = csv.DictReader(io.StringIO(text_data))
            for row in reader:
                team_name = row.get("team_name", "").strip()
                if not team_name: continue
                
                team = db.query(Team).filter_by(name=team_name, company_id=company.id).first()
                if not team:
                    team = Team(
                        name=team_name,
                        company_id=company.id,
                        chat_provider=row.get("chat_provider", "discord"),
                        chat_webhook_url=row.get("chat_webhook_url", ""),
                        vcs_provider=row.get("vcs_provider", "github"),
                        vcs_secret=row.get("vcs_secret") or secrets.token_urlsafe(32),
                        pm_provider=row.get("pm_provider", "jira")
                    )
                    db.add(team)
                    db.flush()
                
                username = row.get("username", "").strip()
                if username:
                    existing = db.query(User).filter_by(username=username).first()
                    if not existing:
                        hashed = bcrypt.hashpw(row.get("password", "change_me")[:72].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                        new_user = User(
                            company_id=company.id,
                            team_id=team.id,
                            username=username,
                            email=row.get("email", ""),
                            full_name=row.get("full_name", ""),
                            role=row.get("role", "Member").capitalize(),
                            password_hash=hashed
                        )
                        db.add(new_user)
            db.commit()
    except Exception as e:
        print(f"Import error: {e}")
        
    return RedirectResponse(url="/admin/dashboard", status_code=302)



@app.get("/settings/passkeys")
def list_passkeys(request: Request, db: Session = Depends(get_db)):
    user = login_required(request, db)
    return db.query(Passkey).filter(Passkey.user_id == user.id).all()

@app.post("/settings/passkeys/delete/{pk_id}")
def delete_passkey(pk_id: int, request: Request, db: Session = Depends(get_db)):
    user = login_required(request, db)
    pk = db.query(Passkey).filter(Passkey.id == pk_id, Passkey.user_id == user.id).first()
    if pk:
        db.delete(pk)
        db.commit()
    return browser_redirect("/settings")

@app.get("/webauthn/register/begin")
def webauthn_register_begin(request: Request, db: Session = Depends(get_db)):
    user = login_required(request, db)
    host = request.headers.get('x-forwarded-host', request.headers.get('host', 'localhost:8000'))
    domain = host.split(':')[0]
    
    existing = db.query(Passkey).filter(Passkey.user_id == user.id).all()
    exclude_credentials = []
    for pk in existing:
        exclude_credentials.append({
            "id": pk.credential_id.encode('utf-8'),
            "type": "public-key"
        })
        
    registration_options = generate_registration_options(
        rp_id=domain,
        rp_name=RP_NAME,
        user_id=str(user.id).encode("utf-8"),
        user_name=user.username,
        user_display_name=user.full_name or user.username,
        exclude_credentials=exclude_credentials
    )
    request.session["webauthn_challenge"] = base64.b64encode(registration_options.challenge).decode("utf-8")
    
    return json.loads(options_to_json(registration_options))


@app.get("/webauthn/authenticate/begin")
def webauthn_authenticate_begin(request: Request, db: Session = Depends(get_db)):
    host = request.headers.get('x-forwarded-host', request.headers.get('host', 'localhost:8000'))
    domain = host.split(':')[0]
    
    # Create options for any supported credential
    options = generate_authentication_options(
        rp_id=domain,
        allow_credentials=[],
    )
    request.session["webauthn_auth_challenge"] = base64.b64encode(options.challenge).decode("utf-8")
    return json.loads(options_to_json(options))

@app.post("/webauthn/authenticate/complete")
async def webauthn_authenticate_complete(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    challenge = request.session.get("webauthn_auth_challenge")
    
    if not challenge:
        raise HTTPException(status_code=400, detail="No auth challenge found")
        
    # We don't know the user yet. We look up the passkey by credential_id
    credential_id = data.get("id")
    if not credential_id:
        raise HTTPException(status_code=400, detail="No credential info provided")
        
    pk = db.query(Passkey).filter(Passkey.credential_id == credential_id).first()
    if not pk:
        raise HTTPException(status_code=400, detail="Passkey not recognized or not found")
        
    host = request.headers.get('x-forwarded-host', request.headers.get('host', 'localhost:8000'))
    scheme = request.headers.get('x-forwarded-proto', request.url.scheme)
    origin = f"{scheme}://{host}"
    domain = host.split(':')[0]
        
    try:
        verification = verify_authentication_response(
            credential=data,
            expected_challenge=base64.b64decode(challenge),
            expected_origin=origin,
            expected_rp_id=domain,
            credential_public_key=base64.b64decode(pk.public_key),
            credential_current_sign_count=pk.sign_count,
        )
        
        pk.sign_count = verification.new_sign_count
        db.commit()
        
        user = db.query(User).filter(User.id == pk.user_id).first()
        request.session['user_id'] = user.id
        
        if user.totp_enabled:
            return {"status": "ok", "redirect": "/login/2fa"}
            
        redirect_url = "/admin/dashboard" if user.role == "Admin" else "/leader/dashboard" if user.role == "Leader" else "/member/dashboard"
        return {"status": "ok", "redirect": redirect_url}
        
    except Exception as e:
        print(f"Webauthn Auth Error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/webauthn/register/complete")
async def webauthn_register_complete(request: Request, db: Session = Depends(get_db)):
    user = login_required(request, db)
    data = await request.json()
    challenge = request.session.get("webauthn_challenge")
    
    if not challenge:
        raise HTTPException(status_code=400, detail="No challenge found")
        
    host = request.headers.get('x-forwarded-host', request.headers.get('host', 'localhost:8000'))
    scheme = request.headers.get('x-forwarded-proto', request.url.scheme)
    origin = f"{scheme}://{host}"
    domain = host.split(':')[0]
    
    try:
        verification = verify_registration_response(
            credential=data,
            expected_challenge=base64.b64decode(challenge),
            expected_origin=origin,
            expected_rp_id=domain,
        )
        
        passkey_name = data.get("name", "Passkey")

        new_passkey = Passkey(
            user_id=user.id,
            name=passkey_name,
            credential_id=data.get("id"),
            public_key=base64.b64encode(verification.credential_public_key).decode("utf-8"),
            sign_count=verification.sign_count
        )
        db.add(new_passkey)
        db.commit()
        return {"status": "ok"}
    except Exception as e:
        print(f"Webauthn Reg Error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/settings", response_class=HTMLResponse)

async def settings_get(request: Request, db: Session = Depends(get_db)):
    user = login_required(request, db)
    return templates.TemplateResponse(request=request, name="settings.html", context={"user": user, "passkeys": db.query(Passkey).filter(Passkey.user_id == user.id).all()})

@app.post("/settings/password")
async def settings_password(request: Request, current_password: str = Form(...), new_password: str = Form(...), db: Session = Depends(get_db)):
    user = login_required(request, db)
    if not bcrypt.checkpw(current_password[:72].encode('utf-8'), user.password_hash.encode('utf-8')):
        return templates.TemplateResponse(request=request, name="settings.html", context={"user": user, "passkeys": db.query(Passkey).filter(Passkey.user_id == user.id).all(), "error": "Incorrect current password."})
    
    user.password_hash = bcrypt.hashpw(new_password[:72].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    db.commit()
    return templates.TemplateResponse(request=request, name="settings.html", context={"user": user, "passkeys": db.query(Passkey).filter(Passkey.user_id == user.id).all(), "success": "Password changed successfully."})

@app.post("/settings/2fa/setup")
async def setup_2fa(request: Request, db: Session = Depends(get_db)):
    user = login_required(request, db)
    import pyotp
    import qrcode
    import io
    import base64
    
    secret = pyotp.random_base32()
    request.session['temp_totp_secret'] = secret
    
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(name=user.username, issuer_name="PulseAI")
    
    img = qrcode.make(provisioning_uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    qr_code_data = f"data:image/png;base64,{qr_b64}"
    
    passkeys = db.query(Passkey).filter(Passkey.user_id == user.id).all()
    return templates.TemplateResponse(request=request, name="settings.html", context={"user": user, "passkeys": passkeys, "qr_code": qr_code_data, "secret": secret})

@app.post("/settings/2fa/verify")
async def verify_2fa_setup(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    user = login_required(request, db)
    secret = request.session.get('temp_totp_secret')
    if not secret:
        return browser_redirect("/settings")
        
    import pyotp
    totp = pyotp.TOTP(secret)
    if totp.verify(code):
        user.totp_secret = secret
        user.totp_enabled = True
        db.commit()
        del request.session['temp_totp_secret']
        return browser_redirect("/settings")
    return templates.TemplateResponse(request=request, name="settings.html", context={"user": user, "passkeys": db.query(Passkey).filter(Passkey.user_id == user.id).all(), "error": "Invalid 2FA code. Please try again."})

@app.post("/settings/2fa/disable")
async def disable_2fa(request: Request, db: Session = Depends(get_db)):
    user = login_required(request, db)
    user.totp_enabled = False
    user.totp_secret = None
    db.commit()
    return browser_redirect("/settings")

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

@app.post("/admin/team/update/{team_id}")
async def admin_update_team(request: Request, team_id: int, name: str = Form(...), chat_provider: str = Form("discord"), chat_webhook_url: str = Form(""), leader_id: int = Form(default=None), db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Admin":
         return active_dashboard_redirect(user)
    
    team = db.query(Team).filter(Team.id == team_id).first()
    if team:
        team.name = name.strip()
        team.chat_provider = chat_provider.strip()
        team.chat_webhook_url = chat_webhook_url.strip()
        
        # Determine current leader
        current_leader = db.query(User).filter(User.team_id == team.id, User.role == "Leader").first()
        
        if leader_id:
            if current_leader and current_leader.id != leader_id:
                current_leader.role = "Member"
            new_leader = db.query(User).filter(User.id == leader_id).first()
            if new_leader:
                new_leader.role = "Leader"
                new_leader.team_id = team.id
        else:
            # If no leader selected, just demote current leader
            if current_leader:
                current_leader.role = "Member"
                
        db.commit()
    return form_redirect("/admin/dashboard")

@app.post("/admin/team/regenerate-vcs-secret/{team_id}")
async def admin_regenerate_secret(request: Request, team_id: int, db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Admin":
         return active_dashboard_redirect(user)
    
    import secrets
    team = db.query(Team).filter(Team.id == team_id).first()
    if team:
        team.vcs_secret = secrets.token_hex(16)
        db.commit()
    return form_redirect("/admin/dashboard")

@app.post("/admin/team/regenerate-pm-secret/{team_id}")
async def admin_regenerate_jira_secret(request: Request, team_id: int, db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Admin":
         return active_dashboard_redirect(user)
    
    import secrets
    team = db.query(Team).filter(Team.id == team_id).first()
    if team:
        team.pm_secret = secrets.token_urlsafe(32)
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
    if user.role != "Leader" or not user.team_id:
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
@app.post("/leader/user/remove/{user_id}")
async def leader_remove_user(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Leader" or not user.team_id:
        return active_dashboard_redirect(user)
        
    target = db.query(User).filter(User.id == user_id, User.team_id == user.team_id, User.role != "Leader").first()
    if target:
        target.team_id = None
        db.commit()
from services.ai_service import generate_daily_executive_summary
from services.alert_service import send_chat_alert
from models import TeamReport, ActivityLog, Company
from datetime import timedelta

        
    
@app.post("/leader/trigger_report")
async def leader_trigger_report(request: Request, db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Leader" or not user.team_id:
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
        await send_chat_alert(team.chat_provider, team.chat_webhook_url, f"📊 **New On-Demand Pulse Report!**\nA Leader ({user.username}) triggered a report.\nView it here: {report_url}")
        
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

@app.post("/leader/chat/update")
async def update_chat_webhook(request: Request, chat_provider: str = Form(...), chat_webhook_url: str = Form(...), db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Leader" or not user.team_id:
        return active_dashboard_redirect(user)
    
    team = user.team
    team.chat_provider = chat_provider.strip()
    team.chat_webhook_url = chat_webhook_url.strip()
    db.commit()
    return browser_redirect("/leader/dashboard")
    
@app.post("/leader/jira/setup")
async def setup_jira(request: Request, db: Session = Depends(get_db)):
    user = login_required(request, db)
    if user.role != "Leader" or not user.team_id:
        return active_dashboard_redirect(user)
    
    team = user.team
    if not team.jira_connection:
        import secrets
        team.pm_secret = secrets.token_urlsafe(32)
        db.commit()
        
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
