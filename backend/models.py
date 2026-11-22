import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, ForeignKey, DateTime, Boolean, Text, Enum as SQLEnum,
    Index, UniqueConstraint
)
from sqlalchemy.orm import relationship
from database import Base


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    FACULTY = "faculty"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(SQLEnum(UserRole), nullable=False, default=UserRole.FACULTY)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    uploads = relationship("FileUpload", back_populates="uploader")


class AcademicYear(Base):
    __tablename__ = "academic_years"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String, unique=True, nullable=False)  # e.g. "2025-2026"
    start_year = Column(Integer, nullable=False)
    end_year = Column(Integer, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    creator = relationship("User")
    subjects = relationship("Subject", back_populates="academic_year", cascade="all, delete-orphan")


class Subject(Base):
    __tablename__ = "subjects"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, nullable=False, index=True)  # e.g. "CS101"
    name = Column(String, nullable=False)
    regulation = Column(String, nullable=False, default="R2023")
    semester = Column(Integer, nullable=False)  # 1-8
    academic_year_id = Column(Integer, ForeignKey("academic_years.id"), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    academic_year = relationship("AcademicYear", back_populates="subjects")
    creator = relationship("User")
    uploads = relationship("FileUpload", back_populates="subject", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('code', 'academic_year_id', 'semester', name='uq_subject_code_year_sem'),
    )


class FacultySubjectAssignment(Base):
    __tablename__ = "faculty_subject_assignments"

    id = Column(Integer, primary_key=True, index=True)
    faculty_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False, index=True)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    faculty = relationship("User", foreign_keys=[faculty_id])
    subject = relationship("Subject")
    assigner = relationship("User", foreign_keys=[assigned_by])

    __table_args__ = (
        UniqueConstraint('faculty_id', 'subject_id', name='uq_faculty_subject'),
    )


class FileUpload(Base):
    __tablename__ = "file_uploads"

    id = Column(Integer, primary_key=True, index=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False, index=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    original_filename = Column(String, nullable=False)  # name user gave
    stored_filename = Column(String, nullable=False)    # actual file on disk
    file_path = Column(String, nullable=False)
    file_size = Column(Integer, nullable=False)         # bytes
    mime_type = Column(String, nullable=False)
    sha256_hash = Column(String, nullable=False, index=True)
    version = Column(Integer, default=1, nullable=False)

    uploaded_at = Column(DateTime, default=datetime.utcnow)

    subject = relationship("Subject", back_populates="uploads")
    uploader = relationship("User", back_populates="uploads")

    __table_args__ = (
        Index('ix_file_subject_version', 'subject_id', 'original_filename', 'version'),
    )
