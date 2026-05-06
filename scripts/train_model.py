import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.preprocessing import StandardScaler
import joblib
import os

DATA_FILE = "data/dataset.csv"
MODEL_FILE = "models/risk_model.pkl"


def main():
    df = pd.read_csv(DATA_FILE)

    print("\n🔍 DEBUG CHECK")
    print("Shape:", df.shape)
    print("Columns:", df.columns.tolist())
    print("Unique risk:", df["risk"].unique())
    print("NaN in risk:", df["risk"].isna().sum())

    # =========================
    # CLEANING
    # =========================

    df = df.copy()

    df["risk"] = pd.to_numeric(df["risk"], errors="coerce")
    df = df.dropna(subset=["risk"])
    df["risk"] = df["risk"].astype(int)
    df = df.dropna()

    print("\n✅ AFTER CLEANING")
    print("Shape:", df.shape)
    print("Unique risk:", df["risk"].unique())

    # =========================
    # FEATURE SELECTION (🔥 REMOVE LEAKAGE)
    # =========================

    DROP_COLS = [
        # Remove overly derived / rule-based features
        "timing_score",
        "tab_score",
        "clipboard_score"
    ]

    for col in DROP_COLS:
        if col in df.columns:
            df = df.drop(columns=[col])

    X = df.drop(columns=["risk"])
    y = df["risk"]

    print("\n🎯 Training with:", len(X), "samples")

    # =========================
    # SPLIT
    # =========================

    print("\n✅ Using stratified split")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y,
        test_size=0.2,
        random_state=42,
        stratify=y
    )

    # =========================
    # NORMALIZATION (🔥 IMPORTANT)
    # =========================

    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train)
    X_test = scaler.transform(X_test)

    # =========================
    # MODEL (LESS OVERFITTING)
    # =========================

    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=4,   # 🔥 reduced from 6
        min_samples_split=10,
        random_state=42
    )

    model.fit(X_train, y_train)

    # =========================
    # EVALUATION
    # =========================

    y_pred = model.predict(X_test)

    print("\n📊 MODEL PERFORMANCE:\n")
    print(classification_report(y_test, y_pred))

    # =========================
    # FEATURE IMPORTANCE (🔥 DEBUG)
    # =========================

    print("\n📊 Feature Importance:")
    for name, score in zip(df.drop(columns=["risk"]).columns, model.feature_importances_):
        print(f"{name}: {round(score, 3)}")

    # =========================
    # SAVE
    # =========================

    os.makedirs("models", exist_ok=True)
    joblib.dump((model, scaler), MODEL_FILE)

    print("\n💾 Model + scaler saved to:", MODEL_FILE)


if __name__ == "__main__":
    main()