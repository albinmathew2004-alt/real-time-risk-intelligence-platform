import streamlit as st
import pandas as pd
import json
import time
from pathlib import Path

LOG_FILE = Path("logs/attempt_logs.jsonl")

st.set_page_config(
    page_title="Risk Intelligence Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ======================
# STYLING
# ======================

st.markdown(
    """
    <style>
    .main {
        background-color: #0f172a;
    }

    .block-container {
        padding-top: 1.5rem;
    }

    div[data-testid="stMetric"] {
        background-color: #111827;
        border: 1px solid #1f2937;
        padding: 18px;
        border-radius: 18px;
        box-shadow: 0 8px 24px rgba(0,0,0,0.25);
    }

    div[data-testid="stMetricLabel"] {
        color: #cbd5e1;
    }

    div[data-testid="stMetricValue"] {
        color: #f8fafc;
        font-size: 2rem;
    }

    .risk-high {
        padding: 10px 14px;
        border-radius: 12px;
        background: rgba(239, 68, 68, 0.15);
        border: 1px solid rgba(239, 68, 68, 0.4);
        color: #fecaca;
        font-weight: 700;
    }

    .risk-medium {
        padding: 10px 14px;
        border-radius: 12px;
        background: rgba(245, 158, 11, 0.15);
        border: 1px solid rgba(245, 158, 11, 0.4);
        color: #fde68a;
        font-weight: 700;
    }

    .risk-low {
        padding: 10px 14px;
        border-radius: 12px;
        background: rgba(34, 197, 94, 0.15);
        border: 1px solid rgba(34, 197, 94, 0.4);
        color: #bbf7d0;
        font-weight: 700;
    }

    .section-card {
        background: #111827;
        padding: 18px;
        border-radius: 18px;
        border: 1px solid #1f2937;
        margin-bottom: 18px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ======================
# LOAD DATA
# ======================

@st.cache_data(ttl=2)
def load_logs():
    if not LOG_FILE.exists():
        return pd.DataFrame()

    rows = []

    with open(LOG_FILE, "r") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    if "confidence" in df.columns:
        df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce").fillna(0)

    if "confidence_score" in df.columns:
        df["confidence_score"] = pd.to_numeric(df["confidence_score"], errors="coerce").fillna(0)

    if "combined_score" in df.columns:
        df["combined_score"] = pd.to_numeric(df["combined_score"], errors="coerce").fillna(0)

    return df


def extract_feature(row, name, default=0):
    features = row.get("features", {}) or {}
    try:
        return features.get(name, default)
    except Exception:
        return default


def risk_badge(risk):
    risk = str(risk)

    if risk == "HIGH":
        return '<div class="risk-high">🔴 HIGH RISK</div>'
    if risk == "MEDIUM":
        return '<div class="risk-medium">🟠 MEDIUM RISK</div>'
    return '<div class="risk-low">🟢 LOW RISK</div>'


df = load_logs()


# ======================
# SIDEBAR
# ======================

st.sidebar.title("🧠 Risk Intel")
st.sidebar.caption("Real-time behavioral monitoring")

st.sidebar.divider()

auto_refresh = st.sidebar.checkbox("Live auto-refresh", value=True)

refresh_rate = st.sidebar.slider(
    "Refresh interval",
    min_value=2,
    max_value=30,
    value=5,
    step=1,
)

st.sidebar.divider()

if not df.empty and "risk" in df.columns:
    risk_options = sorted(df["risk"].dropna().unique().tolist())
    selected_risks = st.sidebar.multiselect(
        "Risk levels",
        options=risk_options,
        default=risk_options,
    )
else:
    selected_risks = []

attempt_search = st.sidebar.text_input("Search attempt ID")

st.sidebar.divider()
st.sidebar.caption("Tip: keep FastAPI and generator running for live data.")


# ======================
# HEADER
# ======================

left, right = st.columns([0.75, 0.25])

with left:
    st.title("Real-Time Risk Intelligence Dashboard")
    st.caption("Hybrid Rule + ML Monitoring • Behavioral Signals • Live Attempt Review")

with right:
    current_time = time.strftime("%H:%M:%S")
    st.markdown("### 🟢 Live")
    st.caption(f"Last refresh: {current_time}")


# ======================
# EMPTY STATE
# ======================

if df.empty:
    st.warning("No logs found yet. Run the API and generate attempts first.")

    if auto_refresh:
        time.sleep(refresh_rate)
        st.rerun()

    st.stop()


# ======================
# FILTERING
# ======================

filtered_df = df.copy()

if selected_risks:
    filtered_df = filtered_df[filtered_df["risk"].isin(selected_risks)]

if attempt_search:
    filtered_df = filtered_df[
        filtered_df["attempt_id"].astype(str).str.contains(attempt_search, case=False, na=False)
    ]

if "timestamp" in filtered_df.columns:
    filtered_df = filtered_df.sort_values("timestamp", ascending=False)


# ======================
# METRICS
# ======================

total_attempts = len(filtered_df)
high_count = int((filtered_df["risk"] == "HIGH").sum())
medium_count = int((filtered_df["risk"] == "MEDIUM").sum())
low_count = int((filtered_df["risk"] == "LOW").sum())

avg_conf = float(filtered_df["confidence"].mean()) if "confidence" in filtered_df.columns and not filtered_df.empty else 0.0
avg_score = float(filtered_df["combined_score"].mean()) if "combined_score" in filtered_df.columns and not filtered_df.empty else 0.0

c1, c2, c3, c4, c5 = st.columns(5)

c1.metric("Total Attempts", total_attempts)
c2.metric("High Risk", high_count)
c3.metric("Medium Risk", medium_count)
c4.metric("Low Risk", low_count)
c5.metric("Avg Score", f"{avg_score:.2f}")


# ======================
# TOP ALERTS
# ======================

st.subheader("🚨 Live Risk Alerts")

alerts = filtered_df[filtered_df["risk"].isin(["HIGH", "MEDIUM"])].head(8)

if alerts.empty:
    st.success("No active high or medium risk alerts.")
else:
    for _, row in alerts.iterrows():
        risk = row.get("risk", "UNKNOWN")
        attempt_id = row.get("attempt_id", "unknown")
        score = float(row.get("combined_score", 0) or 0)
        conf = float(row.get("confidence", 0) or 0)

        if risk == "HIGH":
            st.error(f"🔴 HIGH — {attempt_id} | score={score:.2f} | confidence={conf:.2f}")
        else:
            st.warning(f"🟠 MEDIUM — {attempt_id} | score={score:.2f} | confidence={conf:.2f}")


# ======================
# CHARTS
# ======================

chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.subheader("📊 Risk Distribution")
    risk_counts = filtered_df["risk"].value_counts()

    if not risk_counts.empty:
        st.bar_chart(risk_counts)

with chart_col2:
    st.subheader("📈 Score Trend")

    if "timestamp" in filtered_df.columns and "combined_score" in filtered_df.columns:
        trend_df = filtered_df.dropna(subset=["timestamp"]).sort_values("timestamp")

        if not trend_df.empty:
            st.line_chart(
                trend_df.set_index("timestamp")["combined_score"]
            )
        else:
            st.info("No timestamped score data available.")
    else:
        st.info("Score trend unavailable.")


# ======================
# FEATURE ANALYTICS
# ======================

st.subheader("🧬 Behavioral Feature Analytics")

feature_rows = []

for _, row in filtered_df.iterrows():
    feature_rows.append({
        "attempt_id": row.get("attempt_id"),
        "risk": row.get("risk"),
        "paste_count": extract_feature(row, "paste_count"),
        "tab_hidden_count": extract_feature(row, "tab_hidden_count"),
        "questions_seen": extract_feature(row, "questions_seen"),
        "time_per_question": extract_feature(row, "time_per_question_mean_s"),
        "attempt_duration": extract_feature(row, "attempt_duration_s"),
    })

feature_df = pd.DataFrame(feature_rows)

if not feature_df.empty:
    f1, f2, f3 = st.columns(3)

    with f1:
        st.caption("Average Paste Count by Risk")
        st.bar_chart(feature_df.groupby("risk")["paste_count"].mean())

    with f2:
        st.caption("Average Tab Switches by Risk")
        st.bar_chart(feature_df.groupby("risk")["tab_hidden_count"].mean())

    with f3:
        st.caption("Average Time per Question by Risk")
        st.bar_chart(feature_df.groupby("risk")["time_per_question"].mean())


# ======================
# ATTEMPT TABLE
# ======================

st.subheader("📋 Attempt Log Explorer")

table_cols = [
    c for c in [
        "timestamp",
        "attempt_id",
        "risk",
        "confidence",
        "confidence_score",
        "combined_score",
    ]
    if c in filtered_df.columns
]

st.dataframe(
    filtered_df[table_cols].head(200),
    use_container_width=True,
    hide_index=True,
)


# ======================
# DRILLDOWN
# ======================

st.subheader("🔍 Attempt Drilldown")

if filtered_df.empty:
    st.info("No attempts match the selected filters.")
else:
    selected_attempt = st.selectbox(
        "Select attempt",
        filtered_df["attempt_id"].astype(str).unique(),
    )

    attempt_rows = filtered_df[
        filtered_df["attempt_id"].astype(str) == selected_attempt
    ]

    if not attempt_rows.empty:
        attempt_data = attempt_rows.iloc[0]

        d1, d2 = st.columns([0.35, 0.65])

        with d1:
            st.markdown(risk_badge(attempt_data.get("risk")), unsafe_allow_html=True)

            st.metric("Confidence", f"{float(attempt_data.get('confidence', 0) or 0):.2f}")
            st.metric("Evidence Score", f"{float(attempt_data.get('confidence_score', 0) or 0):.2f}")
            st.metric("Combined Score", f"{float(attempt_data.get('combined_score', 0) or 0):.2f}")

        with d2:
            st.markdown("#### Explanation")
            st.write(attempt_data.get("explanation_text") or attempt_data.get("explanation") or "No explanation available.")

        st.markdown("#### Signal Breakdown")

        signals = attempt_data.get("signals", {}) or {}

        signal_df = pd.DataFrame([
            {
                "signal": name,
                "score": float((data or {}).get("score", 0) or 0),
            }
            for name, data in signals.items()
        ])

        if not signal_df.empty:
            st.bar_chart(signal_df.set_index("signal"))
        else:
            st.info("No signal data available.")

        st.markdown("#### Feature Snapshot")
        st.json(attempt_data.get("features", {}) or {})


# ======================
# AUTO REFRESH
# ======================

if auto_refresh:
    time.sleep(refresh_rate)
    st.rerun()