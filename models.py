from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class Company(Base):
    __tablename__ = "company"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    domain = Column(String)
    gemini_api_key = Column(String, nullable=True)

class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    
    # Modular Chat Provider
    chat_provider = Column(String, default="discord")
    chat_webhook_url = Column(String, nullable=True)
    
    # Modular VCS Provider
    vcs_provider = Column(String, default="github")
    vcs_secret = Column(String, nullable=True)
    
    # Modular PM Provider
    pm_provider = Column(String, default="jira")
    pm_secret = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="team")
    activity_logs = relationship("ActivityLog", back_populates="team")

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("company.id"), nullable=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    username = Column(String, index=True, unique=True)
    email = Column(String, index=True, unique=True, nullable=True)
    password_hash = Column(String)
    role = Column(String, default="member") # "admin", "leader", "member"
    vcs_username = Column(String, nullable=True)
    full_name = Column(String, nullable=True)
    
    # 2FA Authentication
    totp_secret = Column(String, nullable=True)
    totp_enabled = Column(Boolean, default=False)

    team = relationship("Team", back_populates="users")

class ActivityLog(Base):
    __tablename__ = "activity_logs"
    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"))
    developer_name = Column(String)
    action_type = Column(String)
    raw_data = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
    team = relationship("Team", back_populates="activity_logs")

class TeamReport(Base):
    __tablename__ = "team_reports"
    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"))
    summary = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    team = relationship("Team")

class Passkey(Base):
    __tablename__ = "passkeys"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    credential_id = Column(String, unique=True, index=True)
    public_key = Column(String)
    sign_count = Column(Integer, default=0)
    name = Column(String, default="Passkey")
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", backref="passkeys")
