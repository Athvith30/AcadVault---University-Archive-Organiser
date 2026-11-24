"""AcadVault: one FastAPI frontend and backend with a single SQLite database.
Sample records, when requested, are inserted into the same database by seed_sample_data.py.
The visual style is based on File 3; its application code is not used.
"""
from pathlib import Path
from urllib.parse import quote
from datetime import datetime
import json
import os
import re
import secrets
import shutil

from fastapi import Request, UploadFile, HTTPException
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

BASE = Path(__file__).resolve().parent
os.chdir(BASE)  # choose database location BEFORE importing backend_core

import backend_core as backend
from backend_core import app, pwd_context
import models
from database import SessionLocal
app.mount('/static', StaticFiles(directory=str(BASE / 'static')), name='live_static')
views = Jinja2Templates(directory=str(BASE / 'templates'))


def go(location):
    return RedirectResponse(location, status_code=303)


def flash(req, msg, error=False):
    req.session['notice'] = str(msg)
    req.session['notice_error'] = bool(error)


def token(req):
    if 'csrf' not in req.session:
        req.session['csrf'] = secrets.token_hex(24)
    return req.session['csrf']


def guard(req, form):
    if not secrets.compare_digest(str(form.get('csrf', '')), str(req.session.get('csrf', ''))):
        raise HTTPException(status_code=403, detail='Invalid form security token')


def current(req):
    return req.session.get('user')


def is_admin(req):
    return bool(current(req) and current(req).get('role') == 'admin')


def is_faculty(req):
    return bool(current(req) and current(req).get('role') == 'faculty')


def authorized_subjects(req, db):
    q = db.query(models.Subject)
    if is_faculty(req):
        q = q.filter(models.Subject.faculty_id == current(req).get('teacher_id'))
    return q.order_by(models.Subject.academic_year_id.desc(), models.Subject.semester_no, models.Subject.name).all()


def display(req, page, **extra):
    data = {'request': req, 'user': current(req), 'csrf': token(req),
            'notice': req.session.pop('notice', None), 'notice_error': req.session.pop('notice_error', False)}
    data.update(extra)
    return views.TemplateResponse(req, page, data)


def safe_action(fn, req, destination, *args, **kwargs):
    """Run an original backend function and expose its genuine errors on the UI."""
    try:
        result = fn(*args, **kwargs)
        flash(req, result.get('message', 'Saved successfully') if isinstance(result, dict) else 'Saved successfully')
    except HTTPException as exc:
        flash(req, exc.detail, True)
    except Exception as exc:
        flash(req, f'Action failed: {type(exc).__name__}: {exc}', True)
    return go(destination)


@app.middleware('http')
async def protect_api(request: Request, call_next):
    # File 1 originally provided unauthenticated APIs. The UI adds session checks.
    path = request.url.path
    if path.startswith('/api/') and path != '/api/login':
        user = request.session.get('user')
        if not user:
            return JSONResponse({'detail': 'Please log in to access the API'}, status_code=401)
        if path.startswith('/api/admin/') and user.get('role') != 'admin':
            return JSONResponse({'detail': 'Admin only'}, status_code=403)
        if path.startswith('/api/faculty/') and user.get('role') != 'admin':
            teacher = str(user.get('teacher_id', ''))
            if not teacher:
                return JSONResponse({'detail': 'Faculty account is not assigned a teacher ID'}, status_code=403)
            suffix = path.split('/api/faculty/', 1)[1]
            if suffix.startswith(('subjects/', 'upload-options/')) and suffix.split('/')[-1] != teacher:
                return JSONResponse({'detail': 'Only your own subjects can be accessed'}, status_code=403)
            if suffix == 'upload-file' and request.method == 'POST':
                form = await request.form()
                if str(form.get('teacher_id', '')) != teacher:
                    return JSONResponse({'detail': 'Incorrect teacher ID'}, status_code=403)
            if suffix.startswith('files/') and str(request.query_params.get('teacher_id', '')) != teacher:
                return JSONResponse({'detail': 'Incorrect teacher ID'}, status_code=403)
        # A raw API POST is not CSRF-safe without a header; live UI uses server-side calls.
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            if request.headers.get('X-AcadVault-CSRF') != request.session.get('csrf'):
                return JSONResponse({'detail': 'Missing API CSRF header'}, status_code=403)
    return await call_next(request)


@app.get('/', include_in_schema=False)
def index(req: Request):
    return go('/dashboard' if current(req) else '/login')


@app.get('/login', include_in_schema=False)
def login_page(req: Request):
    return go('/dashboard') if current(req) else display(req, 'login.html')


@app.post('/login', include_in_schema=False)
async def login_action(req: Request):
    form = await req.form()
    guard(req, form)
    db = SessionLocal()
    try:
        result = backend.login(backend.LoginRequest(username=form.get('username', ''), password=form.get('password', '')), db=db)
        req.session.clear()
        req.session['user'] = {k: result.get(k) for k in ('username', 'role', 'teacher_id', 'teacher_name')}
        token(req)
        return go('/dashboard')
    except HTTPException:
        flash(req, 'Incorrect username or password', True)
        return go('/login')
    finally:
        db.close()


@app.get('/logout', include_in_schema=False)
def logout(req: Request):
    req.session.clear()
    return go('/login')


@app.get('/dashboard', include_in_schema=False)
def dashboard(req: Request):
    if not current(req): return go('/login')
    db = SessionLocal()
    try:
        assigned = authorized_subjects(req, db)
        ids = [s.id for s in assigned]
        files = db.query(models.FileVersion).filter(models.FileVersion.subject_id.in_(ids)).all() if ids else []
        return display(req, 'dashboard.html', subjects=assigned, files=files,
            years=db.query(models.AcademicYear).order_by(models.AcademicYear.year_range.desc()).all(),
            faculty=db.query(models.Faculty).all() if is_admin(req) else [])
    finally: db.close()


@app.get('/subjects', include_in_schema=False)
def subjects(req: Request):
    if not current(req): return go('/login')
    db = SessionLocal()
    try:
        years = {x.id: x.year_range for x in db.query(models.AcademicYear).all()}
        subj = authorized_subjects(req, db)
        return display(req, 'subjects.html', subjects=subj, years=years,
                       faculty={f.teacher_id: f.teacher_name for f in db.query(models.Faculty).all()})
    finally: db.close()


def valid_name(value):
    """A single safe folder or file component, not a path."""
    name = str(value or '').strip()
    if not name or name in ('.', '..') or name.endswith(('.', ' ')):
        raise HTTPException(400, 'Enter a valid name')
    if len(name) > 180 or any(c in name for c in '/\\\x00<>:"|?*') or any(ord(c) < 32 for c in name):
        raise HTTPException(400, 'Name contains unsupported characters')
    return name


def subject_directory(db, subject):
    year = db.query(models.AcademicYear).filter_by(id=subject.academic_year_id).first()
    if not year:
        raise HTTPException(404, 'Academic year not found')
    return backend.STORAGE_ROOT / year.year_range / f'Year_{subject.year_no}' / f'Semester_{subject.semester_no}' / subject.name


def stored_file_path(record, subject, db):
    """Reject any record pointing outside its actual subject folder."""
    path = Path(record.onedrive_path).resolve()
    root = (subject_directory(db, subject) / record.subfolder_name).resolve()
    if not path.is_relative_to(root) or not root.is_relative_to(backend.STORAGE_ROOT.resolve()):
        raise HTTPException(400, 'Stored file path is unsafe')
    return path


def require_edit_access(req, subject):
    if not current(req):
        raise HTTPException(401, 'Sign in first')
    if not is_admin(req) and subject.faculty_id != current(req).get('teacher_id'):
        raise HTTPException(403, 'Not permitted for this subject')


@app.get('/explorer', include_in_schema=False)
def explorer(req: Request, subject_id: int = 0):
    if not current(req): return go('/login')
    db = SessionLocal()
    try:
        subs = authorized_subjects(req, db)
        years = {y.id: y.year_range for y in db.query(models.AcademicYear).all()}
        chosen = next((s for s in subs if s.id == subject_id), None)
        directory_list = []
        if chosen:
            base = backend.STORAGE_ROOT / years.get(chosen.academic_year_id, '') / f'Year_{chosen.year_no}' / f'Semester_{chosen.semester_no}' / chosen.name
            folders = sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p:p.name.lower()) if base.exists() else []
            versions = db.query(models.FileVersion).filter(models.FileVersion.subject_id == chosen.id).all()
            for folder in folders:
                directory_list.append({'name': folder.name, 'files': [x for x in versions if x.subfolder_name == folder.name]})
        return display(req,'explorer.html', subjects=subs, years=years, chosen=chosen, directory_list=directory_list)
    finally: db.close()


@app.post('/explorer/folder', include_in_schema=False)
async def add_explorer_folder(req: Request):
    if not is_admin(req): return go('/login')
    form = await req.form(); guard(req, form)
    db = SessionLocal()
    try:
        sid = int(form.get('subject_id', '0'))
        sub = db.query(models.Subject).filter(models.Subject.id == sid).first()
        year = db.query(models.AcademicYear).filter(models.AcademicYear.id == sub.academic_year_id).first() if sub else None
        name = str(form.get('name','')).strip()
        if not sub or not year or name in ('','.','..') or '/' in name or '\\' in name:
            raise HTTPException(400,'Invalid subject or folder name')
        base = backend.STORAGE_ROOT / year.year_range / f'Year_{sub.year_no}' / f'Semester_{sub.semester_no}' / sub.name
        path = base / name
        if not path.resolve().is_relative_to(base.resolve()):
            raise HTTPException(400,'Invalid folder path')
        path.mkdir(parents=True,exist_ok=True)
        flash(req,'Folder created')
        return go(f'/explorer?subject_id={sid}')
    except (ValueError, HTTPException) as exc:
        flash(req, exc.detail if isinstance(exc,HTTPException) else 'Invalid subject ID', True)
        return go('/explorer')
    finally:db.close()


@app.post('/explorer/folder/rename', include_in_schema=False)
async def rename_explorer_folder(req: Request):
    if not is_admin(req): return go('/dashboard' if current(req) else '/login')
    form = await req.form(); guard(req, form)
    db = SessionLocal()
    sid = 0
    try:
        sid = int(form.get('subject_id', '0'))
        sub = db.query(models.Subject).filter_by(id=sid).first()
        if not sub: raise HTTPException(404, 'Subject not found')
        old_name = valid_name(form.get('old_name'))
        new_name = valid_name(form.get('new_name'))
        if old_name == new_name: raise HTTPException(400, 'Choose a different folder name')
        base = subject_directory(db, sub).resolve()
        source = (base / old_name).resolve()
        dest = (base / new_name).resolve()
        if not source.is_relative_to(base) or not dest.is_relative_to(base):
            raise HTTPException(400, 'Invalid folder')
        if not source.is_dir(): raise HTTPException(404, 'Folder does not exist')
        if dest.exists(): raise HTTPException(409, 'Destination folder already exists')
        records = db.query(models.FileVersion).filter_by(subject_id=sid, subfolder_name=old_name).all()
        new_paths = []
        for record in records:
            path = stored_file_path(record, sub, db)
            if not path.is_file():
                raise HTTPException(409, 'A registered file is missing. Rename cancelled.')
            new_paths.append((record, dest / path.name))
        source.rename(dest)
        try:
            for record, path in new_paths:
                record.subfolder_name = new_name
                record.onedrive_path = str(path)
            db.commit()
        except Exception:
            db.rollback()
            dest.rename(source)
            raise
        flash(req, f'Folder renamed from {old_name} to {new_name}')
    except (HTTPException, ValueError, OSError) as exc:
        db.rollback()
        flash(req, exc.detail if isinstance(exc, HTTPException) else str(exc), True)
    finally:
        db.close()
    return go(f'/explorer?subject_id={sid}' if sid else '/explorer')


@app.post('/files/{file_id}/edit', include_in_schema=False)
async def edit_file_record(req: Request, file_id: int):
    if not current(req): return go('/login')
    form = await req.form(); guard(req, form)
    db = SessionLocal()
    try:
        record = db.query(models.FileVersion).filter_by(id=file_id).first()
        if not record: raise HTTPException(404, 'File record not found')
        sub = db.query(models.Subject).filter_by(id=record.subject_id).first()
        if not sub: raise HTTPException(404, 'Subject no longer exists')
        require_edit_access(req, sub)
        new_name = valid_name(form.get('file_name'))
        if not re.fullmatch(r'[\w .()\-]+\.(pdf|docx|pptx|xlsx|csv|jpg|jpeg|png|gif|zip)', new_name, re.I):
            raise HTTPException(400, 'File name must have a supported extension')
        new_folder = valid_name(form.get('subfolder_name'))
        subject_dir = subject_directory(db, sub).resolve()
        target_dir = (subject_dir / new_folder).resolve()
        if not target_dir.is_dir() or not target_dir.is_relative_to(subject_dir):
            raise HTTPException(400, 'Destination folder does not exist')
        old_path = stored_file_path(record, sub, db)
        if not old_path.is_file(): raise HTTPException(404, 'Stored file is missing')
        # Preserve version number and physical name format established by the backend.
        year = db.query(models.AcademicYear).filter_by(id=sub.academic_year_id).first()
        new_physical = f'{year.year_range}_Y{sub.year_no}_S{sub.semester_no}_{sub.name}_{new_folder}_{Path(new_name).stem}_v{record.version_number}{Path(new_name).suffix}'
        destination = (target_dir / new_physical).resolve()
        if destination != old_path and destination.exists():
            raise HTTPException(409, 'A file with the destination name already exists')
        duplicate = db.query(models.FileVersion).filter(
            models.FileVersion.id != record.id,
            models.FileVersion.subject_id == sub.id,
            models.FileVersion.file_name == new_name,
            models.FileVersion.subfolder_name == new_folder,
            models.FileVersion.version_number == record.version_number).first()
        if duplicate: raise HTTPException(409, 'A record with this name and version already exists')
        if destination != old_path: old_path.rename(destination)
        try:
            record.file_name = new_name
            record.subfolder_name = new_folder
            record.onedrive_path = str(destination)
            db.commit()
        except Exception:
            db.rollback()
            if destination != old_path and destination.exists(): destination.rename(old_path)
            raise
        flash(req, 'File details updated')
    except (HTTPException, OSError) as exc:
        db.rollback()
        flash(req, exc.detail if isinstance(exc, HTTPException) else str(exc), True)
    finally: db.close()
    return go('/search')


@app.post('/admin/regulation/copy', include_in_schema=False)
async def copy_regulation(req: Request):
    if not is_admin(req): return go('/dashboard' if current(req) else '/login')
    form = await req.form(); guard(req, form)
    db = SessionLocal()
    try:
        source_name = str(form.get('source_name', '')).strip()
        new_name = str(form.get('new_name', '')).strip()
        if not re.fullmatch(r'[A-Za-z0-9_ -]{1,60}', new_name):
            raise HTTPException(400, 'Enter a valid regulation name')
        source = db.query(models.Regulation).filter_by(regulation_name=source_name).first()
        if not source: raise HTTPException(404, 'Source regulation not found')
        if db.query(models.Regulation).filter_by(regulation_name=new_name).first():
            raise HTTPException(409, 'A regulation with that name already exists')
        # A distinct regulation configuration, not a pointer to the original.
        columns = [col.name for col in models.Regulation.__table__.columns
                   if col.name not in ('id', 'regulation_name')]
        values = {column: getattr(source, column) for column in columns}
        copied = models.Regulation(regulation_name=new_name, **values)
        db.add(copied)
        db.commit()
        flash(req, f'Regulation {new_name} created from {source_name}')
    except (HTTPException, Exception) as exc:
        db.rollback()
        flash(req, exc.detail if isinstance(exc, HTTPException) else f'Could not copy regulation: {exc}', True)
    finally: db.close()
    return go('/admin')


@app.get('/search', include_in_schema=False)
def search(req: Request, q: str = '', year: str = '', semester: str = '', folder: str = ''):
    if not current(req): return go('/login')
    db = SessionLocal()
    try:
        subs = authorized_subjects(req, db)
        ids = [s.id for s in subs]
        year_by_id = {y.id: y.year_range for y in db.query(models.AcademicYear).all()}
        matching = []
        if ids:
            records = db.query(models.FileVersion).filter(models.FileVersion.subject_id.in_(ids)).all()
            by_id = {s.id: s for s in subs}
            for record in records:
                subj = by_id[record.subject_id]
                yr = year_by_id.get(subj.academic_year_id, '')
                if (q.lower() not in (record.file_name + ' ' + subj.name).lower() or
                    (year and yr != year) or
                    (semester and str(subj.semester_no) != semester) or
                    (folder and folder.lower() not in record.subfolder_name.lower())):
                    continue
                matching.append({'file': record, 'subject': subj, 'year': yr})
        return display(req, 'search.html', results=matching, q=q, year=year, semester=semester, folder=folder,
                       years=sorted(set(year_by_id.values())))
    finally: db.close()


@app.get('/file-status', include_in_schema=False)
def file_status(req: Request):
    if not is_admin(req): return go('/dashboard' if current(req) else '/login')
    db = SessionLocal()
    try:
        subs = db.query(models.Subject).all()
        ids = [s.id for s in subs]
        records = db.query(models.FileVersion).filter(models.FileVersion.subject_id.in_(ids)).all() if ids else []
        required = ['namelist','course files','notes','modules','cia 1','midsem','cia 2','endsem']
        rows = []
        for subject in subs:
            present = [f for f in records if f.subject_id == subject.id]
            categories = {f.subfolder_name.lower() for f in present}
            rows.append({'subject': subject, 'present': present, 'missing': [c for c in required if c not in categories]})
        return display(req, 'file_status.html', rows=rows)
    finally: db.close()


@app.get('/upload', include_in_schema=False)
def upload_page(req: Request):
    if not current(req): return go('/login')
    db = SessionLocal()
    try:
        subs = authorized_subjects(req, db)
        years = {y.id: y.year_range for y in db.query(models.AcademicYear).all()}
        location_options = {}
        for s in subs:
            location = backend.STORAGE_ROOT / years.get(s.academic_year_id, '') / f'Year_{s.year_no}' / f'Semester_{s.semester_no}' / s.name
            folders = backend.STANDARD_SUBFOLDERS[:]
            if location.is_dir():
                folders.extend(c.name for c in location.iterdir() if c.is_dir() and c.name not in folders)
            location_options[str(s.id)] = folders
        return display(req, 'upload.html', subjects=subs, years=years, locations=json.dumps(location_options))
    finally: db.close()


@app.post('/upload', include_in_schema=False)
async def upload_action(req: Request):
    if not current(req): return go('/login')
    form = await req.form()
    guard(req, form)
    db = SessionLocal()
    try:
        subject_id = int(form.get('subject_id', '0'))
        subj = db.query(models.Subject).filter(models.Subject.id == subject_id).first()
        if not subj or (not is_admin(req) and subj.faculty_id != current(req).get('teacher_id')):
            raise HTTPException(status_code=403, detail='Not assigned to this subject')
        ac_year = db.query(models.AcademicYear).filter(models.AcademicYear.id == subj.academic_year_id).first()
        subfolder = str(form.get('subfolder_name', '')).strip()
        upl = form.get('file')
        if not upl or not getattr(upl, 'filename', ''):
            raise HTTPException(status_code=400, detail='Choose a file')
        # File 1 does not validate these paths. Check them before calling its upload function.
        if subfolder in ('', '.', '..') or '/' in subfolder or '\\' in subfolder:
            raise HTTPException(status_code=400, detail='Invalid folder name')
        valid_folder = backend.STORAGE_ROOT / ac_year.year_range / f'Year_{subj.year_no}' / f'Semester_{subj.semester_no}' / subj.name / subfolder
        if not valid_folder.resolve().is_relative_to((backend.STORAGE_ROOT / ac_year.year_range).resolve()):
            raise HTTPException(status_code=400, detail='Invalid folder path')
        if Path(upl.filename).name != upl.filename or not re.fullmatch(r'[\w .()\-]+\.(pdf|docx|pptx|xlsx|csv|jpg|jpeg|png|gif|zip)', upl.filename, re.I):
            raise HTTPException(status_code=400, detail='Unsupported or unsafe filename')
        if upl.size is not None and upl.size > 25 * 1024 * 1024:
            raise HTTPException(status_code=400, detail='Maximum file size is 25 MB')
        result = await backend.upload_file(teacher_id=subj.faculty_id, academic_year=ac_year.year_range,
            year_no=subj.year_no, semester_no=subj.semester_no, subject_id=subject_id,
            subfolder_name=subfolder, file=upl, db=db)
        flash(req, result['message'])
    except (HTTPException, ValueError) as exc:
        flash(req, exc.detail if isinstance(exc, HTTPException) else 'Invalid subject', True)
    except Exception as exc:
        flash(req, f'Upload failed: {exc}', True)
    finally: db.close()
    return go('/upload')


@app.get('/files/{file_id}/download', include_in_schema=False)
def download(req: Request, file_id: int):
    if not current(req): return go('/login')
    db = SessionLocal()
    try:
        file = db.query(models.FileVersion).filter(models.FileVersion.id == file_id).first()
        if not file: raise HTTPException(404, 'File not found')
        subj = db.query(models.Subject).filter(models.Subject.id == file.subject_id).first()
        if not subj or (is_faculty(req) and subj.faculty_id != current(req).get('teacher_id')):
            raise HTTPException(403, 'Not permitted')
        path = Path(file.onedrive_path).resolve()
        if not path.is_relative_to(backend.STORAGE_ROOT.resolve()) or not path.is_file():
            raise HTTPException(404, 'Stored file not found')
        return FileResponse(path, filename=file.file_name)
    finally: db.close()


@app.post('/files/{file_id}/delete', include_in_schema=False)
async def delete_file(req: Request, file_id: int):
    if not current(req): return go('/login')
    form = await req.form(); guard(req, form)
    db = SessionLocal()
    try:
        obj = db.query(models.FileVersion).filter(models.FileVersion.id == file_id).first()
        subject = db.query(models.Subject).filter(models.Subject.id == obj.subject_id).first() if obj else None
        if not subject or (is_faculty(req) and subject.faculty_id != current(req).get('teacher_id')):
            raise HTTPException(403, 'Not permitted')
        # Do not delete paths outside department_storage even if database was altered.
        if not Path(obj.onedrive_path).resolve().is_relative_to(backend.STORAGE_ROOT.resolve()):
            raise HTTPException(403, 'Unsafe stored path')
        return safe_action(backend.delete_uploaded_file, req, '/search', file_id, subject.faculty_id, db)
    except HTTPException as exc:
        flash(req, exc.detail, True)
        return go('/search')
    finally: db.close()


@app.get('/admin', include_in_schema=False)
def admin_page(req: Request):
    if not is_admin(req): return go('/dashboard' if current(req) else '/login')
    db = SessionLocal()
    try:
        return display(req, 'admin.html', faculty=db.query(models.Faculty).order_by(models.Faculty.teacher_id).all(),
            regulations=db.query(models.Regulation).all(), years=db.query(models.AcademicYear).all(),
            subjects=db.query(models.Subject).order_by(models.Subject.id.desc()).all())
    finally: db.close()


@app.post('/admin/assign', include_in_schema=False)
async def assign_subject(req: Request):
    if not is_admin(req): return go('/login')
    form=await req.form(); guard(req,form)
    db=SessionLocal()
    try:
        sid=int(form.get('subject_id','0'))
        teacher=int(form.get('teacher_id','0'))
        subj=db.query(models.Subject).filter(models.Subject.id==sid).first()
        fac=db.query(models.Faculty).filter(models.Faculty.teacher_id==teacher, models.Faculty.is_active==True).first()
        if not subj or (teacher != 0 and not fac):
            raise HTTPException(400,'Choose a valid subject and active faculty member')
        subj.faculty_id=teacher
        db.commit()
        flash(req,'Faculty assignment updated')
        return go('/admin')
    except (ValueError,HTTPException) as exc:
        flash(req,exc.detail if isinstance(exc,HTTPException) else 'Invalid ID',True)
        return go('/admin')
    finally:db.close()


@app.post('/admin/faculty', include_in_schema=False)
async def add_faculty(req: Request):
    if not is_admin(req): return go('/login')
    form = await req.form(); guard(req, form)
    db = SessionLocal()
    try:
        return safe_action(backend.add_faculty, req, '/admin', backend.FacultyCreateRequest(
            teacher_id=int(form['teacher_id']), teacher_name=form['teacher_name'], password=form['password']), db)
    except ValueError:
        flash(req, 'Teacher ID must be a number', True)
        return go('/admin')
    finally: db.close()


@app.post('/admin/faculty/{teacher_id}/delete', include_in_schema=False)
async def remove_faculty(req: Request, teacher_id: int):
    if not is_admin(req): return go('/login')
    form = await req.form(); guard(req, form)
    db = SessionLocal()
    try: return safe_action(backend.delete_faculty, req, '/admin', teacher_id, db)
    finally: db.close()


@app.post('/admin/regulation', include_in_schema=False)
async def save_regulation(req: Request):
    if not is_admin(req): return go('/login')
    form = await req.form(); guard(req, form)
    db = SessionLocal()
    try:
        kwargs = {'regulation_name': form.get('regulation_name', ''), 'description': form.get('description', ''),
                  'subsequent_folders': [x.strip() for x in form.get('subsequent_folders', '').split(',') if x.strip()]}
        for year in range(1, 5):
            for sem in (1, 2):
                base = f'year_{year}_sem_{sem}'
                entries = [x.strip() for x in form.get(base + '_subjects', '').split(',') if x.strip()]
                teacher_ids = [int(x.strip() or 0) for x in form.get(base + '_teachers', '').split(',')] if entries else []
                kwargs[base + '_subjects'] = entries
                kwargs[base + '_teachers'] = teacher_ids
        return safe_action(backend.upsert_regulation, req, '/admin', backend.RegulationUpsertRequest(**kwargs), db)
    except (ValueError, TypeError):
        flash(req, 'Teacher IDs must be numbers separated by commas', True)
        return go('/admin')
    finally: db.close()


@app.post('/admin/regulation/{name}/delete', include_in_schema=False)
async def delete_regulation(req: Request, name: str):
    if not is_admin(req): return go('/login')
    form = await req.form(); guard(req, form)
    db = SessionLocal()
    try: return safe_action(backend.delete_regulation, req, '/admin', name, db)
    finally: db.close()


@app.post('/admin/year', include_in_schema=False)
async def create_year(req: Request):
    if not is_admin(req): return go('/login')
    form = await req.form(); guard(req, form)
    year = form.get('year_range', '').strip()
    if not re.fullmatch(r'\d{4}-\d{4}', year) or int(year[5:]) != int(year[:4]) + 1:
        flash(req, 'Academic year must look like 2026-2027', True)
        return go('/admin')
    db = SessionLocal()
    try: return safe_action(backend.create_academic_year, req, '/admin', backend.AcademicYearCreateRequest(
            year_range=year, regulation_name=form.get('regulation_name', '')), db)
    finally: db.close()


@app.post('/admin/year/{year_range}/delete', include_in_schema=False)
async def delete_year(req: Request, year_range: str):
    if not is_admin(req): return go('/login')
    form = await req.form(); guard(req, form)
    db = SessionLocal()
    try: return safe_action(backend.delete_academic_year, req, '/admin', year_range, db)
    finally: db.close()

# Session handling wraps the API auth middleware so request.session is available.
app.add_middleware(SessionMiddleware, secret_key=os.environ.get('ACADVAULT_SECRET_KEY', 'local-development-secret-change-before-sharing'), same_site='lax')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8000)
