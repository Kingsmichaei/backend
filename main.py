import os
import json
import jwt
import datetime
import email
import smtplib
import ssl
import secrets
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from typing import List
from google import genai 
from google.genai import types
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Request, Response
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

import models
from database import SessionLocal, engine
from passlib.context import CryptContext

# Create tables
models.Base.metadata.create_all(bind=engine)
load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY")
client = genai.Client()
ALGORITHM = "HS256"

def create_token(email: str):
    payload = {
        "sub": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=7) # Token expires in 7 days
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

app = FastAPI()

# Configure CORS to allow your React app to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://pathfinder-amber-eta.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Temporary mock database to replace your React state
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Define what data we expect from React
class UserAuth(BaseModel):
    email: str
    password: str
    name: str | None= None
    school: str | None = None

class GoogleAuthData(BaseModel):
    email: str
    name: str

class AssessmentData(BaseModel):
    email: str
    profileData: dict
    answers: dict

class ProgressUpdate(BaseModel):
    date: str
    completed_tasks: List[int] = []
    score: int = 0

@app.post("/api/signup")
def signup(user: UserAuth, response: Response, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == user.email.lower()).first()
    if db_user:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")
    
    new_user = models.User(
        email=user.email.lower(),
        password=get_password_hash(user.password),  # In production, hash the password!
        name=user.name,
        school=user.school
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    token = create_token(new_user.email)
    response.set_cookie(
        key="pathfinder_token",
        value=token,
        max_age=604800,  # 7 days in seconds
        httponly=True,
        samesite="none",
        secure=True,  # Set to True in production with HTTPS
        path="/"
    )
    return {
        "message": "User created successfully",
        "user": {
            "email": new_user.email,
            "name": new_user.name,
            "school": new_user.school,
            "hasCompletedOnboarding": new_user.has_completed_onboarding,
            "profileData": None,
            "answers": None,
            "ai_analysis": None
        }
    }

@app.post("/api/login")
def login(user: UserAuth, response: Response, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == user.email.lower()).first()
    
    if not db_user: 
        raise HTTPException(status_code=400, detail="Invalid email or password.")
    if not db_user.password or not db_user.password.startswith("$2"):  # Check if password is missing or not hashed
        raise HTTPException(status_code=400, detail="Please log in with Google ")
    if not verify_password(user.password, db_user.password):
        raise HTTPException(status_code=400, detail="Invalid email or password.")

    token = create_token(db_user.email)
    response.set_cookie(
        key="pathfinder_token",
        value=token,
        httponly=True,
        max_age=604800,  # 7 days in seconds
        samesite="none",
        secure=True,  # Set to True in production with HTTPS
        path="/"
    )
    first_name = db_user.name.split(' ')[0] if db_user.name else "Student"
    # Safely convert the text strings back into dictionaries for React
    profile_data_dict = json.loads(db_user.profile_data) if db_user.profile_data else None
    answers_dict = json.loads(db_user.answers) if db_user.answers else None
    parsed_analysis = None
    if db_user.ai_analysis:
        parsed_analysis = json.loads(db_user.ai_analysis)

    return {
        "message": f"Welcome back, {first_name}!",
        "user": { 
            "email": db_user.email, 
            "name": db_user.name, 
            "school": db_user.school,
            "hasCompletedOnboarding": db_user.has_completed_onboarding,
            "profileData": profile_data_dict,
            "answers": answers_dict,
            "ai_analysis": parsed_analysis
        }
     }

@app.post("/api/save-assessment")
def save_assessment(data: AssessmentSubmission, request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("pathfinder_token")
    if not token:
        raise HTTPException(status_code=401, detail="No active session")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_email = payload.get("sub")
        db_user = db.query(models.User).filter(models.User.email == user_email).first()

        if not db_user:
            raise HTTPException(status_code=404, detail="User not found.")
    
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=404, detail="Invalid token")
    
    
    # Convert dictionaries to strings before saving them to SQLite
    db_user.profile_data = json.dumps(data.profileData)
    db_user.answers = json.dumps(data.answers)
    db_user.has_completed_onboarding = True
    

    db.commit()
    db.refresh(db_user)

    return {"message": "Assessment saved successfully!"}


class AssessmentSubmission(BaseModel):
    answers: dict
    profileData: dict

@app.post("/api/analyze-assessment")
def analyze_assessment(data: AssessmentSubmission, request: Request, db: Session = Depends(get_db)):
    # Verify the user is logged in securely
    token = request.cookies.get("pathfinder_token")
    if not token:
        raise HTTPException(status_code=401, detail="No active session")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_email = payload.get("sub")
        db_user = db.query(models.User).filter(models.User.email == user_email).first()
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=404, detail="Invalid token")
    
    # Prompt Engineering: Tell the AI exactly what to do and how to format it
    prompt = f"""
    You are an expert career counselor for high school students.
    Analyze the following student profile and assessment answers.
    Profile Data: {data.profileData}
    Assessment Answers: {data.answers}

    Return a valid JSON object matching exactly this schema:
    {{
        "strengths": ["List exactly 4 soft skills or strengths"],
        "weaknesses": ["List exactly 3 specific areas to improve"],
        "subjects": ["Array of exactly 4 subject IDs from this list: math, science, lit, cs, art, geo, chem, bio, econ, music, com,cve,fin,hist,fmath,eng,phy,govt,lan,agric"],
        "careers": ["Array of exactly 4 career IDs from this list: se, ds, gd, md, ent, arc, teach, pharma, fin, game, nurse, law, ce, mkt"]
    }}
    """

    try:
        # NEW SYNTAX: Call Gemini 2.5 Flash using the new Client
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", # Forces the structured output
            )
        )
        
        # Parse the JSON string returned by Gemini into a Python dictionary
        analysis = json.loads(response.text)

        if db_user:
            db_user.ai_analysis = json.dumps(analysis)  # Save the analysis as a string in the database
            db.commit()

        return analysis
        
    except Exception as e:
        print("Gemini Error:", e)
        raise HTTPException(status_code=500, detail="Failed to generate AI analysis")

@app.post("/api/google-auth")
def google_auth(data: GoogleAuthData, response: Response, db: Session = Depends(get_db)):
    email_key = data.email.lower()
    db_user = db.query(models.User).filter(models.User.email == email_key).first()
    
    if db_user:
        # USER EXISTS: Treat this as a Login
        first_name = db_user.name.split(' ')[0] if db_user.name else "Student"
        profile_data_dict = json.loads(db_user.profile_data) if db_user.profile_data else None
        answers_dict = json.loads(db_user.answers) if db_user.answers else None
        parsed_analysis = None
        if db_user.ai_analysis:
            parsed_analysis = json.loads(db_user.ai_analysis)
        token = create_token(db_user.email)
        response.set_cookie(
            key="pathfinder_token",
            value=token,
            max_age=604800,  # 7 days in seconds
            httponly=True,
            samesite="none",
            secure=True,  # Set to True in production with HTTPS
            path="/"
        )

        return {
            "message": f"Welcome back, {first_name}!",
            "user": {
                "email": db_user.email,
                "name": db_user.name,
                "school": db_user.school,
                "hasCompletedOnboarding": db_user.has_completed_onboarding,
                "profileData": profile_data_dict,
                "answers": answers_dict,
                "ai_analysis": parsed_analysis
            }
        }
    else:
        # NEW USER: Treat this as a Sign Up
        new_user = models.User(
            email=email_key,
            password="",  # Google users don't need a local password
            name=data.name,
            school="Google Account" # Placeholder, they can edit this later
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        
        token = create_token(new_user.email)
        response.set_cookie(
            key="pathfinder_token",
            value=token,
            max_age=604800,  # 7 days in seconds
            httponly=True,
            samesite="none",
            secure=True,  # Set to True in production with HTTPS
            path="/"
        )
        
        return {
            "message": "Account created via Google",
            "user": {
                "email": new_user.email,
                "name": new_user.name,
                "school": new_user.school,
                "hasCompletedOnboarding": False,
                "profileData": None,
                    "answers": None,
                    "ai_analysis": None
            }
        }    

@app.get("/api/me")
def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("pathfinder_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        db_user = db.query(models.User).filter(models.User.email == email).first()
        if not db_user:
            raise HTTPException(status_code=404, detail="User not found.")
        
        first_name = db_user.name.split(' ')[0] if db_user.name else "Student"
        profile_data_dict = json.loads(db_user.profile_data) if db_user.profile_data else None
        answers_dict = json.loads(db_user.answers) if db_user.answers else None
        
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    parsed_analysis = None
    if db_user.ai_analysis:
        parsed_analysis = json.loads(db_user.ai_analysis)
    return {
        "message": f"Welcome back, {first_name}!",
            "user": {
                "email": db_user.email,
                "name": db_user.name,
                "school": db_user.school,
                "hasCompletedOnboarding": db_user.has_completed_onboarding,
                "profileData": profile_data_dict,
                "answers": answers_dict,
                "ai_analysis": parsed_analysis  
            }
        }

@app.post("/api/progress")
def save_progress(data: ProgressUpdate, request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("pathfinder_token")
    if not token:
        raise HTTPException(status_code=401, detail="No active session")
    
    try:
        # Verify the user
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        db_user = db.query(models.User).filter(models.User.email == payload.get("sub")).first()
        
        # Check if they already have a record for today
        progress = db.query(models.DailyProgress).filter(
            models.DailyProgress.user_id == db_user.id,
            models.DailyProgress.date == data.date
        ).first()

        if progress:
            # Update today's record
            progress.completed_tasks = json.dumps(data.completed_tasks)
            progress.score = data.score
        else:
            # Create a brand new record for today
            new_progress = models.DailyProgress(
                user_id=db_user.id,
                date=data.date,
                completed_tasks=json.dumps(data.completed_tasks),
                score=data.score
            )
            db.add(new_progress)
            
        db.commit()
        return {"message": "Progress saved!"}
        
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

@app.get("/api/progress")
def get_progress(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("pathfinder_token")
    if not token:
        raise HTTPException(status_code=401, detail="No active session")
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        db_user = db.query(models.User).filter(models.User.email == payload.get("sub")).first()
        
        # Fetch all history for this specific user
        records = db.query(models.DailyProgress).filter(models.DailyProgress.user_id == db_user.id).all()
        
        results = []
        for r in records:
            results.append({
                "date": r.date,
                "completed_tasks": json.loads(r.completed_tasks),
                "score": r.score
            })
            
        return {"progress": results}
        
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


@app.post("/api/generate-career-path")
def generate_career_path(request: Request, db: Session = Depends(get_db)):
    """
    Takes the user's saved assessment answers from the database,
    sends them to the Gemini API for analysis, and returns a personalized
    tech career path, skill gap analysis, and OPay subsidy recommendation.
    """
    
    # 1. Authenticate using the JWT cookie (Zero-trust security)
    token = request.cookies.get("pathfinder_token")
    if not token:
        raise HTTPException(status_code=401, detail="No active session")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_email = payload.get("sub")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # 2. Fetch the user securely
    db_user = db.query(models.User).filter(models.User.email == user_email).first()
    
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found.")
        
    if not db_user.answers:
         raise HTTPException(status_code=400, detail="User has not completed the assessment yet.")
    
    if db_user.ai_analysis:
        return {
            "message": "AI Analysis loaded from cache instantly",
            "analysis": json.loads(db_user.ai_analysis)
        }

    user_answers = json.loads(db_user.answers)
    user_profile = json.loads(db_user.profile_data) if db_user.profile_data else {"level": "SS3", "age": 16}
    
    # 3. The Prompt Blueprint
    prompt = f"""
    You are PathFinder, an expert AI career counselor for Nigerian students. 
    Analyze the following student profile and assessment answers to determine their ideal tech career path.
    
    Student Profile: {json.dumps(user_profile)}
    Assessment Answers: {json.dumps(user_answers)}
    
    Based on their logic and subject preferences, provide a JSON response with the following structure exactly:
    {{
        "recommended_career": "The ideal tech career (e.g., Data Scientist, Backend Engineer)",
        "tech_readiness_score": "A number between 1 and 100",
        "strengths": ["Strength 1", "Strength 2"],
        "skill_gaps": ["Area to improve 1", "Area to improve 2"],
        "recommended_learning_path": "A short 2-sentence roadmap for them",
        "opay_subsidy_unlocked": "A suggested Naira amount between 1000 and 5000 for data/exam fees based on their performance"
    }}
    """
    
    try:
        # 4. Use your modern google-genai client and force JSON compliance
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", 
            )
        )
        
        # Parse the JSON response securely
        ai_analysis = json.loads(response.text)
        
        db_user.ai_analysis = json.dumps(ai_analysis)
        db.commit()

        return {
            "message": "AI Analysis Generated Successfully",
            "analysis": ai_analysis
        }
        
    except Exception as e:
        print("Gemini Error:", e)
        raise HTTPException(status_code=500, detail="Failed to generate AI career path")



@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(key="pathfinder_token", path="/")
    return {"message": "Logged out successfully"}

@app.delete("/api/delete-account")
def delete_account(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get("pathfinder_token")
    if not token:
        raise HTTPException(status_code=401, detail="No active session")
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        db_user = db.query(models.User).filter(models.User.email == email).first()
        if not db_user:
            raise HTTPException(status_code=404, detail="User not found.")
        
        db.delete(db_user)
        db.commit()
        
        response.delete_cookie(key="pathfinder_token", path="/")
        return {"message": "Account deleted successfully"}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    

class ForgotPasswordRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

# Helper function to actually send the email
def send_reset_email(user_email: str, token: str):
    sender_email = os.getenv("EMAIL_ADDRESS")
    sender_password = os.getenv("EMAIL_PASSWORD")
    
    if not sender_email or not sender_password:
        print("Error: EMAIL_ADDRESS or EMAIL_PASSWORD not set in backend/.env")
        raise HTTPException(status_code=500, detail="Server email configuration is missing.")
        
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
    
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = user_email
    msg['Subject'] = "PathFinder - Password Reset Request"
    
    # We create a special link pointing to your React app
    reset_link = f"{frontend_url}/?reset_token={token}"
    
    body = f"""
    Hello,
    
    You requested to reset your PathFinder password.
    Click the link below to set a new password. This link will expire in 15 minutes.
    
    {reset_link}
    
    If you did not request this, please ignore this email.
    """
    
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        # Connect to Gmail's SMTP server using SSL (Port 465) which is less likely to be blocked
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context, timeout=15) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
    except Exception as e:
        print(f"Failed to send email: {e}")
        raise HTTPException(status_code=500, detail="Failed to send the email.")

@app.post("/api/forgot-password")
def forgot_password(data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == data.email.lower()).first()
    
    # Even if the user doesn't exist, we return a success message so hackers can't "fish" for valid emails
    if not db_user:
        return {"message": "If that email is in our system, a reset link has been sent."}
        
    # Generate a secure 32-character random string
    token = secrets.token_urlsafe(32)
    
    # Set expiration to 15 minutes from now (using UTC for safety)
    db_user.reset_token = token
    db_user.reset_token_expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    db.commit()
    
    # Send the email
    send_reset_email(db_user.email, token)
    
    return {"message": "If that email is in our system, a reset link has been sent."}

@app.post("/api/reset-password")
def reset_password(data: ResetPasswordRequest, db: Session = Depends(get_db)):
    # Find the user holding this exact token
    db_user = db.query(models.User).filter(models.User.reset_token == data.token).first()
    
    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token.")
        
    # Check if the token has expired
    # We make datetime.now() timezone-aware so it can be safely compared to the UTC expiration time
    if datetime.now(timezone.utc) > db_user.reset_token_expires.replace(tzinfo=timezone.utc):
        raise HTTPException(status_code=400, detail="Token has expired. Please request a new one.")
        
    # Hash the new password using your existing passlib function
    db_user.password = get_password_hash(data.new_password)
    
    # Wipe the token so it can never be used again
    db_user.reset_token = None
    db_user.reset_token_expires = None
    db.commit()
    
    return {"message": "Password successfully reset! You can now log in."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app",  
                host="127.0.0.1",
                port=8000,
                reload=True)