"""Create the initial administrator account in the actual SQLite database.
Faculty accounts 101 and 102 are inserted by seed_sample_data.py.
Run once when setting up the project. Re-running resets the administrator password.
"""
from database import SessionLocal, engine
import models
from passlib.context import CryptContext

models.Base.metadata.create_all(bind=engine)
pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')


def seed_users():
    db = SessionLocal()
    try:
        admin = db.query(models.User).filter(models.User.role == 'admin').first()
        if admin:
            admin.username = 'admin'
            admin.password_hash = pwd_context.hash('admin123')
            print('Updated administrator account.')
        else:
            db.add(models.User(username='admin', password_hash=pwd_context.hash('admin123'), role='admin'))
            print('Created administrator account.')
        db.commit()
    except:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == '__main__':
    seed_users()
