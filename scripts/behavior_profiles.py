"""Realistic behavior profiles for assessment simulation.

These profiles generate human-like event sequences using the *existing* event schema:
- event_type: str
- payload: dict
- occurred_at: ISO timestamp (handled by scripts/simulate_live_exam.py)

Design goals:
- Realistic timing variance (no fixed durations)
- Human-like navigation (re-reading, revisiting, jumping)
- Occasional benign tab switching + small legitimate paste
- Borderline patterns that are ambiguous (important for MEDIUM evaluation)
- High-risk patterns that still trigger deterministic HIGH (tab + paste + fast timing)

NOTE: The scoring engine currently uses these signals heavily:
- clipboard paste events
- visibility_change hidden events
- idle_state idle/active pairs
- question_view enter/leave timing

So each profile emits those in a realistic way.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Any, Dict, Iterable, List, Optional, Sequence


@dataclass(frozen=True)
class BehaviorEvent:
    """A lightweight event spec that later becomes a full API event."""

    offset_s: float
    event_type: str
    payload: Dict[str, Any]


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


def _jitter(seconds: float, *, rel_std: float = 0.12, abs_std: float = 0.35) -> float:
    """Apply small human-like noise to a duration."""

    base = float(seconds)
    std = abs_std + rel_std * max(1.0, base)
    noisy = random.gauss(base, std)
    return max(0.05, noisy)


def _lognormal_seconds(*, median_s: float, sigma: float, min_s: float, max_s: float) -> float:
    """Human timing often looks lognormal (positive + skewed)."""

    mu = math.log(max(0.01, float(median_s)))
    x = random.lognormvariate(mu, float(sigma))
    return _clamp(x, float(min_s), float(max_s))


def _pick_question_ids(n: int) -> List[str]:
    return [f"q{i+1}" for i in range(int(n))]


def _maybe_add(
    out: List[BehaviorEvent],
    *,
    p: float,
    offset_s: float,
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    if random.random() < float(p):
        out.append(BehaviorEvent(offset_s=offset_s, event_type=event_type, payload=payload))


def _tab_switch_pair(
    out: List[BehaviorEvent],
    *,
    offset_s: float,
    away_s: float,
    reason: str,
) -> float:
    """Emit hidden + visible events and return the new offset."""

    out.append(
        BehaviorEvent(
            offset_s=offset_s,
            event_type="visibility_change",
            payload={"state": "hidden", "reason": reason},
        )
    )
    out.append(
        BehaviorEvent(
            offset_s=offset_s + max(0.2, away_s),
            event_type="visibility_change",
            payload={"state": "visible", "reason": reason},
        )
    )
    return offset_s + max(0.2, away_s)


def _idle_pair(out: List[BehaviorEvent], *, offset_s: float, idle_s: float, reason: str) -> float:
    """Emit idle + active pair and return the new offset."""

    out.append(
        BehaviorEvent(
            offset_s=offset_s,
            event_type="idle_state",
            payload={"state": "idle", "duration_seconds": float(idle_s), "reason": reason},
        )
    )
    out.append(
        BehaviorEvent(
            offset_s=offset_s + max(0.2, idle_s),
            event_type="idle_state",
            payload={"state": "active", "reason": reason},
        )
    )
    return offset_s + max(0.2, idle_s)


def _paste_event(out: List[BehaviorEvent], *, offset_s: float, size: str, source: str, question_id: Optional[str]) -> None:
    payload: Dict[str, Any] = {
        "action": "paste",
        "size": size,  # e.g., tiny/small/medium/large
        "source": source,  # e.g., notes/template/search
    }
    if question_id:
        payload["question_id"] = question_id

    out.append(BehaviorEvent(offset_s=offset_s, event_type="clipboard", payload=payload))


def _metadata_event(out: List[BehaviorEvent], *, offset_s: float, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    out.append(BehaviorEvent(offset_s=offset_s, event_type=event_type, payload=payload or {}))


def _typing_activity(out: List[BehaviorEvent], *, offset_s: float, dwell_s: float, qid: str) -> None:
    if dwell_s < 4:
        return

    session_start = offset_s + min(0.8, max(0.2, dwell_s * 0.08))
    session_end = max(session_start + 0.6, offset_s + dwell_s - 0.6)
    if session_end <= session_start:
        return

    out.append(
        BehaviorEvent(
            offset_s=session_start,
            event_type="typing_started",
            payload={"question_id": qid, "input_context": "answer_box"},
        )
    )

    cursor = session_start + min(1.0, max(0.35, dwell_s * 0.05))
    pause_count = 0
    burst_count = 0

    while cursor < session_end - 0.45:
        burst_length = random.randint(4, 16)
        interval_ms = random.choice([70, 85, 95, 110, 140, 180, 220])
        duration_ms = int(max(500, min(2400, burst_length * interval_ms * random.uniform(0.85, 1.25))))
        out.append(
            BehaviorEvent(
                offset_s=cursor,
                event_type="typing_burst",
                payload={
                    "question_id": qid,
                    "burst_length": burst_length,
                    "duration_ms": duration_ms,
                    "interval_ms": interval_ms,
                },
            )
        )
        burst_count += 1

        if random.random() < 0.35:
            backspace_count = random.randint(1, 4)
            out.append(
                BehaviorEvent(
                    offset_s=min(session_end - 0.15, cursor + random.uniform(0.12, 0.35)),
                    event_type="backspace_activity",
                    payload={"question_id": qid, "count": backspace_count, "burst_window_ms": random.randint(250, 900)},
                )
            )

        if random.random() < 0.45 and cursor + 1.2 < session_end - 0.2:
            pause_s = random.uniform(1.2, min(8.5, max(1.4, dwell_s * 0.18)))
            out.append(
                BehaviorEvent(
                    offset_s=min(session_end - 0.2, cursor + random.uniform(0.25, 0.75)),
                    event_type="typing_pause",
                    payload={"question_id": qid, "pause_duration_s": round(pause_s, 2)},
                )
            )
            pause_count += 1
            cursor += pause_s

        cursor += random.uniform(1.2, 4.4)

    out.append(
        BehaviorEvent(
            offset_s=session_end,
            event_type="typing_stopped",
            payload={
                "question_id": qid,
                "session_duration_ms": int(max(400, (session_end - session_start) * 1000)),
                "total_bursts": burst_count,
                "pause_count": pause_count,
            },
        )
    )


def _question_view_pair(out: List[BehaviorEvent], *, offset_s: float, qid: str, dwell_s: float) -> float:
    out.append(BehaviorEvent(offset_s=offset_s, event_type="question_view", payload={"question_id": qid, "action": "enter"}))
    _typing_activity(out, offset_s=offset_s, dwell_s=dwell_s, qid=qid)
    out.append(BehaviorEvent(offset_s=offset_s + max(0.3, dwell_s), event_type="question_view", payload={"question_id": qid, "action": "leave"}))
    return offset_s + max(0.3, dwell_s)


def _answer_event(out: List[BehaviorEvent], *, offset_s: float, qid: str, style: str) -> None:
    out.append(
        BehaviorEvent(
            offset_s=offset_s,
            event_type="question_answer",
            payload={"question_id": qid, "answer": style},
        )
    )


class CandidateProfile:
    """Base class for realistic behavior profiles."""

    name: str = "BaseProfile"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        raise NotImplementedError


class NormalThoughtfulCandidate(CandidateProfile):
    """Mostly normal behavior with occasional benign noise (tab/paste/pauses).

    Realism:
    - Longer reading time
    - Occasional re-read/revisit
    - Sometimes one accidental tab switch
    - Sometimes a tiny paste from allowed notes/template
    """

    name = "NormalThoughtfulCandidate"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        out: List[BehaviorEvent] = []
        t = 0.0

        accidental_tab_budget = 1 if random.random() < 0.55 else 0
        tiny_paste_budget = 1 if random.random() < 0.35 else 0

        # Lightly shuffled order (humans sometimes jump once)
        q_order = list(question_ids)
        if len(q_order) >= 5 and random.random() < 0.25:
            i = random.randint(1, len(q_order) - 2)
            q_order[i], q_order[i + 1] = q_order[i + 1], q_order[i]

        for idx, qid in enumerate(q_order):
            dwell = _lognormal_seconds(median_s=55, sigma=0.35, min_s=18, max_s=140)
            dwell = _jitter(dwell)

            t = _question_view_pair(out, offset_s=t, qid=qid, dwell_s=dwell)

            # Answer near the end (not perfectly aligned)
            _answer_event(out, offset_s=t - _jitter(random.uniform(1.2, 6.0)), qid=qid, style="selected_option")

            # Occasional hesitation pause between questions
            if random.random() < 0.22:
                pause = _lognormal_seconds(median_s=12, sigma=0.55, min_s=3, max_s=60)
                t += _jitter(pause)

            # Accidental tab switch once
            if accidental_tab_budget > 0 and random.random() < 0.25:
                away = _lognormal_seconds(median_s=8, sigma=0.55, min_s=2, max_s=35)
                t = _tab_switch_pair(out, offset_s=t + 0.8, away_s=away, reason="accidental")
                accidental_tab_budget -= 1

            # Tiny benign paste once
            if tiny_paste_budget > 0 and random.random() < 0.20 and idx >= 1:
                _paste_event(out, offset_s=t + 0.6, size="tiny", source="notes", question_id=qid)
                tiny_paste_budget -= 1

            # Sometimes re-read the current or previous question briefly
            if random.random() < 0.15:
                reread = _lognormal_seconds(median_s=9, sigma=0.35, min_s=3, max_s=30)
                t = _question_view_pair(out, offset_s=t + 0.5, qid=qid, dwell_s=reread)

            if idx >= 2 and random.random() < 0.12:
                prev = q_order[idx - 1]
                revisit = _lognormal_seconds(median_s=8, sigma=0.35, min_s=3, max_s=25)
                t = _question_view_pair(out, offset_s=t + 0.6, qid=prev, dwell_s=revisit)

            t += _jitter(random.uniform(1.0, 4.0))

        return out


class FastButLegitCandidate(CandidateProfile):
    """Fast answers with low suspicious activity.

    Realism:
    - Fast timing (but not extreme)
    - A small legitimate paste can happen once
    - Rare tab switch
    """

    name = "FastButLegitCandidate"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        out: List[BehaviorEvent] = []
        t = 0.0

        paste_once = (random.random() < 0.40)

        for qid in question_ids:
            dwell = _lognormal_seconds(median_s=16, sigma=0.30, min_s=7, max_s=40)
            t = _question_view_pair(out, offset_s=t, qid=qid, dwell_s=_jitter(dwell))

            _answer_event(out, offset_s=t - _jitter(random.uniform(0.8, 3.0)), qid=qid, style="selected_option_fast")

            # Rare quick look away (e.g., checking system clock)
            if random.random() < 0.10:
                t = _tab_switch_pair(out, offset_s=t + 0.5, away_s=_jitter(random.uniform(2, 9)), reason="quick_check")

            # One small paste (e.g., helper snippet)
            if paste_once and random.random() < 0.18:
                _paste_event(out, offset_s=t + 0.4, size="small", source="notes", question_id=qid)
                paste_once = False

            t += _jitter(random.uniform(0.8, 3.2))

        return out


class BorderlineCandidate(CandidateProfile):
    """Ambiguous behavior: sometimes suspicious, sometimes normal.

    This is intentionally designed to create challenging LOW/MEDIUM cases:
    - Some fast answers mixed with normal timing
    - A couple of tab switches
    - Occasional paste
    - Re-reading and revisiting patterns
    """

    name = "BorderlineCandidate"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        out: List[BehaviorEvent] = []
        t = 0.0

        # Keep this mostly below deterministic-HIGH thresholds:
        # - tab_hidden_count should usually be <= 2
        # - paste_count should usually be <= 1
        tab_budget = random.randint(0, 2)
        paste_budget = random.randint(0, 1)

        q_order = list(question_ids)
        random.shuffle(q_order)

        for idx, qid in enumerate(q_order):
            is_fast = random.random() < 0.35
            if is_fast:
                # "fast" but not extreme: avoid avg_time < 8s too often.
                dwell = _lognormal_seconds(median_s=16, sigma=0.45, min_s=8, max_s=40)
            else:
                dwell = _lognormal_seconds(median_s=32, sigma=0.45, min_s=12, max_s=95)

            t = _question_view_pair(out, offset_s=t, qid=qid, dwell_s=_jitter(dwell))
            _answer_event(out, offset_s=t - _jitter(random.uniform(0.8, 5.0)), qid=qid, style="uncertain_answer")

            # Some ambiguity: tab switch followed by a pause, not always paste.
            if tab_budget > 0 and random.random() < 0.28:
                away = _lognormal_seconds(median_s=9, sigma=0.60, min_s=2, max_s=45)
                t = _tab_switch_pair(out, offset_s=t + 0.6, away_s=away, reason="maybe_lookup")
                tab_budget -= 1

                if random.random() < 0.30:
                    # brief hesitation after returning
                    t += _jitter(random.uniform(2, 14))

            if paste_budget > 0 and random.random() < 0.16:
                _paste_event(out, offset_s=t + 0.5, size=random.choice(["tiny", "small"]), source="notes", question_id=qid)
                paste_budget -= 1

            # Human navigation: revisit earlier items
            if idx >= 2 and random.random() < 0.20:
                back = q_order[random.randint(0, idx - 1)]
                t = _question_view_pair(out, offset_s=t + 0.8, qid=back, dwell_s=_jitter(random.uniform(4, 18)))

            # Occasional real idle spike
            if random.random() < 0.12:
                t = _idle_pair(out, offset_s=t + 0.5, idle_s=_jitter(random.uniform(18, 65)), reason="thinking")

            t += _jitter(random.uniform(1.0, 4.5))

        return out


class DistractedButLegitCandidate(CandidateProfile):
    """Legit candidate with distractions: long idle, revisits, occasional tab switch.

    This profile helps stress-test false positives:
    - Long pauses (phone call, interruption)
    - A couple of accidental tab switches
    - Slow-ish timing
    - A rare tiny paste
    """

    name = "DistractedButLegitCandidate"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        out: List[BehaviorEvent] = []
        t = 0.0

        tab_budget = random.randint(1, 2)
        paste_budget = 1 if random.random() < 0.30 else 0

        q_order = list(question_ids)
        if random.random() < 0.35:
            random.shuffle(q_order)

        for idx, qid in enumerate(q_order):
            dwell = _lognormal_seconds(median_s=60, sigma=0.55, min_s=20, max_s=180)
            t = _question_view_pair(out, offset_s=t, qid=qid, dwell_s=_jitter(dwell))

            _answer_event(out, offset_s=t - _jitter(random.uniform(1.0, 8.0)), qid=qid, style="selected_option")

            # Distraction idle spike
            if random.random() < 0.28:
                t = _idle_pair(out, offset_s=t + 0.8, idle_s=_lognormal_seconds(median_s=45, sigma=0.60, min_s=12, max_s=220), reason="interruption")

            if tab_budget > 0 and random.random() < 0.30:
                t = _tab_switch_pair(out, offset_s=t + 0.6, away_s=_jitter(random.uniform(3, 20)), reason="accidental")
                tab_budget -= 1

            if paste_budget > 0 and random.random() < 0.18 and idx >= 2:
                _paste_event(out, offset_s=t + 0.4, size="tiny", source="notes", question_id=qid)
                paste_budget -= 1

            # Re-read current question after distraction
            if random.random() < 0.20:
                t = _question_view_pair(out, offset_s=t + 0.6, qid=qid, dwell_s=_jitter(random.uniform(4, 22)))

            t += _jitter(random.uniform(1.2, 6.0))

        return out


class TemplateCopier(CandidateProfile):
    """Candidate who relies on templates/snippets.

    Patterns:
    - More paste events (but not always tab-heavy)
    - Pasting tends to happen after some thinking/idle
    - Timing is moderate (not extreme like aggressive cheating)
    """

    name = "TemplateCopier"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        out: List[BehaviorEvent] = []
        t = 0.0

        # Keep template use realistic but usually not "strong" paste_count.
        # In the current engine, paste_count >= 2 becomes a strong clipboard signal,
        # which often escalates MEDIUM -> deterministic HIGH. For a borderline MEDIUM
        # profile, we keep paste counts mostly at 0-1.
        paste_budget = 1 if random.random() < 0.75 else 2

        for idx, qid in enumerate(question_ids):
            dwell = _lognormal_seconds(median_s=28, sigma=0.50, min_s=10, max_s=90)
            t = _question_view_pair(out, offset_s=t, qid=qid, dwell_s=_jitter(dwell))

            # Some idle before pasting a template
            if random.random() < 0.35:
                t = _idle_pair(out, offset_s=t - _jitter(random.uniform(2, 6)), idle_s=_jitter(random.uniform(8, 35)), reason="recalling_template")

            if paste_budget > 0 and random.random() < 0.40:
                size = random.choice(["tiny", "small", "medium"])
                _paste_event(out, offset_s=t - _jitter(random.uniform(0.6, 2.2)), size=size, source="template", question_id=qid)
                paste_budget -= 1

            _answer_event(out, offset_s=t - _jitter(random.uniform(0.8, 3.5)), qid=qid, style="submitted_after_template")

            # Light tab switches (opening template docs)
            if random.random() < 0.12:
                t = _tab_switch_pair(out, offset_s=t + 0.6, away_s=_jitter(random.uniform(3, 16)), reason="template_reference")

            # Revisit earlier question occasionally
            if idx >= 2 and random.random() < 0.15:
                back = question_ids[idx - 1]
                t = _question_view_pair(out, offset_s=t + 0.8, qid=back, dwell_s=_jitter(random.uniform(3, 16)))

            t += _jitter(random.uniform(1.0, 4.0))

        return out


class SearcherPatternCandidate(CandidateProfile):
    """Candidate showing a search-like pattern (tab away per question).

    Patterns:
    - Tab away shortly after seeing a question
    - Return and answer relatively quickly
    - Occasional paste from search results
    """

    name = "SearcherPatternCandidate"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        out: List[BehaviorEvent] = []
        t = 0.0

        # Searching is common, but doing it *every question* usually becomes deterministic HIGH.
        # Keep tab-away events to a small budget so the profile stays MEDIUM-ish.
        # Keep tab-away events below the strong threshold (tab_hidden_count >= 3).
        tab_budget = random.randint(0, 2)
        paste_budget = random.randint(0, 1)

        for qid in question_ids:
            dwell = _lognormal_seconds(median_s=14, sigma=0.50, min_s=5, max_s=55)

            # Enter
            out.append(BehaviorEvent(offset_s=t, event_type="question_view", payload={"question_id": qid, "action": "enter"}))

            # Tab away only sometimes (docs/search), otherwise stay in-app.
            if tab_budget > 0 and random.random() < 0.45:
                t = _tab_switch_pair(
                    out,
                    offset_s=t + _jitter(random.uniform(1.0, 3.0)),
                    away_s=_jitter(random.uniform(6, 22)),
                    reason="search",
                )
                tab_budget -= 1

            # Back and finish reading
            t = t + _jitter(max(0.5, dwell - 3.0))

            # Paste sometimes after returning
            if paste_budget > 0 and random.random() < 0.28:
                _paste_event(out, offset_s=t - _jitter(random.uniform(0.6, 2.0)), size=random.choice(["small", "medium"]), source="search", question_id=qid)
                paste_budget -= 1

            _answer_event(out, offset_s=t - _jitter(random.uniform(0.8, 3.2)), qid=qid, style="answered_after_search")

            # Leave
            out.append(BehaviorEvent(offset_s=t, event_type="question_view", payload={"question_id": qid, "action": "leave"}))

            t += _jitter(random.uniform(1.0, 4.0))

        return out


class AggressiveCheater(CandidateProfile):
    """High-risk behavior intended to trigger deterministic HIGH.

    Patterns:
    - Repeated tab switches
    - Repeated paste
    - Idle → burst answering
    - Suspiciously fast question dwell time
    """

    name = "AggressiveCheater"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        out: List[BehaviorEvent] = []
        t = 0.0

        # Initial idle period (setting up, opening materials)
        if random.random() < 0.75:
            t = _idle_pair(out, offset_s=t + 1.5, idle_s=_lognormal_seconds(median_s=40, sigma=0.50, min_s=18, max_s=120), reason="setup")

        paste_budget = random.randint(6, 14)
        extra_tabs = random.randint(6, 14)

        for qid in question_ids:
            dwell = _lognormal_seconds(median_s=6, sigma=0.35, min_s=2.5, max_s=14)

            # Enter question
            out.append(BehaviorEvent(offset_s=t, event_type="question_view", payload={"question_id": qid, "action": "enter"}))

            # Rapid suspicious sequence: tab away, paste, answer
            if extra_tabs > 0:
                t = _tab_switch_pair(out, offset_s=t + _jitter(random.uniform(0.4, 1.3)), away_s=_jitter(random.uniform(4, 18)), reason="cheat")
                extra_tabs -= 1

            if paste_budget > 0:
                _paste_event(out, offset_s=t + _jitter(random.uniform(0.2, 1.2)), size=random.choice(["medium", "large"]), source="external", question_id=qid)
                paste_budget -= 1

            # Very fast completion
            t = t + _jitter(dwell)
            _answer_event(out, offset_s=t - _jitter(random.uniform(0.2, 1.2)), qid=qid, style="suspicious_fast_answer")

            # Leave
            out.append(BehaviorEvent(offset_s=t, event_type="question_view", payload={"question_id": qid, "action": "leave"}))

            # Idle->burst between some questions
            if random.random() < 0.30:
                t = _idle_pair(out, offset_s=t + 0.6, idle_s=_jitter(random.uniform(20, 80)), reason="waiting")

            # Additional repeated suspicious sequences
            if extra_tabs > 0 and random.random() < 0.60:
                t = _tab_switch_pair(out, offset_s=t + 0.5, away_s=_jitter(random.uniform(3, 12)), reason="cheat")
                extra_tabs -= 1

            if paste_budget > 0 and random.random() < 0.65:
                _paste_event(out, offset_s=t + 0.4, size=random.choice(["small", "medium"]), source="external", question_id=qid)
                paste_budget -= 1

            t += _jitter(random.uniform(0.8, 3.0))

        return out


class HighRiskLongSessionCandidate(CandidateProfile):
    """Long-running, explainable HIGH-risk candidate for demos.

    Story:
    - starts with mostly normal pacing
    - later shows repeated focus loss / clipboard / answer-change sequences
    - includes typing anomalies after idle periods
    - uses repeated paste-without-typing patterns
    - runs long enough for session intelligence momentum and correlation amplification
    """

    name = "HighRiskLongSessionCandidate"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        out: List[BehaviorEvent] = []
        t = 0.0
        qids = list(question_ids) if question_ids else _pick_question_ids(8)

        # Warm-up: plausible early-session behavior before the suspicious patterns begin.
        warmup_count = min(2, len(qids))
        for qid in qids[:warmup_count]:
            _metadata_event(out, offset_s=t + 0.4, event_type="normal_mouse_activity", payload={"question_id": qid, "activity": "pointer_move"})
            _metadata_event(out, offset_s=t + 1.0, event_type="question_viewed", payload={"question_id": qid})
            dwell = _lognormal_seconds(median_s=24, sigma=0.30, min_s=14, max_s=42)
            t = _question_view_pair(out, offset_s=t, qid=qid, dwell_s=_jitter(dwell))
            _metadata_event(out, offset_s=t - 6.0, event_type="normal_typing", payload={"question_id": qid, "duration_s": round(random.uniform(5.0, 12.0), 2)})
            _answer_event(out, offset_s=t - _jitter(random.uniform(2.4, 7.2)), qid=qid, style="considered_answer")
            _metadata_event(out, offset_s=t - 1.2, event_type="answer_changed", payload={"question_id": qid, "mode": "normal"})
            t += _jitter(random.uniform(16.0, 26.0))

        # Suspicious second phase: repeated correlated patterns across a long session.
        suspicious_passes = 3
        for pass_index in range(suspicious_passes):
            pass_qids = qids[warmup_count:] if pass_index == 0 else qids[max(1, warmup_count - 1):]
            for idx, qid in enumerate(pass_qids):
                question_start = t
                _metadata_event(out, offset_s=t + 0.3, event_type="normal_mouse_activity", payload={"question_id": qid, "activity": "cursor_pause"})
                _metadata_event(out, offset_s=t + 0.8, event_type="question_viewed", payload={"question_id": qid})
                out.append(BehaviorEvent(offset_s=t, event_type="question_view", payload={"question_id": qid, "action": "enter"}))

                # A very short in-question read before the suspicious sequence begins.
                t += _jitter(random.uniform(0.4, 1.1))

                # Repeated focus-loss cluster helps the correlation engine and storyline.
                blur_offset = t + _jitter(random.uniform(0.2, 0.8))
                _metadata_event(out, offset_s=blur_offset, event_type="blur", payload={"question_id": qid, "reason": "window_switch"})
                t = _tab_switch_pair(
                    out,
                    offset_s=blur_offset + 0.05,
                    away_s=_jitter(random.uniform(0.8, 2.2)),
                    reason="external_lookup",
                )
                _metadata_event(out, offset_s=t + 0.02, event_type="focus", payload={"question_id": qid, "reason": "window_return"})

                # Clipboard metadata plus existing engine-driving clipboard event.
                _metadata_event(out, offset_s=t + 0.08, event_type="clipboard_copy", payload={"question_id": qid, "size": "small", "source": "external"})
                _metadata_event(out, offset_s=t + 0.18, event_type="clipboard_paste", payload={"question_id": qid, "size": random.choice(["medium", "large"]), "source": "external"})
                _paste_event(out, offset_s=t + 0.22, size=random.choice(["medium", "large"]), source="external", question_id=qid)

                # Some questions get minimal/no typing after paste to trigger paste-without-typing.
                if (idx + pass_index) % 3 != 0:
                    _metadata_event(out, offset_s=t + 0.6, event_type="typing_started", payload={"question_id": qid, "input_context": "answer_box"})
                    _metadata_event(
                        out,
                        offset_s=t + 0.72,
                        event_type="typing_burst",
                        payload={
                            "question_id": qid,
                            "burst_length": random.randint(6, 10),
                            "duration_ms": random.randint(520, 980),
                            "interval_ms": random.choice([76, 82, 94]),
                        },
                    )
                    _metadata_event(
                        out,
                        offset_s=t + 1.05,
                        event_type="typing_stopped",
                        payload={"question_id": qid, "session_duration_ms": random.randint(500, 1400), "total_bursts": 1, "pause_count": 0},
                    )

                answer_offset = t + _jitter(random.uniform(0.45, 1.35))
                _metadata_event(out, offset_s=answer_offset - 0.18, event_type="rapid_answer_burst", payload={"question_id": qid, "burst_count": random.randint(2, 4)})
                _metadata_event(out, offset_s=answer_offset - 0.1, event_type="answer_changed", payload={"question_id": qid, "mode": "post_return_edit"})
                _answer_event(out, offset_s=answer_offset, qid=qid, style="post_lookup_change")
                out.append(BehaviorEvent(offset_s=answer_offset + 0.18, event_type="question_view", payload={"question_id": qid, "action": "leave"}))
                t = answer_offset + 0.18

                # Extra clustered focus loss to reinforce repeated patterns.
                if random.random() < 0.8:
                    extra_blur = t + _jitter(random.uniform(0.5, 1.2))
                    _metadata_event(out, offset_s=extra_blur, event_type="blur", payload={"question_id": qid, "reason": "repeat_switch"})
                    t = _tab_switch_pair(out, offset_s=extra_blur + 0.04, away_s=_jitter(random.uniform(2.0, 7.0)), reason="repeat_lookup")
                    _metadata_event(out, offset_s=t + 0.02, event_type="focus", payload={"question_id": qid, "reason": "repeat_return"})

                # Longer between-question pauses keep the session realistic without inflating question dwell time.
                if idx % 2 == 0 or pass_index == 1:
                    idle_for = _lognormal_seconds(median_s=46, sigma=0.45, min_s=24, max_s=110)
                    t = _idle_pair(out, offset_s=t + 0.5, idle_s=idle_for, reason="lookup_pause")
                    _metadata_event(out, offset_s=t + 0.08, event_type="typing_pause", payload={"question_id": qid, "pause_duration_s": round(random.uniform(4.5, 12.0), 2)})
                    _metadata_event(out, offset_s=t + 0.2, event_type="typing_started", payload={"question_id": qid, "input_context": "answer_box"})
                    _metadata_event(
                        out,
                        offset_s=t + 0.32,
                        event_type="typing_burst",
                        payload={
                            "question_id": qid,
                            "burst_length": random.randint(11, 18),
                            "duration_ms": random.randint(640, 1500),
                            "interval_ms": random.choice([68, 74, 82, 88]),
                        },
                    )
                    _metadata_event(
                        out,
                        offset_s=t + 0.56,
                        event_type="backspace_activity",
                        payload={"question_id": qid, "count": random.randint(2, 6), "burst_window_ms": random.randint(220, 680)},
                    )
                    _metadata_event(
                        out,
                        offset_s=t + 0.88,
                        event_type="typing_stopped",
                        payload={"question_id": qid, "session_duration_ms": random.randint(900, 2200), "total_bursts": random.randint(1, 3), "pause_count": 1},
                    )
                    t += _jitter(random.uniform(2.4, 5.8))

                t += _jitter(random.uniform(12.0, 26.0))

        # Long final pause before a short suspicious submission sequence.
        final_qid = qids[-1]
        t = _idle_pair(out, offset_s=t + 1.0, idle_s=_lognormal_seconds(median_s=58, sigma=0.45, min_s=30, max_s=120), reason="final_lookup")
        _metadata_event(out, offset_s=t + 0.5, event_type="question_viewed", payload={"question_id": final_qid})
        out.append(BehaviorEvent(offset_s=t, event_type="question_view", payload={"question_id": final_qid, "action": "enter"}))
        _metadata_event(out, offset_s=t + 0.08, event_type="typing_pause", payload={"question_id": final_qid, "pause_duration_s": round(random.uniform(6.0, 14.0), 2)})
        _metadata_event(out, offset_s=t + 0.18, event_type="typing_burst", payload={"question_id": final_qid, "burst_length": random.randint(13, 20), "duration_ms": random.randint(760, 1680), "interval_ms": random.choice([64, 72, 80])})
        _metadata_event(out, offset_s=t + 0.42, event_type="clipboard_paste", payload={"question_id": final_qid, "size": "large", "source": "external"})
        _paste_event(out, offset_s=t + 0.46, size="large", source="external", question_id=final_qid)
        _metadata_event(out, offset_s=t + 0.78, event_type="rapid_answer_burst", payload={"question_id": final_qid, "burst_count": 4})
        _metadata_event(out, offset_s=t + 0.86, event_type="answer_changed", payload={"question_id": final_qid, "mode": "final_revision"})
        _answer_event(out, offset_s=t + 0.94, qid=final_qid, style="submitted_after_external_lookup")
        out.append(BehaviorEvent(offset_s=t + 1.08, event_type="question_view", payload={"question_id": final_qid, "action": "leave"}))

        return out


class DemoLowAaravCandidate(CandidateProfile):
    """Low-risk demo candidate with stable, believable behavior."""

    name = "DemoLowAaravCandidate"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        out: List[BehaviorEvent] = []
        t = 0.0
        qids = list(question_ids) if question_ids else _pick_question_ids(8)
        focus_check_done = False

        for idx, qid in enumerate(qids):
            dwell = _lognormal_seconds(median_s=22, sigma=0.26, min_s=14, max_s=36)
            _metadata_event(out, offset_s=t + 0.25, event_type="question_viewed", payload={"question_id": qid})
            _metadata_event(out, offset_s=t + 0.45, event_type="normal_mouse_activity", payload={"question_id": qid, "activity": "pointer_move"})
            out.append(BehaviorEvent(offset_s=t, event_type="question_view", payload={"question_id": qid, "action": "enter"}))
            _metadata_event(out, offset_s=t + 1.1, event_type="typing_started", payload={"question_id": qid, "input_context": "answer_box"})
            _metadata_event(
                out,
                offset_s=t + 2.8,
                event_type="typing_burst",
                payload={
                    "question_id": qid,
                    "burst_length": random.randint(4, 7),
                    "duration_ms": random.randint(880, 1620),
                    "interval_ms": random.choice([130, 150, 170, 190]),
                },
            )
            if idx in {2, 5}:
                _metadata_event(out, offset_s=t + 5.8, event_type="typing_pause", payload={"question_id": qid, "pause_duration_s": round(random.uniform(1.8, 3.0), 2)})
            _metadata_event(
                out,
                offset_s=t + max(6.8, dwell - 3.6),
                event_type="typing_stopped",
                payload={"question_id": qid, "session_duration_ms": int(max(2200, (dwell - 1.2) * 1000)), "total_bursts": 1, "pause_count": 1 if idx in {2, 5} else 0},
            )
            _metadata_event(out, offset_s=t + max(4.5, dwell - 4.0), event_type="normal_typing", payload={"question_id": qid, "duration_s": round(random.uniform(6.5, 11.0), 2)})
            answer_at = t + _jitter(dwell - random.uniform(1.6, 3.4), rel_std=0.06, abs_std=0.18)
            _answer_event(out, offset_s=answer_at, qid=qid, style="steady_answer")
            if idx % 3 == 1:
                _metadata_event(out, offset_s=answer_at - 0.72, event_type="answer_changed", payload={"question_id": qid, "mode": "minor_revision"})
            t = answer_at + 0.18
            out.append(BehaviorEvent(offset_s=t, event_type="question_view", payload={"question_id": qid, "action": "leave"}))

            if not focus_check_done and idx == max(1, len(qids) // 2):
                blur_at = t + 0.5
                _metadata_event(out, offset_s=blur_at, event_type="blur", payload={"question_id": qid, "reason": "brief_refocus"})
                _metadata_event(out, offset_s=blur_at + 0.85, event_type="focus", payload={"question_id": qid, "reason": "brief_refocus"})
                focus_check_done = True

            if random.random() < 0.18:
                _metadata_event(out, offset_s=t + 0.35, event_type="typing_pause", payload={"question_id": qid, "pause_duration_s": round(random.uniform(1.4, 3.2), 2)})

            t += _jitter(random.uniform(7.0, 14.0), rel_std=0.08, abs_std=0.25)

        return out


class DemoMediumNehaCandidate(CandidateProfile):
    """Medium-risk demo candidate that stays review-worthy and ambiguous."""

    name = "DemoMediumNehaCandidate"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        out: List[BehaviorEvent] = []
        t = 0.0
        qids = list(question_ids) if question_ids else _pick_question_ids(8)
        paste_targets = {1, 4, 6}
        visibility_targets = {2}
        blur_targets = {1, 2, 4}
        sequence_targets = {4}

        for idx, qid in enumerate(qids):
            dwell = _lognormal_seconds(median_s=19, sigma=0.28, min_s=11, max_s=32)
            _metadata_event(out, offset_s=t + 0.2, event_type="question_viewed", payload={"question_id": qid})
            out.append(BehaviorEvent(offset_s=t, event_type="question_view", payload={"question_id": qid, "action": "enter"}))
            _typing_activity(out, offset_s=t, dwell_s=dwell, qid=qid)

            if idx in blur_targets:
                blur_at = t + _jitter(random.uniform(3.4, 6.8), rel_std=0.10, abs_std=0.25)
                _metadata_event(out, offset_s=blur_at, event_type="blur", payload={"question_id": qid, "reason": "distraction"})
                if idx in visibility_targets:
                    t_focus = _tab_switch_pair(out, offset_s=blur_at + 0.05, away_s=_jitter(random.uniform(4.0, 8.0), rel_std=0.10, abs_std=0.25), reason="reference_check")
                    _metadata_event(out, offset_s=t_focus + 0.02, event_type="focus", payload={"question_id": qid, "reason": "return"})
                else:
                    t_focus = blur_at + _jitter(random.uniform(1.2, 2.6), rel_std=0.08, abs_std=0.2)
                    _metadata_event(out, offset_s=t_focus, event_type="focus", payload={"question_id": qid, "reason": "return"})
                t = max(t, t_focus)

            if idx in paste_targets:
                _metadata_event(out, offset_s=t + 0.18, event_type="clipboard_paste", payload={"question_id": qid, "size": random.choice(["small", "medium"]), "source": "notes"})
                _paste_event(out, offset_s=t + 0.22, size=random.choice(["small", "medium"]), source="notes", question_id=qid)
                if idx in sequence_targets:
                    _metadata_event(out, offset_s=t + 0.38, event_type="typing_pause", payload={"question_id": qid, "pause_duration_s": round(random.uniform(5.0, 9.0), 2)})
                    _metadata_event(out, offset_s=t + 0.56, event_type="typing_burst", payload={"question_id": qid, "burst_length": random.randint(8, 10), "duration_ms": random.randint(860, 1560), "interval_ms": random.choice([104, 112, 124])})
                    _metadata_event(out, offset_s=t + 0.78, event_type="backspace_activity", payload={"question_id": qid, "count": random.randint(1, 2), "burst_window_ms": random.randint(280, 620)})
                    _metadata_event(out, offset_s=t + 1.02, event_type="typing_stopped", payload={"question_id": qid, "session_duration_ms": random.randint(900, 1800), "total_bursts": 1, "pause_count": 1})
                    t += _jitter(random.uniform(0.8, 1.4), rel_std=0.08, abs_std=0.2)

            if idx in {4}:
                t = _idle_pair(out, offset_s=t + 0.4, idle_s=_jitter(random.uniform(20.0, 36.0), rel_std=0.10, abs_std=0.25), reason="thinking_pause")
                _metadata_event(out, offset_s=t + 0.14, event_type="typing_pause", payload={"question_id": qid, "pause_duration_s": round(random.uniform(4.4, 8.5), 2)})
                _metadata_event(out, offset_s=t + 0.34, event_type="typing_burst", payload={"question_id": qid, "burst_length": random.randint(7, 9), "duration_ms": random.randint(920, 1540), "interval_ms": random.choice([108, 116, 126])})

            answer_at = t + _jitter(random.uniform(4.0, 8.8), rel_std=0.10, abs_std=0.25)
            _answer_event(out, offset_s=answer_at, qid=qid, style="review_needed_answer")
            if idx in {1, 4}:
                _metadata_event(out, offset_s=answer_at - 0.18, event_type="answer_changed", payload={"question_id": qid, "mode": "uncertain_revision"})
            out.append(BehaviorEvent(offset_s=answer_at + 0.2, event_type="question_view", payload={"question_id": qid, "action": "leave"}))
            t = answer_at + _jitter(random.uniform(12.0, 22.0), rel_std=0.10, abs_std=0.25)

        return out


class DemoHighRohanCandidate(CandidateProfile):
    """High-risk demo candidate with progressive escalation and correlation-rich behavior."""

    name = "DemoHighRohanCandidate"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        out: List[BehaviorEvent] = []
        t = 0.0
        qids = list(question_ids) if question_ids else _pick_question_ids(9)

        # Phase 1: mostly normal opening.
        opener = qids[:1]
        for qid in opener:
            dwell = _lognormal_seconds(median_s=14, sigma=0.22, min_s=9, max_s=22)
            _metadata_event(out, offset_s=t + 0.2, event_type="question_viewed", payload={"question_id": qid})
            _metadata_event(out, offset_s=t + 0.42, event_type="normal_mouse_activity", payload={"question_id": qid, "activity": "pointer_move"})
            t = _question_view_pair(out, offset_s=t, qid=qid, dwell_s=_jitter(dwell, rel_std=0.08, abs_std=0.2))
            _metadata_event(out, offset_s=t - 1.8, event_type="normal_typing", payload={"question_id": qid, "duration_s": round(random.uniform(5.0, 9.0), 2)})
            _answer_event(out, offset_s=t - _jitter(random.uniform(1.1, 2.4), rel_std=0.08, abs_std=0.2), qid=qid, style="initial_answer")
            t += _jitter(random.uniform(10.0, 16.0), rel_std=0.08, abs_std=0.25)

        # Phase 2/3: repeated correlated suspicious behavior.
        suspicious_qids = qids[1:]
        for idx, qid in enumerate(suspicious_qids):
            _metadata_event(out, offset_s=t + 0.16, event_type="question_viewed", payload={"question_id": qid})
            out.append(BehaviorEvent(offset_s=t, event_type="question_view", payload={"question_id": qid, "action": "enter"}))
            t += _jitter(random.uniform(0.5, 1.1), rel_std=0.08, abs_std=0.2)

            blur_at = t + _jitter(random.uniform(0.18, 0.48), rel_std=0.08, abs_std=0.15)
            _metadata_event(out, offset_s=blur_at, event_type="blur", payload={"question_id": qid, "reason": "window_switch"})
            visible_at = _tab_switch_pair(out, offset_s=blur_at + 0.04, away_s=_jitter(random.uniform(0.9, 2.0), rel_std=0.08, abs_std=0.15), reason="external_lookup")
            _metadata_event(out, offset_s=visible_at + 0.02, event_type="focus", payload={"question_id": qid, "reason": "window_return"})
            t = max(t, visible_at)

            if idx % 2 == 0:
                extra_blur = t + _jitter(random.uniform(0.3, 0.8), rel_std=0.08, abs_std=0.15)
                _metadata_event(out, offset_s=extra_blur, event_type="blur", payload={"question_id": qid, "reason": "repeat_switch"})
                visible_again = _tab_switch_pair(out, offset_s=extra_blur + 0.04, away_s=_jitter(random.uniform(0.8, 1.8), rel_std=0.08, abs_std=0.15), reason="repeat_lookup")
                _metadata_event(out, offset_s=visible_again + 0.02, event_type="focus", payload={"question_id": qid, "reason": "repeat_return"})
                t = max(t, visible_again)

            _metadata_event(out, offset_s=t + 0.12, event_type="clipboard_copy", payload={"question_id": qid, "size": "small", "source": "external"})
            _metadata_event(out, offset_s=t + 0.22, event_type="clipboard_paste", payload={"question_id": qid, "size": random.choice(["medium", "large"]), "source": "external"})
            _paste_event(out, offset_s=t + 0.26, size=random.choice(["medium", "large"]), source="external", question_id=qid)

            if idx in {0, 2, 4, 6}:
                _metadata_event(out, offset_s=t + 0.46, event_type="typing_pause", payload={"question_id": qid, "pause_duration_s": round(random.uniform(5.2, 9.4), 2)})
            else:
                _metadata_event(out, offset_s=t + 0.42, event_type="typing_started", payload={"question_id": qid, "input_context": "answer_box"})
                _metadata_event(out, offset_s=t + 0.56, event_type="typing_burst", payload={"question_id": qid, "burst_length": random.randint(7, 10), "duration_ms": random.randint(580, 980), "interval_ms": random.choice([74, 82, 92])})
                _metadata_event(out, offset_s=t + 0.82, event_type="typing_stopped", payload={"question_id": qid, "session_duration_ms": random.randint(520, 1100), "total_bursts": 1, "pause_count": 0})

            answer_at = t + _jitter(random.uniform(0.32, 0.86), rel_std=0.08, abs_std=0.12)
            _metadata_event(out, offset_s=answer_at - 0.16, event_type="rapid_answer_burst", payload={"question_id": qid, "burst_count": random.randint(2, 4)})
            _metadata_event(out, offset_s=answer_at - 0.08, event_type="answer_changed", payload={"question_id": qid, "mode": "post_return_edit"})
            _answer_event(out, offset_s=answer_at, qid=qid, style="post_lookup_change")
            out.append(BehaviorEvent(offset_s=answer_at + 0.16, event_type="question_view", payload={"question_id": qid, "action": "leave"}))
            t = answer_at + 0.16
            if idx in {1, 3, 5}:
                t = _idle_pair(out, offset_s=t + 0.4, idle_s=_jitter(random.uniform(26.0, 42.0), rel_std=0.10, abs_std=0.25), reason="lookup_pause")
                _metadata_event(out, offset_s=t + 0.1, event_type="typing_pause", payload={"question_id": qid, "pause_duration_s": round(random.uniform(6.0, 11.0), 2)})
                _metadata_event(out, offset_s=t + 0.24, event_type="typing_started", payload={"question_id": qid, "input_context": "answer_box"})
                _metadata_event(out, offset_s=t + 0.36, event_type="typing_burst", payload={"question_id": qid, "burst_length": random.randint(12, 18), "duration_ms": random.randint(700, 1400), "interval_ms": random.choice([64, 72, 80, 88])})
                _metadata_event(out, offset_s=t + 0.58, event_type="backspace_activity", payload={"question_id": qid, "count": random.randint(2, 5), "burst_window_ms": random.randint(220, 620)})
                _metadata_event(out, offset_s=t + 0.82, event_type="typing_stopped", payload={"question_id": qid, "session_duration_ms": random.randint(900, 1900), "total_bursts": 1, "pause_count": 1})
                t += _jitter(random.uniform(0.7, 1.2), rel_std=0.08, abs_std=0.15)
            t += _jitter(random.uniform(18.0, 32.0), rel_std=0.10, abs_std=0.25)

        return out


class BurnoutCandidate(CandidateProfile):
    """Legit-but-fatigued candidate.

    Why this matters:
    - Long idle spikes and inconsistent pacing can *look* suspicious.
    - This profile helps ensure the rule engine + ML do not over-penalize
      accessibility pauses, fatigue, or interruptions.

    Patterns:
    - Slow/variable time per question
    - More idle spikes toward the end
    - Occasional re-reads ("did I answer that?")
    - Rare tiny paste from personal notes
    """

    name = "BurnoutCandidate"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        out: List[BehaviorEvent] = []
        t = 0.0

        paste_budget = 1 if random.random() < 0.25 else 0

        for idx, qid in enumerate(question_ids):
            # Gets slower later.
            fatigue_factor = 1.0 + (idx / max(1.0, len(question_ids) - 1)) * random.uniform(0.25, 0.75)
            dwell = _lognormal_seconds(median_s=55 * fatigue_factor, sigma=0.55, min_s=18, max_s=240)
            t = _question_view_pair(out, offset_s=t, qid=qid, dwell_s=_jitter(dwell))

            _answer_event(out, offset_s=t - _jitter(random.uniform(1.2, 10.0)), qid=qid, style="selected_option")

            # Idle spikes become more likely later.
            if random.random() < (0.10 + 0.18 * (idx / max(1.0, len(question_ids) - 1))):
                t = _idle_pair(
                    out,
                    offset_s=t + 0.8,
                    idle_s=_lognormal_seconds(median_s=70, sigma=0.60, min_s=12, max_s=360),
                    reason="fatigue_pause",
                )

            # Occasional re-read after long dwell.
            if random.random() < 0.20:
                t = _question_view_pair(out, offset_s=t + 0.6, qid=qid, dwell_s=_jitter(random.uniform(4, 28)))

            if paste_budget > 0 and random.random() < 0.15 and idx >= 2:
                _paste_event(out, offset_s=t + 0.4, size="tiny", source="notes", question_id=qid)
                paste_budget -= 1

            t += _jitter(random.uniform(1.2, 7.0))

        return out


class PanicSwitcherCandidate(CandidateProfile):
    """Anxious candidate who frequently jumps between questions.

    This is an important MEDIUM generator:
    - Lots of navigation noise (many enter/leave pairs)
    - Some tab switches (checking time / instructions)
    - Usually little/no paste, and timing isn't always "cheater fast"

    Patterns:
    - Frequent switching/revisiting
    - Short dwell bursts mixed with longer thinking
    - Occasional tab-away pairs
    """

    name = "PanicSwitcherCandidate"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        out: List[BehaviorEvent] = []
        t = 0.0

        tab_budget = random.randint(1, 4)

        # Start with a random question, then keep bouncing.
        q_order = list(question_ids)
        random.shuffle(q_order)

        # Do multiple "passes" over the set.
        passes = random.randint(2, 3)
        for p in range(passes):
            for idx, qid in enumerate(q_order):
                # Quick check-ins are common.
                dwell = _lognormal_seconds(median_s=18, sigma=0.60, min_s=7.0, max_s=95)
                if random.random() < 0.35:
                    # Sometimes a longer thinking block.
                    dwell = _lognormal_seconds(median_s=26, sigma=0.55, min_s=8, max_s=140)

                t = _question_view_pair(out, offset_s=t, qid=qid, dwell_s=_jitter(dwell))

                if random.random() < 0.40:
                    _answer_event(out, offset_s=t - _jitter(random.uniform(0.8, 6.0)), qid=qid, style="uncertain_answer")

                if tab_budget > 0 and random.random() < 0.22:
                    t = _tab_switch_pair(out, offset_s=t + 0.5, away_s=_jitter(random.uniform(2, 18)), reason="panic_check")
                    tab_budget -= 1

                if random.random() < 0.18:
                    t = _idle_pair(out, offset_s=t + 0.4, idle_s=_jitter(random.uniform(6, 35)), reason="anxiety_pause")

                t += _jitter(random.uniform(0.8, 4.5))

        return out


class OverpreparedFastCandidate(CandidateProfile):
    """Very fast but legitimate.

    Edge case the model must learn:
    - Speed alone should not create false positives.

    Patterns:
    - Very fast dwell times, but *minimal* tab switching
    - Rare tiny paste (personal snippet)
    - Occasional re-read to avoid looking perfectly robotic
    """

    name = "OverpreparedFastCandidate"

    def generate(self, *, question_ids: Sequence[str]) -> List[BehaviorEvent]:
        out: List[BehaviorEvent] = []
        t = 0.0

        paste_budget = 1 if random.random() < 0.20 else 0

        for idx, qid in enumerate(question_ids):
            # Keep it clearly "fast", but usually not below the engine's strong-timing
            # threshold (avg_time < 8s). This helps preserve LOW trustworthiness.
            dwell = _lognormal_seconds(median_s=11, sigma=0.35, min_s=8.5, max_s=30)
            t = _question_view_pair(out, offset_s=t, qid=qid, dwell_s=_jitter(dwell))
            _answer_event(out, offset_s=t - _jitter(random.uniform(0.5, 2.5)), qid=qid, style="confident_fast")

            # Small benign noise so it isn't perfectly separable.
            if random.random() < 0.12:
                t = _question_view_pair(out, offset_s=t + 0.5, qid=qid, dwell_s=_jitter(random.uniform(2.5, 10)))

            if paste_budget > 0 and random.random() < 0.15 and idx == 0:
                _paste_event(out, offset_s=t + 0.3, size="tiny", source="notes", question_id=qid)
                paste_budget -= 1

            # Very rare quick tab check (clock).
            if random.random() < 0.04:
                t = _tab_switch_pair(out, offset_s=t + 0.4, away_s=_jitter(random.uniform(1.5, 6.5)), reason="quick_check")

            t += _jitter(random.uniform(0.6, 2.8))

        return out


def all_profiles() -> List[CandidateProfile]:
    return [
        DemoLowAaravCandidate(),
        DemoMediumNehaCandidate(),
        DemoHighRohanCandidate(),
        NormalThoughtfulCandidate(),
        FastButLegitCandidate(),
        BorderlineCandidate(),
        AggressiveCheater(),
        HighRiskLongSessionCandidate(),
        TemplateCopier(),
        SearcherPatternCandidate(),
        DistractedButLegitCandidate(),
        BurnoutCandidate(),
        PanicSwitcherCandidate(),
        OverpreparedFastCandidate(),
    ]


def profile_by_name(name: str) -> CandidateProfile:
    target = str(name or "").strip().lower()
    for p in all_profiles():
        if p.name.lower() == target:
            return p

    # Small convenience aliases
    aliases = {
        "normal": "NormalThoughtfulCandidate",
        "fast": "FastButLegitCandidate",
        "borderline": "BorderlineCandidate",
        "high": "AggressiveCheater",
        "cheater": "AggressiveCheater",
        "highlong": "HighRiskLongSessionCandidate",
        "longhigh": "HighRiskLongSessionCandidate",
        "aarav": "DemoLowAaravCandidate",
        "neha": "DemoMediumNehaCandidate",
        "rohan": "DemoHighRohanCandidate",
        "template": "TemplateCopier",
        "searcher": "SearcherPatternCandidate",
        "distracted": "DistractedButLegitCandidate",
        "burnout": "BurnoutCandidate",
        "panic": "PanicSwitcherCandidate",
        "overprepared": "OverpreparedFastCandidate",
    }
    if target in aliases:
        return profile_by_name(aliases[target])

    raise ValueError(
        f"Unknown profile '{name}'. Available: "
        + ", ".join(p.name for p in all_profiles())
    )


def choose_profile_weighted() -> CandidateProfile:
    """Pick a profile mix that supports realistic false positives + borderlines."""

    profiles = all_profiles()
    weights = {
        "DemoLowAaravCandidate": 0.00,
        "DemoMediumNehaCandidate": 0.00,
        "DemoHighRohanCandidate": 0.00,
        "NormalThoughtfulCandidate": 0.26,
        "FastButLegitCandidate": 0.16,
        "BorderlineCandidate": 0.20,
        "DistractedButLegitCandidate": 0.16,
        "TemplateCopier": 0.10,
        "SearcherPatternCandidate": 0.08,
        "AggressiveCheater": 0.04,
        "BurnoutCandidate": 0.10,
        "PanicSwitcherCandidate": 0.07,
        "OverpreparedFastCandidate": 0.09,
    }
    w = [weights.get(p.name, 0.1) for p in profiles]
    return random.choices(profiles, weights=w, k=1)[0]
