# 🧠 Real-Time Risk Intelligence Platform

A hybrid AI-powered behavioral monitoring and cheating detection system built using FastAPI, Machine Learning, and Streamlit.

The platform analyzes candidate behavior during online assessments using:
- rule-based intelligence
- behavioral feature engineering
- machine learning classification
- hybrid AI decision logic
- real-time monitoring dashboards

---

# 🚀 Features

## ✅ Behavioral Event Processing
- Question timing analysis
- Tab switching detection
- Clipboard/paste detection
- Idle behavior monitoring

## ✅ Rule-Based Risk Engine (V1)
- Weighted signal scoring
- Confidence scoring
- Pattern-based escalation
- Human-readable explanations

## ✅ Hybrid AI Engine (V2)
- Random Forest ML classifier
- Hybrid Rule + ML decisions
- Runtime ML inference
- Risk calibration

## ✅ Real-Time Monitoring Dashboard
- Live attempt monitoring
- Risk distribution charts
- Signal breakdowns
- Attempt drilldown
- Auto-refresh dashboard

---

# 🏗️ System Architecture

```text
User Events
    ↓
FastAPI API
    ↓
Event Processing
    ↓
Feature Engineering
    ↓
Rule Engine + ML Engine
    ↓
Hybrid Decision Logic
    ↓
Logging + Dashboard
```

---

# 📂 Project Structure

```text
app/
engine/
  core/
  ml/
scripts/
data/
models/
logs/
dashboard.py
README.md
requirements.txt
```

---

# ⚙️ Installation

## 1️⃣ Create Virtual Environment

```bash
python -m venv venv
```

## 2️⃣ Activate Environment

### Windows
```bash
venv\Scripts\activate
```

### Linux/Mac
```bash
source venv/bin/activate
```

## 3️⃣ Install Dependencies

```bash
pip install -r requirements.txt
```

---

# ▶️ Running The System

## Start FastAPI Backend

```bash
uvicorn app.main:app --reload
```

## Phase 1 — Authentication + RBAC (New)

The reviewer console APIs are now protected with JWT auth:
- Protected (requires login):
    - `GET /v1/logs`
    - `GET /v1/live-risk/{attempt_id}`
    - `GET /v1/events/{attempt_id}`
    - `GET /v1/risk-history/{attempt_id}`
- Not protected (intentionally):
    - `POST /v1/events/ingest` (SDK ingestion must stay anonymous)
    - `GET /`
    - `WS /ws/risk` (kept unauthenticated for now to avoid breaking live dashboards)

### Required environment variables

Set these before running the backend:

- `JWT_SECRET_KEY` (required)
- `JWT_ALGORITHM` (default: `HS256`)
- `ACCESS_TOKEN_EXPIRE_MINUTES` (default: `480`)

Tip: see `.env.example` for a complete list.

Admin bootstrap (for local development):

- `DEFAULT_ADMIN_EMAIL`
- `DEFAULT_ADMIN_PASSWORD`
- `DEFAULT_ADMIN_NAME`

Viewer bootstrap (for local development):

- `DEFAULT_VIEWER_EMAIL`
- `DEFAULT_VIEWER_PASSWORD`
- `DEFAULT_VIEWER_NAME`

### Seed a default admin user

This script is idempotent (won't create duplicates):

```bash
python scripts/seed_admin.py
```

### Seed a default viewer user

This script is idempotent (won't create duplicates):

```bash
python scripts/seed_viewer.py
```

### Login endpoints

- `POST /v1/auth/login` (JSON `{ "email": "...", "password": "..." }`)
- `GET /v1/auth/me` (requires `Authorization: Bearer <token>`)

API docs:
```text
http://127.0.0.1:8000/docs
```

---

## Start React Frontend (Reviewer Console)

```bash
cd frontend
npm install
npm run dev
```

Open the console:

```text
http://127.0.0.1:5173
```

You will be redirected to a login screen. Use the seeded admin credentials.

---

# ✅ Manual Verification Checklist (Auth + RBAC)

1. Install deps: `pip install -r requirements.txt`
2. Set env vars (at minimum `JWT_SECRET_KEY`, plus seed defaults)
3. Run: `python scripts/seed_admin.py`
4. Run: `python scripts/seed_viewer.py`
5. Start API: `uvicorn app.main:app --reload`
6. Confirm login works:
    - `POST /v1/auth/login` returns `access_token`
    - `GET /v1/auth/me` works with `Authorization: Bearer <token>`
7. Confirm protected routes reject missing token:
    - `GET /v1/logs` should return `401`
8. Confirm reviewer console routes accept admin token:
    - `GET /v1/logs` should return `200`
    - `GET /v1/live-risk/{attempt_id}` should return `200`
    - `GET /v1/events/{attempt_id}` should return `200`
    - `GET /v1/risk-history/{attempt_id}` should return `200`
9. Confirm VIEWER role is blocked from reviewer console routes:
    - Login as viewer and call `GET /v1/logs` → `403`
10. Confirm SDK ingest still works without login (must stay anonymous):
    - `POST /v1/events/ingest` should remain `200` with SDK telemetry

---

## Generate Behavioral Events

```bash
python scripts/generate_events.py
```

---

## Build Dataset

```bash
python scripts/build_dataset.py
```

---

## Train ML Model

```bash
python scripts/train_model.py
```

---

## Launch Dashboard

```bash
streamlit run dashboard.py
```

---

# 🧠 Machine Learning

## Current Model
- Random Forest Classifier

## Behavioral Features
- paste_count
- tab_hidden_count
- timing behavior
- density metrics
- suspicious combinations
- fast paste patterns
- fast tab-switch behavior

## Output Classes
| Risk | Meaning |
|---|---|
| LOW | Normal behavior |
| MEDIUM | Suspicious behavior requiring review |
| HIGH | Strong indicators of cheating |

---

# 📊 Dashboard Capabilities

- Live risk monitoring
- Attempt analytics
- Feature visualization
- Signal breakdown
- Risk distribution tracking
- Candidate drilldown

---

# ✅ Completed Phases

## Core Platform
- [x] Event ingestion
- [x] Feature engineering
- [x] Rule-based risk engine
- [x] Pattern detection
- [x] Confidence scoring

## Machine Learning
- [x] Dataset generation
- [x] ML training pipeline
- [x] Hybrid AI integration
- [x] Runtime ML inference

## Monitoring
- [x] Streamlit dashboard
- [x] Real-time monitoring
- [x] Live risk visualization

---

# 🔮 Future Roadmap

## Real-Time Intelligence
- WebSocket streaming
- Real-time scoring updates
- Live alert notifications

## Advanced ML
- Sequence models
- Anomaly detection
- Online learning
- SHAP explainability

## Production Infrastructure
- Docker deployment
- PostgreSQL integration
- Redis/Kafka streaming
- Authentication & RBAC
- Cloud deployment

---

# 📌 Current Status

## Technical Completion
~85% complete for prototype system

## Production Completion
~60% complete for enterprise deployment

---

# 👨‍💻 Tech Stack

- Python
- FastAPI
- Streamlit
- Scikit-learn
- Pandas
- Joblib

---

# 📜 License

MIT License