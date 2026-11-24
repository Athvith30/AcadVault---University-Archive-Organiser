"""Optional, repeatable local demo data for AcadVault.

Run after `python seed.py` from the backend directory:
    python seed_mock.py

Existing records are preserved; matching mock records are not duplicated.
"""
from pathlib import Path
import hashlib

from database import Base, SessionLocal, engine, settings
from models import AcademicYear, Subject, FileUpload, User

YEARS = ("2023-2024", "2024-2025")
SUBJECTS = (
    ("IT3401", "Data Structures", 3, "2023-2024"),
    ("IT3402", "Database Systems", 3, "2023-2024"),
    ("IT2401", "Python Programming", 2, "2023-2024"),
    ("IT4401", "Computer Networks", 4, "2024-2025"),
    ("IT4402", "Web Technology", 4, "2024-2025"),
    ("MA1401", "Mathematics", 1, "2024-2025"),
)
FILES = (
    ("IT3401", "2023-2024", "Namelist - Data Structures.pdf"),
    ("IT3401", "2023-2024", "Course Files - Data Structures.pdf"),
    ("IT3401", "2023-2024", "Notes - Unit 1.pdf"),
    ("IT3402", "2023-2024", "Modules - Database Systems.pdf"),
    ("IT3402", "2023-2024", "CIA 1 - Question Paper.pdf"),
    ("IT2401", "2023-2024", "Midsem - Python Programming.pdf"),
    ("IT4401", "2024-2025", "CIA 2 - Computer Networks.pdf"),
    ("IT4401", "2024-2025", "Endsem - Computer Networks.pdf"),
    ("IT4402", "2024-2025", "Notes - Web Technology.pdf"),
    ("MA1401", "2024-2025", "Course Files - Mathematics.pdf"),
)


def small_pdf(title):
    """A minimal valid PDF containing only a placeholder title."""
    safe = title.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 18 Tf 40 740 Td ({safe}) Tj ET".encode("ascii")
    parts = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(parts, start=1):
        offsets.append(len(data))
        data.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


def seed_mock():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        admin = db.query(User).filter_by(username="admin").first()
        if not admin:
            raise RuntimeError("Run `python seed.py` first to create the admin account.")
        years = {}
        for label in YEARS:
            year = db.query(AcademicYear).filter_by(label=label).first()
            if not year:
                start = int(label[:4])
                year = AcademicYear(label=label, start_year=start, end_year=start + 1, created_by=admin.id)
                db.add(year)
                db.flush()
            years[label] = year
        subjects = {}
        for code, name, sem, label in SUBJECTS:
            year = years[label]
            subject = db.query(Subject).filter_by(code=code, academic_year_id=year.id, semester=sem).first()
            if not subject:
                subject = Subject(code=code, name=name, regulation="R2023", semester=sem,
                                  academic_year_id=year.id, created_by=admin.id)
                db.add(subject)
                db.flush()
            subjects[(code, label)] = subject
        storage = Path(settings.STORAGE_ROOT)
        storage.mkdir(parents=True, exist_ok=True)
        added = 0
        for code, label, name in FILES:
            subject = subjects[(code, label)]
            existing = db.query(FileUpload).filter_by(subject_id=subject.id, original_filename=name).first()
            if existing:
                continue
            content = small_pdf(f"Sample: {name[:-4]}")
            stored = f"mock_{subject.id}_{hashlib.sha256(name.encode()).hexdigest()[:16]}.pdf"
            path = storage / stored
            path.write_bytes(content)
            db.add(FileUpload(subject_id=subject.id, uploaded_by=admin.id,
                              original_filename=name, stored_filename=stored, file_path=str(path),
                              file_size=len(content), mime_type="application/pdf",
                              sha256_hash=hashlib.sha256(content).hexdigest(), version=1))
            added += 1
        db.commit()
        print(f"Mock data ready: {len(YEARS)} years, {len(SUBJECTS)} subjects, {added} new sample files.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_mock()
