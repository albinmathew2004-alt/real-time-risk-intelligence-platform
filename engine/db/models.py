from sqlalchemy import Column, Integer, Float, String, JSON
from .database import Base


class AttemptLog(Base):
    __tablename__ = "attempt_logs"

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(String, index=True)
    risk = Column(String)
    confidence = Column(Float)
    confidence_score = Column(Float)
    combined_score = Column(Float)
    features = Column(JSON)
    signals = Column(JSON)
    timestamp = Column(String)


class RawExamEvent(Base):
    __tablename__ = "raw_exam_events"

    id = Column(Integer, primary_key=True, index=True)

    attempt_id = Column(String, index=True)

    candidate_id = Column(String, nullable=True, index=True)
    candidate_name = Column(String, nullable=True)
    candidate_email = Column(String, nullable=True)

    assessment_id = Column(String, nullable=True, index=True)
    assessment_name = Column(String, nullable=True)

    event_type = Column(String, index=True)
    payload = Column(JSON)

    occurred_at = Column(String)
    received_at = Column(String)


class RiskHistory(Base):
    __tablename__ = "risk_history"

    id = Column(Integer, primary_key=True, index=True)

    attempt_id = Column(String, index=True)

    candidate_id = Column(String, nullable=True, index=True)
    candidate_name = Column(String, nullable=True)
    candidate_email = Column(String, nullable=True)

    assessment_id = Column(String, nullable=True, index=True)
    assessment_name = Column(String, nullable=True)

    risk = Column(String)
    confidence = Column(Float)
    combined_score = Column(Float)

    reason = Column(String)
    timestamp = Column(String)