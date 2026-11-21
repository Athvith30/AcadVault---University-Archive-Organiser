# main.py
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel
from passlib.context import CryptContext
import models
from database import SessionLocal, engine
from pathlib import Path
import shutil
import json
from typing import List, Optional, Union, Dict

# Ensure all ORM tables (User, Faculty, AcademicYear, Regulation, Subject, FileVersion) exist
models.Base.metadata.create_all(bind=engine)

# 1. Initialize FastAPI app FIRST so routes can use it
app = FastAPI(title="Academic Department EDMS")

# Setup password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Define where local folders will be stored (acts as our local drive before OneDrive)
STORAGE_ROOT = Path("department_storage")

# Standard folders automatically created for every subject
STANDARD_SUBFOLDERS = ["course files", "namelist", "midsem", "endsem"]


# Dependency to get database session for each request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _load_json_list(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _load_json_dict(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _clean_folder_list(folders: Optional[List[str]]) -> List[str]:
    if not folders:
        return []
    cleaned = []
    for f in folders:
        name = f.strip()
        if name and name not in STANDARD_SUBFOLDERS and name not in cleaned:
            cleaned.append(name)
    return cleaned


def _normalize_subject_files(
    raw_files: Optional[Union[Dict[str, List[str]], List["SubjectFileRule"]]]
) -> Dict[str, List[str]]:
    """Normalizes subject -> files mapping (applies across Years 1-4)."""
    normalized: Dict[str, List[str]] = {}
    if not raw_files:
        return normalized

    if isinstance(raw_files, dict):
        items = [(subj_name, file_list) for subj_name, file_list in raw_files.items()]
    else:
        items = [(rule.subject, rule.files) for rule in raw_files]

    for subj_name, file_list in items:
        clean_subj = subj_name.strip()
        if not clean_subj:
            continue
        existing_list = normalized.setdefault(clean_subj, [])
        for entry in (file_list or []):
            clean_entry = entry.strip()
            if clean_entry and clean_entry not in existing_list:
                existing_list.append(clean_entry)

    return normalized


def _get_files_for_subject(subject_files_map: Dict[str, List[str]], subject_name: str) -> List[str]:
    """Finds all files/items configured for a subject across Years 1-4 (case-insensitive match)."""
    target_lower = subject_name.strip().lower()
    matched: List[str] = []
    for reg_subj, entries in subject_files_map.items():
        if reg_subj.strip().lower() == target_lower:
            for item in entries:
                if item not in matched:
                    matched.append(item)
    return matched


def _apply_subject_files_to_dir(subject_dir: Path, entries: List[str]) -> List[str]:
    """
    Applies subject-level files/items (such as 'fat1', 'fat2', or 'syllabus.pdf')
    inside a subject directory across any year/semester.
    """
    applied: List[str] = []
    course_files_dir = subject_dir / "course files"
    course_files_dir.mkdir(parents=True, exist_ok=True)

    for raw_entry in entries:
        safe_name = Path(raw_entry.strip()).name
        if not safe_name:
            continue

        p = Path(safe_name)
        if p.suffix:
            target_file = course_files_dir / safe_name
            if not target_file.exists():
                target_file.write_text("", encoding="utf-8")
            applied.append(f"course files/{safe_name}")
        else:
            target_folder = subject_dir / safe_name
            target_folder.mkdir(parents=True, exist_ok=True)
            applied.append(f"{safe_name}/")

    return applied


def ensure_base_year_folders(year_folder_path: Path):
    """Automatically creates Year_1 through Year_4 folders for a new academic year."""
    for year_num in range(1, 5):
        (year_folder_path / f"Year_{year_num}").mkdir(parents=True, exist_ok=True)


def _pair_subjects_and_teachers(subjects_raw: str, teachers_raw: str) -> List[tuple]:
    """
    Pairs subjects[i] with teachers[i] (teacher_id).
    If teachers list is shorter than subjects list, falls back to 0 (unassigned).
    """
    subjects = _load_json_list(subjects_raw)
    teachers = _load_json_list(teachers_raw)
    paired = []
    for idx, subj in enumerate(subjects):
        subj_name = str(subj).strip()
        if not subj_name:
            continue
        teacher_id = int(teachers[idx]) if idx < len(teachers) and teachers[idx] is not None else 0
        paired.append((subj_name, teacher_id))
    return paired


# --- PYDANTIC SCHEMAS (DATA BLUEPRINTS) ---

class LoginRequest(BaseModel):
    username: str
    password: str


class FacultyCreateRequest(BaseModel):
    teacher_id: int                               # e.g., 102, 100
    teacher_name: str                             # e.g., "Prof. Rao"
    user_id: Optional[int] = None


class SubjectFileRule(BaseModel):
    subject: str                                  # Subject name (applies across Year 1 to Year 4)
    files: List[str] = []                         # e.g., ["fat1", "fat2"]


class RegulationUpsertRequest(BaseModel):
    regulation_name: str                          # e.g., "R25"
    description: Optional[str] = ""

    year_1_sem_1_subjects: List[str] = []
    year_1_sem_1_teachers: List[int] = []

    year_1_sem_2_subjects: List[str] = []
    year_1_sem_2_teachers: List[int] = []

    year_2_sem_1_subjects: List[str] = []
    year_2_sem_1_teachers: List[int] = []

    year_2_sem_2_subjects: List[str] = []
    year_2_sem_2_teachers: List[int] = []

    year_3_sem_1_subjects: List[str] = []
    year_3_sem_1_teachers: List[int] = []

    year_3_sem_2_subjects: List[str] = []
    year_3_sem_2_teachers: List[int] = []

    year_4_sem_1_subjects: List[str] = []
    year_4_sem_1_teachers: List[int] = []

    year_4_sem_2_subjects: List[str] = []
    year_4_sem_2_teachers: List[int] = []

    subsequent_folders: Optional[List[str]] = None
    subject_files: Optional[Union[Dict[str, List[str]], List[SubjectFileRule]]] = None


class AcademicYearSubjectFill(BaseModel):
    name: str
    year_no: Optional[int] = None
    semester_no: Optional[int] = None
    course_type: Optional[str] = None             # e.g., "major", "minor", "value-added"
    course_format: Optional[str] = None           # e.g., "theory", "practical", "theory-cum-practical"
    teacher_id: Optional[int] = None              # Optional override when creating the year
    extra_files: Optional[List[str]] = None       # Optional extra files for this academic year


class AcademicYearCreateRequest(BaseModel):
    year_range: str                               # e.g., "2026-2027"
    regulation_name: str                          # e.g., "R25"
    subject_details: Optional[List[AcademicYearSubjectFill]] = None


# --- ADMIN FACULTY ROUTES ---

@app.post("/api/admin/faculty")
def add_faculty(payload: FacultyCreateRequest, db: Session = Depends(get_db)):
    """Adds a teacher with a unique teacher_id into the Faculty table."""
    existing = db.query(models.Faculty).filter(
        models.Faculty.teacher_id == payload.teacher_id
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Faculty with teacher_id {payload.teacher_id} already exists!"
        )

    new_faculty = models.Faculty(
        teacher_id=payload.teacher_id,
        teacher_name=payload.teacher_name.strip(),
        user_id=payload.user_id,
        is_active=True
    )
    db.add(new_faculty)
    db.commit()
    db.refresh(new_faculty)

    return {
        "message": "Faculty member added successfully.",
        "faculty": {
            "id": new_faculty.id,
            "teacher_id": new_faculty.teacher_id,
            "teacher_name": new_faculty.teacher_name
        }
    }


@app.get("/api/admin/faculty")
def list_faculty(db: Session = Depends(get_db)):
    """Lists all faculty members in the Faculty table."""
    members = db.query(models.Faculty).order_by(models.Faculty.teacher_id.asc()).all()
    return {
        "faculty": [
            {
                "id": m.id,
                "teacher_id": m.teacher_id,
                "teacher_name": m.teacher_name,
                "is_active": m.is_active
            }
            for m in members
        ]
    }


# --- ADMIN REGULATION ROUTES ---

@app.post("/api/admin/regulations")
def upsert_regulation(payload: RegulationUpsertRequest, db: Session = Depends(get_db)):
    """
    Creates or updates a Regulation with Year 1-4 / Sem 1-2 subjects,
    parallel teacher_id lists, subsequent folders, and subject_files.
    """
    reg_name = payload.regulation_name.strip()
    if not reg_name:
        raise HTTPException(status_code=400, detail="regulation_name is required.")

    sem_pairs = [
        ("year_1_sem_1_subjects", payload.year_1_sem_1_subjects, "year_1_sem_1_teachers", payload.year_1_sem_1_teachers),
        ("year_1_sem_2_subjects", payload.year_1_sem_2_subjects, "year_1_sem_2_teachers", payload.year_1_sem_2_teachers),
        ("year_2_sem_1_subjects", payload.year_2_sem_1_subjects, "year_2_sem_1_teachers", payload.year_2_sem_1_teachers),
        ("year_2_sem_2_subjects", payload.year_2_sem_2_subjects, "year_2_sem_2_teachers", payload.year_2_sem_2_teachers),
        ("year_3_sem_1_subjects", payload.year_3_sem_1_subjects, "year_3_sem_1_teachers", payload.year_3_sem_1_teachers),
        ("year_3_sem_2_subjects", payload.year_3_sem_2_subjects, "year_3_sem_2_teachers", payload.year_3_sem_2_teachers),
        ("year_4_sem_1_subjects", payload.year_4_sem_1_subjects, "year_4_sem_1_teachers", payload.year_4_sem_1_teachers),
        ("year_4_sem_2_subjects", payload.year_4_sem_2_subjects, "year_4_sem_2_teachers", payload.year_4_sem_2_teachers),
    ]

    for s_col, s_list, t_col, t_list in sem_pairs:
        if t_list and len(t_list) != len(s_list):
            raise HTTPException(
                status_code=400,
                detail=f"Mismatch in {s_col} ({len(s_list)} items) and {t_col} ({len(t_list)} teacher IDs)."
            )
        for tid in t_list:
            fac = db.query(models.Faculty).filter(models.Faculty.teacher_id == tid).first()
            if not fac:
                raise HTTPException(
                    status_code=400,
                    detail=f"Faculty with teacher_id={tid} not found in Faculty table. Add it via POST /api/admin/faculty first."
                )

    incoming_folders = _clean_folder_list(payload.subsequent_folders)
    incoming_subject_files = _normalize_subject_files(payload.subject_files)

    existing_reg = db.query(models.Regulation).filter(
        models.Regulation.regulation_name == reg_name
    ).first()

    if not existing_reg:
        new_reg = models.Regulation(
            regulation_name=reg_name,
            description=payload.description or "",
            year_1_sem_1_subjects=json.dumps(payload.year_1_sem_1_subjects),
            year_1_sem_1_teachers=json.dumps(payload.year_1_sem_1_teachers),
            year_1_sem_2_subjects=json.dumps(payload.year_1_sem_2_subjects),
            year_1_sem_2_teachers=json.dumps(payload.year_1_sem_2_teachers),
            year_2_sem_1_subjects=json.dumps(payload.year_2_sem_1_subjects),
            year_2_sem_1_teachers=json.dumps(payload.year_2_sem_1_teachers),
            year_2_sem_2_subjects=json.dumps(payload.year_2_sem_2_subjects),
            year_2_sem_2_teachers=json.dumps(payload.year_2_sem_2_teachers),
            year_3_sem_1_subjects=json.dumps(payload.year_3_sem_1_subjects),
            year_3_sem_1_teachers=json.dumps(payload.year_3_sem_1_teachers),
            year_3_sem_2_subjects=json.dumps(payload.year_3_sem_2_subjects),
            year_3_sem_2_teachers=json.dumps(payload.year_3_sem_2_teachers),
            year_4_sem_1_subjects=json.dumps(payload.year_4_sem_1_subjects),
            year_4_sem_1_teachers=json.dumps(payload.year_4_sem_1_teachers),
            year_4_sem_2_subjects=json.dumps(payload.year_4_sem_2_subjects),
            year_4_sem_2_teachers=json.dumps(payload.year_4_sem_2_teachers),
            subsequent_folders=json.dumps(incoming_folders),
            subject_files=json.dumps(incoming_subject_files),
        )
        db.add(new_reg)
        db.commit()
        db.refresh(new_reg)
    else:
        existing_folders = _load_json_list(existing_reg.subsequent_folders)
        for f in incoming_folders:
            if f not in existing_folders:
                existing_folders.append(f)

        existing_files_map: Dict[str, List[str]] = _load_json_dict(existing_reg.subject_files)
        for subj_key, flist in incoming_subject_files.items():
            merged_list = existing_files_map.setdefault(subj_key, [])
            for item in flist:
                if item not in merged_list:
                    merged_list.append(item)

        if payload.description:
            existing_reg.description = payload.description

        if len(payload.year_1_sem_1_subjects) > 0:
            existing_reg.year_1_sem_1_subjects = json.dumps(payload.year_1_sem_1_subjects)
            existing_reg.year_1_sem_1_teachers = json.dumps(payload.year_1_sem_1_teachers)
        if len(payload.year_1_sem_2_subjects) > 0:
            existing_reg.year_1_sem_2_subjects = json.dumps(payload.year_1_sem_2_subjects)
            existing_reg.year_1_sem_2_teachers = json.dumps(payload.year_1_sem_2_teachers)
        if len(payload.year_2_sem_1_subjects) > 0:
            existing_reg.year_2_sem_1_subjects = json.dumps(payload.year_2_sem_1_subjects)
            existing_reg.year_2_sem_1_teachers = json.dumps(payload.year_2_sem_1_teachers)
        if len(payload.year_2_sem_2_subjects) > 0:
            existing_reg.year_2_sem_2_subjects = json.dumps(payload.year_2_sem_2_subjects)
            existing_reg.year_2_sem_2_teachers = json.dumps(payload.year_2_sem_2_teachers)
        if len(payload.year_3_sem_1_subjects) > 0:
            existing_reg.year_3_sem_1_subjects = json.dumps(payload.year_3_sem_1_subjects)
            existing_reg.year_3_sem_1_teachers = json.dumps(payload.year_3_sem_1_teachers)
        if len(payload.year_3_sem_2_subjects) > 0:
            existing_reg.year_3_sem_2_subjects = json.dumps(payload.year_3_sem_2_subjects)
            existing_reg.year_3_sem_2_teachers = json.dumps(payload.year_3_sem_2_teachers)
        if len(payload.year_4_sem_1_subjects) > 0:
            existing_reg.year_4_sem_1_subjects = json.dumps(payload.year_4_sem_1_subjects)
            existing_reg.year_4_sem_1_teachers = json.dumps(payload.year_4_sem_1_teachers)
        if len(payload.year_4_sem_2_subjects) > 0:
            existing_reg.year_4_sem_2_subjects = json.dumps(payload.year_4_sem_2_subjects)
            existing_reg.year_4_sem_2_teachers = json.dumps(payload.year_4_sem_2_teachers)

        existing_reg.subsequent_folders = json.dumps(existing_folders)
        existing_reg.subject_files = json.dumps(existing_files_map)
        db.commit()
        db.refresh(existing_reg)

    return {
        "message": f"Regulation '{reg_name}' saved successfully.",
        "regulation_name": reg_name
    }


@app.get("/api/admin/regulations")
def list_regulations(db: Session = Depends(get_db)):
    """Lists all regulations with their subjects, teacher IDs, subsequent folders, and subject_files."""
    regs = db.query(models.Regulation).order_by(models.Regulation.regulation_name.asc()).all()
    return {
        "regulations": [
            {
                "id": r.id,
                "regulation_name": r.regulation_name,
                "description": r.description,
                "year_1_sem_1_subjects": _load_json_list(r.year_1_sem_1_subjects),
                "year_1_sem_1_teachers": _load_json_list(r.year_1_sem_1_teachers),
                "year_1_sem_2_subjects": _load_json_list(r.year_1_sem_2_subjects),
                "year_1_sem_2_teachers": _load_json_list(r.year_1_sem_2_teachers),
                "year_2_sem_1_subjects": _load_json_list(r.year_2_sem_1_subjects),
                "year_2_sem_1_teachers": _load_json_list(r.year_2_sem_1_teachers),
                "year_2_sem_2_subjects": _load_json_list(r.year_2_sem_2_subjects),
                "year_2_sem_2_teachers": _load_json_list(r.year_2_sem_2_teachers),
                "year_3_sem_1_subjects": _load_json_list(r.year_3_sem_1_subjects),
                "year_3_sem_1_teachers": _load_json_list(r.year_3_sem_1_teachers),
                "year_3_sem_2_subjects": _load_json_list(r.year_3_sem_2_subjects),
                "year_3_sem_2_teachers": _load_json_list(r.year_3_sem_2_teachers),
                "year_4_sem_1_subjects": _load_json_list(r.year_4_sem_1_subjects),
                "year_4_sem_1_teachers": _load_json_list(r.year_4_sem_1_teachers),
                "year_4_sem_2_subjects": _load_json_list(r.year_4_sem_2_subjects),
                "year_4_sem_2_teachers": _load_json_list(r.year_4_sem_2_teachers),
                "subsequent_folders": _load_json_list(r.subsequent_folders),
                "subject_files": _load_json_dict(r.subject_files),
            }
            for r in regs
        ]
    }


# --- CORE AUTH & ACADEMIC YEAR ROUTES ---

@app.post("/api/login")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == request.username).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )

    if not pwd_context.verify(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )

    faculty_record = db.query(models.Faculty).filter(models.Faculty.user_id == user.id).first()

    return {
        "message": "Login successful",
        "username": user.username,
        "role": user.role,
        "teacher_id": faculty_record.teacher_id if faculty_record else user.id
    }


def _find_subject_override(
    overrides: List[AcademicYearSubjectFill],
    subj_name: str,
    year_no: int,
    sem_no: int
) -> Optional[AcademicYearSubjectFill]:
    target_name = subj_name.strip().lower()
    for item in overrides:
        if item.name.strip().lower() != target_name:
            continue
        if item.year_no is not None and item.year_no != year_no:
            continue
        if item.semester_no is not None and item.semester_no != sem_no:
            continue
        return item
    return None


@app.post("/api/admin/create-academic-year")
def create_academic_year(data: AcademicYearCreateRequest, db: Session = Depends(get_db)):
    """
    Creates an academic year from year_range and regulation_name,
    with optional subject_details to fill in extra details when creating the year.
    """
    # 1. QUERY & VALIDATE
    existing_year = db.query(models.AcademicYear).filter(
        models.AcademicYear.year_range == data.year_range.strip()
    ).first()
    if existing_year:
        raise HTTPException(status_code=400, detail="This Academic Year already exists!")

    reg_name = data.regulation_name.strip()
    reg_obj = db.query(models.Regulation).filter(
        models.Regulation.regulation_name == reg_name
    ).first()
    if not reg_obj:
        raise HTTPException(
            status_code=404,
            detail=f"Regulation '{reg_name}' not found in Regulations table. Create it via POST /api/admin/regulations first."
        )

    overrides = data.subject_details or []
    for ov in overrides:
        if ov.teacher_id is not None and ov.teacher_id != 0:
            fac = db.query(models.Faculty).filter(models.Faculty.teacher_id == ov.teacher_id).first()
            if not fac:
                raise HTTPException(
                    status_code=400,
                    detail=f"Faculty with teacher_id={ov.teacher_id} not found in Faculty table."
                )

    # 2. EXECUTE LOGIC
    new_academic_year = models.AcademicYear(year_range=data.year_range.strip())
    db.add(new_academic_year)
    db.commit()
    db.refresh(new_academic_year)

    year_folder_path = STORAGE_ROOT / new_academic_year.year_range
    ensure_base_year_folders(year_folder_path)

    reg_subsequent_folders = _clean_folder_list(_load_json_list(reg_obj.subsequent_folders))
    reg_subject_files: Dict[str, List[str]] = _load_json_dict(reg_obj.subject_files)

    semester_slots = [
        (1, 1, reg_obj.year_1_sem_1_subjects, reg_obj.year_1_sem_1_teachers),
        (1, 2, reg_obj.year_1_sem_2_subjects, reg_obj.year_1_sem_2_teachers),
        (2, 1, reg_obj.year_2_sem_1_subjects, reg_obj.year_2_sem_1_teachers),
        (2, 2, reg_obj.year_2_sem_2_subjects, reg_obj.year_2_sem_2_teachers),
        (3, 1, reg_obj.year_3_sem_1_subjects, reg_obj.year_3_sem_1_teachers),
        (3, 2, reg_obj.year_3_sem_2_subjects, reg_obj.year_3_sem_2_teachers),
        (4, 1, reg_obj.year_4_sem_1_subjects, reg_obj.year_4_sem_1_teachers),
        (4, 2, reg_obj.year_4_sem_2_subjects, reg_obj.year_4_sem_2_teachers),
    ]

    created_subjects_info = []

    for year_no, sem_no, subj_json, teacher_json in semester_slots:
        for subj_name, default_teacher_id in _pair_subjects_and_teachers(subj_json, teacher_json):
            ov = _find_subject_override(overrides, subj_name, year_no, sem_no)
            teacher_id = ov.teacher_id if (ov and ov.teacher_id is not None) else default_teacher_id
            course_type = ov.course_type if (ov and ov.course_type) else "major"
            course_format = ov.course_format if (ov and ov.course_format) else "theory"

            faculty_member = db.query(models.Faculty).filter(
                models.Faculty.teacher_id == teacher_id
            ).first()

            db_subject = models.Subject(
                name=subj_name,
                regulation=reg_name,
                course_type=course_type,
                course_format=course_format,
                year_no=year_no,
                semester_no=sem_no,
                academic_year_id=new_academic_year.id,
                faculty_id=teacher_id
            )
            db.add(db_subject)
            db.commit()
            db.refresh(db_subject)

            subject_dir = (
                year_folder_path
                / f"Year_{year_no}"
                / f"Semester_{sem_no}"
                / subj_name
            )

            all_folders_to_create = list(STANDARD_SUBFOLDERS)
            for sf in reg_subsequent_folders:
                if sf not in all_folders_to_create:
                    all_folders_to_create.append(sf)

            for folder_name in all_folders_to_create:
                (subject_dir / folder_name).mkdir(parents=True, exist_ok=True)

            combined_entries: List[str] = []
            for entry in _get_files_for_subject(reg_subject_files, subj_name):
                if entry not in combined_entries:
                    combined_entries.append(entry)
            if ov and ov.extra_files:
                for entry in ov.extra_files:
                    clean_e = entry.strip()
                    if clean_e and clean_e not in combined_entries:
                        combined_entries.append(clean_e)

            applied_entries: List[str] = []
            if combined_entries:
                applied_entries = _apply_subject_files_to_dir(subject_dir, combined_entries)
            else:
                old_subject_with_same_reg = db.query(models.Subject).filter(
                    models.Subject.regulation == reg_name,
                    models.Subject.name == subj_name,
                    models.Subject.year_no == year_no,
                    models.Subject.semester_no == sem_no,
                    models.Subject.id != db_subject.id
                ).first()

                if old_subject_with_same_reg:
                    old_ac_year = db.query(models.AcademicYear).filter(
                        models.AcademicYear.id == old_subject_with_same_reg.academic_year_id
                    ).first()
                    if old_ac_year:
                        old_subject_dir = (
                            STORAGE_ROOT
                            / old_ac_year.year_range
                            / f"Year_{year_no}"
                            / f"Semester_{sem_no}"
                            / subj_name
                        )
                        if old_subject_dir.exists():
                            for item in old_subject_dir.iterdir():
                                if item.is_dir():
                                    dest_sub = subject_dir / item.name
                                    dest_sub.mkdir(parents=True, exist_ok=True)
                                    for file_item in item.iterdir():
                                        if file_item.is_file():
                                            shutil.copy(file_item, dest_sub / file_item.name)
                                            applied_entries.append(f"{item.name}/{file_item.name}")

            created_subjects_info.append({
                "name": subj_name,
                "year_no": year_no,
                "semester_no": sem_no,
                "course_type": course_type,
                "course_format": course_format,
                "teacher_id": teacher_id,
                "teacher_name": faculty_member.teacher_name if faculty_member else "Unassigned",
                "folders_created": all_folders_to_create,
                "subject_files_and_items_applied": applied_entries
            })

    # 3. RETURN RESPONSE
    return {
        "message": f"Academic Year {new_academic_year.year_range} created from regulation '{reg_name}'.",
        "year_range": new_academic_year.year_range,
        "regulation_name": reg_name,
        "base_years_created": ["Year_1", "Year_2", "Year_3", "Year_4"],
        "subjects_created": created_subjects_info
    }


# --- FACULTY ROUTES ---

@app.get("/api/faculty/subjects/{teacher_id}")
def get_faculty_subjects(teacher_id: int, db: Session = Depends(get_db)):
    subjects = db.query(models.Subject).filter(models.Subject.faculty_id == teacher_id).all()
    faculty_member = db.query(models.Faculty).filter(models.Faculty.teacher_id == teacher_id).first()

    if not subjects:
        return {
            "teacher_id": teacher_id,
            "teacher_name": faculty_member.teacher_name if faculty_member else None,
            "message": "No subjects assigned to this faculty member.",
            "subjects": []
        }

    subject_list = []
    for subj in subjects:
        ac_year = db.query(models.AcademicYear).filter(
            models.AcademicYear.id == subj.academic_year_id
        ).first()
        subject_list.append({
            "subject_id": subj.id,
            "name": subj.name,
            "regulation": subj.regulation,
            "course_type": subj.course_type,
            "course_format": subj.course_format,
            "year_no": subj.year_no,
            "semester_no": subj.semester_no,
            "academic_year": ac_year.year_range if ac_year else "Unknown"
        })

    return {
        "teacher_id": teacher_id,
        "teacher_name": faculty_member.teacher_name if faculty_member else None,
        "subjects": subject_list
    }


@app.post("/api/faculty/upload-file")
async def upload_file(
    subject_id: int = Form(...),
    subfolder_name: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    subject = db.query(models.Subject).filter(models.Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found!")

    ac_year = db.query(models.AcademicYear).filter(
        models.AcademicYear.id == subject.academic_year_id
    ).first()
    if not ac_year:
        raise HTTPException(status_code=404, detail="Academic year not found for this subject!")

    target_dir = (
        STORAGE_ROOT
        / ac_year.year_range
        / f"Year_{subject.year_no}"
        / f"Semester_{subject.semester_no}"
        / subject.name
        / subfolder_name
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    original_filename = file.filename

    existing_file_record = db.query(models.FileVersion).filter(
        models.FileVersion.subject_id == subject_id,
        models.FileVersion.subfolder_name == subfolder_name,
        models.FileVersion.file_name == original_filename
    ).order_by(models.FileVersion.version_number.desc()).first()

    next_version = 1
    if existing_file_record:
        next_version = existing_file_record.version_number + 1

    file_stem = Path(original_filename).stem
    file_suffix = Path(original_filename).suffix
    versioned_filename = f"{file_stem}_v{next_version}{file_suffix}" if next_version > 1 else original_filename

    file_path = target_dir / versioned_filename

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    db_file_version = models.FileVersion(
        subject_id=subject_id,
        subfolder_name=subfolder_name,
        file_name=original_filename,
        version_number=next_version,
        onedrive_path=str(file_path)
    )
    db.add(db_file_version)
    db.commit()

    return {
        "message": f"File uploaded successfully as Version {next_version}!",
        "filename": versioned_filename,
        "version": next_version,
        "path": str(file_path)
    }
#uvicorn main:app --reload
#http://127.0.0.1:8000/docs