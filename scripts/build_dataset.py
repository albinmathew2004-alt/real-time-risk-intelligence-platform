import json
import pandas as pd
from pathlib import Path

LOG_FILE = Path("logs/attempt_logs.jsonl")
OUTPUT_FILE = Path("data/dataset.csv")


# =========================
# LOAD LOGS
# =========================

def load_logs():
    rows = []

    with open(LOG_FILE, "r") as f:
        for i, line in enumerate(f):
            try:
                obj = json.loads(line)

                if "features" not in obj or "risk" not in obj:
                    continue

                rows.append(obj)

            except Exception as e:
                print(f"⚠️ Skipping bad line {i}: {e}")

    print(f"✅ Loaded {len(rows)} valid logs")
    return rows


# =========================
# RISK MAPPING
# =========================

def map_risk(r):
    if r == "LOW":
        return 0
    elif r == "MEDIUM":
        return 1
    elif r == "HIGH":
        return 2
    return None


# =========================
# FEATURE EXTRACTION
# =========================

def extract_features(logs):
    dataset = []

    for row in logs:
        f = row["features"]

        questions = max(f.get("questions_seen", 1), 1)
        time_per_q = f.get("time_per_question_mean_s", 1) or 1

        paste = f.get("paste_count", 0)
        tab = f.get("tab_hidden_count", 0)
        idle = f.get("idle_spike_count", 0)
        duration = f.get("attempt_duration_s", 1)

        # =========================
        # BASE FEATURES
        # =========================

        paste_per_q = paste / questions
        tab_per_q = tab / questions
        speed = 1 / (time_per_q + 1e-5)

        tab_density = tab / (duration + 1e-5)
        paste_density = paste / (duration + 1e-5)
        idle_ratio = idle / questions

        # =========================
        # 🔥 INTERACTION FEATURES (NEW)
        # =========================

        suspicious_combo = paste_per_q * tab_per_q
        fast_paste = speed * paste_per_q
        fast_tab = speed * tab_per_q

        data = {
            # RAW
            "paste_count": paste,
            "tab_hidden_count": tab,
            "idle_spike_count": idle,
            "time_per_question": time_per_q,
            "questions_seen": questions,
            "attempt_duration": duration,

            # DERIVED
            "paste_per_question": paste_per_q,
            "tab_per_question": tab_per_q,
            "speed": speed,

            "tab_density": tab_density,
            "paste_density": paste_density,
            "idle_ratio": idle_ratio,

            # 🔥 INTERACTIONS
            "suspicious_combo": suspicious_combo,
            "fast_paste": fast_paste,
            "fast_tab": fast_tab,

            # TARGET
            "risk": map_risk(row["risk"])
        }

        if data["risk"] is not None:
            dataset.append(data)

    df = pd.DataFrame(dataset)

    print(f"✅ Extracted {len(df)} dataset rows")

    return df


# =========================
# MAIN
# =========================

def main():
    if not LOG_FILE.exists():
        print("❌ No logs found")
        return

    logs = load_logs()
    df = extract_features(logs)

    df.to_csv(OUTPUT_FILE, index=False)

    print("\n📊 Class distribution:")
    print(df["risk"].value_counts())

    print("\n📌 Sample:")
    print(df.head())


if __name__ == "__main__":
    main()