from database import SessionLocal, engine, Base
from models import User, UserRole
from auth import get_password_hash


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # Default admin
    if not db.query(User).filter(User.username == "admin").first():
        admin = User(
            username="admin",
            email="admin@college.edu",
            password_hash=get_password_hash("admin123"),
            role=UserRole.ADMIN,
        )
        db.add(admin)
        print("Created admin user: admin / admin123")

    # Default faculty
    if not db.query(User).filter(User.username == "faculty").first():
        faculty = User(
            username="faculty",
            email="faculty@college.edu",
            password_hash=get_password_hash("faculty123"),
            role=UserRole.FACULTY,
        )
        db.add(faculty)
        print("Created faculty user: faculty / faculty123")

    db.commit()
    db.close()
    print("Seed complete.")


if __name__ == "__main__":
    seed()
