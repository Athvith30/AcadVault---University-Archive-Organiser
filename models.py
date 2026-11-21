# models.py
from sqlalchemy import Column, Integer, String, Text, ForeignKey, Boolean
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False)  # "admin" or "faculty"


class Faculty(Base):
    __tablename__ = "faculty"

    id = Column(Integer, primary_key=True, index=True)
    teacher_id = Column(Integer, unique=True, index=True, nullable=False)  # e.g., 102, 100 (also login username)
    teacher_name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)  # Password assigned by admin (hashed)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    is_active = Column(Boolean, default=True)


class AcademicYear(Base):
    __tablename__ = "academic_years"

    id = Column(Integer, primary_key=True, index=True)
    year_range = Column(String, unique=True, index=True, nullable=False)  # e.g., "2026-2027"


class Regulation(Base):
    __tablename__ = "regulations"

    id = Column(Integer, primary_key=True, index=True)
    regulation_name = Column(String, unique=True, index=True, nullable=False)
    description = Column(String, default="")

    # Parallel lists per year & semester: subjects[i] is handled by teachers[i] (teacher_id)
    year_1_sem_1_subjects = Column(Text, nullable=False, default="[]")
    year_1_sem_1_teachers = Column(Text, nullable=False, default="[]")

    year_1_sem_2_subjects = Column(Text, nullable=False, default="[]")
    year_1_sem_2_teachers = Column(Text, nullable=False, default="[]")

    year_2_sem_1_subjects = Column(Text, nullable=False, default="[]")
    year_2_sem_1_teachers = Column(Text, nullable=False, default="[]")

    year_2_sem_2_subjects = Column(Text, nullable=False, default="[]")
    year_2_sem_2_teachers = Column(Text, nullable=False, default="[]")

    year_3_sem_1_subjects = Column(Text, nullable=False, default="[]")
    year_3_sem_1_teachers = Column(Text, nullable=False, default="[]")

    year_3_sem_2_subjects = Column(Text, nullable=False, default="[]")
    year_3_sem_2_teachers = Column(Text, nullable=False, default="[]")

    year_4_sem_1_subjects = Column(Text, nullable=False, default="[]")
    year_4_sem_1_teachers = Column(Text, nullable=False, default="[]")

    year_4_sem_2_subjects = Column(Text, nullable=False, default="[]")
    year_4_sem_2_teachers = Column(Text, nullable=False, default="[]")

    subsequent_folders = Column(Text, nullable=False, default="[]")
    subject_files = Column(Text, nullable=False, default="{}")


class Subject(Base):
    __tablename__ = "subjects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    regulation = Column(String, nullable=False)
    course_type = Column(String, default="major")
    course_format = Column(String, default="theory")
    year_no = Column(Integer, nullable=False)
    semester_no = Column(Integer, nullable=False)
    academic_year_id = Column(Integer, ForeignKey("academic_years.id"), nullable=False)
    faculty_id = Column(Integer, nullable=False)  # Stores teacher_id (e.g., 102, 100)


class FileVersion(Base):
    __tablename__ = "file_versions"

    id = Column(Integer, primary_key=True, index=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    subfolder_name = Column(String, nullable=False)
    file_name = Column(String, nullable=False)
    version_number = Column(Integer, nullable=False, default=1)
    onedrive_path = Column(String, nullable=False)