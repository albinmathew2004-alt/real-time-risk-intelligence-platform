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
  MoreVertical,
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
} from "lucide-react";

import "./App.css";
import EvidenceTable from "./components/EvidenceTable";
import InvestigationHeader from "./components/InvestigationHeader";
import ReviewerWorkflow from "./components/ReviewerWorkflow";
import RiskHistoryChart from "./components/RiskHistoryChart";
import TechnicalDetails from "./components/TechnicalDetails";
import ViolationOverview from "./components/ViolationOverview";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

function toWsUrl(baseUrl, path) {
  try {
    const url = new URL(baseUrl);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = path;
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    return `ws://127.0.0.1:8000${path}`;
  }
}

const API_URL = `${API_BASE_URL}/v1/logs`;
const WS_URL = toWsUrl(API_BASE_URL, "/ws/risk");
const EVENTS_URL = `${API_BASE_URL}/v1/events`;
const RISK_HISTORY_URL = `${API_BASE_URL}/v1/risk-history`;
const REPORTS_URL = `${API_BASE_URL}/v1/reports`;
const DASHBOARD_SUMMARY_URL = `${API_BASE_URL}/v1/dashboard/summary`;
const CASES_URL = `${API_BASE_URL}/v1/cases`;
const AUTH_URL = `${API_BASE_URL}/v1/auth`;

const TOKEN_KEY = "riskintel_access_token";

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
  if (!value) return "â€”";
  try {
    return new Date(value).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return value;
  }
}

function formatDateTime(value) {
  if (!value) return "â€”";
  try {
    return new Date(value).toLocaleString([], {
      hour: "2-digit",
      minute: "2-digit",
      day: "2-digit",
      month: "short",
    });
  } catch {
    return value;
  }
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
  const hasSubmit = events.some((e) => e.event_type === "exam_submitted");

  if (f?.has_submit_event === true) return "COMPLETED";

  if (latestEvent === "exam_submitted" || hasSubmit) return "COMPLETED";
  return "ONGOING";
}

function statusClass(status) {
  if (status === "COMPLETED") return "status completed";
  return "status ongoing";
}

function caseStatusTone(status) {
  if (status === "ESCALATED" || status === "CONFIRMED_RISK") return "urgent";
  if (status === "CLEARED" || status === "FALSE_POSITIVE" || status === "CLOSED") return "completed";
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

  return "No major violation detected";
}

function decisionText(risk) {
  if (risk === "HIGH") return "Urgent evaluator review recommended";
  if (risk === "MEDIUM") return "Manual review recommended";
  return "No immediate review required";
}

function decisionSummaryLine(risk) {
  if (risk === "HIGH") return "This attempt should be escalated for reviewer action before a final decision.";
  if (risk === "MEDIUM") return "This attempt should be manually reviewed before a final decision.";
  return "Current telemetry does not indicate a high-confidence integrity concern.";
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
      finding: "No major suspicious behavior detected",
      explanation: "The available behavioral signals do not indicate strong assessment integrity risk.",
    });
  }

  return evidence;
}

function reportSummary(attempt) {
  const risk = attempt?.risk || "LOW";

  if (risk === "HIGH") {
    return `This attempt needs urgent review. The strongest reason is: ${primaryIssue(attempt)}.`;
  }
  if (risk === "MEDIUM") {
    return `This attempt should be manually reviewed. The strongest reason is: ${primaryIssue(attempt)}.`;
  }
  return "This attempt currently appears normal. No strong suspicious behavioral pattern was detected.";
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

function getKeystrokeAnomalyCount(events) {
  return events.filter((event) => {
    const type = String(event.event_type || "").toLowerCase();
    if (type.includes("keystroke")) return true;
    const payloadFlag = findNestedPayloadValue(event.payload, ["keystroke_anomaly", "typing_anomaly", "keyboard_anomaly"]);
    return payloadFlag === true || Number(payloadFlag || 0) > 0;
  }).length;
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
  const keystrokeAnomalies = getKeystrokeAnomalyCount(events);
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
      key: "keystroke_anomalies",
      title: "Keystroke Anomalies",
      subtitle: "Typing Irregularities",
      value: String(keystrokeAnomalies),
      severity: getSeverityFromCount(keystrokeAnomalies, 1, 3),
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
  const keystrokeAnomalies = Number(counts.keystroke_anomaly_count || 0);
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
      key: "keystroke_anomalies",
      title: "Keystroke Anomalies",
      subtitle: "Typing Irregularities",
      value: String(keystrokeAnomalies),
      severity: getSeverityFromCount(keystrokeAnomalies, 1, 3),
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
  });

  for (const item of overviewItems) {
    if (item.severity === "LOW" && item.key !== "idle_time") continue;
    if (item.value === "0" || item.value === "00:00" || item.value === "0%") continue;
    rows.push({
      key: item.key,
      title: item.title,
      subtitle: item.subtitle,
      severity: item.severity,
      details: `${item.value} observed during this attempt`,
    });
  }

  rows.sort((a, b) => severityRank(b.severity) - severityRank(a.severity));
  return rows.slice(0, 6);
}

function buildEvidenceRowsFromReport(evidenceItems = []) {
  return evidenceItems.map((item) => ({
    key: String(item.signal_type || "signal").toLowerCase(),
    title: item.title || "Observed Signal",
    subtitle: item.explanation || "Behavioral evidence was observed for this attempt.",
    severity: item.severity || "LOW",
    details: `${Number(item.count || 0)} observed; ${Number(item.related_event_count || 0)} related event${Number(item.related_event_count || 0) === 1 ? "" : "s"}`,
  }));
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
  const date = parseDateValue(value);
  if (!date) return "No activity";
  const minutes = Math.max(0, Math.round((Date.now() - date.getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function isResolvedCaseStatus(status) {
  return ["CLEARED", "FALSE_POSITIVE", "CLOSED"].includes(status || "");
}

function isQueueResolvedStatus(status) {
  return [...["RESOLVED"], "CLEARED", "FALSE_POSITIVE", "CLOSED"].includes(status || "");
}

function getQueueStatus(caseRecord) {
  const status = caseRecord?.status || "NEW";
  if (status === "CLEARED" || status === "FALSE_POSITIVE") return "RESOLVED";
  return status;
}

function getStatusPriority(status) {
  if (status === "ESCALATED") return 0;
  if (status === "CONFIRMED_RISK") return 1;
  if (status === "UNDER_INVESTIGATION") return 2;
  if (status === "TRIAGED") return 3;
  if (status === "NEW") return 4;
  if (status === "RESOLVED") return 5;
  if (status === "CLOSED") return 6;
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
    new: rows.filter((row) => row.queueStatus === "NEW").length,
    under_investigation: rows.filter((row) => row.queueStatus === "UNDER_INVESTIGATION").length,
    escalated: rows.filter((row) => row.queueStatus === "ESCALATED").length,
    resolved: rows.filter((row) => row.queueStatus === "RESOLVED").length,
    closed: rows.filter((row) => row.queueStatus === "CLOSED").length,
  };
}

function filterQueueRows(rows, tab) {
  if (tab === "all") return rows;
  if (tab === "new") return rows.filter((row) => row.queueStatus === "NEW");
  if (tab === "under_investigation") return rows.filter((row) => row.queueStatus === "UNDER_INVESTIGATION");
  if (tab === "escalated") return rows.filter((row) => row.queueStatus === "ESCALATED");
  if (tab === "resolved") return rows.filter((row) => row.queueStatus === "RESOLVED");
  if (tab === "closed") return rows.filter((row) => row.queueStatus === "CLOSED");
  return rows;
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

async function authJsonFetch(url, { token, method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(url, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
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
}

export default function App() {
  const [page, setPage] = useState("dashboard");
  const [logs, setLogs] = useState([]);
  const [cases, setCases] = useState([]);
  const [dashboardSummary, setDashboardSummary] = useState(null);
  const [caseAccessError, setCaseAccessError] = useState("");
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [riskFilter, setRiskFilter] = useState({ LOW: true, MEDIUM: true, HIGH: true });

  const [token, setToken] = useState(() => getStoredToken());
  const [currentUser, setCurrentUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(false);
  const [authError, setAuthError] = useState("");

  const logout = useCallback(() => {
    setStoredToken(null);
    setToken(null);
    setCurrentUser(null);
    setCases([]);
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
      const result = await authJsonFetch(DASHBOARD_SUMMARY_URL, { token });
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
    return { ok: true, data: result.data };
  }, [fetchCases, logout, token]);

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
      if (token) void fetchCases();
      if (token) void fetchDashboardSummary();
    }, 0);
    const ws = new WebSocket(WS_URL);

    ws.onopen = () => setWsConnected(true);
    ws.onclose = () => setWsConnected(false);
    ws.onerror = () => setWsConnected(false);

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
        void fetchCases();
        void fetchDashboardSummary();
      } catch (err) {
        console.error("WS parse error:", err);
      }
    };

    return () => {
      window.clearTimeout(timeoutId);
      ws.close();
    };
  }, [fetchCases, fetchDashboardSummary, fetchLogs, token]);

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
          <div className="brand-mark"><ShieldAlert size={24} /></div>
          <h2>RiskIntel</h2>
          <p>Assessment Integrity Console</p>
          {currentUser && (
            <div className="user-pill">
              <div>
                <strong>{currentUser.full_name || currentUser.email}</strong>
                <small>{currentUser.email} â€¢ {currentUser.role}</small>
              </div>
              <button className="logout-btn" onClick={logout}>Logout</button>
            </div>
          )}
          <div className={`connection-pill ${wsConnected ? "online" : "offline"}`}>
            <span className="pulse"></span>
            {wsConnected ? "Live stream connected" : "Live stream offline"}
          </div>
        </div>

        <nav className="nav">
          <button className={page === "dashboard" ? "active" : ""} onClick={() => setPage("dashboard")}>
            <Home size={18} /> Command Center
          </button>
          <button className={page === "queue" ? "active" : ""} onClick={() => setPage("queue")}>
            <ListChecks size={18} /> Review Queue
          </button>
          <button className={page === "report" ? "active" : ""} onClick={() => setPage("report")}>
            <ShieldAlert size={18} /> Investigation Report
          </button>
          <button className={page === "settings" ? "active" : ""} onClick={() => setPage("settings")}>
            <Settings size={18} /> Settings
          </button>
        </nav>

        <div className="sidebar-footer">
          <div className={`connection-card ${wsConnected ? "online" : "offline"}`}>
            <div className="connection-card-row">
              <span className="pulse"></span>
              <strong>{wsConnected ? "Live stream connected" : "Live stream disconnected"}</strong>
            </div>
            <small>{wsConnected ? "Receiving events" : "Waiting for stream recovery"}</small>
          </div>

          {currentUser ? (
            <button className="sidebar-user-card" type="button">
              <span className="sidebar-user-avatar">{getInitials(currentUser.full_name || currentUser.email)}</span>
              <span className="sidebar-user-copy">
                <strong>{currentUser.full_name || currentUser.email}</strong>
                <small>{currentUser.role}</small>
              </span>
              <ChevronDown size={16} />
            </button>
          ) : null}
        </div>
      </aside>

      <main className="main">
        <div className="shell-toolbar">
          {page === "dashboard" ? <span className="shell-live-badge"><span className="pulse"></span>LIVE</span> : null}
          {currentUser ? (
            <button className="shell-user-button" type="button">
              <span className="shell-user-avatar">{getInitials(currentUser.full_name || currentUser.email)}</span>
              <span className="shell-user-details">
                <strong>{currentUser.full_name || currentUser.email}</strong>
                <small>{currentUser.role}</small>
              </span>
              <ChevronDown size={16} />
            </button>
          ) : null}
          <button className="shell-logout-btn" onClick={logout} type="button">
            <ArrowRight size={16} /> Logout
          </button>
        </div>

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
          />
        )}

        {page === "queue" && (
          <QueuePage
            logs={logs}
            cases={cases}
            caseByAttemptId={caseByAttemptId}
            caseAccessError={caseAccessError}
            currentUser={currentUser}
            onRefresh={() => {
              void fetchLogs();
              void fetchCases();
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
            onUnauthorized={logout}
            onCaseAction={performCaseAction}
            onBack={() => setPage("queue")}
          />
        )}

        {page === "settings" && (
          <SettingsPage currentUser={currentUser} apiBaseUrl={API_BASE_URL} wsConnected={wsConnected} />
        )}
      </main>
    </div>
  );
}

function DashboardPage({ logs, cases, stats, dashboardSummary, currentUser, setSelected, setPage, loading, wsConnected }) {
  const sortedLogs = [...logs].sort((a, b) => compareIso(b?.timestamp, a?.timestamp));
  const caseByAttemptId = Object.fromEntries(cases.map((item) => [item.attempt_id, item]));
  const reviewRows = buildReviewQueueItems(logs, caseByAttemptId);
  const fallbackFeedRows = sortedLogs.slice(0, 5);
  const fallbackReviewCases = reviewRows.filter((row) => !isQueueResolvedStatus(row.queueStatus)).slice(0, 5);
  const unresolvedCases = cases.filter((item) => !isResolvedCaseStatus(item.status) && item.status !== "CLOSED").length;
  const fallbackSignalSummary = buildAggregateSignals(logs);
  const fallbackLiveEventRate = getLiveEventRate(logs);
  const riskTotal = Math.max(1, stats.total);
  const fallbackLastUpdated = sortedLogs[0]?.timestamp || null;
  const feedRows = Array.isArray(dashboardSummary?.recent_risk_feed) && dashboardSummary.recent_risk_feed.length
    ? dashboardSummary.recent_risk_feed
    : fallbackFeedRows;
  const reviewCases = Array.isArray(dashboardSummary?.cases_needing_review) && dashboardSummary.cases_needing_review.length
    ? dashboardSummary.cases_needing_review
    : fallbackReviewCases;
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
  const metricTotalAttempts = dashboardSummary?.total_attempts ?? stats.total;
  const metricHighRisk = dashboardSummary?.high_risk_count ?? stats.high;
  const metricMediumRisk = dashboardSummary?.medium_risk_count ?? stats.medium;
  const metricLowRisk = dashboardSummary?.low_risk_count ?? stats.low;
  const metricActiveSessions = dashboardSummary?.active_sessions ?? stats.ongoing;
  const metricNeedsReview = dashboardSummary?.needs_review_count ?? unresolvedCases;
  const metricAvgConfidence = dashboardSummary?.avg_confidence ?? stats.avgConfidence;

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
          <PanelHeader title="Live Risk Feed" subtitle="Real-time suspicious activity" actionLabel="View All" />
          {feedRows.length === 0 ? (
            <EmptyState text="No recent live activity has been received yet." />
          ) : (
            <div className="activity-feed-list">
              {feedRows.map((item) => (
                <button
                  className="activity-feed-row"
                  key={item.attempt_id}
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
          <div className="panel-footer-link">Showing latest 5 events <span>View All Activity</span></div>
        </div>

        <div className="enterprise-panel">
          <PanelHeader title="Cases Needing Review" subtitle="Prioritized unresolved cases" actionLabel="View Review Queue" />
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
                <span>Last Activity</span>
                <span>Action</span>
              </div>
              {reviewCases.map((row, index) => (
                <div className="review-priority-row" key={row.attemptId || row.attempt_id}>
                  <span className={`priority-index tone-${riskTone(row.risk || row.risk_level)}`}>{row.priority || index + 1}</span>
                  <span className="review-priority-candidate">
                    <strong>{row.candidateName || row.candidate_name || "Unknown Candidate"}</strong>
                    <small>{row.attemptId || row.attempt_id}</small>
                  </span>
                  <span className={riskClass(row.risk || row.risk_level)}>{row.risk || row.risk_level}</span>
                  <span className={caseStatusClass(row.queueStatus || row.status)}>{row.queueStatus || row.status}</span>
                  <span>{formatNumber(row.score)}</span>
                  <span>{formatDateTime(row.lastActivity || row.last_activity)}</span>
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
          <div className="panel-footer-link">Showing top 5 cases <span>View All Cases</span></div>
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
            <SystemHealthRow icon={<Wifi size={16} />} label="WebSocket Stream" value={dashboardSummary?.system_health_basic?.websocket_stream || (wsConnected ? "Connected" : "Disconnected")} tone={(dashboardSummary?.system_health_basic?.websocket_stream || (wsConnected ? "Connected" : "Disconnected")) === "Disconnected" ? "danger" : "success"} />
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

function QueuePage({ logs, caseByAttemptId, caseAccessError, currentUser, onRefresh, setSelected, setPage }) {
  const [activeTab, setActiveTab] = useState("all");
  const [pageIndex, setPageIndex] = useState(0);
  const rowsPerPage = 8;
  const reviewRows = useMemo(() => buildReviewQueueItems(logs, caseByAttemptId), [logs, caseByAttemptId]);
  const tabCounts = useMemo(() => buildQueueTabCounts(reviewRows), [reviewRows]);
  const filteredRows = useMemo(() => filterQueueRows(reviewRows, activeTab), [reviewRows, activeTab]);
  const totalPages = Math.max(1, Math.ceil(filteredRows.length / rowsPerPage));
  const currentPage = Math.min(pageIndex, totalPages - 1);
  const pageRows = filteredRows.slice(currentPage * rowsPerPage, currentPage * rowsPerPage + rowsPerPage);

  const summary = {
    highPriority: reviewRows.filter((row) => row.risk === "HIGH" && !isQueueResolvedStatus(row.queueStatus)).length,
    mediumPriority: reviewRows.filter((row) => row.risk === "MEDIUM" && !isQueueResolvedStatus(row.queueStatus)).length,
    newCases: reviewRows.filter((row) => row.queueStatus === "NEW").length,
    underInvestigation: reviewRows.filter((row) => row.queueStatus === "UNDER_INVESTIGATION").length,
    resolved: reviewRows.filter((row) => row.queueStatus === "RESOLVED").length,
    total: reviewRows.length,
  };

  const queueHealth = [
    { label: "High", count: summary.highPriority, color: "#ff5f67" },
    { label: "Medium", count: summary.mediumPriority, color: "#ffb703" },
    { label: "Low", count: reviewRows.filter((row) => row.risk === "LOW" && !isQueueResolvedStatus(row.queueStatus)).length, color: "#65d46e" },
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
  const reviewerMap = new Map();
  for (const row of reviewRows) {
    reviewerMap.set(row.assignedTo, (reviewerMap.get(row.assignedTo) || 0) + 1);
  }
  if (currentUser && reviewerMap.size === 0) reviewerMap.set(currentUser.full_name || currentUser.email, 0);
  const topReviewers = Array.from(reviewerMap.entries()).sort((a, b) => b[1] - a[1]).slice(0, 4);

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

  return (
    <div className="review-queue-page">
      <div className="page-title-row">
        <div className="page-title-block">
          <h1>Review Queue</h1>
          <p>Monitor and review all assessment attempts requiring investigation.</p>
        </div>
        <div className="page-inline-actions">
          <button className="toolbar-button" type="button"><User size={16} /> All Reviewers <ChevronDown size={14} /></button>
          <button className="toolbar-button" type="button"><SlidersHorizontal size={16} /> Filters <ChevronDown size={14} /></button>
          <button className="toolbar-button" onClick={onRefresh} type="button"><RefreshCw size={16} /> Refresh</button>
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

      <section className="enterprise-panel queue-table-shell">
        <div className="queue-tabs-row">
          <div className="queue-tabs">
            {tabs.map((tab) => (
              <button
                className={`queue-tab ${activeTab === tab.key ? "active" : ""}`}
                key={tab.key}
                onClick={() => {
                  setActiveTab(tab.key);
                  setPageIndex(0);
                }}
                type="button"
              >
                {tab.label}
                <span>{tab.count}</span>
              </button>
            ))}
          </div>
          <div className="queue-sort-control">Sort by: <button className="toolbar-button small" type="button">Priority (High to Low) <ChevronDown size={14} /></button></div>
        </div>

        {caseAccessError ? <div className="workflow-message workflow-message-warning">{caseAccessError}</div> : null}

        <div className="queue-enterprise-table">
          <div className="queue-enterprise-head">
            <span></span>
            <span>Priority</span>
            <span>Candidate / Attempt</span>
            <span>Assessment</span>
            <span>Risk Score</span>
            <span>Risk Level</span>
            <span>Status</span>
            <span>Assigned To</span>
            <span>Last Activity</span>
            <span>Action</span>
            <span></span>
          </div>

          {pageRows.length === 0 ? (
            <EmptyState text="No cases match the selected queue tab." />
          ) : (
            pageRows.map((row, index) => (
              <div className="queue-enterprise-row" key={row.attemptId}>
                <span><input type="checkbox" /></span>
                <span className={`priority-index tone-${riskTone(row.risk)}`}>{currentPage * rowsPerPage + index + 1}</span>
                <span className="queue-candidate-block">
                  <span className="queue-candidate-avatar">{getInitials(row.candidateName)}</span>
                  <span>
                    <strong>{row.candidateName}</strong>
                    <small>{row.attemptId}</small>
                  </span>
                </span>
                <span className="queue-assessment-cell">{row.assessmentName}</span>
                <span className="queue-score-cell">
                  <strong>{formatNumber(row.score)}</strong>
                  <MiniSparkline tone={riskTone(row.risk)} seed={`${row.attemptId}-${row.score}`} />
                </span>
                <span className={riskClass(row.risk)}>{row.risk}</span>
                <span className={caseStatusClass(row.queueStatus)}>{row.queueStatus}</span>
                <span>{row.assignedTo}</span>
                <span className="queue-last-activity">
                  <strong>{formatDateTime(row.lastActivity)}</strong>
                  <small>{formatRelativeTime(row.lastActivity)}</small>
                </span>
                <span>
                  <button
                    className="action-link-button"
                    onClick={() => {
                      setSelected(row.item);
                      setPage("report");
                    }}
                    type="button"
                  >
                    View
                  </button>
                </span>
                <span><MoreVertical size={16} /></span>
              </div>
            ))
          )}
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
          <span className="rows-per-page">Rows per page: <button className="toolbar-button small" type="button">10 <ChevronDown size={14} /></button></span>
        </div>
      </section>

      <section className="queue-bottom-grid">
        <div className="enterprise-panel">
          <PanelHeader title="Queue Health" />
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

        <div className="enterprise-panel">
          <PanelHeader title="SLA Compliance" />
          <div className="sla-card">
            <div className="sla-ring" style={buildDonutStyle([{ value: slaPercent, color: "#65d46e" }, { value: Math.max(0, 100 - slaPercent), color: "rgba(255,255,255,0.08)" }])}>
              <span>{slaPercent}%</span>
            </div>
            <div className="sla-copy">
              <strong>Within SLA {withinSla}</strong>
              <small>Breached SLA {Math.max(0, reviewRows.length - withinSla)}</small>
            </div>
          </div>
        </div>

        <div className="enterprise-panel">
          <PanelHeader title="Average Review Time" />
          <div className="avg-time-card">
            <div className="avg-time-circle"><Clock3 size={22} /></div>
            <div>
              <strong>{avgReviewSeconds ? formatDuration(avgReviewSeconds) : "18:00"}</strong>
              <small>Average time to resolution</small>
            </div>
          </div>
        </div>

        <div className="enterprise-panel">
          <PanelHeader title="Top Reviewers" />
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
function ReportPage({ selected, selectedCase, caseAccessError, token, onUnauthorized, onCaseAction, onBack }) {
  const [events, setEvents] = useState([]);
  const [riskHistory, setRiskHistory] = useState([]);
  const [liveRisk, setLiveRisk] = useState(null);
  const [reportData, setReportData] = useState(null);
  const [caseDetail, setCaseDetail] = useState(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [workflowNote, setWorkflowNote] = useState("");
  const [workflowBusy, setWorkflowBusy] = useState(false);
  const [workflowMessage, setWorkflowMessage] = useState("");

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
        setReportData(reportJson?.status === "success" ? reportJson.data || null : null);
      } catch (err) {
        console.error("Failed to fetch report data:", err);
        setReportData(null);
      } finally {
        setReportLoading(false);
      }
    }
    void fetchReportData();
  }, [onUnauthorized, selected?.attempt_id, selected?.combined_score, selected?.event_count, selected?.latest_event?.event_type, selected?.latest_event?.occurred_at, token]);

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
  const status = getExamStatus(reportAttempt, events);
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
  const chartData = riskHistory.map((item) => ({
    ...item,
    label: formatTime(item.timestamp).slice(0, 5),
    score: item.score ?? item.combined_score,
    risk_level: item.risk_level || item.risk,
  }));

  return (
    <section className={`investigation-report-shell report-${riskTone(displayRisk)}`}>
      <div className="report-toolbar">
        <button className="back-link-btn" onClick={onBack} type="button">
          <ArrowLeft size={16} />
          Back to Review Queue
        </button>
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
      />

      <ViolationOverview items={overviewItems} />

      <div className="investigation-main-grid">
        <EvidenceTable rows={evidenceRows} />
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

      <div className="investigation-footer-grid">
        <RiskHistoryChart data={chartData} loading={reportLoading} />
        <TechnicalDetails items={technicalDetails} />
      </div>
    </section>
  );
}

function SettingsPage({ currentUser, apiBaseUrl, wsConnected }) {
  return (
    <div className="settings-page">
      <div className="page-title-block">
        <span className="page-kicker">DEMO ENVIRONMENT</span>
        <h1>Settings</h1>
        <p>Review account, environment, privacy, and upcoming administration controls.</p>
      </div>

      <section className="settings-grid">
        <div className="enterprise-panel settings-card">
          <PanelHeader title="Profile / Account" />
          <div className="settings-profile">
            <span className="settings-avatar">{getInitials(currentUser?.full_name || currentUser?.email)}</span>
            <div>
              <strong>{currentUser?.full_name || currentUser?.email || "Reviewer"}</strong>
              <p>{currentUser?.email || "No email available"}</p>
              <small>Role: {currentUser?.role || "reviewer"}</small>
            </div>
          </div>
        </div>

        <div className="enterprise-panel settings-card">
          <PanelHeader title="Environment" />
          <div className="settings-list">
            <SettingsRow label="System Mode" value="Local Demo" />
            <SettingsRow label="API Base URL" value={apiBaseUrl} />
            <SettingsRow label="WebSocket Stream" value={wsConnected ? "Connected" : "Disconnected"} />
            <SettingsRow label="Reviewer Workflow" value="Enabled" />
          </div>
        </div>

        <div className="enterprise-panel settings-card">
          <PanelHeader title="Privacy Summary" />
          <div className="settings-list">
            <SettingsRow label="Webcam" value="Not used" />
            <SettingsRow label="Audio" value="Not recorded" />
            <SettingsRow label="Screen Recording" value="Disabled" />
            <SettingsRow label="Clipboard Content" value="Content not stored" />
          </div>
        </div>

        <div className="enterprise-panel settings-card">
          <PanelHeader title="Coming Soon" />
          <div className="coming-soon-grid">
            {[
              "Organization settings",
              "Reviewer management",
              "WebSocket auth controls",
              "Report export configuration",
            ].map((item) => (
              <div className="coming-soon-item" key={item}>
                <Settings size={16} />
                <span>{item}</span>
                <small>Future work</small>
              </div>
            ))}
          </div>
        </div>
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

function PanelHeader({ title, subtitle, actionLabel }) {
  return (
    <div className="panel-header-row">
      <div>
        <h3>{title}</h3>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
      {actionLabel ? <button className="panel-header-action" type="button">{actionLabel}</button> : null}
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

function LoginPage({ onLogin, loading, error }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="brand-mark"><ShieldAlert size={24} /></div>
        <h1>RiskIntel Console</h1>
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



