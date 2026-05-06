import requests
import random
from datetime import datetime, timedelta

API_URL = "http://127.0.0.1:8000/v1/score"


def now():
    return datetime.now()


def iso(t):
    return t.isoformat() + "Z"


def random_id():
    return f"attempt_{random.randint(1000,9999)}"


def build_question_events(start_time, qid, duration):
    events = [
        {
            "event_type": "question_view",
            "payload": {"question_id": qid, "action": "enter"},
            "occurred_at": iso(start_time),
        }
    ]

    end_time = start_time + timedelta(seconds=duration)

    events.append({
        "event_type": "question_view",
        "payload": {"question_id": qid, "action": "leave"},
        "occurred_at": iso(end_time),
    })

    return events, end_time


def add_tab_switch(events, t):
    events.append({
        "event_type": "visibility_change",
        "payload": {"state": "hidden"},
        "occurred_at": iso(t),
    })


def add_paste(events, t):
    events.append({
        "event_type": "clipboard",
        "payload": {"action": "paste"},
        "occurred_at": iso(t),
    })


def choose_profile():
    return random.choices(
        population=[
            "clean",
            "fast_clean",
            "tab_heavy",
            "paste_heavy",
            "mixed_medium",
            "suspicious_but_slow",
            "chaotic",
            "high_risk",
        ],
        weights=[
            0.18,  # clean
            0.10,  # fast but mostly clean
            0.12,  # tab-heavy only
            0.12,  # paste-heavy only
            0.18,  # medium
            0.10,  # suspicious but slow
            0.10,  # chaotic
            0.10,  # clear high risk
        ],
        k=1,
    )[0]


def generate_attempt():
    t = now()
    events = []
    questions = random.randint(4, 8)
    profile = choose_profile()

    for i in range(questions):
        if profile == "clean":
            duration = random.randint(30, 80)

        elif profile == "fast_clean":
            duration = random.randint(5, 15)

        elif profile == "suspicious_but_slow":
            duration = random.randint(40, 90)

        elif profile == "chaotic":
            duration = random.choice([
                random.randint(5, 15),
                random.randint(40, 90),
            ])

        elif profile == "high_risk":
            duration = random.randint(3, 8)

        else:
            duration = random.randint(10, 40)

        ev, t = build_question_events(t, f"q{i}", duration)
        events.extend(ev)

        if profile == "clean":
            if random.random() < 0.08:
                add_tab_switch(events, t)

        elif profile == "fast_clean":
            if random.random() < 0.08:
                add_paste(events, t)

        elif profile == "tab_heavy":
            if random.random() < 0.80:
                add_tab_switch(events, t)
            if random.random() < 0.10:
                add_paste(events, t)

        elif profile == "paste_heavy":
            if random.random() < 0.80:
                add_paste(events, t)
            if random.random() < 0.15:
                add_tab_switch(events, t)

        elif profile == "mixed_medium":
            if random.random() < 0.50:
                add_tab_switch(events, t)
            if random.random() < 0.40:
                add_paste(events, t)

        elif profile == "suspicious_but_slow":
            if random.random() < 0.60:
                add_tab_switch(events, t)
            if random.random() < 0.45:
                add_paste(events, t)

        elif profile == "chaotic":
            if random.random() < 0.65:
                add_tab_switch(events, t)
            if random.random() < 0.65:
                add_paste(events, t)

        elif profile == "high_risk":
            if random.random() < 0.95:
                add_tab_switch(events, t)
            if random.random() < 0.95:
                add_paste(events, t)

            if random.random() < 0.50:
                add_tab_switch(events, t)
            if random.random() < 0.50:
                add_paste(events, t)

        if profile != "high_risk":
            if random.random() < 0.10:
                add_tab_switch(events, t)

            if random.random() < 0.07:
                add_paste(events, t)

    return events


def send_attempt():
    attempt_id = random_id()
    events = generate_attempt()

    payload = {
        "attempt_id": attempt_id,
        "events": events,
    }

    try:
        res = requests.post(API_URL, json=payload)
        print(f"{attempt_id} → {res.status_code}")
    except Exception as e:
        print("Error:", e)


def run(n=500):
    for _ in range(n):
        send_attempt()


if __name__ == "__main__":
    run(500)