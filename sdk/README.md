# Phase 27 — Risk Browser Telemetry SDK

This folder contains a lightweight JavaScript SDK that collects **behavioral telemetry metadata** from a browser-based assessment page and sends it to your existing backend endpoint:

- `POST {baseUrl}/v1/events/ingest`

It is designed to work with the current project architecture (FastAPI + Redis + Postgres + WebSockets + rule engine + LOW/MEDIUM ML refinement).

## Files

- `sdk/risk-telemetry-sdk.js` — The SDK (embed with a `<script>` tag)
- `sdk/example-assessment.html` — Minimal demo assessment page using the SDK

## Privacy Guarantees (Important)

This SDK is **metadata-only**.

It does **NOT** collect:
- Webcam / microphone
- Screen recordings
- Clipboard content
- Actual typed answer text

It only sends metadata such as:
- Event type
- Timestamp
- Question ID
- Answer length (number of characters)
- Paste size category (`small|medium|large|unknown`)
- Idle duration
- Visibility state
- Typing duration (approx)

## Candidate Consent Placeholder

Before using this SDK with external candidates, show a consent notice similar to:

> This assessment collects behavioral telemetry metadata to help protect assessment integrity.
> It does not record webcam, audio, screen video, typed answer text, or clipboard contents.
> It may collect timestamps, focus changes, idle periods, paste-size metadata, and question-navigation behavior.

## How To Include

Add the SDK to your assessment page:

```html
<script src="/path/to/sdk/risk-telemetry-sdk.js"></script>
```

Then initialize it:

```js
RiskTelemetry.init({
  baseUrl: 'https://your-demo-backend.example.com',
  attemptId: 'attempt_123',

  candidateId: 'cand_001',
  candidateName: 'Ada Lovelace',
  candidateEmail: 'ada@example.com',

  assessmentId: 'assessment_python_01',
  assessmentName: 'Python Coding Assessment',

  // Optional developer flags:
  devMode: true,
  idleTimeoutMs: 30000,
  typingStopMs: 1200,
})
```

The SDK endpoint is configurable through `baseUrl`. For a hosted or multi-device demo, this should be your public backend URL rather than `localhost`.

## SDK API

```js
RiskTelemetry.init({ baseUrl, attemptId, candidateId, candidateName, candidateEmail, assessmentId, assessmentName })

RiskTelemetry.startExam()
RiskTelemetry.endExam()

RiskTelemetry.enterQuestion(questionId)
RiskTelemetry.leaveQuestion(questionId)

RiskTelemetry.trackAnswerChange(questionId, answerValue)

RiskTelemetry.destroy()
```

Notes:
- `trackAnswerChange(questionId, answerValue)` accepts the current input value, but the SDK **only uses `answerValue.length`** and never sends the text.

## Events Captured

The SDK emits events that match the backend ingest model in `app/main.py`:

- `exam_started` — when you call `RiskTelemetry.startExam()`
- `exam_submitted` — when you call `RiskTelemetry.endExam()`
- `visibility_change` — from `document.visibilitychange` (`payload.state` is `hidden|visible`)
- `clipboard` — copy/paste events (metadata-only)
  - `payload.action`: `copy|paste`
  - `payload.size`: `small|medium|large|unknown`
  - `payload.question_id`: best-effort (from focused element `data-question-id`)
- `idle_state` — inactivity-based idle detection
  - emits an `idle` event with `duration_seconds`, then an `active` event
- `typing_started` / `typing_stopped` — derived from answer changes
  - `typing_stopped` includes `duration_seconds` and a simple `keystrokes` approximation
- `answer_change` — called via `trackAnswerChange`
  - includes `answer_length` and whether the change looked like `input|paste` (based on recent paste)
- `question_view` — called via `enterQuestion/leaveQuestion`
  - `payload.action`: `enter|leave`
  - `payload.question_id`: `q1`, `q2`, etc.
- `focus` / `blur` — window focus changes

## Robustness: Retry + Offline Queue

- Uses `fetch` + JSON.
- Maintains a queue.
- If offline or the backend returns a non-2xx response, events remain queued.
- Queue is persisted to `localStorage` under `riskTelemetryQueue:{attemptId}`.
- Queue flushes periodically and when the browser comes back online.

## How To Test With Your Current Backend

### 1) Start backend

```bash
docker compose up
```

The API should be available at your configured backend URL, for example `http://localhost:8000` locally or `https://your-demo-backend.example.com` when hosted.

### 2) Start frontend (dashboard)

```bash
cd frontend
npm install
npm run dev
```

### 3) Open the example assessment

Open `sdk/example-assessment.html` directly in your browser.

You can also prefill the backend endpoint with a query string:

```text
sdk/example-assessment.html?baseUrl=https://your-demo-backend.example.com&attemptId=demo_hosted_1001&candidateName=Hosted%20Demo%20Candidate&candidateEmail=hosted.demo@example.com&assessmentId=assessment_python_01&assessmentName=Python%20Coding%20Assessment
```

Then:
1. Click **Init SDK**
2. Click **Start Exam**
3. Focus inputs, type, paste, switch tabs, wait idle
4. Click **Submit Exam**

### 4) Confirm events and risk updates

- The backend will ingest events at `POST /v1/events/ingest`
- Your dashboard should display the attempt/risk updates (depending on how you’re viewing data)

Tip: The SDK uses `devMode: true` in the example page so you can watch console logs.

## Troubleshooting

- If your browser blocks requests due to CORS, ensure you are using an origin allowed by the backend CORS list.
- For hosted or tunneled demos, add the public frontend / SDK origin to backend `CORS_ALLOW_ORIGINS`.
  - For local testing, opening the HTML file as `file://` may not be treated as `http://localhost:5173`.
  - Easiest: serve `sdk/` via a simple static server or open the file in a browser that allows local requests.

- If you don’t see events:
  - Open DevTools → Network tab → check `POST /v1/events/ingest` requests
  - Open DevTools → Console → look for `[RiskTelemetry]` logs
