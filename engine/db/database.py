from sqlalchemy import create_engine, inspect, text
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


def ensure_demo_schema() -> None:
    """Lightweight local schema sync for demo environments without Alembic."""
    with engine.begin() as connection:
        inspector = inspect(connection)
        if "investigation_cases" not in inspector.get_table_names():
            return

        columns = {column["name"] for column in inspector.get_columns("investigation_cases")}
        missing_statements = {
            "final_decision": "ALTER TABLE investigation_cases ADD COLUMN final_decision VARCHAR",
            "resolved_at": "ALTER TABLE investigation_cases ADD COLUMN resolved_at DATETIME",
            "escalation_level": "ALTER TABLE investigation_cases ADD COLUMN escalation_level INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, statement in missing_statements.items():
            if column_name not in columns:
                connection.execute(text(statement))
