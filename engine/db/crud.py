from .database import SessionLocal
from .models import AttemptLog


def save_attempt(log_data: dict):

    db = SessionLocal()

    try:
        row = AttemptLog(**log_data)

        db.add(row)

        db.commit()

    finally:
        db.close()