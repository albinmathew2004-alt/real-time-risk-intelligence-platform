from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os


DATABASE_URL = os.getenv("DATABASE_URL")

# Local-dev fallback: SQLite (so the app can run without Docker/Postgres).
# If you want Postgres, set DATABASE_URL explicitly.
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./risk_intel.db"


connect_args = {}
if DATABASE_URL.startswith("sqlite:"):
    # Needed for FastAPI + SQLAlchemy in multi-threaded dev servers.
    connect_args = {"check_same_thread": False}


engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()