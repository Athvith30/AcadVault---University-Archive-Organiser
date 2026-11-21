# seed_users.py
from database import SessionLocal, engine
import models
from passlib.context import CryptContext

models.Base.metadata.create_all(bind=engine)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def seed_users():
    db = SessionLocal()

    admin_password_hash = pwd_context.hash("admin123")
    faculty_password_hash = pwd_context.hash("faculty123")

    # 1. Create or update Admin user
    admin_user = db.query(models.User).filter(models.User.role == "admin").first()
    if admin_user:
        admin_user.username = "admin"
        admin_user.password_hash = admin_password_hash
        print("Updated admin user ('admin').")
    else:
        admin_user = models.User(
            username="admin",
            password_hash=admin_password_hash,
            role="admin"
        )
        db.add(admin_user)
        print("Created admin user ('admin').")

    # 2. Create or update default Faculty user
    faculty_user = db.query(models.User).filter(models.User.username == "faculty").first()
    if faculty_user:
        faculty_user.password_hash = faculty_password_hash
        faculty_user.role = "faculty"
        print("Updated default faculty user ('faculty').")
    else:
        faculty_user = models.User(
            username="faculty",
            password_hash=faculty_password_hash,
            role="faculty"
        )
        db.add(faculty_user)
        print("Created default faculty user ('faculty').")

    db.commit()
    db.close()


if __name__ == "__main__":
    seed_users()