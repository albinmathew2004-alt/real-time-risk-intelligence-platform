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

API docs:
```text
http://127.0.0.1:8000/docs
```

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