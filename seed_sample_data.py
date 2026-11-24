"""Optional testing data for the actual AcadVault SQLite backend.

Run after init_db.py and seed_users.py; re-running will not duplicate records.
All example accounts, subjects and folders use the same system as real entries.
Never use these sample passwords in a deployed application.
"""
from pathlib import Path
import json
from passlib.context import CryptContext
from database import SessionLocal, engine
import models

models.Base.metadata.create_all(bind=engine)
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

SAMPLE_SUBJECTS = [
    ("2023-2024", "IT3401 - Data Structures", 3, 101, "major", "theory"),
    ("2023-2024", "IT3402 - Database Systems", 3, 101, "major", "theory cum practical"),
    ("2023-2024", "IT2401 - Python Programming", 2, 102, "minor", "practical"),
    ("2024-2025", "IT4401 - Computer Networks", 4, 101, "major", "theory"),
    ("2024-2025", "IT4402 - Web Technology", 4, 102, "value added", "practical"),
    ("2024-2025", "MA1401 - Mathematics", 1, 101, "other", "theory"),
]
FOLDERS = ["Namelist", "Course Files", "Notes", "Modules", "CIA 1", "Midsem", "CIA 2", "Endsem"]

def seed():
    db = SessionLocal()
    try:
        for teacher_id, name, password in [(101,"Prof. Alice","alice123"),(102,"Prof. Bob","bob123")]:
            user = db.query(models.User).filter_by(username=str(teacher_id)).first()
            faculty = db.query(models.Faculty).filter_by(teacher_id=teacher_id).first()
            if not faculty and not user:
                user = models.User(username=str(teacher_id), password_hash=pwd.hash(password), role="faculty")
                db.add(user); db.flush()
                db.add(models.Faculty(teacher_id=teacher_id, teacher_name=name, password_hash=user.password_hash, user_id=user.id, is_active=True))
        regulation = db.query(models.Regulation).filter_by(regulation_name="R2023").first()
        if not regulation:
            db.add(models.Regulation(regulation_name="R2023",description="Information Technology curriculum", subsequent_folders=json.dumps(FOLDERS)))
        years = {}
        for label in ["2023-2024", "2024-2025"]:
            yr = db.query(models.AcademicYear).filter_by(year_range=label).first()
            if not yr:
                yr = models.AcademicYear(year_range=label)
                db.add(yr); db.flush()
            years[label] = yr.id
        for label, name, sem, teacher, course_type, course_format in SAMPLE_SUBJECTS:
            year_no = (sem+1)//2
            existing = db.query(models.Subject).filter_by(academic_year_id=years[label],name=name,semester_no=sem).first()
            if not existing:
                db.add(models.Subject(name=name,regulation="R2023",course_type=course_type,course_format=course_format,year_no=year_no,semester_no=sem,academic_year_id=years[label],faculty_id=teacher))
            for folder in FOLDERS:
                (Path("department_storage")/label/f"Year_{year_no}"/f"Semester_{sem}"/name/folder).mkdir(parents=True,exist_ok=True)
        db.commit()
        print("Sample faculty, academic years and subjects added to the SAME backend database.")
        print("Sample faculty logins: 101 / alice123 and 102 / bob123")
    except:
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed()
