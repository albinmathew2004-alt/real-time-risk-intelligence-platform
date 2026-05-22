import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Clock3,
  ClipboardList,
  Copy,
  Database,
  Eye,
  Gauge,
  Home,
  ListChecks,
  Mail,
  MonitorOff,
  RefreshCw,
  Search,
  Server,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Timer,
  User,
  Wifi,
  Zap,
  CheckCircle2,
  CircleDot,
  ArrowLeft,
  BarChart3,
  Settings,
  Users,
  Radio,
  ChevronDown,
  ArrowUpRight,
  FileDown,
  CheckSquare,
  Code2,
  BrainCircuit,
  Bug,
  DatabaseZap,
  Play,
  Lock,
  TimerReset,
} from "lucide-react";

import "../../sdk/risk-telemetry-sdk.js";
import "./App.css";
import EvidenceTable from "./components/EvidenceTable";
import InvestigationHeader from "./components/InvestigationHeader";
import ReviewerWorkflow from "./components/ReviewerWorkflow";
import RiskHistoryChart from "./components/RiskHistoryChart";
import TechnicalDetails from "./components/TechnicalDetails";
import ViolationOverview from "./components/ViolationOverview";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

function toWsUrl(baseUrl, path) {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  try {
    const rawBase = String(baseUrl || "").trim();
    const withProtocol = /^https?:\/\//i.test(rawBase)
      ? rawBase
      : `${window.location.protocol === "https:" ? "https:" : "http:"}//${rawBase.replace(/^\/+/, "")}`;
    const url = new URL(withProtocol);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = normalizedPath;
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    const pageProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const fallbackHost = window.location.host || "127.0.0.1:8000";
    return `${pageProtocol}//${fallbackHost}${normalizedPath}`;
  }
}

const API_URL = `${API_BASE_URL}/v1/logs`;
const WS_URL = toWsUrl(API_BASE_URL, "/ws/risk");
const EVENTS_URL = `${API_BASE_URL}/v1/events`;
const RISK_HISTORY_URL = `${API_BASE_URL}/v1/risk-history`;
const REPORTS_URL = `${API_BASE_URL}/v1/reports`;
const DASHBOARD_SUMMARY_URL = `${API_BASE_URL}/v1/dashboard/summary`;
const REVIEW_QUEUE_URL = `${API_BASE_URL}/v1/review-queue`;
const CASES_URL = `${API_BASE_URL}/v1/cases`;
const AUTH_URL = `${API_BASE_URL}/v1/auth`;
const RECENT_DEMO_HOURS = 24;
const ACTIONABLE_CASE_STATUSES = ["NEW", "TRIAGED", "UNDER_INVESTIGATION", "ESCALATED"];
const COMPLETED_CASE_STATUSES = ["CONFIRMED_RISK", "RESOLVED", "FALSE_POSITIVE", "CLEARED", "CLOSED", "COMPLETED"];

const TOKEN_KEY = "riskintel_access_token";
const CLIENT_TIME_SKEW_GRACE_MS = 2 * 60 * 1000;
let reviewerRefreshTimerId = null;

function formatNumber(value, digits = 2) {
  return Number(value || 0).toFixed(digits);
}

function riskTone(risk) {
  if (risk === "HIGH") return "high";
  if (risk === "MEDIUM") return "medium";
  return "low";
}

function riskClass(risk) {
  return `risk-badge ${riskTone(risk)}`;
}

function formatTime(value) {
  const date = getDisplayDate(value);
  if (!date) return "â€”";
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDateTime(value) {
  if (!value) return "â€”";
  const date = getDisplayDate(value);
  if (!date) return "â€”";

  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const targetDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const dayDiff = Math.round((today.getTime() - targetDay.getTime()) / 86400000);
  const timePart = date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  if (dayDiff === 0) return `Today, ${timePart}`;
  if (dayDiff === 1) return `Yesterday, ${timePart}`;
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "â€”";
  const total = Number(seconds);
  if (!Number.isFinite(total) || total <= 0) return "â€”";

  const rounded = Math.floor(total);
  const h = Math.floor(rounded / 3600);
  const m = Math.floor((rounded % 3600) / 60);
  const s = rounded % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function getFeatures(attempt) {
  return attempt?.features || {};
}

function getCandidateName(item) {
  return item?.candidate_name || item?.attempt_id || "Unknown Candidate";
}

function getCandidateEmail(item) {
  return item?.candidate_email || "No email available";
}

function getAssessmentName(item) {
  return item?.assessment_name || "Python Coding Assessment";
}

function getExamStatus(item, events = []) {
  const f = getFeatures(item);
  const latestEvent = item?.latest_event?.event_type;
  const canonicalStatus = String(item?.attempt_status || item?.status || "").toUpperCase();
  const hasSubmit = events.some((e) => ["exam_submitted", "assessment_submitted", "submit", "completed"].includes(String(e.event_type || "").toLowerCase()));

  if (canonicalStatus === "SUBMITTED" || canonicalStatus === "UNDER_REVIEW" || canonicalStatus === "RESOLVED" || canonicalStatus === "COMPLETED") {
    return "COMPLETED";
  }

  if (f?.has_submit_event === true) return "COMPLETED";

  if (["exam_submitted", "assessment_submitted", "submit", "completed"].includes(String(latestEvent || "").toLowerCase()) || hasSubmit) return "COMPLETED";
  return "ONGOING";
}

function statusClass(status) {
  if (status === "COMPLETED") return "status completed";
  return "status ongoing";
}

function caseStatusTone(status) {
  if (status === "ESCALATED") return "urgent";
  if (COMPLETED_CASE_STATUSES.includes(String(status || "").toUpperCase())) return "completed";
  if (status === "TRIAGED" || status === "UNDER_INVESTIGATION" || status === "NEW") return "review";
  return "ongoing";
}

function caseStatusClass(status) {
  return `status ${caseStatusTone(status)}`;
}

function eventLabel(event) {
  if (!event) return "Unknown event";

  if (event.event_type === "exam_started") return "Exam started";
  if (event.event_type === "exam_submitted") return "Exam submitted";
  if (event.event_type === "clipboard") return "Clipboard paste detected";
  if (event.event_type === "visibility_change") return "Tab/window switch detected";
  if (event.event_type === "idle_state") return "Idle activity gap detected";
  if (event.event_type === "question_view") return `Question ${event.payload?.question_id || ""} viewed`;
  if (event.event_type === "question_answer") return `Answer submitted for ${event.payload?.question_id || "question"}`;

  return event.event_type.replaceAll("_", " ");
}

function eventTone(eventType) {
  if (eventType === "clipboard") return "violation paste";
  if (eventType === "visibility_change") return "violation tab";
  if (eventType === "idle_state") return "violation idle";
  if (eventType === "question_answer") return "violation answer";
  if (eventType === "exam_started" || eventType === "exam_submitted") return "violation exam";
  return "violation neutral";
}

function eventIcon(eventType) {
  if (eventType === "clipboard") return <Copy size={16} />;
  if (eventType === "visibility_change") return <MonitorOff size={16} />;
  if (eventType === "idle_state") return <Clock3 size={16} />;
  if (eventType === "question_answer") return <Timer size={16} />;
  if (eventType === "exam_started" || eventType === "exam_submitted") return <CheckCircle2 size={16} />;
  return <CircleDot size={16} />;
}

function primaryIssue(attempt) {
  const f = getFeatures(attempt);
  const paste = Number(f.paste_count || 0);
  const tab = Number(f.tab_hidden_count || 0);
  const idle = Number(f.idle_spike_count || 0);
  const avgTime = Number(f.time_per_question_mean_s || 0);

  if (paste > 0 && tab > 0 && avgTime > 0 && avgTime <= 20) {
    return "Clipboard + tab switch + fast answering pattern";
  }
  if (paste > 0 && tab > 0) return "Clipboard and tab switching detected";
  if (paste > 0) return "Clipboard activity detected";
  if (tab > 0) return "Tab switching detected";
  if (idle > 0) return "Idle activity gap detected";
  if (avgTime > 0 && avgTime <= 12) return "Fast answering pattern";

  return "Stable assessment engagement observed";
}

function decisionText(risk) {
  if (risk === "HIGH") return "Escalation recommended";
  if (risk === "MEDIUM") return "Reviewer assessment required";
  return "Stable session with limited concern";
}

function decisionSummaryLine(risk) {
  if (risk === "HIGH") return "Repeated, connected integrity signals warrant escalation before any final outcome is released.";
  if (risk === "MEDIUM") return "This attempt contains enough ambiguity to require reviewer judgement before a final decision.";
  return "Observed behavior remains stable and explainable without repeated suspicious progression.";
}

function buildEvidence(attempt) {
  const f = getFeatures(attempt);
  const paste = Number(f.paste_count || 0);
  const tab = Number(f.tab_hidden_count || 0);
  const idle = Number(f.idle_spike_count || 0);
  const avgTime = Number(f.time_per_question_mean_s || 0);
  const score = Number(attempt?.combined_score || 0);
  const evidence = [];

  if (paste > 0) {
    evidence.push({
      title: "Clipboard Activity",
      icon: <Copy size={21} />,
      severity: paste >= 4 ? "HIGH" : paste >= 2 ? "MEDIUM" : "LOW",
      finding: `${paste} paste event${paste === 1 ? "" : "s"} detected`,
      explanation: "Clipboard usage during an assessment may indicate copied or externally generated answers.",
    });
  }

  if (tab > 0) {
    evidence.push({
      title: "Tab Switching",
      icon: <MonitorOff size={21} />,
      severity: tab >= 5 ? "HIGH" : tab >= 2 ? "MEDIUM" : "LOW",
      finding: `${tab} tab switch event${tab === 1 ? "" : "s"} detected`,
      explanation: "The candidate left the assessment window. Repeated switching is suspicious when paired with other signals.",
    });
  }

  if (avgTime > 0 && avgTime <= 15) {
    evidence.push({
      title: "Fast Answer Timing",
      icon: <Timer size={21} />,
      severity: avgTime <= 8 ? "HIGH" : "MEDIUM",
      finding: `Average answer time: ${formatNumber(avgTime, 1)} seconds`,
      explanation: "Very fast submissions can indicate external assistance, especially after tab or clipboard events.",
    });
  }

  if (idle > 0) {
    evidence.push({
      title: "Idle + Burst Pattern",
      icon: <Clock3 size={21} />,
      severity: idle >= 3 ? "HIGH" : "MEDIUM",
      finding: `${idle} idle spike${idle === 1 ? "" : "s"} detected`,
      explanation: "Long inactivity followed by sudden answer activity can indicate external lookup behavior.",
    });
  }

  if (paste > 0 && tab > 0) {
    evidence.push({
      title: "Multi-Signal Pattern",
      icon: <ShieldAlert size={21} />,
      severity: "HIGH",
      finding: "Multiple suspicious signals occurred in one attempt",
      explanation: "Combined behavioral evidence is stronger than any single violation alone.",
    });
  }

  if (score >= 0.6) {
    evidence.push({
      title: "High Risk Score",
      icon: <Gauge size={21} />,
      severity: "HIGH",
      finding: `Combined score: ${formatNumber(score)}`,
      explanation: "The total risk score crossed the high-risk review threshold.",
    });
  } else if (score >= 0.25) {
    evidence.push({
      title: "Moderate Risk Score",
      icon: <Gauge size={21} />,
      severity: "MEDIUM",
      finding: `Combined score: ${formatNumber(score)}`,
      explanation: "The attempt contains enough behavioral evidence to justify manual review.",
    });
  }

  if (!evidence.length) {
    evidence.push({
      title: "Normal Behavior Pattern",
      icon: <ShieldCheck size={21} />,
      severity: "LOW",
      finding: "Stable engagement observed",
      explanation: "The available behavioral signals do not indicate repeated or escalating integrity concerns.",
    });
  }

  return evidence;
}

function reportSummary(attempt) {
  const risk = attempt?.risk || "LOW";

  if (risk === "HIGH") {
    return `This attempt should be escalated because repeated suspicious behaviors built a strong integrity concern around ${primaryIssue(attempt)}.`;
  }
  if (risk === "MEDIUM") {
    return `This attempt presents mixed signals that deserve reviewer judgement, primarily around ${primaryIssue(attempt)}.`;
  }
  return "This attempt remained broadly stable, with only limited behavioral noise and no sustained suspicious progression.";
}

function getLatestActivity(item) {
  if (item?.latest_event?.event_type) return eventLabel(item.latest_event);
  if (item?.explanation) return item.explanation;
  return primaryIssue(item);
}

function getLastActivityAt(item) {
  return item?.latest_event?.occurred_at || item?.latest_event?.timestamp || item?.timestamp || null;
}

function getAttemptDurationSeconds(item) {
  const f = getFeatures(item);
  const seconds = f?.attempt_duration_s ?? item?.attempt_duration_s ?? null;
  const value = seconds === null || seconds === undefined ? null : Number(seconds);
  if (!Number.isFinite(value)) return null;
  return value;
}

function getEventCount(item) {
  if (Number.isFinite(Number(item?.event_count))) return Number(item.event_count);
  return null;
}

function getInitials(name) {
  const parts = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2);
  if (!parts.length) return "RC";
  return parts.map((part) => part[0]?.toUpperCase() || "").join("");
}

function severityRank(severity) {
  if (severity === "HIGH") return 3;
  if (severity === "MEDIUM") return 2;
  return 1;
}

function severityLabel(severity) {
  if (severity === "HIGH") return "High";
  if (severity === "MEDIUM") return "Medium";
  return "Low";
}

function getSeverityFromCount(count, medium = 1, high = 4) {
  const value = Number(count || 0);
  if (value >= high) return "HIGH";
  if (value >= medium) return "MEDIUM";
  return "LOW";
}

function getConfidenceLabel(value) {
  const numeric = Number(value || 0);
  if (numeric >= 0.8) return "High";
  if (numeric >= 0.6) return "Moderate";
  return "Low";
}

function parseDateValue(value) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function getDisplayDate(value) {
  const parsed = parseDateValue(value);
  if (!parsed) return null;
  const now = new Date();
  if (parsed.getTime() - now.getTime() > CLIENT_TIME_SKEW_GRACE_MS) {
    return now;
  }
  return parsed;
}

function formatClockLabel(value) {
  const date = getDisplayDate(value);
  if (!date) return "—";
  return date.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

function filterRecentItems(items, getTimestamp, hours = RECENT_DEMO_HOURS) {
  const cutoffMs = Date.now() - hours * 60 * 60 * 1000;
  const recentItems = (items || []).filter((item) => {
    const parsed = parseDateValue(getTimestamp(item));
    return parsed ? parsed.getTime() >= cutoffMs : false;
  });
  return recentItems.length ? recentItems : (items || []);
}

function getSecondsBetween(a, b) {
  const start = parseDateValue(a);
  const end = parseDateValue(b);
  if (!start || !end) return null;
  return Math.max(0, Math.round((end.getTime() - start.getTime()) / 1000));
}

function findNestedPayloadValue(input, keys) {
  if (input === null || input === undefined) return null;
  if (Array.isArray(input)) {
    for (const item of input) {
      const found = findNestedPayloadValue(item, keys);
      if (found !== null && found !== undefined && found !== "") return found;
    }
    return null;
  }
  if (typeof input !== "object") return null;

  for (const [key, value] of Object.entries(input)) {
    if (keys.includes(key) && value !== null && value !== undefined && value !== "") {
      return value;
    }
  }

  for (const value of Object.values(input)) {
    const found = findNestedPayloadValue(value, keys);
    if (found !== null && found !== undefined && found !== "") return found;
  }

  return null;
}

function getFirstPayloadValue(events, keys) {
  for (const event of events) {
    const found = findNestedPayloadValue(event?.payload, keys);
    if (found !== null && found !== undefined && found !== "") return found;
  }
  return null;
}

function getIdleMaxSeconds(events, attempt) {
  const values = events
    .filter((event) => event.event_type === "idle_state")
    .map((event) => {
      const payloadValue = findNestedPayloadValue(event.payload, [
        "duration_s",
        "idle_duration_s",
        "idle_seconds",
        "seconds",
        "gap_s",
      ]);
      const numeric = Number(payloadValue);
      return Number.isFinite(numeric) ? numeric : 0;
    })
    .filter((value) => value > 0);

  if (values.length) return Math.max(...values);

  const featureValue = Number(getFeatures(attempt)?.idle_max_duration_s || 0);
  return Number.isFinite(featureValue) && featureValue > 0 ? featureValue : 0;
}

function getRapidAnswerBurstCount(events) {
  const answers = events.filter((event) => event.event_type === "question_answer");
  let bursts = 0;
  for (let index = 1; index < answers.length; index += 1) {
    const delta = getSecondsBetween(answers[index - 1]?.occurred_at, answers[index]?.occurred_at);
    if (delta !== null && delta <= 15) bursts += 1;
  }
  return bursts;
}

function getSuspiciousSequenceCount(events) {
  let count = 0;
  for (let index = 0; index < events.length - 2; index += 1) {
    const windowEvents = events.slice(index, index + 3).map((event) => event.event_type);
    const hasClipboard = windowEvents.includes("clipboard");
    const hasFocusLoss = windowEvents.includes("visibility_change");
    const hasAnswer = windowEvents.includes("question_answer");
    if ((hasClipboard && hasAnswer) || (hasFocusLoss && hasAnswer)) count += 1;
  }
  return count;
}

function getTypingBehaviorAnomalyCount(events, attempt) {
  const features = getFeatures(attempt);
  const featureCount = Number(
    features.typing_behavior_anomaly_count
    ?? features.keystroke_anomaly_count
    ?? 0,
  );
  if (featureCount > 0) return featureCount;

  let rapidTypingSequences = 0;
  let abnormalPauseRecovery = 0;
  let pasteWithoutTyping = 0;
  let recentPause = null;
  const pendingPasteIndexes = [];

  for (const event of events) {
    const type = String(event.event_type || "").toLowerCase();
    const payload = event.payload || {};

    if (type === "clipboard" && String(payload.action || payload.operation || "").toLowerCase() === "paste") {
      pendingPasteIndexes.push({ recovered: false });
      continue;
    }

    if (type === "typing_pause") {
      const pauseSeconds = Number(
        findNestedPayloadValue(payload, ["duration_s", "pause_duration_s", "duration_seconds"]) ||
        (Number(findNestedPayloadValue(payload, ["pause_duration_ms", "duration_ms"]) || 0) / 1000),
      );
      if (Number.isFinite(pauseSeconds) && pauseSeconds >= 8) {
        recentPause = {
          at: parseDateValue(event.occurred_at),
          duration: pauseSeconds,
        };
      }
      continue;
    }

    if (type === "typing_burst") {
      const burstLength = Number(findNestedPayloadValue(payload, ["burst_length", "keystroke_count", "count"]) || 0);
      const intervalMs = Number(findNestedPayloadValue(payload, ["interval_ms", "avg_interval_ms", "mean_interval_ms"]) || 0);
      const durationMs = Number(findNestedPayloadValue(payload, ["duration_ms", "burst_duration_ms"]) || 0);
      if (
        burstLength >= 12
        || (burstLength >= 8 && intervalMs > 0 && intervalMs <= 90)
        || (burstLength >= 8 && durationMs > 0 && durationMs <= 1500)
      ) {
        rapidTypingSequences += 1;
      }
      if (burstLength >= 3) {
        pendingPasteIndexes.forEach((item) => {
          if (!item.recovered) item.recovered = true;
        });
      }
      if (recentPause?.at) {
        const delta = getSecondsBetween(recentPause.at, event.occurred_at);
        if (delta !== null && delta <= 20 && (burstLength >= 10 || (intervalMs > 0 && intervalMs <= 85))) {
          abnormalPauseRecovery += 1;
          recentPause = null;
        }
      }
      continue;
    }

    if (type === "backspace_activity") {
      const count = Number(findNestedPayloadValue(payload, ["count", "backspace_count"]) || 0);
      if (count >= 2) {
        pendingPasteIndexes.forEach((item) => {
          if (!item.recovered) item.recovered = true;
        });
      }
      continue;
    }
  }

  pasteWithoutTyping = pendingPasteIndexes.filter((item) => !item.recovered).length;
  return rapidTypingSequences + pasteWithoutTyping + abnormalPauseRecovery;
}

function getBlurEventCount(events) {
  return events.filter((event) => {
    if (event.event_type === "window_blur" || event.event_type === "blur") return true;
    if (event.event_type !== "visibility_change") return false;
    const state = String(findNestedPayloadValue(event.payload, ["state", "visibility_state", "visibility"]) || "").toLowerCase();
    return state.includes("hidden") || state.includes("blur");
  }).length;
}

function getFocusLossRate(events) {
  if (!events.length) return 0;
  const focusLoss = events.filter((event) => event.event_type === "visibility_change" || event.event_type === "window_blur").length;
  return Math.round((focusLoss / events.length) * 100);
}

function buildViolationOverview(attempt, events) {
  const features = getFeatures(attempt);
  const clipboardCount = Number(features.paste_count || events.filter((event) => event.event_type === "clipboard").length || 0);
  const tabSwitchCount = Number(features.tab_hidden_count || events.filter((event) => event.event_type === "visibility_change").length || 0);
  const blurEvents = getBlurEventCount(events) || tabSwitchCount;
  const idleMaxSeconds = getIdleMaxSeconds(events, attempt);
  const rapidAnswerBursts = getRapidAnswerBurstCount(events);
  const suspiciousSequences = getSuspiciousSequenceCount(events);
  const typingBehaviorAnomalies = getTypingBehaviorAnomalyCount(events, attempt);
  const focusLossRate = getFocusLossRate(events);

  return [
    {
      key: "clipboard",
      title: "Clipboard",
      subtitle: "Copy/Paste Count",
      value: String(clipboardCount),
      severity: getSeverityFromCount(clipboardCount, 1, 5),
    },
    {
      key: "tab_switches",
      title: "Tab Switches",
      subtitle: "Count",
      value: String(tabSwitchCount),
      severity: getSeverityFromCount(tabSwitchCount, 2, 6),
    },
    {
      key: "blur_events",
      title: "Blur Events",
      subtitle: "Window Focus Loss",
      value: String(blurEvents),
      severity: getSeverityFromCount(blurEvents, 2, 6),
    },
    {
      key: "idle_time",
      title: "Idle Time",
      subtitle: "Max",
      value: idleMaxSeconds > 0 ? formatDuration(idleMaxSeconds) : "00:00",
      severity: idleMaxSeconds >= 180 ? "MEDIUM" : idleMaxSeconds > 0 ? "LOW" : "LOW",
    },
    {
      key: "rapid_answer_bursts",
      title: "Rapid Answer Bursts",
      subtitle: "Fast Sequence Count",
      value: String(rapidAnswerBursts),
      severity: getSeverityFromCount(rapidAnswerBursts, 2, 5),
    },
    {
      key: "suspicious_sequences",
      title: "Suspicious Sequences",
      subtitle: "Correlated Patterns",
      value: String(suspiciousSequences),
      severity: getSeverityFromCount(suspiciousSequences, 1, 3),
    },
    {
      key: "typing_behavior_anomalies",
      title: "Typing Behavior Anomalies",
      subtitle: "Typing Pattern Irregularities",
      value: String(typingBehaviorAnomalies),
      severity: typingBehaviorAnomalies >= 3 ? "MEDIUM" : typingBehaviorAnomalies > 0 ? "LOW" : "LOW",
    },
    {
      key: "focus_loss_rate",
      title: "Focus Loss Rate",
      subtitle: "Session Share",
      value: `${focusLossRate}%`,
      severity: focusLossRate >= 25 ? "HIGH" : focusLossRate >= 10 ? "MEDIUM" : "LOW",
    },
  ].map((item) => ({
    ...item,
    severityLabel: severityLabel(item.severity),
  }));
}

function buildViolationOverviewFromCounts(counts = {}) {
  const clipboardCount = Number(counts.clipboard_count || 0);
  const tabSwitchCount = Number(counts.tab_switch_count || 0);
  const blurEvents = Number(counts.focus_blur_count || 0);
  const idleMaxSeconds = Number(counts.idle_max_duration_s || 0);
  const rapidAnswerBursts = Number(counts.rapid_answer_burst_count || 0);
  const suspiciousSequences = Number(counts.suspicious_sequence_count || 0);
  const typingBehaviorAnomalies = Number((counts.typing_behavior_anomaly_count ?? counts.keystroke_anomaly_count) || 0);
  const focusLossRate = Number(counts.focus_loss_rate || 0);

  return [
    {
      key: "clipboard",
      title: "Clipboard",
      subtitle: "Copy/Paste Count",
      value: String(clipboardCount),
      severity: getSeverityFromCount(clipboardCount, 1, 5),
    },
    {
      key: "tab_switches",
      title: "Tab Switches",
      subtitle: "Count",
      value: String(tabSwitchCount),
      severity: getSeverityFromCount(tabSwitchCount, 2, 6),
    },
    {
      key: "blur_events",
      title: "Blur Events",
      subtitle: "Window Focus Loss",
      value: String(blurEvents),
      severity: getSeverityFromCount(blurEvents, 2, 6),
    },
    {
      key: "idle_time",
      title: "Idle Time",
      subtitle: "Max",
      value: idleMaxSeconds > 0 ? formatDuration(idleMaxSeconds) : "00:00",
      severity: idleMaxSeconds >= 300 ? "HIGH" : idleMaxSeconds >= 180 ? "MEDIUM" : "LOW",
    },
    {
      key: "rapid_answer_bursts",
      title: "Rapid Answer Bursts",
      subtitle: "Fast Sequence Count",
      value: String(rapidAnswerBursts),
      severity: getSeverityFromCount(rapidAnswerBursts, 2, 5),
    },
    {
      key: "suspicious_sequences",
      title: "Suspicious Sequences",
      subtitle: "Correlated Patterns",
      value: String(suspiciousSequences),
      severity: getSeverityFromCount(suspiciousSequences, 1, 3),
    },
    {
      key: "typing_behavior_anomalies",
      title: "Typing Behavior Anomalies",
      subtitle: "Typing Pattern Irregularities",
      value: String(typingBehaviorAnomalies),
      severity: typingBehaviorAnomalies >= 3 ? "MEDIUM" : typingBehaviorAnomalies > 0 ? "LOW" : "LOW",
    },
    {
      key: "focus_loss_rate",
      title: "Focus Loss Rate",
      subtitle: "Session Share",
      value: `${focusLossRate}%`,
      severity: focusLossRate >= 25 ? "HIGH" : focusLossRate >= 10 ? "MEDIUM" : "LOW",
    },
  ].map((item) => ({
    ...item,
    severityLabel: severityLabel(item.severity),
  }));
}

function buildEvidenceRows(attempt, overviewItems, eventCount) {
  const rows = [];
  const score = Number(attempt?.combined_score || 0);
  const scoreSeverity = score >= 0.7 ? "HIGH" : score >= 0.4 ? "MEDIUM" : "LOW";

  rows.push({
    key: "score",
    title: score >= 0.7 ? "High Risk Score" : score >= 0.4 ? "Elevated Risk Score" : "Observed Risk Score",
    subtitle: score >= 0.7 ? "Overall risk exceeded high-risk threshold" : "Combined telemetry signals pushed this attempt into review territory",
    severity: scoreSeverity,
    details: `Combined score ${formatNumber(score)} across ${eventCount ?? 0} captured events`,
    count: formatNumber(score),
    countDisplay: formatNumber(score),
    relatedEventCount: eventCount ?? 0,
    impactLabel: severityLabel(scoreSeverity),
    signalType: "RISK_SCORE",
    firstSeen: attempt?.timestamp || null,
    lastSeen: attempt?.latest_event?.occurred_at || attempt?.timestamp || null,
  });

  for (const item of overviewItems) {
    if (item.severity === "LOW" && !["idle_time", "typing_behavior_anomalies"].includes(item.key)) continue;
    if (item.value === "0" || item.value === "00:00" || item.value === "0%") continue;
    rows.push({
      key: item.key,
      title: item.title,
      subtitle: item.subtitle,
      severity: item.severity,
      details: `${item.value} observed during this attempt`,
      count: Number(item.value) || 0,
      countDisplay: item.value,
      relatedEventCount: eventCount ?? 0,
      impactLabel: item.severityLabel,
      signalType: String(item.key || "signal").toUpperCase(),
      firstSeen: attempt?.timestamp || null,
      lastSeen: attempt?.latest_event?.occurred_at || attempt?.timestamp || null,
    });
  }

  rows.sort((a, b) => severityRank(b.severity) - severityRank(a.severity));
  return rows;
}

function buildEvidenceRowsFromReport(evidenceItems = []) {
  return evidenceItems.map((item) => ({
    key: String(item.signal_type || "signal").toLowerCase(),
    title: item.title || "Observed Signal",
    subtitle: item.reviewer_summary || item.explanation || "Behavioral evidence was observed for this attempt.",
    severity: item.severity || "LOW",
    details: `${Number(item.count || 0)} observed; ${Number(item.related_event_count || 0)} related event${Number(item.related_event_count || 0) === 1 ? "" : "s"}`,
    count: Number(item.count || 0),
    countDisplay: Number(item.count || 0),
    relatedEventCount: Number(item.related_event_count || 0),
    impactLabel: severityLabel(item.severity || "LOW"),
    signalType: item.signal_type || "SIGNAL",
    firstSeen: item.first_seen || null,
    lastSeen: item.last_seen || null,
    reviewerSummary: item.reviewer_summary || "",
    evidenceCategory: item.evidence_category || "",
    involvedEventTypes: Array.isArray(item.involved_event_types) ? item.involved_event_types : [],
    confidence: Number(item.correlation_confidence || 0),
  }));
}

function buildInvestigationInsights(reportData, evidenceRows, displayRisk) {
  const explicitInsights = Array.isArray(reportData?.investigation_insights)
    ? reportData.investigation_insights.filter(Boolean).map((item) => String(item).trim()).filter(Boolean)
    : [];
  if (explicitInsights.length) return explicitInsights.slice(0, 4);

  const insightRows = evidenceRows
    .filter((row) => row.key !== "high_risk_score" && row.key !== "score")
    .sort((a, b) => {
      const severityDelta = severityRank(b.severity) - severityRank(a.severity);
      if (severityDelta !== 0) return severityDelta;
      return Number(b.count || 0) - Number(a.count || 0);
    })
    .slice(0, 4);

  if (insightRows.length) {
    return insightRows.map((row) => row.reviewerSummary || row.subtitle || `${row.title} was observed.`).map((item) => String(item).trim());
  }

  if (displayRisk === "HIGH") {
    return ["Repeated deterministic integrity signals escalated over the course of the attempt."];
  }
  if (displayRisk === "MEDIUM") {
    return ["Moderate behavioral irregularities were observed and should be reviewed in context."];
  }
  return ["Assessment engagement remained stable without repeated suspicious progression."];
}

function evidencePriority(row) {
  if (row.key === "suspicious_sequence" || row.key === "suspicious_sequences") return 3;
  if (row.key === "high_risk_score" || row.key === "score") return 2;
  return 0;
}

function buildTechnicalDetails({ attempt, events, eventCount, firstEvent, lastEvent, durationSeconds }) {
  const sdkVersion = getFirstPayloadValue(events, ["sdk_version", "sdkVersion", "version"]) || "1.0.0";
  const userAgent = getFirstPayloadValue(events, ["user_agent", "userAgent", "ua"]) || "Telemetry did not capture a user agent";
  const ipAddress = getFirstPayloadValue(events, ["ip", "ip_address", "client_ip"]) || "Not provided";

  return [
    { label: "Assessment", value: getAssessmentName(attempt) },
    { label: "Attempt ID", value: attempt?.attempt_id || "â€”" },
    { label: "Duration", value: formatDuration(durationSeconds) },
    { label: "Events", value: eventCount === null ? "â€”" : String(eventCount) },
    { label: "SDK Version", value: String(sdkVersion) },
    { label: "User Agent", value: String(userAgent) },
    { label: "IP", value: String(ipAddress) },
    { label: "Started", value: formatDateTime(firstEvent) },
    { label: "Last Activity", value: formatDateTime(lastEvent) },
  ];
}

function formatRelativeTime(value) {
  const date = getDisplayDate(value);
  if (!date) return "No activity";
  const deltaMinutes = Math.round((Date.now() - date.getTime()) / 60000);
  const minutes = Math.max(0, deltaMinutes);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 8) return `${hours}h ago`;
  if (hours < 24) return formatDateTime(date);
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function isResolvedCaseStatus(status) {
  return COMPLETED_CASE_STATUSES.includes(String(status || "").toUpperCase());
}

function isQueueResolvedStatus(status) {
  return COMPLETED_CASE_STATUSES.includes(normalizeQueueStatus(status));
}

function isQueueActionableStatus(status) {
  return ACTIONABLE_CASE_STATUSES.includes(normalizeQueueStatus(status));
}

function getQueueStatus(caseRecord) {
  const status = caseRecord?.status || "NEW";
  if (status === "CLEARED" || status === "FALSE_POSITIVE") return "RESOLVED";
  return status;
}

function getStatusPriority(status) {
  if (status === "ESCALATED") return 0;
  if (status === "UNDER_INVESTIGATION") return 1;
  if (status === "TRIAGED") return 2;
  if (status === "NEW") return 3;
  if (isQueueResolvedStatus(status)) return 4;
  return 7;
}

function getRiskPriority(risk) {
  if (risk === "HIGH") return 0;
  if (risk === "MEDIUM") return 1;
  return 2;
}

function hashSeed(input) {
  const text = String(input || "");
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash * 31 + text.charCodeAt(index)) >>> 0;
  }
  return hash || 7;
}

function buildSparklinePath(seedInput, tone = "low") {
  const seed = hashSeed(`${seedInput}-${tone}`);
  const height = tone === "high" ? 18 : tone === "medium" ? 14 : 10;
  const points = [];
  for (let index = 0; index < 18; index += 1) {
    const x = (index / 17) * 100;
    const wobble = ((seed >> (index % 12)) & 7) - 3;
    const baseline = 50 - height / 2 + ((index % 4) * height) / 10;
    const y = Math.max(10, Math.min(90, baseline - wobble * 4));
    points.push(`${x},${y}`);
  }
  return points.join(" ");
}

function getLiveEventRate(logs) {
  if (!logs.length) return 0;
  const latestMs = Math.max(...logs.map((item) => parseDateValue(item?.timestamp)?.getTime() || 0));
  const windowStart = latestMs - 5 * 60 * 1000;
  const recent = logs.filter((item) => {
    const ms = parseDateValue(item?.timestamp)?.getTime() || 0;
    return ms >= windowStart;
  }).length;
  return Math.max(1, Math.round(recent / 5));
}

function buildAggregateSignals(logs) {
  return logs.reduce((acc, item) => {
    const features = getFeatures(item);
    acc.clipboard += Number(features.paste_count || 0);
    acc.tabSwitches += Number(features.tab_hidden_count || 0);
    acc.idleSpikes += Number(features.idle_spike_count || 0);
    acc.rapidBursts += Number(features.time_per_question_mean_s > 0 && features.time_per_question_mean_s <= 15 ? 1 : 0);
    acc.focusBlur += Number(features.tab_hidden_count || 0);
    return acc;
  }, {
    clipboard: 0,
    tabSwitches: 0,
    idleSpikes: 0,
    rapidBursts: 0,
    focusBlur: 0,
  });
}

function getHealthLabel(hasLogs, loading) {
  if (loading) return "Syncing";
  return hasLogs ? "Healthy" : "Awaiting Data";
}

function buildReviewQueueItems(logs, caseByAttemptId) {
  return logs.map((item) => {
    const caseRecord = caseByAttemptId[item.attempt_id] || null;
    const queueStatus = getQueueStatus(caseRecord);
    const assignedTo = caseRecord?.assigned_reviewer_name || caseRecord?.assigned_reviewer_email || "Unassigned";
    const lastActivity = getLastActivityAt(item);
    return {
      item,
      caseRecord,
      attemptId: item.attempt_id,
      candidateName: getCandidateName(item),
      candidateEmail: getCandidateEmail(item),
      assessmentName: getAssessmentName(item),
      score: Number(item.combined_score || 0),
      risk: item.risk || "LOW",
      queueStatus,
      assignedTo,
      lastActivity,
      eventCount: getEventCount(item),
    };
  }).sort((a, b) => {
    const statusDiff = getStatusPriority(a.queueStatus) - getStatusPriority(b.queueStatus);
    if (statusDiff !== 0) return statusDiff;
    const riskDiff = getRiskPriority(a.risk) - getRiskPriority(b.risk);
    if (riskDiff !== 0) return riskDiff;
    if (b.score !== a.score) return b.score - a.score;
    return compareIso(b.lastActivity, a.lastActivity);
  });
}

function buildQueueTabCounts(rows) {
  return {
    all: rows.length,
    new: rows.filter((row) => normalizeQueueStatus(row.queueStatus) === "NEW").length,
    under_investigation: rows.filter((row) => normalizeQueueStatus(row.queueStatus) === "UNDER_INVESTIGATION").length,
    escalated: rows.filter((row) => normalizeQueueStatus(row.queueStatus) === "ESCALATED").length,
    resolved: rows.filter((row) => isQueueResolvedStatus(row.queueStatus)).length,
    closed: rows.filter((row) => normalizeQueueStatus(row.queueStatus) === "CLOSED").length,
  };
}

function filterQueueRows(rows, tab) {
  if (tab === "all") return rows;
  if (tab === "new") return rows.filter((row) => normalizeQueueStatus(row.queueStatus) === "NEW");
  if (tab === "under_investigation") return rows.filter((row) => normalizeQueueStatus(row.queueStatus) === "UNDER_INVESTIGATION");
  if (tab === "escalated") return rows.filter((row) => normalizeQueueStatus(row.queueStatus) === "ESCALATED");
  if (tab === "resolved") return rows.filter((row) => isQueueResolvedStatus(row.queueStatus));
  if (tab === "closed") return rows.filter((row) => normalizeQueueStatus(row.queueStatus) === "CLOSED");
  return rows;
}

function normalizeQueueRisk(risk) {
  const normalized = String(risk || "LOW").toUpperCase();
  if (normalized === "HIGH" || normalized === "MEDIUM") return normalized;
  return "LOW";
}

function normalizeQueueStatus(status) {
  const normalized = String(status || "NEW").toUpperCase();
  if (normalized === "COMPLETED") return "COMPLETED";
  return normalized;
}

function getQueuePrioritySortValue(row) {
  const status = normalizeQueueStatus(row.queueStatus);
  const risk = normalizeQueueRisk(row.risk);
  if (status === "ESCALATED") return 0;
  if (risk === "HIGH") return 1;
  if (status === "UNDER_INVESTIGATION") return 2;
  if (risk === "MEDIUM") return 3;
  if (status === "NEW") return 4;
  if (risk === "LOW") return 5;
  if (isQueueResolvedStatus(status)) return 6;
  return 7;
}

function compareQueueRows(rowA, rowB, sortKey) {
  if (sortKey === "score_desc") return Number(rowB.score || 0) - Number(rowA.score || 0);
  if (sortKey === "score_asc") return Number(rowA.score || 0) - Number(rowB.score || 0);
  if (sortKey === "last_oldest") return compareIso(rowA.lastActivity, rowB.lastActivity);
  if (sortKey === "last_newest") return compareIso(rowB.lastActivity, rowA.lastActivity);
  if (sortKey === "status") {
    const statusCompare = normalizeQueueStatus(rowA.queueStatus).localeCompare(normalizeQueueStatus(rowB.queueStatus));
    if (statusCompare !== 0) return statusCompare;
    return compareIso(rowB.lastActivity, rowA.lastActivity);
  }
  if (sortKey === "candidate_name") {
    const nameCompare = String(rowA.candidateName || rowA.attemptId || "").localeCompare(String(rowB.candidateName || rowB.attemptId || ""));
    if (nameCompare !== 0) return nameCompare;
    return compareIso(rowB.lastActivity, rowA.lastActivity);
  }

  const priorityCompare = getQueuePrioritySortValue(rowA) - getQueuePrioritySortValue(rowB);
  if (priorityCompare !== 0) return priorityCompare;
  const scoreCompare = Number(rowB.score || 0) - Number(rowA.score || 0);
  if (scoreCompare !== 0) return scoreCompare;
  return compareIso(rowB.lastActivity, rowA.lastActivity);
}

function sortLabel(sortKey) {
  if (sortKey === "score_desc") return "Risk Score: High to Low";
  if (sortKey === "score_asc") return "Risk Score: Low to High";
  if (sortKey === "last_newest") return "Last Activity: Newest";
  if (sortKey === "last_oldest") return "Last Activity: Oldest";
  if (sortKey === "status") return "Status";
  if (sortKey === "candidate_name") return "Candidate Name";
  return "Priority: High to Low";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatWorkflowStatus(value) {
  return String(value || "—")
    .toLowerCase()
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function buildRiskHistorySvg(points = []) {
  if (!points.length) {
    return `<div class="pdf-empty">No risk history points are available for this attempt yet.</div>`;
  }

  const width = 700;
  const height = 220;
  const padLeft = 42;
  const padRight = 18;
  const padTop = 18;
  const padBottom = 36;
  const plotWidth = width - padLeft - padRight;
  const plotHeight = height - padTop - padBottom;
  const maxIndex = Math.max(1, points.length - 1);
  const toX = (index) => padLeft + (plotWidth * index) / maxIndex;
  const toY = (score) => padTop + plotHeight - plotHeight * Math.max(0, Math.min(1, Number(score || 0)));
  const path = points.map((point, index) => `${index === 0 ? "M" : "L"} ${toX(index)} ${toY(point.score)}`).join(" ");
  const markers = points.map((point, index) => `<circle cx="${toX(index)}" cy="${toY(point.score)}" r="4.5" fill="#ff7b57" stroke="#fff7f1" stroke-width="1.4" />`).join("");
  const labels = points.map((point, index) => {
    if (!(index === 0 || index === points.length - 1 || index === Math.floor((points.length - 1) / 2))) return "";
    return `<text x="${toX(index)}" y="${height - 12}" text-anchor="middle" font-size="11" fill="#475569">${escapeHtml(point.label || formatChartTime(point.timestamp, "Time"))}</text>`;
  }).join("");

  return `
    <svg width="100%" viewBox="0 0 ${width} ${height}" role="img" aria-label="Risk history chart">
      <rect x="${padLeft}" y="${padTop}" width="${plotWidth}" height="${plotHeight * 0.3}" fill="rgba(255,77,109,0.08)" />
      <rect x="${padLeft}" y="${padTop + plotHeight * 0.3}" width="${plotWidth}" height="${plotHeight * 0.3}" fill="rgba(255,183,3,0.08)" />
      <rect x="${padLeft}" y="${padTop + plotHeight * 0.6}" width="${plotWidth}" height="${plotHeight * 0.4}" fill="rgba(34,197,94,0.07)" />
      <line x1="${padLeft}" y1="${padTop}" x2="${padLeft}" y2="${padTop + plotHeight}" stroke="#cbd5e1" stroke-width="1" />
      <line x1="${padLeft}" y1="${padTop + plotHeight}" x2="${padLeft + plotWidth}" y2="${padTop + plotHeight}" stroke="#cbd5e1" stroke-width="1" />
      <line x1="${padLeft}" y1="${padTop + plotHeight * 0.3}" x2="${padLeft + plotWidth}" y2="${padTop + plotHeight * 0.3}" stroke="#e2e8f0" stroke-dasharray="4 4" stroke-width="0.8" />
      <line x1="${padLeft}" y1="${padTop + plotHeight * 0.6}" x2="${padLeft + plotWidth}" y2="${padTop + plotHeight * 0.6}" stroke="#e2e8f0" stroke-dasharray="4 4" stroke-width="0.8" />
      <path d="${path}" fill="none" stroke="#ff7b57" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
      ${markers}
      <text x="8" y="${padTop + 6}" font-size="11" fill="#475569">1.00</text>
      <text x="8" y="${padTop + plotHeight * 0.32}" font-size="11" fill="#475569">0.70</text>
      <text x="8" y="${padTop + plotHeight * 0.62}" font-size="11" fill="#475569">0.40</text>
      <text x="8" y="${padTop + plotHeight + 4}" font-size="11" fill="#475569">0.00</text>
      ${labels}
    </svg>
  `;
}

function buildDonutStyle(segments) {
  let cursor = 0;
  const stops = segments.map((segment) => {
    const start = cursor;
    cursor += segment.value;
    return `${segment.color} ${start}% ${cursor}%`;
  });
  return { background: `conic-gradient(${stops.join(", ")})` };
}

function formatPercent(part, total) {
  if (!total) return "0.0%";
  return `${((part / total) * 100).toFixed(1)}%`;
}

function compareIso(a, b) {
  const ta = a ? new Date(a).getTime() : 0;
  const tb = b ? new Date(b).getTime() : 0;
  return ta - tb;
}

function dedupeLatestByAttemptId(rows) {
  const map = new Map();
  for (const row of rows || []) {
    const attemptId = row?.attempt_id;
    if (!attemptId) continue;
    const prev = map.get(attemptId);
    if (!prev) {
      map.set(attemptId, row);
      continue;
    }

    const nextTs = row?.timestamp;
    const prevTs = prev?.timestamp;
    if (compareIso(prevTs, nextTs) <= 0) {
      map.set(attemptId, { ...prev, ...row });
    }
  }
  return Array.from(map.values()).sort((a, b) => compareIso(a?.timestamp, b?.timestamp));
}

function getStoredToken() {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function setStoredToken(token) {
  try {
    if (!token) window.localStorage.removeItem(TOKEN_KEY);
    else window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // ignore
  }
}

function getStoredSetting(key, fallback) {
  try {
    const value = window.localStorage.getItem(key);
    return value ?? fallback;
  } catch {
    return fallback;
  }
}

function setStoredSetting(key, value) {
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // ignore
  }
}

async function authJsonFetch(url, { token, method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 12000);
  try {
    const res = await fetch(url, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    const text = await res.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = { detail: text };
      }
    }
    return { ok: res.ok, status: res.status, data };
  } catch {
    return {
      ok: false,
      status: 0,
      data: { detail: "Backend is unavailable or waking up. Please retry shortly." },
    };
  } finally {
    window.clearTimeout(timeoutId);
  }
}

const PUBLIC_DEMO_ASSESSMENTS = [
  {
    id: "assessment_coding_01",
    name: "Coding Assessment",
    durationMinutes: 14,
    icon: Code2,
    intro: "Demonstrate implementation thinking, debugging discipline, and short-form coding judgement.",
    questions: [
      {
        id: "code_q1",
        title: "Python Data Handling",
        prompt: "Describe how you would safely parse a CSV file with missing values and normalize the rows before analysis.",
        type: "textarea",
        placeholder: "Outline the parsing steps, validation approach, and how you would handle missing fields.",
      },
      {
        id: "code_q2",
        title: "Algorithm Tradeoff",
        prompt: "A teammate proposes a nested loop solution over 100k rows. What would you review before approving it?",
        type: "textarea",
        placeholder: "Discuss time complexity, memory, edge cases, and operational implications.",
      },
      {
        id: "code_q3",
        title: "Function Signature",
        prompt: "Write a concise function signature for validating and scoring a browser telemetry event batch.",
        type: "input",
        placeholder: "def score_batch(events: list[dict], attempt_id: str) -> dict:",
      },
    ],
  },
  {
    id: "assessment_reasoning_01",
    name: "Logical Reasoning",
    durationMinutes: 12,
    icon: BrainCircuit,
    intro: "Assess structured reasoning, prioritization, and concise decision making under time pressure.",
    questions: [
      {
        id: "reason_q1",
        title: "Incident Triage",
        prompt: "Three assessment integrity alerts arrive at once. How would you prioritize them and why?",
        type: "textarea",
        placeholder: "Explain your triage sequence and the signals that would drive escalation.",
      },
      {
        id: "reason_q2",
        title: "Evidence Review",
        prompt: "What combination of signals would make a MEDIUM-risk case worth manual review instead of automatic clearance?",
        type: "textarea",
        placeholder: "Discuss ambiguity, evidence correlation, and reviewer judgement.",
      },
      {
        id: "reason_q3",
        title: "Decision Note",
        prompt: "Summarize a final reviewer decision in one sentence for the audit trail.",
        type: "input",
        placeholder: "Example: Repeated focus loss and paste recovery justified escalation for manual action.",
      },
    ],
  },
  {
    id: "assessment_frontend_01",
    name: "Frontend Debugging",
    durationMinutes: 13,
    icon: Bug,
    intro: "Evaluate debugging clarity, frontend diagnosis, and risk-based prioritization of UI issues.",
    questions: [
      {
        id: "front_q1",
        title: "Layout Bug",
        prompt: "A dashboard table is clipping its action buttons on 1440px screens. What would you inspect first?",
        type: "textarea",
        placeholder: "Mention containers, overflow, min-width, flex/grid constraints, and responsive checks.",
      },
      {
        id: "front_q2",
        title: "State Bug",
        prompt: "A reviewer page shows stale risk levels after an action. How would you isolate the source of truth issue?",
        type: "textarea",
        placeholder: "Describe how you would trace state, API payloads, and refresh/update timing.",
      },
      {
        id: "front_q3",
        title: "Quick Fix Note",
        prompt: "Write a short engineering note describing the likely cause of a blank export popup.",
        type: "input",
        placeholder: "Example: popup opened before printable content finished rendering.",
      },
    ],
  },
  {
    id: "assessment_sql_01",
    name: "SQL Basics",
    durationMinutes: 11,
    icon: DatabaseZap,
    intro: "Measure database reasoning, filtering logic, and confidence with simple analytics tasks.",
    questions: [
      {
        id: "sql_q1",
        title: "Query Intent",
        prompt: "How would you retrieve the latest review case per attempt without showing stale statuses?",
        type: "textarea",
        placeholder: "Describe the SQL shape or logic you would use.",
      },
      {
        id: "sql_q2",
        title: "Operational Metric",
        prompt: "How would you count only actionable HIGH-risk cases for a dashboard KPI?",
        type: "textarea",
        placeholder: "Explain the filters or case lifecycle rules you would apply.",
      },
      {
        id: "sql_q3",
        title: "Index Hint",
        prompt: "Name one index that would help a review queue ordered by last activity.",
        type: "input",
        placeholder: "Example: index on (status, updated_at desc)",
      },
    ],
  },
];

function slugify(value) {
  return String(value || "")
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "") || "candidate";
}

function createDemoAttemptId(name, assessmentId) {
  const candidateSlug = slugify(name).slice(0, 18);
  const assessmentSlug = slugify(assessmentId).replace(/^assessment_/, "").slice(0, 16);
  return `demo_public_${candidateSlug}_${assessmentSlug}_${Date.now()}`;
}

function createDemoCandidateId(name, email) {
  const source = slugify(email || name || "demo_candidate");
  return `candidate_${source}_${Math.floor(Date.now() / 1000)}`;
}

function PublicDemoPage({ apiBaseUrl }) {
  const [consented, setConsented] = useState(false);
  const [candidateName, setCandidateName] = useState("");
  const [candidateEmail, setCandidateEmail] = useState("");
  const [assessmentId, setAssessmentId] = useState(PUBLIC_DEMO_ASSESSMENTS[0].id);
  const [stage, setStage] = useState("welcome");
  const [attemptId, setAttemptId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [answers, setAnswers] = useState({});
  const [questionIndex, setQuestionIndex] = useState(0);
  const [timeRemaining, setTimeRemaining] = useState(PUBLIC_DEMO_ASSESSMENTS[0].durationMinutes * 60);
  const [telemetryStatus, setTelemetryStatus] = useState("Telemetry inactive");
  const [telemetryError, setTelemetryError] = useState("");
  const [submittedAt, setSubmittedAt] = useState("");

  const selectedAssessment = useMemo(
    () => PUBLIC_DEMO_ASSESSMENTS.find((item) => item.id === assessmentId) || PUBLIC_DEMO_ASSESSMENTS[0],
    [assessmentId],
  );
  const currentQuestion = selectedAssessment.questions[questionIndex] || selectedAssessment.questions[0];
  const totalQuestions = selectedAssessment.questions.length;
  const progressPercent = totalQuestions > 0 ? ((questionIndex + 1) / totalQuestions) * 100 : 0;

  useEffect(() => {
    if (stage !== "assessment") {
      setTimeRemaining(selectedAssessment.durationMinutes * 60);
    }
  }, [selectedAssessment.durationMinutes, stage]);

  useEffect(() => {
    if (stage !== "assessment") return undefined;
    const intervalId = window.setInterval(() => {
      setTimeRemaining((prev) => {
        if (prev <= 1) {
          window.clearInterval(intervalId);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => window.clearInterval(intervalId);
  }, [stage]);

  useEffect(() => {
    if (stage !== "assessment" || !currentQuestion || typeof window === "undefined" || !window.RiskTelemetry) return undefined;
    window.RiskTelemetry.enterQuestion(currentQuestion.id);
    return () => {
      try {
        window.RiskTelemetry.leaveQuestion(currentQuestion.id);
      } catch {
        // ignore SDK cleanup issues
      }
    };
  }, [currentQuestion, stage]);

  useEffect(() => {
    if (timeRemaining !== 0 || stage !== "assessment") return;
    void handleSubmit();
  }, [stage, timeRemaining]);

  useEffect(() => {
    return () => {
      if (typeof window !== "undefined" && window.RiskTelemetry) {
        try {
          window.RiskTelemetry.destroy();
        } catch {
          // ignore
        }
      }
    };
  }, []);

  const handleStart = useCallback(() => {
    if (!candidateName.trim() || !candidateEmail.trim()) {
      setTelemetryError("Enter a candidate name and email to begin the demo assessment.");
      return;
    }
    if (!consented) {
      setTelemetryError("Candidate consent is required before telemetry can begin.");
      return;
    }

    const nextAttemptId = createDemoAttemptId(candidateName, selectedAssessment.id);
    const nextCandidateId = createDemoCandidateId(candidateName, candidateEmail);
    setAttemptId(nextAttemptId);
    setCandidateId(nextCandidateId);
    setAnswers({});
    setQuestionIndex(0);
    setSubmittedAt("");
    setTelemetryError("");

    try {
      if (!window.RiskTelemetry) {
        setTelemetryError("Telemetry SDK is unavailable in this build.");
        return;
      }
      try {
        window.RiskTelemetry.destroy();
      } catch {
        // ignore prior session cleanup failures
      }
      window.RiskTelemetry.init({
        baseUrl: apiBaseUrl,
        attemptId: nextAttemptId,
        candidateId: nextCandidateId,
        candidateName: candidateName.trim(),
        candidateEmail: candidateEmail.trim(),
        assessmentId: selectedAssessment.id,
        assessmentName: selectedAssessment.name,
        idleTimeoutMs: 25000,
        typingStopMs: 1200,
        devMode: false,
      });
      window.RiskTelemetry.startExam();
      setTelemetryStatus("Telemetry active");
      setStage("assessment");
    } catch (error) {
      setTelemetryError(error instanceof Error ? error.message : "Unable to initialize assessment telemetry.");
    }
  }, [apiBaseUrl, assessmentId, candidateEmail, candidateName, consented, selectedAssessment.id, selectedAssessment.name]);

  const handleAnswerChange = useCallback((questionId, value) => {
    setAnswers((prev) => ({ ...prev, [questionId]: value }));
    try {
      window.RiskTelemetry?.trackAnswerChange(questionId, value);
    } catch (error) {
      setTelemetryError(error instanceof Error ? error.message : "Unable to record typing telemetry.");
    }
  }, []);

  const handleSubmit = useCallback(async () => {
    try {
      window.RiskTelemetry?.endExam();
      setTelemetryStatus("Assessment submitted");
    } catch (error) {
      setTelemetryError(error instanceof Error ? error.message : "Unable to submit telemetry cleanly.");
    }
    setSubmittedAt(new Date().toISOString());
    setStage("submitted");
  }, []);

  const handleRestart = useCallback(() => {
    try {
      window.RiskTelemetry?.destroy();
    } catch {
      // ignore
    }
    setTelemetryStatus("Telemetry inactive");
    setTelemetryError("");
    setAttemptId("");
    setCandidateId("");
    setAnswers({});
    setQuestionIndex(0);
    setSubmittedAt("");
    setStage("welcome");
    setTimeRemaining(selectedAssessment.durationMinutes * 60);
  }, [selectedAssessment.durationMinutes]);

  return (
    <div className="public-demo-shell">
      <div className="public-demo-page">
        <section className="public-demo-hero">
          <div className="public-demo-hero-copy">
            <span className="page-kicker">PUBLIC DEMO</span>
            <h1>Experience a live ProctorIQ assessment</h1>
            <p>
              ProctorIQ analyzes privacy-preserving behavioral telemetry in real time to produce deterministic,
              explainable assessment integrity signals without webcam, audio, or content capture.
            </p>
          </div>
          <div className="public-demo-hero-actions">
            <a className="demo-link-btn" href="/">Open Reviewer Console</a>
          </div>
        </section>

        <section className="public-demo-grid">
          <div className="public-demo-main">
            {stage === "welcome" && (
              <section className="public-demo-card">
                <div className="public-demo-card-head">
                  <h2>Welcome</h2>
                  <span className="demo-status-chip neutral">Metadata-only telemetry</span>
                </div>
                <div className="public-demo-copy-block">
                  <p>
                    This interactive demo sends the same assessment telemetry used by the reviewer console: answer timing,
                    focus changes, clipboard metadata, idle recovery, and typing behavior patterns.
                  </p>
                  <ul className="public-demo-bullets">
                    <li>No webcam or audio capture</li>
                    <li>No answer text stored</li>
                    <li>No clipboard contents stored</li>
                    <li>No screen recording</li>
                    <li>Deterministic and explainable risk analysis</li>
                  </ul>
                </div>
                <div className="public-demo-consent">
                  <label className="public-demo-checkbox">
                    <input checked={consented} onChange={(event) => setConsented(event.target.checked)} type="checkbox" />
                    <span>I understand this demo collects behavioral telemetry metadata and does not capture sensitive content.</span>
                  </label>
                </div>
                <div className="public-demo-card-head compact">
                  <h3>Candidate Setup</h3>
                </div>
                <div className="public-demo-form-grid">
                  <label>
                    <span>Candidate Name</span>
                    <input value={candidateName} onChange={(event) => setCandidateName(event.target.value)} placeholder="Aarav Menon" />
                  </label>
                  <label>
                    <span>Email</span>
                    <input value={candidateEmail} onChange={(event) => setCandidateEmail(event.target.value)} placeholder="aarav.menon@example.com" />
                  </label>
                </div>
                <div className="public-demo-assessment-grid">
                  {PUBLIC_DEMO_ASSESSMENTS.map((assessment) => {
                    const Icon = assessment.icon;
                    const active = assessment.id === assessmentId;
                    return (
                      <button
                        className={`public-demo-assessment-card ${active ? "active" : ""}`}
                        key={assessment.id}
                        onClick={() => setAssessmentId(assessment.id)}
                        type="button"
                      >
                        <span className="public-demo-assessment-icon"><Icon size={18} /></span>
                        <strong>{assessment.name}</strong>
                        <small>{assessment.intro}</small>
                      </button>
                    );
                  })}
                </div>
                {telemetryError ? <div className="workflow-message workflow-message-warning">{telemetryError}</div> : null}
                <div className="public-demo-actions">
                  <button className="workflow-action-btn primary" onClick={handleStart} type="button">
                    <Play size={16} />
                    Begin Assessment
                  </button>
                </div>
              </section>
            )}

            {stage === "assessment" && (
              <section className="public-demo-card">
                <div className="public-demo-card-head">
                  <div>
                    <span className="report-section-kicker">{selectedAssessment.name}</span>
                    <h2>{currentQuestion.title}</h2>
                  </div>
                  <span className="demo-status-chip active">{telemetryStatus}</span>
                </div>
                <div className="public-demo-progress-head">
                  <div>
                    <strong>Question {questionIndex + 1} of {totalQuestions}</strong>
                    <p>{selectedAssessment.intro}</p>
                  </div>
                  <div className="public-demo-timer">
                    <TimerReset size={16} />
                    <strong>{formatDuration(timeRemaining)}</strong>
                  </div>
                </div>
                <div className="public-demo-progress-bar">
                  <span style={{ width: `${progressPercent}%` }}></span>
                </div>
                <div className="public-demo-question">
                  <p>{currentQuestion.prompt}</p>
                  {currentQuestion.type === "textarea" ? (
                    <textarea
                      rows={6}
                      value={answers[currentQuestion.id] || ""}
                      onChange={(event) => handleAnswerChange(currentQuestion.id, event.target.value)}
                      placeholder={currentQuestion.placeholder}
                    />
                  ) : (
                    <input
                      value={answers[currentQuestion.id] || ""}
                      onChange={(event) => handleAnswerChange(currentQuestion.id, event.target.value)}
                      placeholder={currentQuestion.placeholder}
                    />
                  )}
                </div>
                <div className="public-demo-actions spread">
                  <button
                    className="workflow-action-btn"
                    disabled={questionIndex === 0}
                    onClick={() => setQuestionIndex((prev) => Math.max(0, prev - 1))}
                    type="button"
                  >
                    Previous
                  </button>
                  <div className="public-demo-inline-hint">
                    <Lock size={14} />
                    Try copy/paste, tab switching, short idle gaps, or rapid answering to see reviewer-side telemetry evolve.
                  </div>
                  {questionIndex < totalQuestions - 1 ? (
                    <button
                      className="workflow-action-btn primary"
                      onClick={() => setQuestionIndex((prev) => Math.min(totalQuestions - 1, prev + 1))}
                      type="button"
                    >
                      Next
                    </button>
                  ) : (
                    <button className="workflow-action-btn warning" onClick={() => void handleSubmit()} type="button">
                      Submit Assessment
                    </button>
                  )}
                </div>
              </section>
            )}

            {stage === "submitted" && (
              <section className="public-demo-card">
                <div className="public-demo-card-head">
                  <h2>Assessment submitted</h2>
                  <span className="demo-status-chip success">Telemetry delivered</span>
                </div>
                <div className="public-demo-copy-block">
                  <p>
                    The assessment session has been submitted. Reviewer dashboards can now inspect the attempt,
                    generated evidence, and real-time risk history.
                  </p>
                </div>
                <div className="public-demo-summary-grid">
                  <div className="public-demo-summary-item">
                    <span>Attempt ID</span>
                    <strong>{attemptId}</strong>
                  </div>
                  <div className="public-demo-summary-item">
                    <span>Candidate</span>
                    <strong>{candidateName || "—"}</strong>
                  </div>
                  <div className="public-demo-summary-item">
                    <span>Assessment</span>
                    <strong>{selectedAssessment.name}</strong>
                  </div>
                  <div className="public-demo-summary-item">
                    <span>Submitted</span>
                    <strong>{formatDateTime(submittedAt)}</strong>
                  </div>
                </div>
                <div className="public-demo-actions">
                  <button className="workflow-action-btn primary" onClick={handleRestart} type="button">
                    <CheckSquare size={16} />
                    Start Another Demo Attempt
                  </button>
                  <a className="demo-link-btn secondary" href="/">
                    Open Reviewer Console
                  </a>
                </div>
              </section>
            )}
          </div>

          <aside className="public-demo-sidebar">
            <section className="public-demo-card compact">
              <div className="public-demo-card-head compact">
                <h3>What telemetry is collected?</h3>
              </div>
              <ul className="public-demo-bullets compact">
                <li>Focus loss and visibility changes</li>
                <li>Clipboard copy/paste metadata</li>
                <li>Question timing and answer change timing</li>
                <li>Idle recovery and typing rhythm metadata</li>
                <li>Correlated suspicious event sequences</li>
              </ul>
            </section>

            <section className="public-demo-card compact">
              <div className="public-demo-card-head compact">
                <h3>Privacy notice</h3>
              </div>
              <ul className="public-demo-bullets compact">
                <li>No webcam monitoring</li>
                <li>No microphone recording</li>
                <li>No answer text stored</li>
                <li>No clipboard contents stored</li>
                <li>No screen recording</li>
              </ul>
            </section>

            <section className="public-demo-card compact">
              <div className="public-demo-card-head compact">
                <h3>Demo session</h3>
              </div>
              <div className="public-demo-summary-grid single">
                <div className="public-demo-summary-item">
                  <span>Telemetry Status</span>
                  <strong>{telemetryStatus}</strong>
                </div>
                <div className="public-demo-summary-item">
                  <span>Candidate ID</span>
                  <strong>{candidateId || "Generated on start"}</strong>
                </div>
                <div className="public-demo-summary-item">
                  <span>Backend</span>
                  <strong>{apiBaseUrl}</strong>
                </div>
              </div>
              {telemetryError ? <div className="workflow-message workflow-message-warning">{telemetryError}</div> : null}
            </section>
          </aside>
        </section>
      </div>
    </div>
  );
}

export default function App() {
  const isPublicDemoRoute = typeof window !== "undefined" && window.location.pathname.replace(/\/+$/, "") === "/demo";
  const [page, setPage] = useState("dashboard");
  const [logs, setLogs] = useState([]);
  const [cases, setCases] = useState([]);
  const [dashboardSummary, setDashboardSummary] = useState(null);
  const [reviewQueuePayload, setReviewQueuePayload] = useState(null);
  const [caseAccessError, setCaseAccessError] = useState("");
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [wsState, setWsState] = useState("paused");
  const [searchTerm, setSearchTerm] = useState("");
  const [riskFilter, setRiskFilter] = useState({ LOW: true, MEDIUM: true, HIGH: true });

  const [token, setToken] = useState(() => getStoredToken());
  const [currentUser, setCurrentUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(false);
  const [authError, setAuthError] = useState("");
  const [themeMode, setThemeMode] = useState(() => getStoredSetting("riskintel_theme_mode", "dark"));
  const [autoRefreshInterval, setAutoRefreshInterval] = useState(() => getStoredSetting("riskintel_auto_refresh", "30"));
  const [liveUpdatesEnabled, setLiveUpdatesEnabled] = useState(() => getStoredSetting("riskintel_live_updates", "true") === "true");
  const [notificationsEnabled, setNotificationsEnabled] = useState(() => getStoredSetting("riskintel_notifications", "true") === "true");

  useEffect(() => {
    document.documentElement.dataset.theme = isPublicDemoRoute ? "dark" : themeMode;
    if (!isPublicDemoRoute) {
      setStoredSetting("riskintel_theme_mode", themeMode);
    }
  }, [isPublicDemoRoute, themeMode]);

  const logout = useCallback(() => {
    window.clearTimeout(reviewerRefreshTimerId);
    reviewerRefreshTimerId = null;
    setStoredToken(null);
    setToken(null);
    setCurrentUser(null);
    setCases([]);
    setReviewQueuePayload(null);
    setSelected(null);
    setPage("dashboard");
  }, []);

  const fetchLogs = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch(API_URL, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (res.status === 401) {
        logout();
        return;
      }
      const json = await res.json();
      const data = json.data || [];
      const latest = dedupeLatestByAttemptId(data);
      setLogs(latest);
      setSelected((prev) => prev || (latest.length ? latest[latest.length - 1] : null));
    } catch (err) {
      console.error("Failed to fetch logs:", err);
    } finally {
      setLoading(false);
    }
  }, [logout, token]);

  const fetchCases = useCallback(async () => {
    try {
      const result = await authJsonFetch(CASES_URL, { token });
      if (!result.ok) {
        if (result.status === 401) {
          logout();
          return;
        }
        if (result.status === 403) {
          setCaseAccessError("Your role can sign in, but reviewer workflow access is restricted.");
          setCases([]);
          return;
        }
        return;
      }
      setCaseAccessError("");
      setCases(Array.isArray(result.data?.data) ? result.data.data : []);
    } catch (err) {
      console.error("Failed to fetch cases:", err);
    }
  }, [logout, token]);

  const fetchDashboardSummary = useCallback(async () => {
    try {
      const result = await authJsonFetch(`${DASHBOARD_SUMMARY_URL}?recent_hours=${RECENT_DEMO_HOURS}`, { token });
      if (!result.ok) {
        if (result.status === 401) {
          logout();
          return;
        }
        setDashboardSummary(null);
        return;
      }
      setDashboardSummary(result.data?.data || null);
    } catch (err) {
      console.error("Failed to fetch dashboard summary:", err);
      setDashboardSummary(null);
    }
  }, [logout, token]);

  const fetchReviewQueue = useCallback(async () => {
    try {
      const result = await authJsonFetch(`${REVIEW_QUEUE_URL}?recent_hours=${RECENT_DEMO_HOURS}`, { token });
      if (!result.ok) {
        if (result.status === 401) {
          logout();
          return;
        }
        if (result.status === 403) {
          setCaseAccessError("Your role can sign in, but reviewer workflow access is restricted.");
          setReviewQueuePayload(null);
          return;
        }
        setReviewQueuePayload(null);
        return;
      }
      setCaseAccessError("");
      setReviewQueuePayload(result.data || null);
    } catch (err) {
      console.error("Failed to fetch review queue:", err);
      setReviewQueuePayload(null);
    }
  }, [logout, token]);

  const scheduleReviewerRefresh = useCallback((delayMs = 1200) => {
    if (!token || reviewerRefreshTimerId !== null) return;
    reviewerRefreshTimerId = window.setTimeout(() => {
      reviewerRefreshTimerId = null;
      void fetchCases();
      void fetchDashboardSummary();
      void fetchReviewQueue();
    }, delayMs);
  }, [fetchCases, fetchDashboardSummary, fetchReviewQueue, token]);

  const performCaseAction = useCallback(async ({ caseId, endpoint, body }) => {
    if (!caseId) {
      return { ok: false, error: "Missing case id" };
    }
    const result = await authJsonFetch(`${CASES_URL}/${caseId}/${endpoint}`, {
      token,
      method: "POST",
      body,
    });
    if (!result.ok) {
      if (result.status === 401) logout();
      return {
        ok: false,
        error: result?.data?.detail || result?.data?.message || "Case action failed",
      };
    }
    await fetchCases();
    await fetchDashboardSummary();
    await fetchReviewQueue();
    return { ok: true, data: result.data };
  }, [fetchCases, fetchDashboardSummary, fetchReviewQueue, logout, token]);

  const login = useCallback(async ({ email, password }) => {
    setAuthError("");
    setAuthLoading(true);
    try {
      const result = await authJsonFetch(`${AUTH_URL}/login`, {
        method: "POST",
        body: { email, password },
      });
      if (!result.ok) {
        const msg = result?.data?.detail || result?.data?.message || "Login failed";
        setAuthError(String(msg));
        return;
      }
      const nextToken = result.data?.access_token;
      const user = result.data?.user;
      if (!nextToken || !user) {
        setAuthError("Login failed: missing token");
        return;
      }
      setStoredToken(nextToken);
      setToken(nextToken);
      setCurrentUser(user);
    } catch (err) {
      setAuthError("Login failed");
      console.error(err);
    } finally {
      setAuthLoading(false);
    }
  }, []);

  useEffect(() => {
    setStoredSetting("riskintel_auto_refresh", autoRefreshInterval);
  }, [autoRefreshInterval]);

  useEffect(() => {
    setStoredSetting("riskintel_live_updates", liveUpdatesEnabled);
  }, [liveUpdatesEnabled]);

  useEffect(() => {
    setStoredSetting("riskintel_notifications", notificationsEnabled);
  }, [notificationsEnabled]);

  useEffect(() => {
    let cancelled = false;
    async function loadMe() {
      if (!token) return;
      setAuthLoading(true);
      try {
        const result = await authJsonFetch(`${AUTH_URL}/me`, { token });
        if (!result.ok) {
          if (!cancelled) logout();
          return;
        }
        if (!cancelled) setCurrentUser(result.data);
      } catch {
        if (!cancelled) logout();
      } finally {
        if (!cancelled) setAuthLoading(false);
      }
    }
    void loadMe();
    return () => {
      cancelled = true;
    };
  }, [logout, token]);

  useEffect(() => {
    if (!token) return;
    const timeoutId = window.setTimeout(() => {
      if (token) void fetchLogs();
      scheduleReviewerRefresh(0);
    }, 0);
    if (!liveUpdatesEnabled) {
      setWsConnected(false);
      setWsState("paused");
      return () => {
        window.clearTimeout(timeoutId);
      };
    }
    let ws;
    let reconnectTimerId;
    let disposed = false;

    const scheduleReconnect = () => {
      if (disposed || !liveUpdatesEnabled) return;
      setWsState("reconnecting");
      reconnectTimerId = window.setTimeout(() => {
        if (!disposed) {
          connect();
        }
      }, 2500);
    };

    const connect = () => {
      try {
        setWsState("reconnecting");
        ws = new WebSocket(WS_URL);
      } catch (error) {
        console.error("WS connection error:", error);
        setWsConnected(false);
        scheduleReconnect();
        return;
      }

      ws.onopen = () => {
        setWsConnected(true);
        setWsState("connected");
      };
      ws.onerror = () => {
        setWsConnected(false);
        setWsState("reconnecting");
      };
      ws.onclose = () => {
        setWsConnected(false);
        if (!disposed) {
          scheduleReconnect();
        }
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type !== "risk_update") return;

          const newLog = {
            timestamp: new Date().toISOString(),
            attempt_id: data.attempt_id,
            candidate_id: data.candidate_id,
            candidate_name: data.candidate_name,
            candidate_email: data.candidate_email,
            assessment_id: data.assessment_id,
            assessment_name: data.assessment_name,
            risk: data.risk,
            confidence: data.confidence,
            combined_score: data.combined_score,
            explanation: data.explanation,
            explanation_text: data.explanation,
            latest_event: data.latest_event,
            timeline_point: data.timeline_point,
            event_count: data.event_count,
            features: data.features || {},
            signals: data.signals || {},
          };

          setLogs((prev) => {
            const exists = prev.some((x) => x.attempt_id === newLog.attempt_id);
            if (exists) {
              return prev.map((x) => (x.attempt_id === newLog.attempt_id ? { ...x, ...newLog } : x));
            }
            return [...prev, newLog].slice(-500);
          });

          setSelected((prev) => (prev?.attempt_id === newLog.attempt_id ? { ...prev, ...newLog } : prev || newLog));
          scheduleReviewerRefresh();
        } catch (err) {
          console.error("WS parse error:", err);
        }
      };
    };

    connect();

    return () => {
      disposed = true;
      window.clearTimeout(timeoutId);
      window.clearTimeout(reconnectTimerId);
      window.clearTimeout(reviewerRefreshTimerId);
      reviewerRefreshTimerId = null;
      setWsState("paused");
      if (ws && ws.readyState < WebSocket.CLOSING) {
        ws.close();
      }
    };
  }, [fetchLogs, liveUpdatesEnabled, scheduleReviewerRefresh, token]);

  useEffect(() => {
    if (!token) return;
    const seconds = Number(autoRefreshInterval);
    if (!Number.isFinite(seconds) || seconds <= 0) return;
    const intervalId = window.setInterval(() => {
      void fetchLogs();
      void fetchCases();
      void fetchDashboardSummary();
      void fetchReviewQueue();
    }, seconds * 1000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [autoRefreshInterval, fetchCases, fetchDashboardSummary, fetchLogs, fetchReviewQueue, token]);

  const caseByAttemptId = useMemo(() => {
    return Object.fromEntries(cases.map((item) => [item.attempt_id, item]));
  }, [cases]);

  const selectedCase = useMemo(() => {
    if (!selected?.attempt_id) return null;
    return caseByAttemptId[selected.attempt_id] || null;
  }, [caseByAttemptId, selected?.attempt_id]);

  const filteredLogs = useMemo(() => {
    return logs.filter((x) => {
      const risk = x.risk || "LOW";
      const matchesRisk = riskFilter[risk];
      const haystack = `${x.attempt_id || ""} ${x.candidate_name || ""} ${x.candidate_email || ""}`.toLowerCase();
      const matchesSearch = haystack.includes(searchTerm.toLowerCase());
      return matchesRisk && matchesSearch;
    });
  }, [logs, riskFilter, searchTerm]);

  const stats = useMemo(() => {
    const total = filteredLogs.length;
    const high = filteredLogs.filter((x) => x.risk === "HIGH").length;
    const medium = filteredLogs.filter((x) => x.risk === "MEDIUM").length;
    const low = filteredLogs.filter((x) => x.risk === "LOW").length;
    const ongoing = filteredLogs.filter((x) => getExamStatus(x) === "ONGOING").length;
    const completed = filteredLogs.filter((x) => getExamStatus(x) === "COMPLETED").length;
    const avgConfidence = filteredLogs.reduce((sum, x) => sum + Number(x.confidence || 0), 0) / (total || 1);
    return { total, high, medium, low, ongoing, completed, avgConfidence };
  }, [filteredLogs]);
  const wsStatusLabel = wsState === "connected"
    ? "Live stream connected"
    : wsState === "reconnecting"
      ? "Live stream reconnecting"
      : "Live stream paused";
  const wsStatusDetail = wsState === "connected"
    ? "Receiving events"
    : wsState === "reconnecting"
      ? "REST polling continues while the stream reconnects"
      : "Live updates are paused";

  if (isPublicDemoRoute) {
    return <PublicDemoPage apiBaseUrl={API_BASE_URL} />;
  }

  if (!token) {
    return (
      <div className="login-shell">
        <LoginPage onLogin={login} loading={authLoading} error={authError} />
      </div>
    );
  }

  if (authLoading && !currentUser) {
    return (
      <div className="login-shell">
        <div className="panel empty-report">
          <ShieldAlert size={42} />
          <h3>Loading sessionâ€¦</h3>
          <p>Verifying your access token.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-top">
          <div className="sidebar-brand">
            <ProctorIQLogo className="brand-mark brand-mark-proctoriq" />
            <div className="sidebar-brand-copy">
              <h2>ProctorIQ</h2>
              <p>Assessment Integrity Console</p>
            </div>
          </div>
        </div>

        <nav className="nav">
          <button className={page === "dashboard" ? "active" : ""} onClick={() => setPage("dashboard")}>
            <Home size={18} />
            <span>Command Center</span>
          </button>
          <button className={page === "queue" ? "active" : ""} onClick={() => setPage("queue")}>
            <ListChecks size={18} />
            <span>Review Queue</span>
          </button>
          <button className={page === "report" ? "active" : ""} onClick={() => setPage("report")}>
            <ShieldAlert size={18} />
            <span>Investigation</span>
          </button>
          <button className={page === "settings" ? "active" : ""} onClick={() => setPage("settings")}>
            <Settings size={18} />
            <span>Settings</span>
          </button>
        </nav>

        <div className="sidebar-footer">
          <div className={`connection-card ${wsConnected ? "online" : "offline"}`}>
            <div className="connection-card-row">
              <span className="pulse"></span>
              <strong>{wsStatusLabel}</strong>
            </div>
            <small>{wsStatusDetail}</small>
          </div>

          {currentUser ? (
            <div className="sidebar-user-panel">
              <button className="sidebar-user-card" type="button">
                <span className="sidebar-user-avatar">{getInitials(currentUser.full_name || currentUser.email)}</span>
                <span className="sidebar-user-copy">
                  <strong>{currentUser.full_name || currentUser.email}</strong>
                  <small>{currentUser.role}</small>
                </span>
                <ChevronDown size={16} />
              </button>
              <button className="sidebar-logout-btn" onClick={logout} type="button">
                <ArrowRight size={16} /> Logout
              </button>
            </div>
          ) : null}
        </div>
      </aside>

      <main className="main">
        {page === "dashboard" && (
          <DashboardPage
            logs={logs}
            cases={cases}
            stats={stats}
            dashboardSummary={dashboardSummary}
            currentUser={currentUser}
            setSelected={setSelected}
            setPage={setPage}
            loading={loading}
            wsConnected={wsConnected}
            wsState={wsState}
          />
        )}

        {page === "queue" && (
          <QueuePage
            logs={logs}
            cases={cases}
            reviewQueuePayload={reviewQueuePayload}
            caseByAttemptId={caseByAttemptId}
            caseAccessError={caseAccessError}
            currentUser={currentUser}
            onRefresh={() => {
              void fetchLogs();
              void fetchCases();
              void fetchReviewQueue();
            }}
            setSelected={setSelected}
            setPage={setPage}
          />
        )}

        {page === "report" && (
          <ReportPage
            selected={selected}
            selectedCase={selectedCase}
            caseAccessError={caseAccessError}
            token={token}
            currentUser={currentUser}
            onUnauthorized={logout}
            onCaseAction={performCaseAction}
            onBack={() => setPage("queue")}
          />
        )}

        {page === "settings" && (
          <SettingsPage
            currentUser={currentUser}
            apiBaseUrl={API_BASE_URL}
            wsConnected={wsConnected}
            wsState={wsState}
            themeMode={themeMode}
            onThemeModeChange={setThemeMode}
            autoRefreshInterval={autoRefreshInterval}
            onAutoRefreshIntervalChange={setAutoRefreshInterval}
            liveUpdatesEnabled={liveUpdatesEnabled}
            onLiveUpdatesEnabledChange={setLiveUpdatesEnabled}
            notificationsEnabled={notificationsEnabled}
            onNotificationsEnabledChange={setNotificationsEnabled}
          />
        )}
      </main>
    </div>
  );
}

function DashboardPage({ logs, cases, stats, dashboardSummary, currentUser, setSelected, setPage, loading, wsConnected, wsState }) {
  const [feedExpanded, setFeedExpanded] = useState(false);
  const recentLogs = useMemo(
    () => filterRecentItems(logs, (item) => item?.timestamp || getLastActivityAt(item)),
    [logs],
  );
  const sortedLogs = [...recentLogs].sort((a, b) => compareIso(b?.timestamp, a?.timestamp));
  const caseByAttemptId = Object.fromEntries(cases.map((item) => [item.attempt_id, item]));
  const reviewRows = buildReviewQueueItems(recentLogs, caseByAttemptId);
  const fallbackStats = {
    total: recentLogs.length,
    high: recentLogs.filter((x) => x.risk === "HIGH").length,
    medium: recentLogs.filter((x) => x.risk === "MEDIUM").length,
    low: recentLogs.filter((x) => x.risk === "LOW").length,
    ongoing: recentLogs.filter((x) => getExamStatus(x) === "ONGOING").length,
    completed: recentLogs.filter((x) => getExamStatus(x) === "COMPLETED").length,
    avgConfidence: recentLogs.reduce((sum, x) => sum + Number(x.confidence || 0), 0) / (recentLogs.length || 1),
  };
  const fallbackFeedRows = sortedLogs.slice(0, 5);
  const fallbackReviewCases = reviewRows.filter((row) => isQueueActionableStatus(row.queueStatus)).slice(0, 5);
  const unresolvedCases = reviewRows.filter((row) => isQueueActionableStatus(row.queueStatus)).length;
  const fallbackSignalSummary = buildAggregateSignals(recentLogs);
  const fallbackLiveEventRate = getLiveEventRate(recentLogs);
  const riskTotal = Math.max(1, fallbackStats.total);
  const fallbackLastUpdated = sortedLogs[0]?.timestamp || null;
  const feedRows = Array.isArray(dashboardSummary?.recent_risk_feed) && dashboardSummary.recent_risk_feed.length
    ? dashboardSummary.recent_risk_feed
    : fallbackFeedRows;
  const visibleFeedRows = feedExpanded ? feedRows : feedRows.slice(0, 5);
  const reviewCases = Array.isArray(dashboardSummary?.cases_needing_review) && dashboardSummary.cases_needing_review.length
    ? dashboardSummary.cases_needing_review
    : fallbackReviewCases;
  const visibleReviewCases = reviewCases.slice(0, 5);
  const signalSummary = dashboardSummary?.recent_evidence_signals
    ? {
        clipboard: Number(dashboardSummary.recent_evidence_signals.clipboard_copy_paste || 0),
        tabSwitches: Number(dashboardSummary.recent_evidence_signals.tab_switch_events || 0),
        idleSpikes: Number(dashboardSummary.recent_evidence_signals.idle_time_spikes || 0),
        rapidBursts: Number(dashboardSummary.recent_evidence_signals.rapid_answer_bursts || 0),
        focusBlur: Number(dashboardSummary.recent_evidence_signals.focus_blur_events || 0),
      }
    : fallbackSignalSummary;
  const liveEventRate = dashboardSummary?.live_event_rate ?? fallbackLiveEventRate;
  const lastUpdated = dashboardSummary?.system_health_basic?.last_updated || fallbackLastUpdated;
  const metricTotalAttempts = dashboardSummary?.total_attempts ?? fallbackStats.total;
  const metricHighRisk = dashboardSummary?.high_risk_count ?? fallbackStats.high;
  const metricMediumRisk = dashboardSummary?.medium_risk_count ?? fallbackStats.medium;
  const metricLowRisk = dashboardSummary?.low_risk_count ?? fallbackStats.low;
  const metricActiveSessions = dashboardSummary?.active_sessions ?? fallbackStats.ongoing;
  const metricNeedsReview = dashboardSummary?.needs_review_count ?? unresolvedCases;
  const metricAvgConfidence = dashboardSummary?.avg_confidence ?? fallbackStats.avgConfidence;

  const metrics = [
    { title: "Active Sessions", value: metricActiveSessions, subtitle: "Live exams in progress", tone: "blue", icon: <Users size={20} /> },
    { title: "Total Attempts", value: metricTotalAttempts, subtitle: "All time attempts", tone: "cyan", icon: <ClipboardList size={20} /> },
    { title: "High Risk Now", value: metricHighRisk, subtitle: "Requires attention", tone: "high", icon: <AlertTriangle size={20} /> },
    { title: "Medium Risk", value: metricMediumRisk, subtitle: "Monitoring", tone: "medium", icon: <ShieldAlert size={20} /> },
    { title: "Needs Review", value: metricNeedsReview, subtitle: "Unresolved cases", tone: "violet", icon: <ListChecks size={20} /> },
    { title: "Avg Confidence", value: formatNumber(metricAvgConfidence, 2), subtitle: "Model certainty", tone: "purple", icon: <Gauge size={20} /> },
    { title: "Live Event Rate", value: liveEventRate, subtitle: "Events / min", tone: "blue", icon: <Radio size={20} /> },
  ];

  const distributionSegments = [
    { label: "High Risk", value: Number((((dashboardSummary?.risk_distribution?.high_risk_count ?? metricHighRisk) / Math.max(1, dashboardSummary?.risk_distribution?.total_attempts ?? metricTotalAttempts ?? riskTotal)) * 100).toFixed(1)), color: "#ff5f67", count: dashboardSummary?.risk_distribution?.high_risk_count ?? metricHighRisk },
    { label: "Medium Risk", value: Number((((dashboardSummary?.risk_distribution?.medium_risk_count ?? metricMediumRisk) / Math.max(1, dashboardSummary?.risk_distribution?.total_attempts ?? metricTotalAttempts ?? riskTotal)) * 100).toFixed(1)), color: "#ffb703", count: dashboardSummary?.risk_distribution?.medium_risk_count ?? metricMediumRisk },
    { label: "Low Risk", value: Number((((dashboardSummary?.risk_distribution?.low_risk_count ?? metricLowRisk) / Math.max(1, dashboardSummary?.risk_distribution?.total_attempts ?? metricTotalAttempts ?? riskTotal)) * 100).toFixed(1)), color: "#65d46e", count: dashboardSummary?.risk_distribution?.low_risk_count ?? metricLowRisk },
  ];

  return (
    <div className="command-center-page">
      <div className="page-title-block">
        <span className="page-kicker">LIVE ASSESSMENT MONITORING</span>
        <h1>Risk Command Center</h1>
        <p>Track active attempts, risk movement, and candidates requiring review.</p>
      </div>

      <section className="command-metrics-grid">
        {metrics.map((metric) => (
          <EnterpriseMetricCard
            key={metric.title}
            title={metric.title}
            value={metric.value}
            subtitle={metric.subtitle}
            tone={metric.tone}
            icon={metric.icon}
            sparklineSeed={`${metric.title}-${metric.value}`}
          />
        ))}
      </section>

      <section className="command-primary-grid">
        <div className="enterprise-panel">
          <PanelHeader
            title="Live Risk Feed"
            subtitle="Real-time suspicious activity"
            actionLabel={feedRows.length > 5 ? (feedExpanded ? "Show Less" : "View All") : null}
            onActionClick={feedRows.length > 5 ? () => setFeedExpanded((value) => !value) : undefined}
          />
          {feedRows.length === 0 ? (
            <EmptyState text="No recent live activity has been received yet." />
          ) : (
            <div className="activity-feed-list">
              {visibleFeedRows.map((item, index) => (
                <button
                  className="activity-feed-row"
                  key={`${item.attempt_id}-${item.timestamp || item.last_activity || index}`}
                  onClick={() => {
                    setSelected(item);
                    setPage("report");
                  }}
                >
                  <span className="activity-feed-time">{formatTime(item.timestamp)}</span>
                  <span className="activity-feed-event">{eventIcon(item.latest_event?.event_type || item.latest_event_type || item.risk || item.risk_level)}</span>
                  <span className="activity-feed-copy">
                    <strong>{getCandidateName(item)} <small>({item.attempt_id})</small></strong>
                    <p>{item.attempt_summary || getLatestActivity(item)}</p>
                  </span>
                  <span className={riskClass(item.risk || item.risk_level)}>{item.risk || item.risk_level || "LOW"}</span>
                  <ArrowRight size={16} />
                </button>
              ))}
            </div>
          )}
          <div className="panel-footer-link">
            <span>{feedExpanded ? `Showing ${visibleFeedRows.length} recent events` : `Showing latest ${visibleFeedRows.length} events`}</span>
            {feedRows.length > 5 ? (
              <button className="panel-footer-action" onClick={() => setFeedExpanded((value) => !value)} type="button">
                {feedExpanded ? "Show Less" : "View All Activity"}
              </button>
            ) : null}
          </div>
        </div>

        <div className="enterprise-panel">
          <PanelHeader
            title="Cases Needing Review"
            subtitle="Prioritized unresolved cases"
            actionLabel="View Review Queue"
            onActionClick={() => setPage("queue")}
          />
          {reviewCases.length === 0 ? (
            <EmptyState text="No unresolved cases require review right now." />
          ) : (
            <div className="review-priority-table">
              <div className="review-priority-head">
                <span>Priority</span>
                <span>Candidate / Attempt</span>
                <span>Risk</span>
                <span>Status</span>
                <span>Score</span>
                <span>Action</span>
              </div>
              {visibleReviewCases.map((row, index) => (
                <div className="review-priority-row" key={row.attemptId || row.attempt_id}>
                  <span className={`priority-index tone-${riskTone(row.risk || row.risk_level)}`}>{row.priority || index + 1}</span>
                  <span className="review-priority-candidate">
                    <strong>{row.candidateName || row.candidate_name || "Unknown Candidate"}</strong>
                    <small>{row.attemptId || row.attempt_id}</small>
                    <small>{formatDateTime(row.lastActivity || row.last_activity)}</small>
                  </span>
                  <span className={riskClass(row.risk || row.risk_level)}>{row.risk || row.risk_level}</span>
                  <span className={caseStatusClass(row.queueStatus || row.status)}>{formatWorkflowStatus(row.queueStatus || row.status)}</span>
                  <span>{formatNumber(row.score)}</span>
                  <button
                    className="action-link-button"
                    onClick={() => {
                      setSelected(row.item || logs.find((item) => item.attempt_id === (row.attemptId || row.attempt_id)) || null);
                      setPage("report");
                    }}
                    type="button"
                  >
                    View
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className="panel-footer-link">
            <span>Showing top {visibleReviewCases.length} cases</span>
            <button className="panel-footer-action" onClick={() => setPage("queue")} type="button">
              View All Cases
            </button>
          </div>
        </div>
      </section>

      <section className="command-bottom-grid">
        <div className="enterprise-panel">
          <PanelHeader title="Risk Distribution" subtitle="Distribution of attempts by risk level" />
          <div className="distribution-panel">
            <div className="distribution-donut-wrap">
              <div className="distribution-donut" style={buildDonutStyle(distributionSegments)}></div>
            </div>
            <div className="distribution-legend">
              {distributionSegments.map((segment) => (
                <div className="distribution-legend-row" key={segment.label}>
                  <span><i style={{ background: segment.color }}></i>{segment.label}</span>
                  <strong>{segment.count} ({formatPercent(segment.count, stats.total)})</strong>
                </div>
              ))}
            </div>
          </div>
          <div className="panel-caption">Total Attempts: {dashboardSummary?.risk_distribution?.total_attempts ?? metricTotalAttempts}</div>
        </div>

        <div className="enterprise-panel">
          <PanelHeader title="Recent Evidence Signals" subtitle="Summary of recent suspicious activity" />
          <div className="signals-grid">
            <SignalStatCard title="Clipboard Copy/Paste" value={signalSummary.clipboard} tone="high" icon={<Copy size={18} />} />
            <SignalStatCard title="Tab Switch Events" value={signalSummary.tabSwitches} tone="medium" icon={<MonitorOff size={18} />} />
            <SignalStatCard title="Idle Time Spikes" value={signalSummary.idleSpikes} tone="medium" icon={<Clock3 size={18} />} />
            <SignalStatCard title="Rapid Answer Bursts" value={signalSummary.rapidBursts} tone="medium" icon={<Zap size={18} />} />
            <SignalStatCard title="Focus/Blur Events" value={signalSummary.focusBlur} tone="violet" icon={<Eye size={18} />} />
          </div>
        </div>

        <div className="enterprise-panel">
          <PanelHeader title="System Health" subtitle="Real-time system status" />
          <div className="system-health-list">
            <SystemHealthRow icon={<Server size={16} />} label="Backend API" value={dashboardSummary?.system_health_basic?.backend_api || getHealthLabel(logs.length > 0, loading)} tone="success" />
            <SystemHealthRow
              icon={<Wifi size={16} />}
              label="WebSocket Stream"
              value={dashboardSummary?.system_health_basic?.websocket_stream || (wsState === "connected" ? "Connected" : wsState === "reconnecting" ? "Reconnecting" : "Paused")}
              tone={(dashboardSummary?.system_health_basic?.websocket_stream || (wsState === "connected" ? "Connected" : wsState === "reconnecting" ? "Reconnecting" : "Paused")) === "Connected" ? "success" : "danger"}
            />
            <SystemHealthRow icon={<Radio size={16} />} label="Event Stream" value={dashboardSummary?.system_health_basic?.event_stream || (logs.length ? "Receiving" : "Idle")} tone={(dashboardSummary?.system_health_basic?.event_stream || (logs.length ? "Receiving" : "Idle")) === "Idle" ? "warning" : "success"} />
            <SystemHealthRow icon={<ShieldCheck size={16} />} label="Authentication" value={dashboardSummary?.system_health_basic?.authentication || (currentUser ? "Active" : "Unknown")} tone="success" />
            <SystemHealthRow icon={<Database size={16} />} label="Database" value={dashboardSummary?.system_health_basic?.database || (logs.length || cases.length ? "Healthy" : "Awaiting Data")} tone="success" />
          </div>
          <div className="panel-caption">Last Updated: {formatDateTime(lastUpdated)}</div>
        </div>
      </section>
    </div>
  );
}

function QueuePage({ logs, reviewQueuePayload, caseByAttemptId, caseAccessError, currentUser, onRefresh, setSelected, setPage }) {
  const [activeTab, setActiveTab] = useState("all");
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [riskFilter, setRiskFilter] = useState("all");
  const [sortKey, setSortKey] = useState("priority_desc");
  const [pageIndex, setPageIndex] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);
  const [refreshing, setRefreshing] = useState(false);
  const isAdminDemo = String(currentUser?.role || "").toUpperCase() === "ADMIN";

  const recentLogs = useMemo(
    () => filterRecentItems(logs, (item) => item?.timestamp || getLastActivityAt(item)),
    [logs],
  );
  const fallbackRows = useMemo(() => buildReviewQueueItems(recentLogs, caseByAttemptId), [recentLogs, caseByAttemptId]);
  const reviewRows = useMemo(() => {
    if (!Array.isArray(reviewQueuePayload?.data)) {
      return fallbackRows.map((row) => ({
        ...row,
        candidateName: row.candidateName || row.attemptId || "Unknown Candidate",
        candidateEmail: row.candidateEmail || "No email available",
        assessmentName: row.assessmentName || "Unknown Assessment",
        risk: normalizeQueueRisk(row.risk),
        queueStatus: normalizeQueueStatus(row.queueStatus),
        assignedTo: row.assignedTo && row.assignedTo !== "Unassigned" && isAdminDemo ? "Admin" : (row.assignedTo || "Unassigned"),
      }));
    }

    return reviewQueuePayload.data.map((row) => ({
      item: recentLogs.find((item) => item.attempt_id === row.attempt_id) || {
        attempt_id: row.attempt_id,
        candidate_name: row.candidate_name,
        candidate_email: row.candidate_email,
        assessment_name: row.assessment_name,
        combined_score: row.risk_score,
        confidence: row.confidence,
        risk: row.risk_level,
        timestamp: row.last_activity,
        event_count: row.event_count,
      },
      caseRecord: caseByAttemptId[row.attempt_id] || null,
      attemptId: row.attempt_id,
      candidateName: row.candidate_name || row.attempt_id || "Unknown Candidate",
      candidateEmail: row.candidate_email || "No email available",
      assessmentName: row.assessment_name || "Unknown Assessment",
      score: Number(row.risk_score || 0),
      risk: normalizeQueueRisk(row.risk_level || "LOW"),
      queueStatus: normalizeQueueStatus(row.case_status || "NEW"),
      assignedTo: row.assigned_to ? (isAdminDemo ? "Admin" : row.assigned_to) : "Unassigned",
      lastActivity: row.last_activity,
      eventCount: Number(row.event_count || 0),
      strongestSignal: row.strongest_signal || "No major signal",
      priorityRank: Number(row.priority_rank || 0),
      caseId: row.case_id,
      confidence: Number(row.confidence || 0),
    }));
  }, [caseByAttemptId, fallbackRows, isAdminDemo, recentLogs, reviewQueuePayload]);

  const baseFilteredRows = useMemo(() => {
    const query = searchTerm.trim().toLowerCase();
    return reviewRows.filter((row) => {
      const matchesSearch = !query || [
        row.candidateName,
        row.candidateEmail,
        row.attemptId,
        row.assessmentName,
        row.caseId,
      ].some((value) => String(value || "").toLowerCase().includes(query));

      const matchesRisk = riskFilter === "all" || normalizeQueueRisk(row.risk) === riskFilter;
      const matchesStatus = statusFilter === "all" || normalizeQueueStatus(row.queueStatus) === statusFilter;
      return matchesSearch && matchesRisk && matchesStatus;
    });
  }, [reviewRows, riskFilter, searchTerm, statusFilter]);

  const tabCounts = useMemo(() => buildQueueTabCounts(baseFilteredRows), [baseFilteredRows]);
  const filteredRows = useMemo(() => {
    const tabbed = filterQueueRows(baseFilteredRows, activeTab);
    return [...tabbed].sort((a, b) => compareQueueRows(a, b, sortKey));
  }, [activeTab, baseFilteredRows, sortKey]);

  const totalPages = Math.max(1, Math.ceil(filteredRows.length / rowsPerPage));
  const currentPage = Math.min(pageIndex, totalPages - 1);
  const pageRows = filteredRows.slice(currentPage * rowsPerPage, currentPage * rowsPerPage + rowsPerPage);

  const summary = {
    highPriority: reviewRows.filter((row) => normalizeQueueRisk(row.risk) === "HIGH" && isQueueActionableStatus(row.queueStatus)).length,
    mediumPriority: reviewRows.filter((row) => normalizeQueueRisk(row.risk) === "MEDIUM" && isQueueActionableStatus(row.queueStatus)).length,
    newCases: reviewRows.filter((row) => normalizeQueueStatus(row.queueStatus) === "NEW").length,
    underInvestigation: reviewRows.filter((row) => normalizeQueueStatus(row.queueStatus) === "UNDER_INVESTIGATION").length,
    resolved: reviewRows.filter((row) => isQueueResolvedStatus(row.queueStatus)).length,
    total: reviewRows.length,
  };

  const queueHealth = [
    { label: "High", count: summary.highPriority, color: "#ff5f67" },
    { label: "Medium", count: summary.mediumPriority, color: "#ffb703" },
    { label: "Low", count: reviewRows.filter((row) => normalizeQueueRisk(row.risk) === "LOW" && isQueueActionableStatus(row.queueStatus)).length, color: "#65d46e" },
    { label: "Resolved", count: reviewRows.filter((row) => isQueueResolvedStatus(row.queueStatus)).length, color: "#4f8cff" },
  ];

  const resolvedWithDates = Object.values(caseByAttemptId).filter((item) => item?.updated_at && item?.created_at && isResolvedCaseStatus(item.status));
  const avgReviewSeconds = resolvedWithDates.length
    ? resolvedWithDates.reduce((sum, item) => sum + (getSecondsBetween(item.created_at, item.updated_at) || 0), 0) / resolvedWithDates.length
    : 0;
  const withinSla = reviewRows.filter((row) => {
    const ageSeconds = getSecondsBetween(row.caseRecord?.created_at || row.item?.timestamp, row.lastActivity);
    return ageSeconds === null || ageSeconds / 3600 <= 24;
  }).length;
  const slaPercent = reviewRows.length ? Math.round((withinSla / reviewRows.length) * 100) : 100;
  const reviewerNames = Array.from(new Set(reviewRows.map((row) => row.assignedTo).filter((value) => value && value !== "Unassigned")));
  const topReviewers = reviewerNames.length
    ? reviewerNames
        .map((name) => [name, reviewRows.filter((row) => row.assignedTo === name).length])
        .sort((a, b) => b[1] - a[1])
        .slice(0, 4)
    : (isAdminDemo ? [["Admin", 0]] : []);

  const summaryCards = [
    { title: "High Priority", value: summary.highPriority, subtitle: "Requires immediate attention", tone: "high", icon: <AlertTriangle size={20} /> },
    { title: "Medium Priority", value: summary.mediumPriority, subtitle: "Needs review", tone: "medium", icon: <ShieldAlert size={20} /> },
    { title: "New Cases", value: summary.newCases, subtitle: "Unassigned cases", tone: "violet", icon: <ListChecks size={20} /> },
    { title: "Under Investigation", value: summary.underInvestigation, subtitle: "In progress", tone: "blue", icon: <Search size={20} /> },
    { title: "Resolved", value: summary.resolved, subtitle: "Completed reviews", tone: "success", icon: <ShieldCheck size={20} /> },
    { title: "Total in Queue", value: summary.total, subtitle: "Total cases", tone: "cyan", icon: <Activity size={20} /> },
  ];

  const tabs = [
    { key: "all", label: "All Cases", count: tabCounts.all },
    { key: "new", label: "New", count: tabCounts.new },
    { key: "under_investigation", label: "Under Investigation", count: tabCounts.under_investigation },
    { key: "escalated", label: "Escalated", count: tabCounts.escalated },
    { key: "resolved", label: "Resolved", count: tabCounts.resolved },
    { key: "closed", label: "Closed", count: tabCounts.closed },
  ];

  useEffect(() => {
    setPageIndex(0);
  }, [activeTab, riskFilter, rowsPerPage, searchTerm, sortKey, statusFilter]);

  const clearFilters = () => {
    setSearchTerm("");
    setStatusFilter("all");
    setRiskFilter("all");
    setSortKey("priority_desc");
    setActiveTab("all");
    setPageIndex(0);
  };

  const handleRefresh = async () => {
    try {
      setRefreshing(true);
      await Promise.resolve(onRefresh?.());
    } finally {
      setTimeout(() => setRefreshing(false), 250);
    }
  };

  const handleScrollToFilters = () => {
    const el = document.getElementById("review-queue-filters");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="review-queue-page">
      <div className="page-title-row">
        <div className="page-title-block">
          <h1>Review Queue</h1>
          <p>Prioritized investigation queue</p>
        </div>
        <div className="page-inline-actions">
          <button className="toolbar-button" onClick={handleScrollToFilters} type="button">
            <SlidersHorizontal size={16} /> Filters
          </button>
          <button className="toolbar-button" onClick={() => void handleRefresh()} type="button" disabled={refreshing}>
            <RefreshCw size={16} className={refreshing ? "spin-icon" : ""} />
            {refreshing ? "Refreshing" : "Refresh"}
          </button>
        </div>
      </div>

      <section className="queue-summary-grid">
        {summaryCards.map((card) => (
          <EnterpriseMetricCard
            key={card.title}
            title={card.title}
            value={card.value}
            subtitle={card.subtitle}
            tone={card.tone}
            icon={card.icon}
            sparklineSeed={`${card.title}-${card.value}`}
          />
        ))}
      </section>

      <section className="enterprise-panel queue-table-shell review-queue-content">
        <div className="queue-filter-toolbar" id="review-queue-filters">
          <label className="queue-search-box" htmlFor="queue-search">
            <Search size={16} />
            <input
              id="queue-search"
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="Search candidate, email, attempt, assessment, or case ID"
            />
          </label>

          <div className="queue-filter-group">
            <label className="queue-select-filter">
              <span>Status</span>
              <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                <option value="all">All Statuses</option>
                <option value="NEW">New</option>
                <option value="TRIAGED">Triaged</option>
                <option value="UNDER_INVESTIGATION">Under Investigation</option>
                <option value="ESCALATED">Escalated</option>
                <option value="CLEARED">Cleared</option>
                <option value="CONFIRMED_RISK">Confirmed Risk</option>
                <option value="FALSE_POSITIVE">False Positive</option>
                <option value="CLOSED">Closed</option>
                <option value="COMPLETED">Completed</option>
              </select>
            </label>

            <label className="queue-select-filter">
              <span>Risk</span>
              <select value={riskFilter} onChange={(event) => setRiskFilter(event.target.value)}>
                <option value="all">All Risks</option>
                <option value="HIGH">High</option>
                <option value="MEDIUM">Medium</option>
                <option value="LOW">Low</option>
              </select>
            </label>

            <label className="queue-select-filter">
              <span>Sort</span>
              <select value={sortKey} onChange={(event) => setSortKey(event.target.value)}>
                <option value="priority_desc">Priority: High to Low</option>
                <option value="score_desc">Risk Score: High to Low</option>
                <option value="score_asc">Risk Score: Low to High</option>
                <option value="last_newest">Last Activity: Newest</option>
                <option value="last_oldest">Last Activity: Oldest</option>
                <option value="status">Status</option>
                <option value="candidate_name">Candidate Name</option>
              </select>
            </label>

            <button className="toolbar-button secondary" onClick={clearFilters} type="button">
              Clear Filters
            </button>
          </div>
        </div>

        <div className="queue-tabs-row">
          <div className="queue-tabs">
            {tabs.map((tab) => (
              <button
                className={`queue-tab ${activeTab === tab.key ? "active" : ""}`}
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                type="button"
              >
                {tab.label}
                <span>{tab.count}</span>
              </button>
            ))}
          </div>
          <div className="queue-sort-control">{sortLabel(sortKey)}</div>
        </div>

        {caseAccessError ? <div className="workflow-message workflow-message-warning">{caseAccessError}</div> : null}

        <div className="queue-table-scroll">
          <div className="queue-enterprise-table">
            <div className="queue-enterprise-head">
              <span>Priority</span>
              <span>Candidate / Attempt</span>
              <span>Assessment</span>
              <span>Risk Score</span>
              <span>Risk Level</span>
              <span>Status</span>
              <span>Assigned To</span>
              <span>Last Activity</span>
              <span>Action</span>
            </div>

            {pageRows.length === 0 ? (
              <div className="queue-empty-shell">
                <EmptyState text={reviewRows.length ? "No cases found for this search/filter. Try clearing filters." : "No review cases yet."} />
              </div>
            ) : (
              pageRows.map((row, index) => (
                <div className="queue-enterprise-row" key={row.caseId || row.attemptId}>
                  <span className={`priority-index tone-${riskTone(row.risk)}`}>{row.priorityRank || currentPage * rowsPerPage + index + 1}</span>
                  <span className="queue-candidate-block">
                    <span className="queue-candidate-avatar">{getInitials(row.candidateName || row.attemptId)}</span>
                    <span>
                      <strong>{row.candidateName || row.attemptId}</strong>
                      <small>{row.attemptId}</small>
                    </span>
                  </span>
                  <span className="queue-assessment-cell">{row.assessmentName || "Unknown Assessment"}</span>
                  <span className="queue-score-cell">
                    <strong>{Number.isFinite(Number(row.score)) ? formatNumber(row.score) : "—"}</strong>
                    <MiniSparkline tone={riskTone(row.risk)} seed={`${row.attemptId}-${row.score}`} />
                  </span>
                  <span className={riskClass(normalizeQueueRisk(row.risk))}>{normalizeQueueRisk(row.risk)}</span>
                  <span className={caseStatusClass(normalizeQueueStatus(row.queueStatus))}>{formatWorkflowStatus(normalizeQueueStatus(row.queueStatus))}</span>
                  <span>{row.assignedTo || "Unassigned"}</span>
                  <span className="queue-last-activity">
                    <strong>{row.lastActivity ? formatDateTime(row.lastActivity) : "—"}</strong>
                    <small>{row.lastActivity ? formatRelativeTime(row.lastActivity) : "No activity"}</small>
                  </span>
                  <span>
                    <button
                      className="action-link-button"
                      onClick={() => {
                        setSelected(row.item || logs.find((item) => item.attempt_id === row.attemptId) || null);
                        setPage("report");
                      }}
                      type="button"
                    >
                      View
                    </button>
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="queue-pagination-row">
          <span>
            Showing {filteredRows.length ? currentPage * rowsPerPage + 1 : 0} to {Math.min(filteredRows.length, (currentPage + 1) * rowsPerPage)} of {filteredRows.length} cases
          </span>
          <div className="queue-pagination-controls">
            <button className="pagination-btn" disabled={currentPage === 0} onClick={() => setPageIndex((value) => Math.max(0, value - 1))} type="button">‹</button>
            <button className="pagination-btn active" type="button">{currentPage + 1}</button>
            {totalPages > 1 ? <button className="pagination-btn" onClick={() => setPageIndex((value) => Math.min(totalPages - 1, value + 1))} type="button">{Math.min(totalPages, currentPage + 2)}</button> : null}
            <button className="pagination-btn" disabled={currentPage >= totalPages - 1} onClick={() => setPageIndex((value) => Math.min(totalPages - 1, value + 1))} type="button">›</button>
          </div>
          <label className="rows-per-page">
            Rows per page:
            <select value={rowsPerPage} onChange={(event) => setRowsPerPage(Number(event.target.value))}>
              <option value={10}>10</option>
              <option value={25}>25</option>
              <option value={50}>50</option>
            </select>
          </label>
        </div>
      </section>

      <section className="enterprise-panel review-analytics-panel">
        <div className="review-analytics-section">
          <div className="review-analytics-title">Queue Health</div>
          <div className="distribution-panel">
            <div className="distribution-donut-wrap">
              <div
                className="distribution-donut"
                style={buildDonutStyle(queueHealth.map((segment) => ({ ...segment, value: Number(((segment.count / Math.max(1, summary.total)) * 100).toFixed(1)) })))}
              ></div>
            </div>
            <div className="distribution-legend">
              {queueHealth.map((segment) => (
                <div className="distribution-legend-row" key={segment.label}>
                  <span><i style={{ background: segment.color }}></i>{segment.label}</span>
                  <strong>{segment.count} ({formatPercent(segment.count, summary.total)})</strong>
                </div>
              ))}
            </div>
          </div>
          <div className="panel-caption">Total {summary.total}</div>
        </div>

        <div className="review-analytics-section">
          <div className="review-analytics-title">SLA Compliance</div>
          <div className="sla-card">
            <div className="sla-ring" style={buildDonutStyle([{ value: slaPercent, color: "#65d46e" }, { value: Math.max(0, 100 - slaPercent), color: "rgba(255,255,255,0.08)" }])}>
              <span>{slaPercent}%</span>
            </div>
            <div className="sla-copy">
              <strong>On Track</strong>
              <small>Within SLA {withinSla}</small>
              <small>Breached SLA {Math.max(0, reviewRows.length - withinSla)}</small>
              <small>Target: 90%</small>
            </div>
          </div>
        </div>

        <div className="review-analytics-section">
          <div className="review-analytics-title">Average Review Time</div>
          <div className="avg-time-card">
            <div className="avg-time-circle"><Clock3 size={22} /></div>
            <div>
              <strong>{avgReviewSeconds ? formatDuration(avgReviewSeconds) : "—"}</strong>
              <small>Average time to resolution</small>
            </div>
          </div>
        </div>

        <div className="review-analytics-section">
          <div className="review-analytics-title">Top Reviewers</div>
          <div className="top-reviewer-list">
            {topReviewers.map(([name, count]) => (
              <div className="top-reviewer-row" key={name}>
                <span className="top-reviewer-avatar">{getInitials(name)}</span>
                <span className="top-reviewer-copy">
                  <strong>{name}</strong>
                  <small>{count} cases</small>
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
function ReportPage({ selected, selectedCase, caseAccessError, token, currentUser, onUnauthorized, onCaseAction, onBack }) {
  const [events, setEvents] = useState([]);
  const [riskHistory, setRiskHistory] = useState([]);
  const [liveRisk, setLiveRisk] = useState(null);
  const [reportData, setReportData] = useState(null);
  const [caseDetail, setCaseDetail] = useState(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [workflowNote, setWorkflowNote] = useState("");
  const [workflowBusy, setWorkflowBusy] = useState(false);
  const [workflowMessage, setWorkflowMessage] = useState("");
  const [exportingPdf, setExportingPdf] = useState(false);

  useEffect(() => {
    setEvents([]);
    setRiskHistory([]);
    setLiveRisk(null);
    setReportData(null);
    setCaseDetail(null);
    setWorkflowMessage("");
  }, [selected?.attempt_id]);

  useEffect(() => {
    async function fetchReportData() {
      if (!selected?.attempt_id) return;
      try {
        setReportLoading(true);
        const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
        const [liveRes, eventsRes, historyRes, reportRes] = await Promise.all([
          fetch(`${API_BASE_URL}/v1/live-risk/${selected.attempt_id}`, { headers }),
          fetch(`${EVENTS_URL}/${selected.attempt_id}`, { headers }),
          fetch(`${RISK_HISTORY_URL}/${selected.attempt_id}`, { headers }),
          fetch(`${REPORTS_URL}/${selected.attempt_id}`, { headers }),
        ]);

        if (liveRes.status === 401 || eventsRes.status === 401 || historyRes.status === 401 || reportRes.status === 401) {
          onUnauthorized();
          return;
        }

        const [liveJson, eventsJson, historyJson, reportJson] = await Promise.all([
          liveRes.json(),
          eventsRes.json(),
          historyRes.json(),
          reportRes.json(),
        ]);
        setLiveRisk(liveJson.data || null);
        setEvents(eventsJson.data || []);
        setRiskHistory(historyJson.data || []);
        setReportData((prev) => {
          if (reportJson?.status !== "success") {
            return prev;
          }
          const nextData = reportJson.data || null;
          const prevEvidence = Array.isArray(prev?.evidence_items) ? prev.evidence_items : [];
          const nextEvidence = Array.isArray(nextData?.evidence_items) ? nextData.evidence_items : [];
          const prevOverview = prev?.violation_overview_counts ? Object.keys(prev.violation_overview_counts).length : 0;
          const nextOverview = nextData?.violation_overview_counts ? Object.keys(nextData.violation_overview_counts).length : 0;
          if (prev && prevEvidence.length > 0 && nextData && nextEvidence.length === 0 && nextOverview === 0) {
            return {
              ...prev,
              ...nextData,
              evidence_items: prevEvidence,
              violation_overview_counts: prev.violation_overview_counts || nextData.violation_overview_counts,
            };
          }
          if (prev && prevOverview > 0 && nextData && nextEvidence.length === 0 && nextOverview === 0) {
            return {
              ...prev,
              ...nextData,
              violation_overview_counts: prev.violation_overview_counts,
            };
          }
          return nextData;
        });
      } catch (err) {
        console.error("Failed to fetch report data:", err);
      } finally {
        setReportLoading(false);
      }
    }
    void fetchReportData();
  }, [onUnauthorized, selected?.attempt_id, token]);

  useEffect(() => {
    async function fetchCaseDetail() {
      if (!selectedCase?.id) {
        setCaseDetail(null);
        return;
      }
      const result = await authJsonFetch(`${CASES_URL}/${selectedCase.id}`, { token });
      if (!result.ok) {
        if (result.status === 401) {
          onUnauthorized();
          return;
        }
        if (result.status === 403) {
          setCaseDetail(null);
          setWorkflowMessage("You do not have permission to open reviewer workflow details.");
          return;
        }
        setWorkflowMessage(result?.data?.detail || "Unable to load case details");
        return;
      }
      setWorkflowMessage("");
      setCaseDetail(result.data?.data || null);
    }

    void fetchCaseDetail();
  }, [onUnauthorized, selectedCase?.id, token]);

  const runCaseAction = useCallback(async (endpoint, body) => {
    if (!caseDetail?.id) {
      setWorkflowMessage("Case record is not ready yet.");
      return;
    }
    try {
      setWorkflowBusy(true);
      setWorkflowMessage("");
      const result = await onCaseAction({ caseId: caseDetail.id, endpoint, body });
      if (!result?.ok) {
        setWorkflowMessage(result?.error || "Case action failed");
        return;
      }
      setCaseDetail(result.data?.data || null);
      setWorkflowMessage("Case updated.");
      if (endpoint === "notes") setWorkflowNote("");
    } finally {
      setWorkflowBusy(false);
    }
  }, [caseDetail?.id, onCaseAction]);

  if (!selected) {
    return (
      <>
        <PageHeader
          eyebrow="Investigation"
          title="Candidate Report"
          subtitle="Select an attempt from the Review Queue to inspect evidence."
          status="NO ATTEMPT"
        />
        <div className="panel empty-report">
          <ShieldAlert size={42} />
          <h3>No attempt selected</h3>
          <p>Open the Review Queue and choose an attempt to view its report.</p>
        </div>
      </>
    );
  }

  const reportAttempt = liveRisk ? { ...selected, ...liveRisk } : selected;
  const displayRisk = reportData?.risk_level || reportAttempt.risk || "LOW";
  const displayScore = reportData?.risk_score ?? reportAttempt.combined_score;
  const displayConfidence = reportData?.confidence ?? reportAttempt.confidence;
  const candidateName = reportData?.candidate_name || reportAttempt.candidate_name || events[0]?.candidate_name || riskHistory[0]?.candidate_name || "Unknown Candidate";
  const candidateEmail = reportData?.candidate_email || reportAttempt.candidate_email || events[0]?.candidate_email || riskHistory[0]?.candidate_email || "No email available";
  const status = getExamStatus(reportData ? { ...reportAttempt, attempt_status: reportData.attempt_status } : reportAttempt, events);
  const firstEvent = events[0]?.occurred_at || reportAttempt.timestamp;
  const lastEvent = events[events.length - 1]?.occurred_at || getLastActivityAt(reportAttempt) || reportAttempt.timestamp;
  const durationSeconds = getAttemptDurationSeconds(reportAttempt) || getSecondsBetween(firstEvent, lastEvent);
  const eventCount = getEventCount(reportAttempt) ?? (events.length ? events.length : null);
  const activeCase = caseDetail || selectedCase;
  const caseStatus = activeCase?.status || "NEW";
  const assignedReviewer = activeCase?.assigned_reviewer_name || activeCase?.assigned_reviewer_email || "Unassigned";
  const actionHistory = Array.isArray(activeCase?.actions) ? activeCase.actions : [];
  const overviewItems = reportData?.violation_overview_counts
    ? buildViolationOverviewFromCounts(reportData.violation_overview_counts)
    : buildViolationOverview(reportAttempt, events);
  const evidenceRows = reportData?.evidence_items?.length
    ? buildEvidenceRowsFromReport(reportData.evidence_items)
    : buildEvidenceRows(reportAttempt, overviewItems, eventCount);
  const strongestReason = reportData?.strongest_reason || primaryIssue(reportAttempt);
  const finalDecision = reportData?.final_decision_title || decisionText(displayRisk);
  const summaryText = reportData?.recommendation || decisionSummaryLine(displayRisk);
  const whyScoreText = reportData?.why_this_score || reportAttempt?.explanation_text || reportAttempt?.explanation || reportSummary(reportAttempt);
  const technicalDetails = buildTechnicalDetails({
    attempt: reportAttempt,
    events,
    eventCount,
    firstEvent,
    lastEvent,
    durationSeconds,
  });
  const headerMetadata = [
    { label: "Assessment", value: getAssessmentName(reportAttempt) },
    { label: "Attempt ID", value: reportAttempt?.attempt_id || "—" },
    { label: "Started", value: formatDateTime(firstEvent) },
    { label: "Duration", value: formatDuration(durationSeconds) },
  ];
  const chartData = riskHistory.map((item) => ({
    ...item,
    label: formatClockLabel(item.timestamp),
    score: item.score ?? item.combined_score,
    risk_level: item.risk_level || item.risk,
  }));
  const sortedEvidenceRows = [...evidenceRows].sort((a, b) => {
    const severityDelta = severityRank(b.severity) - severityRank(a.severity);
    if (severityDelta !== 0) return severityDelta;
    const priorityDelta = evidencePriority(b) - evidencePriority(a);
    if (priorityDelta !== 0) return priorityDelta;
    const countDelta = Number(b.count || 0) - Number(a.count || 0);
    if (countDelta !== 0) return countDelta;
    const impactDelta = Number(b.relatedEventCount || 0) - Number(a.relatedEventCount || 0);
    if (impactDelta !== 0) return impactDelta;
    const aTime = a.lastSeen ? new Date(a.lastSeen).getTime() : 0;
    const bTime = b.lastSeen ? new Date(b.lastSeen).getTime() : 0;
    return bTime - aTime;
  });
  const topEvidenceRows = sortedEvidenceRows.slice(0, 5);
  const investigationInsights = buildInvestigationInsights(reportData, sortedEvidenceRows, displayRisk);
  const canExportPdf = ["ADMIN", "REVIEWER"].includes(String(currentUser?.role || "").toUpperCase());

  const handleExportPdf = useCallback(async () => {
    if (!canExportPdf) return;
    if (!selected?.attempt_id) {
      console.warn("[Investigation PDF Export] No attempt is selected for export.");
      setWorkflowMessage("Select a case before exporting the investigation report.");
      return;
    }
    if (reportLoading) {
      console.warn("[Investigation PDF Export] Report data is still loading.");
      setWorkflowMessage("Report data is still loading. Please wait and try again.");
      return;
    }
    if (!liveRisk && !reportData) {
      console.warn("[Investigation PDF Export] Report data is unavailable for export.");
      setWorkflowMessage("Unable to export yet because the investigation report data is unavailable.");
      return;
    }

    try {
      setExportingPdf(true);
      setWorkflowMessage("");
      const fileName = `investigation_${selected.attempt_id}.pdf`;
      const generatedAt = new Date().toLocaleString();
      const violationOverviewHtml = overviewItems.map((item) => `
        <div class="pdf-mini-card">
          <div class="pdf-mini-label">${escapeHtml(item.title)}</div>
          <div class="pdf-mini-value">${escapeHtml(item.value)}</div>
          <div class="pdf-mini-subtle">${escapeHtml(item.severityLabel || severityLabel(item.severity))}</div>
        </div>
      `).join("");
      const evidenceHtml = topEvidenceRows.map((row) => `
        <tr>
          <td>${escapeHtml(row.title)}</td>
          <td>${escapeHtml(row.severity)}</td>
          <td>${escapeHtml(row.countDisplay ?? row.count ?? "—")}</td>
          <td>${escapeHtml(row.impactLabel || "—")}</td>
          <td>${escapeHtml(row.subtitle || row.details || "—")}</td>
        </tr>
      `).join("");
      const insightsHtml = investigationInsights.length
        ? investigationInsights.map((item) => `<li>${escapeHtml(item)}</li>`).join("")
        : `<li>Waiting for evidence aggregation.</li>`;
      const historyHtml = actionHistory.length
        ? actionHistory.map((action) => `
            <div class="pdf-history-item">
              <strong>${escapeHtml(action.action_type || "Action")}</strong>
              <div>${escapeHtml(formatWorkflowStatus(action.previous_status))} → ${escapeHtml(formatWorkflowStatus(action.new_status || action.previous_status))}</div>
              <p>${escapeHtml(action.comment || "No reviewer comment provided.")}</p>
              <small>${escapeHtml(action.reviewer_name || action.reviewer_email || `Reviewer ${action.reviewer_id}`)} • ${escapeHtml(formatDateTime(action.created_at))}</small>
            </div>
          `).join("")
        : `<div class="pdf-empty">No reviewer workflow actions recorded yet.</div>`;
      const technicalHtml = technicalDetails.map((item) => `
        <div class="pdf-detail-row">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
        </div>
      `).join("");
      const chartSvg = buildRiskHistorySvg(chartData);
      const printWindow = window.open("", `investigation-export-${selected.attempt_id}`, "width=1100,height=900");
      if (!printWindow) {
        setWorkflowMessage("Export popup was blocked by the browser. Please allow popups and try again.");
        return;
      }

      printWindow.document.open();
      printWindow.document.write(`
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8" />
            <title>${escapeHtml(fileName)}</title>
            <style>
              body {
                margin: 0;
                min-height: 100vh;
                display: grid;
                place-items: center;
                background: #f8fafc;
                color: #0f172a;
                font-family: "Segoe UI", Arial, sans-serif;
              }
              .pdf-loading {
                padding: 24px 28px;
                border: 1px solid #dbe3f1;
                border-radius: 18px;
                background: #ffffff;
                box-shadow: 0 18px 40px rgba(15, 23, 42, 0.08);
                text-align: center;
              }
              .pdf-loading strong {
                display: block;
                margin-bottom: 8px;
                font-size: 18px;
              }
              .pdf-loading span {
                color: #475569;
              }
            </style>
          </head>
          <body>
            <div class="pdf-loading">
              <strong>Preparing Investigation Report</strong>
              <span>Rendering the printable export for ${escapeHtml(selected.attempt_id)}...</span>
            </div>
          </body>
        </html>
      `);
      printWindow.document.close();

      const html = `
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8" />
            <title>${escapeHtml(fileName)}</title>
            <style>
              @page { size: A4; margin: 18mm 14mm; }
              * { box-sizing: border-box; }
              body { margin: 0; font-family: "Segoe UI", Arial, sans-serif; color: #0f172a; background: #ffffff; }
              .pdf-shell { padding: 8px 0 18px; }
              .pdf-header { display: grid; grid-template-columns: 1.2fr 1fr; gap: 18px; margin-bottom: 18px; }
              .pdf-brand { color: #6366f1; font-size: 12px; font-weight: 800; letter-spacing: 0.14em; text-transform: uppercase; }
              .pdf-title { margin: 6px 0 4px; font-size: 28px; color: #0f172a; }
              .pdf-subtitle { margin: 0; color: #475569; line-height: 1.45; }
              .pdf-summary-card, .pdf-section { border: 1px solid #dbe3f1; border-radius: 16px; background: #fff; }
              .pdf-summary-card { padding: 18px; }
              .pdf-badges { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
              .pdf-badge { display: inline-flex; align-items: center; min-height: 28px; padding: 0 10px; border-radius: 999px; font-size: 11px; font-weight: 800; letter-spacing: 0.06em; text-transform: uppercase; }
              .pdf-badge.risk { background: #eef2ff; color: #4f46e5; }
              .pdf-badge.status { background: #ecfeff; color: #0f766e; }
              .pdf-meta { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 16px; margin-top: 14px; }
              .pdf-meta div span, .pdf-detail-row span { display: block; font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 4px; }
              .pdf-meta div strong, .pdf-detail-row strong { display: block; font-size: 14px; color: #0f172a; line-height: 1.35; }
              .pdf-scoreboard { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
              .pdf-score-item { padding: 14px; border-radius: 14px; background: #f8fafc; border: 1px solid #e2e8f0; }
              .pdf-score-item span { display: block; font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.08em; }
              .pdf-score-item strong { display: block; margin-top: 8px; font-size: 20px; color: #0f172a; }
              .pdf-section { padding: 16px 18px; margin-top: 16px; page-break-inside: avoid; }
              .pdf-section h2 { margin: 0 0 14px; font-size: 18px; color: #0f172a; }
              .pdf-section h3 { margin: 0 0 10px; font-size: 15px; color: #0f172a; }
              .pdf-grid-two { display: grid; grid-template-columns: 1.35fr 1fr; gap: 16px; }
              .pdf-grid-overview { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
              .pdf-mini-card { padding: 12px; border-radius: 12px; background: #f8fafc; border: 1px solid #e2e8f0; }
              .pdf-mini-label { font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.08em; }
              .pdf-mini-value { margin-top: 8px; font-size: 18px; font-weight: 800; color: #0f172a; }
              .pdf-mini-subtle { margin-top: 4px; font-size: 12px; color: #475569; }
              .pdf-table { width: 100%; border-collapse: collapse; }
              .pdf-table th, .pdf-table td { border-top: 1px solid #e2e8f0; padding: 10px 8px; text-align: left; vertical-align: top; font-size: 13px; }
              .pdf-table th { border-top: 0; font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.08em; }
              .pdf-history-item { padding: 12px 0; border-top: 1px solid #e2e8f0; }
              .pdf-history-item:first-child { border-top: 0; padding-top: 0; }
              .pdf-history-item p { margin: 8px 0; color: #334155; line-height: 1.45; }
              .pdf-history-item small { color: #64748b; }
              .pdf-insight-list { margin: 0; padding-left: 18px; color: #334155; display: grid; gap: 8px; }
              .pdf-insight-list li { line-height: 1.45; }
              .pdf-detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 18px; }
              .pdf-empty { padding: 12px 0; color: #64748b; }
              .pdf-generated { margin-top: 18px; color: #64748b; font-size: 12px; }
            </style>
          </head>
          <body>
            <div class="pdf-shell">
              <div class="pdf-brand">ProctorIQ • Assessment Integrity Console</div>
              <div class="pdf-header">
                <div class="pdf-summary-card">
                  <div class="pdf-badges">
                    <span class="pdf-badge risk">${escapeHtml(displayRisk)} Risk</span>
                    <span class="pdf-badge status">${escapeHtml(status)}</span>
                  </div>
                  <div class="pdf-title">${escapeHtml(candidateName)}</div>
                  <p class="pdf-subtitle">${escapeHtml(candidateEmail)}</p>
                  <div class="pdf-meta">
                    ${headerMetadata.map((item) => `<div><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong></div>`).join("")}
                  </div>
                </div>
                <div class="pdf-scoreboard">
                  <div class="pdf-score-item"><span>Risk Level</span><strong>${escapeHtml(displayRisk)}</strong></div>
                  <div class="pdf-score-item"><span>Risk Score</span><strong>${escapeHtml(formatNumber(displayScore))}</strong></div>
                  <div class="pdf-score-item"><span>Confidence</span><strong>${escapeHtml(formatNumber(displayConfidence))}</strong></div>
                </div>
              </div>

              <section class="pdf-section">
                <h2>Final Decision Summary</h2>
                <h3>${escapeHtml(finalDecision)}</h3>
                <p>${escapeHtml(summaryText)}</p>
                <p><strong>Strongest reason:</strong> ${escapeHtml(strongestReason)}</p>
                <p><strong>Why this score:</strong> ${escapeHtml(whyScoreText)}</p>
              </section>

              <section class="pdf-section">
                <h2>Violation Overview</h2>
                <div class="pdf-grid-overview">${violationOverviewHtml}</div>
              </section>

              <section class="pdf-section">
                <h2>Most Suspicious Behaviors Observed</h2>
                <ul class="pdf-insight-list">${insightsHtml}</ul>
              </section>

              <section class="pdf-section">
                <h2>Top Evidence</h2>
                <table class="pdf-table">
                  <thead>
                    <tr><th>Type</th><th>Severity</th><th>Count</th><th>Impact</th><th>Details</th></tr>
                  </thead>
                  <tbody>${evidenceHtml}</tbody>
                </table>
              </section>

              <section class="pdf-section">
                <h2>Reviewer Workflow History</h2>
                ${historyHtml}
              </section>

              <section class="pdf-section">
                <h2>Risk History Snapshot</h2>
                ${chartSvg}
              </section>

              <section class="pdf-section">
                <h2>Technical Details</h2>
                <div class="pdf-detail-grid">${technicalHtml}</div>
              </section>

              <div class="pdf-generated">Generated ${escapeHtml(generatedAt)}</div>
            </div>
          </body>
        </html>
      `;

      printWindow.document.open();
      printWindow.document.write(html);
      printWindow.document.close();

      await new Promise((resolve) => {
        const completeRender = async () => {
          try {
            if (printWindow.document?.fonts?.ready) {
              await printWindow.document.fonts.ready;
            }
          } catch {
            // Ignore font readiness failures and continue with printing.
          }

          printWindow.focus();

          let settled = false;
          const finish = () => {
            if (settled) return;
            settled = true;
            try {
              printWindow.close();
            } catch {
              // Ignore close failures.
            }
            resolve();
          };

          printWindow.onafterprint = finish;
          window.setTimeout(finish, 1500);
          window.setTimeout(() => {
            try {
              printWindow.print();
            } catch (error) {
              console.warn("[Investigation PDF Export] Failed to launch print dialog.", error);
              finish();
            }
          }, 200);
        };

        if (printWindow.document.readyState === "complete") {
          void completeRender();
          return;
        }

        printWindow.onload = () => {
          void completeRender();
        };
      });
    } catch (error) {
      console.warn("[Investigation PDF Export] Export failed.", error);
      setWorkflowMessage("Unable to generate the PDF export. Please try again.");
    } finally {
      setExportingPdf(false);
    }
  }, [
    actionHistory,
    canExportPdf,
    chartData,
    displayConfidence,
    displayRisk,
    displayScore,
    evidenceRows,
    finalDecision,
    headerMetadata,
    investigationInsights,
    liveRisk,
    overviewItems,
    reportData,
    reportLoading,
    selected?.attempt_id,
    status,
    strongestReason,
    summaryText,
    technicalDetails,
    whyScoreText,
  ]);

  return (
    <div className="report-scroll-container">
      <section className={`investigation-page investigation-report-shell report-${riskTone(displayRisk)}`}>
        <div className="report-toolbar">
          <button className="back-link-btn" onClick={onBack} type="button">
            <ArrowLeft size={16} />
            Back to Review Queue
          </button>
          <div className="report-toolbar-actions">
            <button
              className="panel-header-action"
              disabled={!canExportPdf || exportingPdf || reportLoading}
              onClick={() => void handleExportPdf()}
              type="button"
              title={canExportPdf ? "Export the current investigation report as a PDF" : "Only reviewer and admin roles can export reports"}
            >
              <FileDown size={16} />
              {exportingPdf ? "Exporting..." : "Export PDF"}
            </button>
          </div>
        </div>

        <InvestigationHeader
          candidateName={candidateName}
          candidateEmail={candidateEmail}
          initials={getInitials(candidateName)}
          risk={displayRisk}
          status={status}
          finalDecision={finalDecision}
          summaryText={summaryText}
          strongestReason={strongestReason}
          explanation={whyScoreText}
          score={formatNumber(displayScore)}
          confidence={formatNumber(displayConfidence)}
          confidenceLabel={getConfidenceLabel(displayConfidence)}
          metadataItems={headerMetadata}
        />

        <ViolationOverview items={overviewItems} />

        <section className="investigation-card investigation-insights-card">
          <div className="report-card-head accent">
            <h3>Most Suspicious Behaviors Observed</h3>
          </div>
          <div className="investigation-insights-list">
            {investigationInsights.map((item) => (
              <article className="investigation-insight-item" key={item}>
                <span className={`insight-dot ${String(displayRisk || "LOW").toLowerCase()}`}></span>
                <p>{item}</p>
              </article>
            ))}
          </div>
        </section>

        <div className="report-main-row investigation-main-grid">
          <EvidenceTable rows={sortedEvidenceRows} loading={reportLoading} />
          <ReviewerWorkflow
            caseStatus={caseStatus}
            assignedReviewer={assignedReviewer}
            caseId={activeCase?.id}
            workflowNote={workflowNote}
            onWorkflowNoteChange={setWorkflowNote}
            workflowBusy={workflowBusy}
            workflowMessage={workflowMessage}
            caseAccessError={caseAccessError}
            actionHistory={actionHistory}
            onAssign={() => void runCaseAction("assign", {})}
            onAddNote={() => void runCaseAction("notes", { comment: workflowNote })}
            onEscalate={() => void runCaseAction("transition", { new_status: "ESCALATED", comment: "Escalated from investigation console" })}
            onConfirmRisk={() => void runCaseAction("transition", { new_status: "CONFIRMED_RISK", comment: "Confirmed risk from investigation console" })}
            onFalsePositive={() => void runCaseAction("transition", { new_status: "FALSE_POSITIVE", comment: "Marked false positive from investigation console" })}
          />
        </div>

        <div className="report-bottom-row investigation-footer-grid">
          <RiskHistoryChart data={chartData} loading={reportLoading} />
          <TechnicalDetails items={technicalDetails} />
        </div>
      </section>
    </div>
  );
}

function SettingsPage({
  currentUser,
  apiBaseUrl,
  wsConnected,
  wsState,
  themeMode,
  onThemeModeChange,
  autoRefreshInterval,
  onAutoRefreshIntervalChange,
  liveUpdatesEnabled,
  onLiveUpdatesEnabledChange,
  notificationsEnabled,
  onNotificationsEnabledChange,
}) {
  const sessionState = currentUser ? "Authenticated" : "Signed out";
  const browserTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "System default";
  const appVersion = import.meta.env.VITE_APP_VERSION || "Demo Build";
  const wsStatus = wsState === "connected" ? "Connected" : liveUpdatesEnabled ? "Reconnecting" : "Paused";

  return (
    <div className="settings-page">
      <div className="page-title-block">
        <span className="page-kicker">DEMO ENVIRONMENT</span>
        <h1>Settings</h1>
        <p>Manage your account, environment, and platform preferences.</p>
      </div>

      <section className="settings-dashboard-grid">
        <SettingsDashboardCard
          title="Profile & Account"
          subtitle="Authenticated operator profile and current session details."
          className="settings-profile-card"
        >
          <div className="settings-profile-hero">
            <span className="settings-avatar">{getInitials(currentUser?.full_name || currentUser?.email)}</span>
            <div className="settings-profile-copy">
              <strong>{currentUser?.full_name || currentUser?.email || "Reviewer"}</strong>
              <p>{currentUser?.email || "No email available"}</p>
              <small>{currentUser?.role || "reviewer"}</small>
            </div>
            <span className="settings-role-badge">{(currentUser?.role || "reviewer").toUpperCase()}</span>
          </div>

          <div className="settings-list">
            <SettingsRow label="Session State" value={sessionState} />
            <SettingsRow label="Authenticated" value={currentUser ? "Yes" : "No"} />
            <SettingsRow label="Last Login" value="Today, current session" />
          </div>

          <div className="settings-card-footer">
            <button className="settings-secondary-btn" type="button" disabled>
              <User size={14} /> Manage Profile
            </button>
          </div>
        </SettingsDashboardCard>

        <SettingsDashboardCard
          title="Environment"
          subtitle="Local runtime diagnostics and operator context."
        >
          <div className="settings-list">
            <SettingsRow label="API Base URL" value={apiBaseUrl} />
            <SettingsRow label="App Version" value={appVersion} />
            <SettingsRow label="Mode" value="Local Demo" />
            <SettingsRow label="WebSocket" value={wsStatus} />
            <SettingsRow label="Current Role" value={currentUser?.role || "reviewer"} />
          </div>

          <div className="settings-card-footer">
            <button className="settings-secondary-btn" type="button" disabled>
              <ClipboardList size={14} /> Copy Diagnostics
            </button>
          </div>
        </SettingsDashboardCard>

        <SettingsDashboardCard
          title="Privacy & Data"
          subtitle="Privacy-first protections enforced in this demo environment."
        >
          <div className="settings-status-list">
            {[
              "No webcam access",
              "No audio recording",
              "No screen recording",
              "Clipboard content not stored",
              "Only behavioral signals analyzed",
              "All data stays local for demo",
            ].map((item) => (
              <div className="settings-status-item" key={item}>
                <span className="settings-status-icon"><CheckCircle2 size={14} /></span>
                <span>{item}</span>
              </div>
            ))}
          </div>
        </SettingsDashboardCard>

        <SettingsDashboardCard
          title="System Preferences"
          subtitle="Control how the console behaves in this browser."
        >
          <div className="settings-controls">
            <SettingsControl
              label="Theme"
              help="Persisted locally for this browser."
              control={(
                <select value={themeMode} onChange={(event) => onThemeModeChange(event.target.value)}>
                  <option value="dark">Dark</option>
                  <option value="midnight">Midnight</option>
                </select>
              )}
            />
            <SettingsControl
              label="Live Updates"
              help="Enable or pause websocket-driven updates."
              control={(
                <button
                  className={`settings-toggle ${liveUpdatesEnabled ? "enabled" : ""}`}
                  onClick={() => onLiveUpdatesEnabledChange(!liveUpdatesEnabled)}
                  type="button"
                >
                  <span></span>
                  {liveUpdatesEnabled ? "On" : "Off"}
                </button>
              )}
            />
            <SettingsControl
              label="Notifications"
              help="Stored locally for this device."
              control={(
                <button
                  className={`settings-toggle ${notificationsEnabled ? "enabled" : ""}`}
                  onClick={() => onNotificationsEnabledChange(!notificationsEnabled)}
                  type="button"
                >
                  <span></span>
                  {notificationsEnabled ? "On" : "Off"}
                </button>
              )}
            />
            <SettingsControl
              label="Auto Refresh"
              help="Choose how often data refreshes automatically."
              control={(
                <select value={autoRefreshInterval} onChange={(event) => onAutoRefreshIntervalChange(event.target.value)}>
                  <option value="0">Off</option>
                  <option value="15">15s</option>
                  <option value="30">30s</option>
                  <option value="60">60s</option>
                </select>
              )}
            />
            <SettingsControl label="Time Zone" help="Detected from your browser." control={<div className="settings-static-value">{browserTimeZone}</div>} />
          </div>
        </SettingsDashboardCard>

        <SettingsDashboardCard
          title="Reviewer Tools"
          subtitle="Operational defaults and shortcuts for future workflow tooling."
        >
          <div className="settings-placeholder-list">
            {[
              ["Queue View Defaults", "Saved case filters and sort presets"],
              ["Investigation Defaults", "Preferred evidence, chart, and history defaults"],
              ["Action Shortcuts", "Quick reviewer actions and keyboard mapping"],
              ["Export Settings", "Report packaging and redaction controls"],
            ].map(([title, text]) => (
              <div className="settings-placeholder-item" key={title}>
                <div>
                  <strong>{title}</strong>
                  <small>{text}</small>
                </div>
                <span className="settings-soon-tag">Coming soon</span>
              </div>
            ))}
          </div>
        </SettingsDashboardCard>

        <SettingsDashboardCard
          title="About"
          subtitle="Platform identity and operating posture."
        >
          <div className="settings-list">
            <SettingsRow label="Platform" value="ProctorIQ Platform" />
            <SettingsRow label="Capability" value="Real-Time Risk Intelligence" />
            <SettingsRow label="Purpose" value="Assessment Integrity Monitoring" />
            <SettingsRow label="Compliance" value="Privacy-first, no content capture" />
            <SettingsRow label="Support" value="Contact administrator" />
          </div>

          <div className="settings-card-footer">
            <button className="settings-secondary-btn" type="button" disabled>
              <RefreshCw size={14} /> Check for Updates
            </button>
          </div>
        </SettingsDashboardCard>
      </section>
    </div>
  );
}

function EnterpriseMetricCard({ title, value, subtitle, tone, icon, sparklineSeed }) {
  return (
    <div className={`enterprise-metric-card ${tone}`}>
      <div className="enterprise-metric-top">
        <span className="enterprise-metric-icon">{icon}</span>
        <strong>{title}</strong>
      </div>
      <div className="enterprise-metric-value">{value}</div>
      <p>{subtitle}</p>
      <MiniSparkline tone={tone} seed={sparklineSeed} />
    </div>
  );
}

function MiniSparkline({ tone = "low", seed }) {
  return (
    <svg className={`mini-sparkline ${tone}`} viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
      <polyline fill="none" points={buildSparklinePath(seed, tone)} />
    </svg>
  );
}

function PanelHeader({ title, subtitle, actionLabel, onActionClick, actionDisabled = false }) {
  return (
    <div className="panel-header-row">
      <div>
        <h3>{title}</h3>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
      {actionLabel ? (
        <button className="panel-header-action" disabled={actionDisabled} onClick={onActionClick} type="button">
          {actionLabel}
        </button>
      ) : null}
    </div>
  );
}

function SignalStatCard({ title, value, tone, icon }) {
  return (
    <div className={`signal-stat-card ${tone}`}>
      <span className="signal-stat-icon">{icon}</span>
      <strong>{value}</strong>
      <p>{title}</p>
    </div>
  );
}

function SystemHealthRow({ icon, label, value, tone }) {
  return (
    <div className="system-health-row">
      <span className="system-health-label">{icon}{label}</span>
      <span className={`system-health-value ${tone}`}>{value}</span>
    </div>
  );
}

function SettingsRow({ label, value }) {
  return (
    <div className="settings-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function SettingsControl({ label, help, control }) {
  return (
    <div className="settings-control-row">
      <div>
        <strong>{label}</strong>
        <p>{help}</p>
      </div>
      <div className="settings-control-slot">{control}</div>
    </div>
  );
}

function SettingsDashboardCard({ title, subtitle, className = "", children }) {
  return (
    <div className={`enterprise-panel settings-card settings-dashboard-card ${className}`.trim()}>
      <div className="panel-header-row settings-panel-header">
        <div>
          <h3>{title}</h3>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
      </div>
      {children}
    </div>
  );
}

function ProctorIQLogo({ className = "" }) {
  return (
    <svg className={className} viewBox="0 0 64 64" aria-hidden="true">
      <defs>
        <linearGradient id="proctoriq-shield-gradient" x1="12%" y1="10%" x2="88%" y2="92%">
          <stop offset="0%" stopColor="#6d5dfc" />
          <stop offset="100%" stopColor="#60a5fa" />
        </linearGradient>
        <radialGradient id="proctoriq-glow-gradient" cx="38%" cy="30%" r="76%">
          <stop offset="0%" stopColor="rgba(255,255,255,0.38)" />
          <stop offset="100%" stopColor="rgba(255,255,255,0)" />
        </radialGradient>
      </defs>
      <rect x="4" y="4" width="56" height="56" rx="18" fill="url(#proctoriq-glow-gradient)" opacity="0.28" />
      <path
        d="M32 8 49 13.5v15.7c0 11.4-6.2 20.8-17 27.4C21.2 50 15 40.6 15 29.2V13.5L32 8Z"
        fill="rgba(10,16,31,0.18)"
        stroke="url(#proctoriq-shield-gradient)"
        strokeWidth="1.8"
      />
      <path
        d="M32 16 43 19.6v10.2c0 7.7-4.1 14.2-11 19.1-6.9-4.9-11-11.4-11-19.1V19.6L32 16Z"
        fill="url(#proctoriq-shield-gradient)"
        opacity="0.95"
      />
      <path
        d="m25.2 31.9 5.1 5.2 10.6-12"
        fill="none"
        stroke="#f8fbff"
        strokeWidth="3.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M41.5 18.5c4.8 1.2 8.7 4.8 10 9.5"
        fill="none"
        stroke="#a5b4fc"
        strokeWidth="2"
        strokeLinecap="round"
        opacity="0.72"
      />
      <path
        d="M41.8 24c2.4.8 4.2 2.5 5 4.8"
        fill="none"
        stroke="#c4b5fd"
        strokeWidth="2"
        strokeLinecap="round"
        opacity="0.9"
      />
    </svg>
  );
}

function LoginPage({ onLogin, loading, error }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  return (
    <div className="login-page">
      <div className="login-card">
        <ProctorIQLogo className="brand-mark brand-mark-proctoriq" />
        <h1>ProctorIQ</h1>
        <p className="login-subtitle">Assessment Integrity Console</p>
        <p className="muted">Sign in to access the review queue and investigation reports.</p>

        <div className="login-form">
          <label>Email</label>
          <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="admin@example.com" />
          <label>Password</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="â€¢â€¢â€¢â€¢â€¢â€¢â€¢â€¢" />

          {error && <div className="login-error">{error}</div>}

          <button
            className="login-btn"
            disabled={loading || !email || !password}
            onClick={() => onLogin({ email, password })}
          >
            {loading ? "Signing inâ€¦" : "Sign In"}
          </button>
        </div>

        <div className="login-help muted">
          Backend must have `JWT_SECRET_KEY` set and an admin user seeded.
        </div>
      </div>
    </div>
  );
}

function ViolationTable({ events }) {
  if (!events.length) {
    return <div className="timeline-empty">No important events captured for this attempt yet.</div>;
  }

  return (
    <div className="violation-table">
      <div className="violation-header">
        <span>Time</span>
        <span>Event Type</span>
        <span>Explanation</span>
      </div>

      {events.map((event, index) => (
        <div className="violation-row" key={`${event.occurred_at}-${index}`}>
          <span className="violation-time">{formatTime(event.occurred_at)}</span>
          <span className="violation-type">
            <span className={eventTone(event.event_type)}>{eventIcon(event.event_type)}</span>
            <strong>{(event.event_type || "unknown").toUpperCase()}</strong>
          </span>
          <span className="violation-explanation">
            {eventLabel(event)}
            {event.payload && Object.keys(event.payload).length > 0 && (
              <small className="muted">{JSON.stringify(event.payload)}</small>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}

function AttemptPanel({ title, subtitle, emptyText, items, onOpen }) {
  return (
    <div className="panel">
      <PanelTitle title={title} subtitle={subtitle} />
      {items.length === 0 ? (
        <EmptyState text={emptyText} />
      ) : (
        <div className="attempt-list">
          {items.map((item, index) => {
            const status = getExamStatus(item);
            const lastAt = getLastActivityAt(item);
            const duration = getAttemptDurationSeconds(item);
            return (
              <button key={`${item.attempt_id}-${index}`} className="attempt-row" onClick={() => onOpen(item)}>
                <div className="attempt-left">
                  <div className="case-topline">
                    <span className={riskClass(item.risk)}>{item.risk || "LOW"}</span>
                    <span className={statusClass(status)}>{status}</span>
                  </div>
                  <strong>{getCandidateName(item)}</strong>
                  <small>{getAssessmentName(item)} â€¢ {item.attempt_id}</small>
                </div>

                <div className="attempt-right">
                  <div className="attempt-score">{formatNumber(item.combined_score)}</div>
                  <div className="attempt-meta">{formatDateTime(lastAt)} â€¢ {formatDuration(duration)}</div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function PageHeader({ eyebrow, title, subtitle, status }) {
  return (
    <header className="page-header">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
      <div className="page-status">{status}</div>
    </header>
  );
}

function PanelTitle({ title, subtitle }) {
  return (
    <div className="panel-title">
      <h3>{title}</h3>
      <span>{subtitle}</span>
    </div>
  );
}

function MetricCard({ title, value, icon, tone = "neutral" }) {
  return (
    <div className={`metric-card ${tone}`}>
      <div>
        <p>{title}</p>
        <h2>{value}</h2>
      </div>
      <div className="metric-icon">{icon}</div>
    </div>
  );
}

function MiniStat({ label, value }) {
  return (
    <div className="mini-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function EvidenceCard({ item }) {
  return (
    <div className={`evidence-card ${riskTone(item.severity)}`}>
      <div className="evidence-icon">{item.icon}</div>
      <div>
        <div className="evidence-head">
          <h4>{item.title}</h4>
          <span className={riskClass(item.severity)}>{item.severity}</span>
        </div>
        <strong>{item.finding}</strong>
        <p>{item.explanation}</p>
      </div>
    </div>
  );
}

function EmptyState({ text }) {
  return (
    <div className="empty-state">
      <Zap size={32} />
      <p>{text}</p>
    </div>
  );
}



