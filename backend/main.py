import hashlib
import io
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form, Request
from fastapi.responses import RedirectResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from database import engine, Base, get_db, settings
from models import User, UserRole, AcademicYear, Subject, FileUpload, FacultySubjectAssignment
from auth import (
    verify_password, get_password_hash, create_access_token,
    decode_token, oauth2_scheme, get_current_user, require_role,
)

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="AcadVault — University Archive Organiser")

# Session middleware for cookie-based auth (simpler than JWT for server-rendered UI)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY, session_cookie="acadvault_session")

# Static files
app.mount("/static", StaticFiles(directory="../frontend/static"), name="static")
app.mount("/uploads", StaticFiles(directory=settings.STORAGE_ROOT), name="uploads")

# Templates
templates = Jinja2Templates(directory="../frontend/templates")


def role_label(role):
    return role.value.title() if role else ""


templates.env.filters["role_label"] = role_label


# ---------- helpers ----------

def get_current_user_from_session(request: Request, db: Session = Depends(get_db)):
    """Get user from session cookie (for HTML routes)."""
    username = request.session.get("username")
    if not username:
        return None
    return db.query(User).filter(User.username == username).first()


def require_auth_html(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user_from_session(request, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"location": "/login"})
    return user


def require_role_html(allowed_roles: list):
    def checker(request: Request, db: Session = Depends(get_db)) -> User:
        user = require_auth_html(request, db)
        if user.role.value not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user
    return checker


def compute_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def stored_filename_for(original: str) -> str:
    ext = Path(original).suffix if original else ".bin"
    return f"{uuid.uuid4().hex}{ext}"


def next_version(db: Session, subject_id: int, original_filename: str) -> int:
    last = (
        db.query(FileUpload)
        .filter(FileUpload.subject_id == subject_id, FileUpload.original_filename == original_filename)
        .order_by(FileUpload.version.desc())
        .first()
    )
    return (last.version + 1) if last else 1


def file_size_str(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


# ---------- auth (API + HTML) ----------

@app.post("/auth/login")
def api_login(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    token = create_access_token(data={"sub": user.username})
    return {"access_token": token, "token_type": "bearer"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
def login_form(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid username or password"})
    request.session["username"] = user.username
    request.session["role"] = user.role.value
    request.session["user_id"] = user.id
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


# ---------- dashboard ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_session(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user: User = Depends(require_auth_html),
    db: Session = Depends(get_db),
):
    years = db.query(AcademicYear).order_by(AcademicYear.created_at.desc()).all()
    subjects = db.query(Subject).order_by(Subject.created_at.desc()).all()
    recent_files = db.query(FileUpload).order_by(FileUpload.uploaded_at.desc()).limit(4).all()
    total_files = db.query(FileUpload).count()
    total_users = db.query(User).count()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "years": years,
            "subjects": subjects,
            "recent_files": recent_files,
            "total_files": total_files,
            "total_users": total_users,
            "file_size_str": file_size_str,
        },
    )


# ---------- users (admin only) ----------

@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    user: User = Depends(require_role_html(["admin"])),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.created_at.desc()).all()
    faculty = db.query(User).filter(User.role == UserRole.FACULTY).order_by(User.username).all()
    subjects = db.query(Subject).order_by(Subject.academic_year_id, Subject.semester, Subject.code).all()
    assignments = db.query(FacultySubjectAssignment).all()
    assigned_set = {(a.faculty_id, a.subject_id) for a in assignments}
    return templates.TemplateResponse(
        "users.html",
        {
            "request": request,
            "user": user,
            "users": users,
            "faculty": faculty,
            "subjects": subjects,
            "assignments": assignments,
            "assigned_set": assigned_set,
        },
    )


@app.post("/admin/users")
def create_user(
    request: Request,
    user: User = Depends(require_role_html(["admin"])),
    db: Session = Depends(get_db),
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
):
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="Email already exists")
    new_user = User(
        username=username,
        email=email,
        password_hash=get_password_hash(password),
        role=UserRole(role),
    )
    db.add(new_user)
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


# ---------- academic years (admin only) ----------

@app.get("/admin/academic-years", response_class=HTMLResponse)
def admin_academic_years(
    request: Request,
    user: User = Depends(require_role_html(["admin"])),
    db: Session = Depends(get_db),
):
    years = db.query(AcademicYear).order_by(AcademicYear.created_at.desc()).all()
    return templates.TemplateResponse("academic_years.html", {"request": request, "user": user, "years": years})


@app.post("/admin/academic-years")
def create_academic_year(
    request: Request,
    user: User = Depends(require_role_html(["admin"])),
    db: Session = Depends(get_db),
    label: str = Form(...),
    start_year: int = Form(...),
    end_year: int = Form(...),
):
    if end_year != start_year + 1:
        raise HTTPException(status_code=400, detail="End year must be start year + 1")
    if db.query(AcademicYear).filter(AcademicYear.label == label).first():
        raise HTTPException(status_code=400, detail="Academic year already exists")
    year = AcademicYear(
        label=label,
        start_year=start_year,
        end_year=end_year,
        created_by=user.id,
    )
    db.add(year)
    db.commit()
    return RedirectResponse(url="/admin/academic-years", status_code=status.HTTP_302_FOUND)


# ---------- faculty subject access (admin only) ----------

@app.get("/admin/faculty-access", response_class=HTMLResponse)
def admin_faculty_access(
    request: Request,
    user: User = Depends(require_role_html(["admin"])),
    db: Session = Depends(get_db),
):
    faculty = db.query(User).filter(User.role == UserRole.FACULTY).order_by(User.username).all()
    subjects = db.query(Subject).order_by(Subject.academic_year_id, Subject.semester, Subject.code).all()
    assignments = db.query(FacultySubjectAssignment).all()
    assigned_set = {(a.faculty_id, a.subject_id) for a in assignments}
    return templates.TemplateResponse(
        "faculty_access.html",
        {
            "request": request,
            "user": user,
            "faculty": faculty,
            "subjects": subjects,
            "assigned_set": assigned_set,
        },
    )


@app.post("/admin/faculty-access")
def create_faculty_access(
    request: Request,
    user: User = Depends(require_role_html(["admin"])),
    db: Session = Depends(get_db),
    faculty_id: int = Form(...),
    subject_id: int = Form(...),
):
    exists = (
        db.query(FacultySubjectAssignment)
        .filter(FacultySubjectAssignment.faculty_id == faculty_id, FacultySubjectAssignment.subject_id == subject_id)
        .first()
    )
    if exists:
        request.session["flash_error"] = "This faculty already has access to that subject"
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)
    assignment = FacultySubjectAssignment(
        faculty_id=faculty_id,
        subject_id=subject_id,
        assigned_by=user.id,
    )
    db.add(assignment)
    db.commit()
    request.session["flash_success"] = "Faculty given access to subject"
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


@app.post("/admin/faculty-access/{assignment_id}/delete")
def delete_faculty_access(
    assignment_id: int,
    request: Request,
    user: User = Depends(require_role_html(["admin"])),
    db: Session = Depends(get_db),
):
    assignment = db.query(FacultySubjectAssignment).filter(FacultySubjectAssignment.id == assignment_id).first()
    if not assignment:
        request.session["flash_error"] = "Access record not found"
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)
    db.delete(assignment)
    db.commit()
    request.session["flash_success"] = "Faculty access removed from subject"
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


# ---------- subjects (admin + faculty) ----------

@app.get("/subjects", response_class=HTMLResponse)
def list_subjects(
    request: Request,
    user: User = Depends(require_auth_html),
    db: Session = Depends(get_db),
    year_id: Optional[int] = None,
):
    query = db.query(Subject)
    if year_id:
        query = query.filter(Subject.academic_year_id == year_id)
    subjects = query.order_by(Subject.semester, Subject.code).all()
    years = db.query(AcademicYear).all()
    return templates.TemplateResponse(
        "subjects.html",
        {"request": request, "user": user, "subjects": subjects, "years": years, "selected_year": year_id},
    )


@app.post("/subjects")
def create_subject(
    request: Request,
    user: User = Depends(require_role_html(["admin", "faculty"])),
    db: Session = Depends(get_db),
    code: str = Form(...),
    name: str = Form(...),
    regulation: str = Form("R2023"),
    semester: int = Form(...),
    academic_year_id: int = Form(...),
):
    subject = Subject(
        code=code.strip().upper(),
        name=name.strip(),
        regulation=regulation.strip(),
        semester=semester,
        academic_year_id=academic_year_id,
        created_by=user.id,
    )
    db.add(subject)
    db.commit()
    return RedirectResponse(url=f"/subjects?year_id={academic_year_id}", status_code=status.HTTP_302_FOUND)


# ---------- file upload (faculty + admin) ----------

@app.get("/files/upload", response_class=HTMLResponse)
def upload_page(
    request: Request,
    user: User = Depends(require_role_html(["admin", "faculty"])),
    db: Session = Depends(get_db),
):
    query = db.query(Subject).order_by(Subject.academic_year_id, Subject.semester, Subject.code)
    if user.role == UserRole.FACULTY:
        assigned_ids = db.query(FacultySubjectAssignment.subject_id).filter(
            FacultySubjectAssignment.faculty_id == user.id
        ).subquery()
        query = query.filter(Subject.id.in_(assigned_ids))
    subjects = query.all()
    return templates.TemplateResponse("upload.html", {"request": request, "user": user, "subjects": subjects, "settings": settings})


@app.post("/files/upload")
async def upload_file(
    request: Request,
    user: User = Depends(require_role_html(["admin", "faculty"])),
    db: Session = Depends(get_db),
    subject_id: int = Form(...),
    file: UploadFile = File(...),
):
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        request.session["flash_error"] = "Subject not found"
        return RedirectResponse(url="/files/upload", status_code=status.HTTP_302_FOUND)

    # Faculty can only upload to subjects they are assigned to
    if user.role == UserRole.FACULTY:
        assigned = db.query(FacultySubjectAssignment).filter(
            FacultySubjectAssignment.faculty_id == user.id,
            FacultySubjectAssignment.subject_id == subject_id,
        ).first()
        if not assigned:
            request.session["flash_error"] = "You are not assigned to this subject"
            return RedirectResponse(url="/files/upload", status_code=status.HTTP_302_FOUND)

    content = await file.read()
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File too large. Max {settings.MAX_FILE_SIZE_MB} MB")

    file_hash = compute_sha256(content)
    stored_name = stored_filename_for(file.filename or "upload.bin")
    file_path = Path(settings.STORAGE_ROOT) / stored_name
    file_path.write_bytes(content)

    version = next_version(db, subject_id, file.filename or "upload.bin")

    upload = FileUpload(
        subject_id=subject_id,
        uploaded_by=user.id,
        original_filename=file.filename or "upload.bin",
        stored_filename=stored_name,
        file_path=str(file_path),
        file_size=len(content),
        mime_type=file.content_type or "application/octet-stream",
        sha256_hash=file_hash,
        version=version,
    )
    db.add(upload)
    db.commit()

    return RedirectResponse(url=f"/subjects/{subject_id}", status_code=status.HTTP_302_FOUND)


# ---------- subject detail / file list ----------

@app.get("/subjects/{subject_id}", response_class=HTMLResponse)
def subject_detail(
    subject_id: int,
    request: Request,
    user: User = Depends(require_auth_html),
    db: Session = Depends(get_db),
):
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    files = (
        db.query(FileUpload)
        .filter(FileUpload.subject_id == subject_id)
        .order_by(FileUpload.original_filename, FileUpload.version.desc())
        .all()
    )
    return templates.TemplateResponse(
        "subject_files.html",
        {"request": request, "user": user, "subject": subject, "files": files, "file_size_str": file_size_str},
    )


# ---------- file download ----------

@app.get("/files/{file_id}/download")
def download_file(
    file_id: int,
    request: Request,
    user: User = Depends(require_auth_html),
    db: Session = Depends(get_db),
):
    upload = db.query(FileUpload).filter(FileUpload.id == file_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="File not found")
    path = Path(upload.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")
    return StreamingResponse(
        io.BytesIO(path.read_bytes()),
        media_type=upload.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{upload.original_filename}"'},
    )


# ---------- file delete ----------

@app.post("/files/{file_id}/delete")
def delete_file(
    file_id: int,
    request: Request,
    user: User = Depends(require_auth_html),
    db: Session = Depends(get_db),
):
    upload = db.query(FileUpload).filter(FileUpload.id == file_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="File not found")
    subject = db.query(Subject).filter(Subject.id == upload.subject_id).first()
    # Admin or the faculty who created the subject can delete
    if user.role == UserRole.FACULTY and (not subject or subject.created_by != user.id):
        raise HTTPException(status_code=403, detail="You can only delete files in your own subjects")
    # Remove from disk
    try:
        Path(upload.file_path).unlink(missing_ok=True)
    except OSError:
        pass
    db.delete(upload)
    db.commit()
    return RedirectResponse(url=f"/subjects/{upload.subject_id}", status_code=status.HTTP_302_FOUND)


# ---------- API routes (token-based) ----------

@app.get("/api/me")
def api_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role.value,
        "is_active": current_user.is_active,
    }


@app.get("/api/subjects")
def api_list_subjects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    year_id: Optional[int] = None,
):
    query = db.query(Subject)
    if year_id:
        query = query.filter(Subject.academic_year_id == year_id)
    subjects = query.all()
    return [
        {
            "id": s.id,
            "code": s.code,
            "name": s.name,
            "regulation": s.regulation,
            "semester": s.semester,
            "academic_year_id": s.academic_year_id,
            "created_by": s.created_by,
        }
        for s in subjects
    ]


@app.get("/api/files")
def api_list_files(
    subject_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    files = db.query(FileUpload).filter(FileUpload.subject_id == subject_id).all()
    return [
        {
            "id": f.id,
            "original_filename": f.original_filename,
            "file_size": f.file_size,
            "mime_type": f.mime_type,
            "version": f.version,
            "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
            "uploaded_by": f.uploader.username if f.uploader else None,
        }
        for f in files
    ]


@app.get("/health")
def health():
    return {"status": "ok"}
