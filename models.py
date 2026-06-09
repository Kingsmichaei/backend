from sqlalchemy import Boolean, Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from database import Base

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)
    name = Column(String, nullable=True)
    school = Column(String, nullable=True)

    profile_data = Column(String, nullable=True)  # Store JSON as string for simplicity
    answers = Column(String, nullable=True)  
    has_completed_onboarding = Column(Boolean, default=False)
    ai_analysis = Column(String, nullable=True)  # Store JSON as string for simplicity
    reset_token = Column(String, nullable=True)  # For password reset functionality
    reset_token_expires = Column(DateTime, nullable=True)  # For password reset functionality
    progress_records = relationship("DailyProgress", back_populates="user", cascade="all, delete-orphan")
    
class DailyProgress(Base):
    __tablename__ = "daily_progress"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(String, index=True, nullable=False)  # Store date as string for simplicity
    completed_tasks = Column(String, default="[]", nullable=True)  # Store JSON as string for simplicity
    score = Column(Integer, default=0, nullable=True)  # Store JSON as string for simplicity    

    user = relationship("User", back_populates="progress_records")