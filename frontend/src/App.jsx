import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import html2canvas from "html2canvas";
import jsPDF from "jspdf";
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
  Maximize2,
  Minimize2,
  CheckCheck,
  BookOpen,
  FileCode2,
  Brain,
  Shield,
  WifiOff,
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
const DEMO_SIGNAL_TOASTS_ENABLED = String(
  import.meta.env.VITE_DEMO_SIGNAL_TOASTS_ENABLED
  ?? import.meta.env.DEMO_SIGNAL_TOASTS_ENABLED
  ?? "true",
).toLowerCase() !== "false";
const DEMO_SIGNAL_TOAST_DISMISS_MS = 4800;
const DEMO_SIGNAL_TOAST_COOLDOWN_MS = 4500;
const DEMO_SIGNAL_TOAST_LIMIT = 2;

/* ── Shared Demo Signal Map ─────────────────────────────────────────────
   Maps telemetry signal keys → toast label, severity, and report-style
   category.  The same categories appear in the Evidence Table and
   Violation Overview so observers see narrative continuity.            */
const DEMO_SIGNAL_MAP = {
  visibility_hidden:   { key: "visibility_hidden",   group: "focus_shift",        label: "Focus changed away from assessment",         severity: "medium", category: "Focus Blur" },
  visibility_visible:  { key: "visibility_visible",  group: "focus_recovery",     label: "Focus returned after interruption",           severity: "low",    category: "Focus Blur" },
  focus_loss:          { key: "focus_loss",           group: "focus_shift",        label: "Focus changed away from assessment",         severity: "medium", category: "Focus Blur" },
  focus_return:        { key: "focus_return",         group: "focus_recovery",     label: "Focus returned after interruption",           severity: "low",    category: "Focus Blur" },
  clipboard_paste:     { key: "clipboard_paste",      group: "clipboard_activity", label: "Clipboard paste recorded",                   severity: "high",   category: "Clipboard" },
  clipboard_copy:      { key: "clipboard_copy",       group: "clipboard_activity", label: "Clipboard copy recorded",                    severity: "low",    category: "Clipboard" },
  large_answer_insert: { key: "large_answer_insert",  group: "answer_shift",       label: "Large answer block inserted",                severity: "high",   category: "Rapid Answer" },
  rapid_answer_change: { key: "rapid_answer_change",  group: "answer_shift",       label: "Rapid answer rewrite detected",              severity: "medium", category: "Rapid Answer" },
  idle_period:         { key: "idle_period",          group: "idle_transition",    label: "Long inactivity followed by answer activity", severity: "medium", category: "Idle" },
  idle_recovery:       { key: "idle_recovery",        group: "idle_transition",    label: "Activity resumed after idle period",          severity: "low",    category: "Idle" },
};

/* ── Sequence Pattern Definitions ───────────────────────────────────────
   Each pattern lists an ordered subset of signal keys that must ALL
   appear within the recent-signal sliding window to trigger a sequence
   toast.  `replacesKeys` lists individual toast keys that should be
   dismissed if the sequence toast fires.                               */
const DEMO_SEQUENCE_PATTERNS = [
  {
    id: "focus_paste_rapid",
    keys: ["visibility_hidden", "clipboard_paste", "rapid_answer_change"],
    label: "Sequence detected: tab switch \u2192 paste \u2192 rapid overwrite",
    severity: "high",
    category: "Correlated Pattern",
    replacesKeys: ["visibility_hidden", "clipboard_paste", "rapid_answer_change"],
  },
  {
    id: "focus_paste",
    keys: ["visibility_hidden", "clipboard_paste"],
    label: "Sequence detected: focus loss \u2192 clipboard paste",
    severity: "high",
    category: "Correlated Pattern",
    replacesKeys: ["visibility_hidden", "clipboard_paste"],
  },
  {
    id: "return_insert",
    keys: ["visibility_visible", "large_answer_insert"],
    label: "Sequence detected: focus return \u2192 large answer insertion",
    severity: "high",
    category: "Correlated Pattern",
    replacesKeys: ["visibility_visible", "large_answer_insert"],
  },
  {
    id: "idle_paste",
    keys: ["idle_period", "clipboard_paste"],
    label: "Sequence detected: idle period \u2192 paste \u2192 minimal editing",
    severity: "high",
    category: "Correlated Pattern",
    replacesKeys: ["idle_period", "clipboard_paste"],
  },
  {
    id: "idle_insert",
    keys: ["idle_recovery", "large_answer_insert"],
    label: "Sequence detected: idle period \u2192 large answer insertion",
    severity: "high",
    category: "Correlated Pattern",
    replacesKeys: ["idle_recovery", "large_answer_insert"],
  },
  {
    id: "focus_loss_paste",
    keys: ["focus_loss", "clipboard_paste"],
    label: "Sequence detected: focus loss \u2192 clipboard paste",
    severity: "high",
    category: "Correlated Pattern",
    replacesKeys: ["focus_loss", "clipboard_paste"],
  },
];

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
let reviewerRefreshDueAtMs = 0;
const SIDEBAR_EXPANDED_WIDTH = 248;
const SIDEBAR_COLLAPSED_WIDTH = 80;

function getReportAttemptIdFromLocation() {
  if (typeof window === "undefined") return "";
  try {
    return new URLSearchParams(window.location.search).get("attemptId") || "";
  } catch {
    return "";
  }
}

function replaceReportAttemptIdInLocation(attemptId) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (attemptId) {
    url.searchParams.set("attemptId", attemptId);
  } else {
    url.searchParams.delete("attemptId");
  }
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function getDemoCompletionFromLocation() {
  if (typeof window === "undefined") return null;
  try {
    const params = new URLSearchParams(window.location.search);
    const attemptId = params.get("demoAttemptId") || "";
    if (!attemptId) return null;
    return {
      attemptId,
      submittedAt: params.get("demoSubmittedAt") || "",
      candidateName: params.get("demoCandidateName") || "",
      assessmentId: params.get("demoAssessmentId") || "",
      reportReady: params.get("demoReportReady") === "true",
      provenanceReady: params.get("demoProvenanceReady") === "true",
    };
  } catch {
    return null;
  }
}

function replaceDemoCompletionInLocation(completion) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  const keys = [
    "demoAttemptId",
    "demoSubmittedAt",
    "demoCandidateName",
    "demoAssessmentId",
    "demoReportReady",
    "demoProvenanceReady",
  ];
  keys.forEach((key) => url.searchParams.delete(key));
  if (completion?.attemptId) {
    url.searchParams.set("demoAttemptId", completion.attemptId);
    if (completion.submittedAt) url.searchParams.set("demoSubmittedAt", completion.submittedAt);
    if (completion.candidateName) url.searchParams.set("demoCandidateName", completion.candidateName);
    if (completion.assessmentId) url.searchParams.set("demoAssessmentId", completion.assessmentId);
    url.searchParams.set("demoReportReady", completion.reportReady ? "true" : "false");
    url.searchParams.set("demoProvenanceReady", completion.provenanceReady ? "true" : "false");
  }
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function getPublicDemoReportAttemptIdFromPath() {
  if (typeof window === "undefined") return "";
  const match = window.location.pathname.match(/^\/demo\/report\/([^/?#]+)/i);
  return match ? decodeURIComponent(match[1]) : "";
}

function buildReportSelectionFallback(source, attemptIdOverride = "") {
  if (!source && !attemptIdOverride) return null;
  const attemptId = attemptIdOverride || source?.attempt_id || source?.attemptId || "";
  if (!attemptId) return null;
  return {
    attempt_id: attemptId,
    candidate_name: source?.candidate_name || source?.candidateName || attemptId,
    candidate_email: source?.candidate_email || source?.candidateEmail || "",
    assessment_name: source?.assessment_name || source?.assessmentName || "",
    combined_score: source?.combined_score ?? source?.risk_score ?? source?.score ?? 0,
    confidence: source?.confidence ?? 0,
    risk: source?.risk || source?.risk_level || "LOW",
    timestamp: source?.timestamp || source?.last_activity || source?.updated_at || source?.created_at || null,
    event_count: source?.event_count ?? source?.eventCount ?? 0,
    attempt_status: source?.attempt_status || source?.status || null,
  };
}

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

function getDashboardAssessmentLabel(item) {
  const rawValue = String(item?.assessmentName || item?.assessment_name || item?.assessmentId || item?.assessment_id || item?.attempt_id || "").trim();
  const normalized = rawValue.toLowerCase();

  if (!normalized) return "Assessment";
  if (normalized.includes("frontend")) return "Frontend Debugging";
  if (normalized.includes("sql")) return "SQL Analysis";
  if (normalized.includes("reason") || normalized.includes("logical")) return "Logical Reasoning";
  if (normalized.includes("security") || normalized.includes("investigation")) return "Security Investigation";
  if (normalized.includes("coding") || normalized.includes("python")) return "Coding Assessment";

  const cleaned = rawValue
    .replace(/^assessment[_\s-]*/i, "")
    .replace(/^demo[_\s-]public[_\s-]*/i, "")
    .replace(/\b(assessment|demo|public)\b/gi, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (!cleaned) return "Assessment";

  return cleaned
    .split(" ")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");
}

function getDashboardReviewStatus(row) {
  const status = normalizeQueueStatus(row?.queueStatus || row?.status || "NEW");
  if (status === "ESCALATED") return "Escalated for reviewer attention";
  if (status === "UNDER_INVESTIGATION") return "Under active review";
  if (status === "TRIAGED") return "Triaged for follow-up";
  if (status === "NEW") return "Awaiting initial review";
  return formatWorkflowStatus(status);
}

function getExamStatus(item, events = []) {
  const f = getFeatures(item);
  const latestEvent = item?.latest_event?.event_type;
  const canonicalStatus = String(item?.attempt_status || item?.status || "").toUpperCase();
  const hasSubmit = events.some((e) => ["exam_submitted", "assessment_submitted", "submit", "completed"].includes(String(e.event_type || "").toLowerCase()));

  if (canonicalStatus === "EXPIRED" || canonicalStatus === "ABANDONED") {
    return canonicalStatus;
  }

  if (canonicalStatus === "SUBMITTED" || canonicalStatus === "UNDER_REVIEW" || canonicalStatus === "RESOLVED" || canonicalStatus === "COMPLETED") {
    return "COMPLETED";
  }

  if (f?.has_submit_event === true) return "COMPLETED";

  if (["exam_submitted", "assessment_submitted", "submit", "completed"].includes(String(latestEvent || "").toLowerCase()) || hasSubmit) return "COMPLETED";
  return "ONGOING";
}

function statusClass(status) {
  if (status === "COMPLETED") return "status completed";
  if (status === "EXPIRED" || status === "ABANDONED") return "status review";
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
  const blur = Number(f.focus_blur_count || 0);
  const idle = Number(f.idle_spike_count || 0);
  const avgTime = Number(f.time_per_question_mean_s || 0);

  if (paste > 0 && tab > 0 && avgTime > 0 && avgTime <= 20) {
    return "Clipboard + tab switch + fast answering pattern";
  }
  if (paste > 0 && tab > 0) return "Clipboard and tab switching detected";
  if (paste > 0) return "Clipboard activity detected";
  if (tab > 0) return "Tab switching detected";
  if (blur > 0) return "Minor focus interruptions observed";
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
  const blur = Number(f.focus_blur_count || 0);
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

  if (blur > 0) {
    evidence.push({
      title: "Focus Loss Events",
      icon: <CircleDot size={21} />,
      severity: blur >= 7 ? "HIGH" : blur >= 4 ? "MEDIUM" : "LOW",
      finding: `${blur} focus interruption${blur === 1 ? "" : "s"} observed`,
      explanation: blur >= 4
        ? "Window focus was interrupted repeatedly and should be reviewed in context with nearby answer activity."
        : "Minor focus interruptions were observed without sustained suspicious progression.",
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

function getDashboardStrongestReason(item) {
  const candidates = [
    item?.strongest_reason,
    item?.strongest_signal,
    item?.attempt_summary,
    item?.explanation_text,
    item?.explanation,
    getLatestActivity(item),
    primaryIssue(item),
  ];

  const rawReason = candidates.find((value) => typeof value === "string" && value.trim()) || "Review signals detected";
  const cleanedReason = rawReason
    .replace(/^behavioral irregularities were detected and warrant reviewer attention\.?\s*/i, "")
    .replace(/^review recommended due to\s*/i, "")
    .replace(/^candidate\s+/i, "")
    .trim();

  const firstSegment = cleanedReason
    .split(/(?:\.\s+|;\s+|\s+\|\s+|\s+and\s+then\s+)/)
    .map((segment) => segment.trim())
    .find(Boolean) || cleanedReason;

  if (firstSegment.length <= 72) return firstSegment;
  return `${firstSegment.slice(0, 69).trimEnd()}...`;
}

function getDashboardFeedTimestamp(item) {
  return item?.latest_event?.occurred_at || item?.latest_event?.timestamp || item?.submitted_at || item?.timestamp || item?.last_activity || null;
}

function isMeaningfulDashboardFeedItem(item) {
  const risk = String(item?.risk || item?.risk_level || "LOW").toUpperCase();
  const latestEventType = String(item?.latest_event?.event_type || item?.latest_event_type || "").toLowerCase();
  const reason = String(item?.strongest_reason || item?.explanation || item?.attempt_summary || "").toLowerCase();
  const features = getFeatures(item);
  const suspiciousSequences = Number(features?.suspicious_sequence_count || features?.correlated_pattern_count || 0);
  const clipboardCount = Number(features?.clipboard_count || 0);
  const focusLossCount = Number(features?.focus_blur_count || features?.tab_hidden_count || 0);
  const typingAnomalies = Number(features?.typing_behavior_anomaly_count || 0);
  const rapidAnswers = Number(features?.rapid_answer_count || features?.rapid_answer_burst_count || 0);

  const suspiciousEventTypes = new Set(["clipboard", "visibility_change", "idle_state", "rapid_answer_burst", "typing_burst", "typing_pause", "backspace_activity"]);
  const genericReasonPhrases = [
    "stable engagement observed",
    "normal behavior pattern",
    "review signals detected",
    "no major violation detected",
    "minor focus interruptions observed",
  ];
  const reasonKeywords = [
    "clipboard",
    "focus loss",
    "focus recovery",
    "tab switch",
    "visibility",
    "paste",
    "rapid answer",
    "typing",
    "idle",
    "correlated",
    "suspicious sequence",
    "review recommended",
    "escalat",
    "confidence spike",
    "overwrite",
  ];

  const hasReasonKeyword = reasonKeywords.some((keyword) => reason.includes(keyword));
  const isGenericReason = genericReasonPhrases.some((phrase) => reason.includes(phrase));
  const hasBehavioralCounts = suspiciousSequences > 0 || clipboardCount > 0 || focusLossCount >= 3 || typingAnomalies > 0 || rapidAnswers > 0;

  if (risk === "HIGH" && (hasReasonKeyword || suspiciousEventTypes.has(latestEventType) || hasBehavioralCounts)) return true;
  if (risk === "MEDIUM" && (hasReasonKeyword || suspiciousEventTypes.has(latestEventType) || hasBehavioralCounts || Number(item?.confidence || 0) >= 0.55)) return true;
  if (suspiciousSequences > 0 || clipboardCount > 0) return true;
  if (hasReasonKeyword && !isGenericReason) return true;
  return false;
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
    return event.event_type === "window_blur" || event.event_type === "blur";
  }).length;
}

function getFocusLossRate(events) {
  if (!events.length) return 0;
  const focusLoss = events.filter((event) => event.event_type === "window_blur" || event.event_type === "blur").length;
  return Math.round((focusLoss / events.length) * 100);
}

function buildViolationOverview(attempt, events) {
  const features = getFeatures(attempt);
  const clipboardCount = Number(features.paste_count || events.filter((event) => event.event_type === "clipboard").length || 0);
  const tabSwitchCount = Number(features.tab_hidden_count || 0);
  const blurEvents = Number(features.focus_blur_count || getBlurEventCount(events) || 0);
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
      subtitle: "Focus Loss Count",
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
      subtitle: "Focus Loss Count",
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

async function authJsonFetch(url, options = {}) {
  const {
    token,
    method = "GET",
    body,
    timeoutMs = 12000,
    retries = 0,
    retryDelayMs = 700,
  } = options;
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
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
      const payload = { ok: res.ok, status: res.status, data };
      if ((payload.ok || payload.status !== 0) || attempt >= retries) {
        return payload;
      }
    } catch {
      if (attempt >= retries) {
        return {
          ok: false,
          status: 0,
          data: { detail: "Backend is unavailable or waking up. Please retry shortly." },
        };
      }
    } finally {
      window.clearTimeout(timeoutId);
    }

    await new Promise((resolve) => window.setTimeout(resolve, retryDelayMs * (attempt + 1)));
  }

  return {
    ok: false,
    status: 0,
    data: { detail: "Backend is unavailable or waking up. Please retry shortly." },
  };
}

const DEMO_MONACO_LOADER_URL = "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/loader.js";
let demoMonacoLoaderPromise = null;

function loadDemoMonaco() {
  if (typeof window === "undefined") return Promise.reject(new Error("Monaco requires a browser environment."));
  if (window.monaco?.editor) return Promise.resolve(window.monaco);
  if (demoMonacoLoaderPromise) return demoMonacoLoaderPromise;

  demoMonacoLoaderPromise = new Promise((resolve, reject) => {
    const finishLoad = () => {
      const requireLoader = window.require;
      if (!requireLoader) {
        reject(new Error("Monaco loader is unavailable."));
        return;
      }
      requireLoader.config({ paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs" } });
      requireLoader(["vs/editor/editor.main"], () => {
        if (window.monaco?.editor) resolve(window.monaco);
        else reject(new Error("Monaco editor failed to initialize."));
      });
    };

    const existing = document.querySelector('script[data-proctoriq-monaco="true"]');
    if (existing) {
      if (window.monaco?.editor || window.require) {
        finishLoad();
        return;
      }
      existing.addEventListener("load", finishLoad, { once: true });
      existing.addEventListener("error", () => reject(new Error("Unable to load Monaco assets.")), { once: true });
      return;
    }

    const script = document.createElement("script");
    script.src = DEMO_MONACO_LOADER_URL;
    script.async = true;
    script.dataset.proctoriqMonaco = "true";
    script.onload = finishLoad;
    script.onerror = () => reject(new Error("Unable to load Monaco assets."));
    document.head.appendChild(script);
  });

  return demoMonacoLoaderPromise;
}

function getDemoSectionVisual(sectionId) {
  if (sectionId === "provenance") {
    return {
      icon: Search,
      label: "Provenance Validation",
      accent: "knowledge",
      estimate: "2 prompts",
    };
  }
  if (sectionId === "frontend") {
    return {
      icon: BookOpen,
      label: "Knowledge Assessment",
      accent: "knowledge",
      estimate: "2 prompts",
    };
  }
  if (sectionId === "sql") {
    return {
      icon: FileCode2,
      label: "Coding Challenge",
      accent: "coding",
      estimate: "2 prompts",
    };
  }
  if (sectionId === "logic") {
    return {
      icon: Brain,
      label: "Analytical Thinking",
      accent: "thinking",
      estimate: "2 prompts",
    };
  }
  return {
    icon: Shield,
    label: "Security Investigation Scenario",
    accent: "security",
    estimate: "2 prompts",
  };
}

function getQuestionCompletionState(question, answer, isCurrent, isMarked) {
  if (isCurrent) return "current";
  if (isDemoAnswerFilled(question, answer)) return "answered";
  if (isMarked) return "marked";
  return "pending";
}

function DemoMonacoEditor({ language, value, onChange, readOnly = false }) {
  const containerRef = useRef(null);
  const editorRef = useRef(null);
  const latestOnChangeRef = useRef(onChange);

  useEffect(() => {
    latestOnChangeRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    let disposed = false;
    let localEditor = null;

    async function initEditor() {
      try {
        const monaco = await loadDemoMonaco();
        if (disposed || !containerRef.current) return;

        localEditor = monaco.editor.create(containerRef.current, {
          value: value || "",
          language: language || "javascript",
          theme: "vs-dark",
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          wordWrap: "on",
          automaticLayout: true,
          fontSize: 13,
          fontFamily: "'JetBrains Mono', 'Fira Code', Consolas, monospace",
          lineHeight: 20,
          smoothScrolling: true,
          padding: { top: 16, bottom: 16 },
          overviewRulerLanes: 0,
          renderLineHighlight: "gutter",
          tabSize: 2,
          readOnly,
        });

        localEditor.onDidChangeModelContent(() => {
          latestOnChangeRef.current?.(localEditor.getValue());
        });
        editorRef.current = localEditor;
      } catch {
        editorRef.current = null;
      }
    }

    initEditor();
    return () => {
      disposed = true;
      if (localEditor) {
        localEditor.dispose();
      }
      editorRef.current = null;
    };
  }, []);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const model = editor.getModel();
    if (model && model.getLanguageId() !== language) {
      window.monaco?.editor?.setModelLanguage(model, language || "javascript");
    }
  }, [language]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const nextValue = value || "";
    if (editor.getValue() !== nextValue) {
      editor.setValue(nextValue);
    }
  }, [value]);

  return <div className="public-demo-monaco" ref={containerRef} />;
}

const PUBLIC_DEMO_ASSESSMENTS = [
  {
    id: "assessment_behavioral_integrity_demo",
    name: "Behavioral Integrity Demo",
    durationMinutes: 9,
    icon: Shield,
    intro: "A polished integrity-focused walkthrough that highlights focus loss, tab switching, clipboard activity, and the reviewer evidence timeline.",
    sectionOrder: ["frontend", "logic", "sql", "security"],
  },
  {
    id: "assessment_enterprise_investigation_demo",
    name: "Enterprise Investigation Demo",
    durationMinutes: 10,
    icon: ClipboardList,
    intro: "A reviewer-storytelling track designed to showcase escalation paths, evidence grouping, and end-to-end enterprise investigation workflow.",
    sectionOrder: ["security", "frontend", "sql", "logic"],
  },
  {
    id: "assessment_integrity_showcase",
    name: "Assessment Integrity Showcase",
    durationMinutes: 10,
    icon: ShieldAlert,
    intro: "A full enterprise assessment simulation spanning frontend debugging, SQL reasoning, logical analysis, and a security investigation scenario.",
    sectionOrder: ["frontend", "sql", "logic", "security"],
  },
  {
    id: "assessment_frontend_debugging",
    name: "Frontend Debugging Track",
    durationMinutes: 9,
    icon: Bug,
    intro: "A developer-oriented assessment flow that still culminates in reviewer-visible behavioral intelligence.",
    sectionOrder: ["frontend", "logic", "sql", "security"],
  },
  {
    id: "assessment_sql_analysis",
    name: "SQL Analysis Track",
    durationMinutes: 9,
    icon: DatabaseZap,
    intro: "A data-and-analytics flavored assessment that creates realistic hesitation, answer rewrites, and navigation patterns.",
    sectionOrder: ["sql", "logic", "frontend", "security"],
  },
  {
    id: "assessment_logical_reasoning",
    name: "Logical Reasoning Track",
    durationMinutes: 8,
    icon: BrainCircuit,
    intro: "A compact reasoning assessment with timing pressure, revisits, and explainable reviewer-side telemetry.",
    sectionOrder: ["logic", "frontend", "sql", "security"],
  },
  {
    id: "assessment_provenance_validation",
    name: "Testing Post-Submission Answer Provenance Analysis",
    durationMinutes: 8,
    icon: Search,
    intro: "An internal validation track used to test external similarity detection, behavioral correlation, clipboard correlation, and reviewer evidence rendering after submission.",
    sectionOrder: ["provenance"],
    isProvenanceValidation: true,
    internalBadge: "Provenance Validation Mode",
    questionCount: 2,
  },
];

const PROVENANCE_VALIDATION_QUESTION_BANK = [
  {
    id: "prov_react_sync",
    title: "React State Synchronization After WebSocket Refresh",
    difficulty: "Medium",
    inputType: "textarea",
    category: "React debugging",
    assessmentType: "Frontend Debugging",
    provenance_test_intent: "Encourage tutorial-style explanations about stale state, canonical data sources, and effect-driven refetch issues.",
    expected_reference_style: ["react_docs", "stackoverflow", "engineering_blog"],
    tags: ["React", "State", "WebSocket", "Source of Truth"],
    placeholder: "Explain the likely synchronization issue, how React state can be overwritten after a refresh, and the safest production fix.",
    promptVariants: [
      "A reviewer dashboard re-renders stale rows after a WebSocket refresh even though local state updates correctly. Explain the most likely synchronization issue and describe a production-safe fix.",
      "A case table looks correct immediately after a local update, but older rows return as soon as a WebSocket-driven refresh runs. Explain the most likely React synchronization mistake and how you would fix it safely in production.",
    ],
    expectedBehavior: "Designed to produce research-style written answers, copy/paste temptation, short idle periods, and limited edits after a large insertion.",
  },
  {
    id: "prov_react_keys",
    title: "React Reconciliation and Stable Keys",
    difficulty: "Medium",
    inputType: "textarea",
    category: "React debugging",
    assessmentType: "Frontend Debugging",
    provenance_test_intent: "Generate long-form explanations that closely resemble React docs and tutorial-style list rendering guidance.",
    expected_reference_style: ["react_docs", "geeksforgeeks", "mdn_docs"],
    tags: ["React", "Reconciliation", "Keys", "List Rendering"],
    placeholder: "Explain reconciliation, why keys matter, how unstable keys cause stale UI, and how you would debug or prevent the issue.",
    promptVariants: [
      "Explain React reconciliation and why unstable keys in dynamic lists can cause stale UI rendering issues after updates. Your answer should cover how React compares trees, why keys matter, what goes wrong with index-based keys, and how you would debug or prevent stale row reuse.",
      "Describe how React reconciliation works and why unstable or index-based keys in dynamic lists can produce stale rows or reused component state. Include how React matches list items and a practical prevention approach.",
    ],
    expectedBehavior: "Designed to produce hesitation, answer rewrites, copy/paste temptation, idle time before response, and minimal edits after a large insertion.",
  },
  {
    id: "prov_sql_triage_indexing",
    title: "Operational Review Queue Index Redesign",
    difficulty: "Hard",
    inputType: "textarea",
    category: "SQL optimization",
    assessmentType: "SQL Analysis",
    provenance_test_intent: "Encourage reference-like explanations about indexing strategy, filtered query plans, and large queue workloads.",
    expected_reference_style: ["postgres_docs", "sql_blog", "stackoverflow"],
    tags: ["SQL", "Indexing", "Query Plan", "Triage"],
    placeholder: "Describe how you would redesign indexing and filtering for a large triage queue query, including actionable case filters and ordering by meaningful activity.",
    promptVariants: [
      "A review queue query becomes slow after the case table grows beyond 2 million rows. Explain how indexing and query filtering should be redesigned to support operational triage workloads.",
      "An operational review queue now scans millions of rows and reviewer pages feel slow. Explain how indexing, filtering, and ordering should be reworked so triage queries stay fast at scale.",
    ],
    expectedBehavior: "Often produces long tutorial-style SQL reasoning, answer revisions, and research-like pasted phrasing.",
  },
  {
    id: "prov_sql_latest_activity",
    title: "Latest Meaningful Activity Ordering",
    difficulty: "Medium",
    inputType: "textarea",
    category: "SQL optimization",
    assessmentType: "SQL Analysis",
    provenance_test_intent: "Produce blog-style explanations about joins, COALESCE ordering, and operational queue relevance.",
    expected_reference_style: ["sql_blog", "stackoverflow", "postgres_docs"],
    tags: ["SQL", "Ordering", "Queue Logic", "COALESCE"],
    placeholder: "Explain how you would join case and attempt state data and order by the latest meaningful activity rather than generic updates.",
    promptVariants: [
      "Explain how a review queue query should join case data with attempt state data and order results by the latest meaningful behavioral activity instead of generic updated timestamps.",
      "A dashboard feed is showing stale ordering because it sorts by generic updates. Explain how you would redesign the SQL join and ordering logic so reviewer queues prioritize the most recent meaningful activity.",
    ],
    expectedBehavior: "Creates thoughtful paragraph responses that resemble SQL tutorials and production design notes.",
  },
  {
    id: "prov_fastapi_pooling",
    title: "FastAPI and SQLAlchemy Session Safety",
    difficulty: "Hard",
    inputType: "textarea",
    category: "FastAPI backend design",
    assessmentType: "FastAPI Backend Design",
    provenance_test_intent: "Encourage documentation-like explanations about dependency-scoped sessions, connection pools, and request safety.",
    expected_reference_style: ["fastapi_docs", "sqlalchemy_docs", "engineering_blog"],
    tags: ["FastAPI", "SQLAlchemy", "Connection Pool", "Session Scope"],
    placeholder: "Explain how FastAPI should scope SQLAlchemy sessions safely, avoid pool exhaustion, and keep read endpoints from triggering heavy sync work.",
    promptVariants: [
      "A FastAPI service begins hitting SQLAlchemy pool timeouts during dashboard refreshes and websocket reconnects. Explain how request-scoped sessions and backend read paths should be designed to avoid connection exhaustion.",
      "A reviewer dashboard causes intermittent SQLAlchemy QueuePool timeouts under load. Explain how you would structure FastAPI session handling, request lifecycles, and expensive sync operations to prevent pool exhaustion.",
    ],
    expectedBehavior: "Produces long-form technical prose similar to docs, issue threads, and backend design references.",
  },
  {
    id: "prov_auth_rotation",
    title: "Demo Admin Credential Drift Recovery",
    difficulty: "Medium",
    inputType: "textarea",
    category: "Authentication security",
    assessmentType: "Authentication Security",
    provenance_test_intent: "Generate explanations similar to auth setup guides about env-driven credentials, password rotation, and idempotent seeding.",
    expected_reference_style: ["fastapi_docs", "security_writeup", "stackoverflow"],
    tags: ["Authentication", "Password Hashing", "Env Config", "Seeding"],
    placeholder: "Explain how you would safely keep demo admin credentials synchronized with environment values without bypassing password hashing.",
    promptVariants: [
      "A seeded demo admin user already exists in the database with an outdated password hash. Explain how a secure FastAPI seeding routine should update credentials from environment variables while keeping hashing and role enforcement intact.",
      "A local demo environment drifts because the admin account was created with an old password. Explain how an idempotent seeding workflow should repair the account from env configuration without introducing insecure login shortcuts.",
    ],
    expectedBehavior: "Encourages reference-like security explanations, paste temptation, and minimal edits after insertion.",
  },
  {
    id: "prov_investigation_notes",
    title: "Reviewer Investigation Narrative Design",
    difficulty: "Medium",
    inputType: "textarea",
    category: "Investigation workflow reasoning",
    assessmentType: "Investigation Workflow Reasoning",
    provenance_test_intent: "Encourage playbook-like explanations about reviewer judgement, escalation notes, and evidence-backed summaries.",
    expected_reference_style: ["operational_playbook", "security_writeup", "internal_runbook"],
    tags: ["Investigation", "Reviewer Workflow", "Escalation", "Summary"],
    placeholder: "Describe how an investigation summary should connect evidence, ambiguity, and escalation decisions without making unsupported accusations.",
    promptVariants: [
      "Explain how an investigation report should summarize suspicious behavior so a reviewer can understand the evidence, ambiguity, and escalation path without making unsupported accusations.",
      "A reviewer needs to hand off a medium-to-high risk case to another analyst. Explain how the investigation summary should connect behavioral evidence, uncertainty, and the escalation decision in a credible enterprise style.",
    ],
    expectedBehavior: "Produces policy-style prose and reviewer-language that often resembles internal runbooks and security notes.",
  },
  {
    id: "prov_explainability",
    title: "Explainable Risk Reasoning Without Black-Box Claims",
    difficulty: "Medium",
    inputType: "textarea",
    category: "Explainable AI reasoning",
    assessmentType: "Explainable AI Reasoning",
    provenance_test_intent: "Generate article-style answers about explainability, reviewer-assisted decisions, and confidence communication.",
    expected_reference_style: ["ai_governance_note", "enterprise_blog", "product_docs"],
    tags: ["Explainability", "Risk Scoring", "Reviewer Judgment", "Confidence"],
    placeholder: "Explain how a risk system can remain explainable and reviewer-assisted without pretending to offer certainty.",
    promptVariants: [
      "Explain how an assessment integrity platform can communicate risk scores, confidence, and reviewer guidance without making black-box or certainty claims.",
      "Describe how a deterministic integrity system should explain its decisions to reviewers so that confidence, evidence, and uncertainty remain clear without pretending the platform knows intent.",
    ],
    expectedBehavior: "Often leads to polished paragraph answers similar to enterprise product docs and explainability writeups.",
  },
  {
    id: "prov_telemetry_metadata",
    title: "Privacy-Preserving Browser Telemetry Interpretation",
    difficulty: "Medium",
    inputType: "textarea",
    category: "Browser telemetry interpretation",
    assessmentType: "Browser Telemetry Interpretation",
    provenance_test_intent: "Encourage doc-style explanations about metadata-only browser signals and how they can be interpreted safely.",
    expected_reference_style: ["mdn_docs", "security_writeup", "product_docs"],
    tags: ["Browser Telemetry", "Clipboard", "Focus", "Privacy"],
    placeholder: "Explain how metadata-only focus, visibility, clipboard, and typing signals can be interpreted without collecting sensitive content.",
    promptVariants: [
      "Explain how a browser telemetry system can analyze focus changes, visibility state, clipboard metadata, and typing rhythm without storing clipboard contents or raw keystrokes.",
      "A product promises privacy-preserving telemetry rather than invasive surveillance. Explain how browser focus, visibility, clipboard metadata, and typing signals can still support reviewer intelligence without capturing sensitive content.",
    ],
    expectedBehavior: "Produces documentation-like language with clear technical phrasing and potential reference overlap.",
  },
  {
    id: "prov_queue_priority",
    title: "Reviewer Queue Prioritization Logic",
    difficulty: "Medium",
    inputType: "textarea",
    category: "Queue prioritization",
    assessmentType: "Queue Prioritization",
    provenance_test_intent: "Generate triage-style explanations about actionable statuses, escalation relevance, and reviewer ordering logic.",
    expected_reference_style: ["operational_playbook", "sql_blog", "security_writeup"],
    tags: ["Queue", "Priority", "Review", "Triage"],
    placeholder: "Explain how reviewer queues should prioritize new and escalated cases while avoiding stale or already resolved activity.",
    promptVariants: [
      "Explain how a review queue should prioritize new, triaged, under-investigation, and escalated cases while avoiding stale historical items that are no longer actionable.",
      "A reviewer queue is mixing live escalations with resolved history. Explain how queue prioritization should separate actionable work from stale records so analysts focus on the right cases first.",
    ],
    expectedBehavior: "Encourages paragraph answers that resemble triage playbooks and operational guidance.",
  },
  {
    id: "prov_event_correlation",
    title: "Behavioral Event Correlation Narrative",
    difficulty: "Hard",
    inputType: "textarea",
    category: "Event correlation",
    assessmentType: "Event Correlation",
    provenance_test_intent: "Encourage security-writeup style explanations about linked behavioral sequences and why correlation matters more than isolated events.",
    expected_reference_style: ["security_writeup", "incident_review", "internal_runbook"],
    tags: ["Correlation", "Timeline", "Clipboard", "Focus Loss"],
    placeholder: "Explain why a focus loss followed by clipboard activity and rapid overwrites should be treated as a correlated sequence rather than isolated events.",
    promptVariants: [
      "Explain why the sequence focus loss, visibility hidden, clipboard activity, and rapid answer overwrite is more meaningful than isolated events when deciding whether a case warrants escalation.",
      "A candidate leaves the page, returns, pastes a large answer, and immediately overwrites a response. Explain why this should be interpreted as a correlated behavioral sequence rather than a set of unrelated events.",
    ],
    expectedBehavior: "Produces analyst-style narratives that often resemble public incident writeups or security escalation notes.",
  },
  {
    id: "prov_risk_thresholds",
    title: "Deterministic Risk Scoring and False Positive Control",
    difficulty: "Hard",
    inputType: "textarea",
    category: "Risk scoring logic",
    assessmentType: "Risk Scoring Logic",
    provenance_test_intent: "Generate reference-style answers about thresholds, corroboration, and not escalating solely on one suspicious signal.",
    expected_reference_style: ["product_docs", "security_writeup", "engineering_blog"],
    tags: ["Risk Scoring", "Thresholds", "False Positives", "Deterministic Logic"],
    placeholder: "Explain how a deterministic scoring system should use corroboration and thresholds to avoid escalating cases solely because one suspicious signal exists.",
    promptVariants: [
      "Explain how a deterministic integrity scoring system should combine thresholds, corroborating telemetry, and reviewer judgement so that one suspicious signal does not automatically create a high-risk outcome.",
      "A platform wants strong explainability without flooding reviewers with false positives. Explain how deterministic thresholds, corroboration, and reviewer oversight should work together in the final risk decision.",
    ],
    expectedBehavior: "Encourages polished long-form technical responses similar to product explainability docs and engineering essays.",
  },
  {
    id: "prov_concurrent_queue_cache",
    title: "Concurrent Queue and Cache Contention",
    difficulty: "Hard",
    inputType: "textarea",
    category: "FastAPI backend design",
    assessmentType: "Concurrency Systems Design",
    provenance_test_intent: "Encourage answers that resemble StackOverflow and concurrency-guide explanations about synchronized collections and proper concurrent queues.",
    expected_reference_style: ["stackoverflow", "java_docs", "engineering_blog"],
    tags: ["Concurrency", "Queue", "Cache", "BlockingQueue"],
    placeholder: "Explain why synchronized ArrayList contention hurts throughput and what concurrent collection patterns are safer for read/remove queue workloads.",
    promptVariants: [
      "A cache-backed work queue uses a synchronized ArrayList and starts showing contention when multiple threads read and remove items concurrently. Explain the likely performance and correctness problems, and describe which concurrent queue or collection design would be safer.",
      "An internal queue shares a synchronized ArrayList between readers and removers, and cache throughput collapses under concurrency. Explain why that happens and which concurrent data structures should replace it in a production design.",
    ],
    expectedBehavior: "Often produces reference-like concurrency explanations, paste temptation, and long paragraph answers with minimal follow-up edits.",
  },
  {
    id: "prov_triage_automation_research",
    title: "Triage Automation Evaluation Design",
    difficulty: "Hard",
    inputType: "textarea",
    category: "Investigation workflow reasoning",
    assessmentType: "Triage Automation Research",
    provenance_test_intent: "Generate research-style answers about empirical studies, open-source datasets, and evaluation metrics for triage automation.",
    expected_reference_style: ["research_paper", "industrial_study", "engineering_blog"],
    tags: ["Triage Automation", "Empirical Study", "Datasets", "Evaluation"],
    placeholder: "Describe how a triage automation study should use datasets, evaluation metrics, and industrial validation instead of relying only on intuition.",
    promptVariants: [
      "A team wants to automate issue triage and claims the approach works well, but has not evaluated it rigorously. Explain how empirical studies, industrial software engineering datasets, and evaluation metrics should be used to validate triage automation.",
      "Explain how triage automation should be evaluated using open-source or industrial datasets, measurable quality metrics, and empirical study design instead of anecdotal evidence.",
    ],
    expectedBehavior: "Encourages longer research-paper style prose and answers that resemble academic or industrial engineering references.",
  },
  {
    id: "prov_splunk_telemetry",
    title: "Telemetry vs Monitoring in Operational Systems",
    difficulty: "Medium",
    inputType: "textarea",
    category: "Browser telemetry interpretation",
    assessmentType: "Browser Telemetry Interpretation",
    provenance_test_intent: "Encourage doc-style explanations about telemetry, remote data collection, monitoring differences, and privacy tradeoffs.",
    expected_reference_style: ["splunk_docs", "mdn_docs", "product_docs"],
    tags: ["Telemetry", "Monitoring", "Privacy", "Data Integrity"],
    placeholder: "Explain what telemetry means in operational systems, how it differs from monitoring, and what tradeoffs matter for privacy and data integrity.",
    promptVariants: [
      "Explain what telemetry means in an operational software platform, how it differs from simple monitoring, and why privacy, latency, data volume, and data integrity matter when telemetry is collected remotely.",
      "A team uses the words monitoring and telemetry interchangeably. Explain the difference, the major telemetry types, and the operational tradeoffs around privacy, data volume, latency, and data integrity.",
    ],
    expectedBehavior: "Produces tutorial-style responses that often resemble product documentation or observability guides.",
  },
  {
    id: "prov_agentic_ai_payer",
    title: "Agentic AI in Healthcare Payer Workflows",
    difficulty: "Hard",
    inputType: "textarea",
    category: "Explainable AI reasoning",
    assessmentType: "Operational AI Workflow",
    provenance_test_intent: "Generate article-style answers about agentic AI, decision support, workflow automation, and operational intelligence.",
    expected_reference_style: ["industry_article", "product_docs", "enterprise_blog"],
    tags: ["Agentic AI", "Healthcare", "Decision Support", "Workflow"],
    placeholder: "Explain how agentic AI can support healthcare payer workflows without replacing human oversight or making unsupported autonomous decisions.",
    promptVariants: [
      "Explain how agentic AI can be used as decision support in healthcare payer workflows, including automation opportunities, operational intelligence benefits, and the need for human oversight.",
      "A healthcare payer wants to introduce agentic AI into authorization and operations workflows. Explain how decision support, automation, and operational intelligence can help without removing human review or governance.",
    ],
    expectedBehavior: "Encourages polished article-style prose that resembles enterprise AI workflow writeups.",
  },
];

const PUBLIC_DEMO_SECTION_LIBRARY = {
  frontend: {
    id: "frontend",
    title: "Knowledge Assessment",
    subtitle: "Frontend systems reasoning, diagnosis, and implementation judgement",
    telemetryFocus: ["Hesitation", "Answer rewrites", "Copy/paste temptation"],
    questions: [
      {
        id: "front_q1",
        title: "React Re-render Bug",
        difficulty: "Medium",
        inputType: "mcq",
        prompt: "A review table updates correctly in local state, but stale rows reappear after a WebSocket refresh. Which root cause should you inspect first?",
        snippetLanguage: "jsx",
        snippet: `const [rows, setRows] = useState([])\n\nuseEffect(() => {\n  fetchRows().then(setRows)\n}, [selectedCaseId])`,
        options: [
          "The page is refetching from a stale API source of truth after local updates.",
          "The CSS grid is clipping state updates visually.",
          "The table key prop is too short for React reconciliation.",
          "The rows are sorted alphabetically instead of by time.",
        ],
        expectedBehavior: "Often triggers careful reading, brief idling, and option changes before committing.",
      },
      {
        id: "front_q2",
        title: "React Reconciliation and Stable Keys",
        difficulty: "Medium",
        inputType: "textarea",
        prompt: "Explain React reconciliation and why unstable keys in dynamic lists can cause stale UI rendering issues after updates.\n\nYour answer should include:\n- how React compares component trees during rendering\n- why list keys matter\n- what happens when index-based or unstable keys are used\n- how stale UI or incorrect row reuse can appear\n- a practical debugging or prevention approach",
        tags: ["React", "Reconciliation", "Keys", "List Rendering"],
        placeholder: "Explain how React compares trees, why stable keys matter, what can go wrong with index-based keys, and how you would debug or prevent stale row reuse.",
        expectedBehavior: "Designed to produce hesitation, answer rewrites, copy/paste temptation, idle time before response, and minimal edits after a large insertion.",
      },
    ],
  },
  sql: {
    id: "sql",
    title: "Coding Challenge",
    subtitle: "Operational SQL reasoning, lifecycle filtering, and queue prioritization",
    telemetryFocus: ["Idle thinking", "Answer rewrites", "Navigation revisits"],
    questions: [
      {
        id: "sql_q1",
        title: "Latest Actionable Cases",
        difficulty: "Medium",
        inputType: "code",
        prompt: "Write a SQL query shape that returns only actionable review cases ordered by the most recent meaningful activity.",
        snippetLanguage: "sql",
        tags: ["SQL", "PostgreSQL", "Queue Logic"],
        constraints: [
          "Return only actionable cases.",
          "Sort by newest meaningful activity first.",
          "Preserve risk and reviewer status visibility.",
        ],
        examples: [
          "Use an IN filter for NEW, TRIAGED, UNDER_INVESTIGATION, and ESCALATED.",
          "Prefer the most recent risk-history or behavioral-event timestamp over generic updates.",
        ],
        starterCode: {
          sql: "SELECT\n  c.id,\n  c.attempt_id,\n  c.status,\n  s.final_risk_level,\n  s.final_risk_score\nFROM investigation_cases c\nJOIN attempt_states s ON s.attempt_id = c.attempt_id\nWHERE c.status IN ('NEW', 'TRIAGED', 'UNDER_INVESTIGATION', 'ESCALATED')\nORDER BY COALESCE(s.latest_event_at, s.updated_at) DESC;\n",
        },
        languageOptions: ["sql"],
        placeholder: "SELECT ... FROM investigation_cases ... WHERE status IN ('NEW', 'UNDER_INVESTIGATION', 'ESCALATED') ORDER BY ...",
        expectedBehavior: "Often causes pauses, text edits, and navigation between question and section summaries.",
      },
      {
        id: "sql_q2",
        title: "Aggregation Check",
        difficulty: "Easy",
        inputType: "mcq",
        prompt: "Which metric should back 'High Risk Now' on the dashboard?",
        options: [
          "All historical HIGH attempts regardless of status",
          "Only unresolved/actionable HIGH attempts",
          "All attempts with any clipboard activity",
          "Any case opened in the last week",
        ],
        expectedBehavior: "Encourages a quick choice, then second-guessing and answer changes.",
      },
    ],
  },
  logic: {
    id: "logic",
    title: "Analytical Thinking",
    subtitle: "Decision patterns, ambiguity handling, and prioritization",
    telemetryFocus: ["Hesitation", "Rapid answer changes", "Question revisits"],
    questions: [
      {
        id: "logic_q1",
        title: "Priority Sequence",
        difficulty: "Medium",
        inputType: "mcq",
        prompt: "Three cases arrive: one escalated HIGH with correlated clipboard evidence, one MEDIUM with repeated focus loss, and one LOW with minor blur noise. Which should be reviewed first?",
        options: [
          "The LOW case, because it has the fewest events and is quickest to close",
          "The MEDIUM case, because ambiguity always beats severity",
          "The escalated HIGH case with correlated evidence",
          "Review them in candidate-name order",
        ],
        expectedBehavior: "Creates clear reviewer prioritization reasoning and quick answer toggles.",
      },
      {
        id: "logic_q2",
        title: "Reviewer Decision Note",
        difficulty: "Medium",
        inputType: "textarea",
        prompt: "A candidate shows moderate focus loss, one clipboard sequence, and inconsistent typing after idle recovery. Explain why this should remain a reviewer decision instead of being auto-escalated.",
        placeholder: "Keep it concise and operational. Mention ambiguity, corroboration, and why explainability matters.",
        expectedBehavior: "Generates uneven timing, rewrites, and deliberate thinking pauses.",
      },
    ],
  },
  security: {
    id: "security",
    title: "Security Investigation Scenario",
    subtitle: "Event correlation, analyst notes, and escalation reasoning",
    telemetryFocus: ["Careful reading", "Idle analysis", "Reasoning text input"],
    questions: [
      {
        id: "sec_q1",
        title: "Event Log Correlation",
        difficulty: "Hard",
        inputType: "mcq",
        prompt: "Review the behavioral timeline and choose the strongest correlated suspicious sequence.",
        snippetLanguage: "log",
        snippet: `12:03:10  focus_lost\n12:03:18  visibility_hidden\n12:03:24  clipboard_paste(size=large)\n12:03:29  answer_change(length=184)\n12:03:35  rapid_answer_burst`,
        options: [
          "Normal hesitation followed by a harmless answer save",
          "Focus loss -> clipboard activity -> rapid overwrite sequence",
          "Pure typing inconsistency without any corroboration",
          "A completed submission with no prior behavioral risk",
        ],
        expectedBehavior: "Designed to create careful reading, copy temptation, and section revisit behavior.",
      },
      {
        id: "sec_q2",
        title: "Escalation Recommendation",
        difficulty: "Hard",
        inputType: "textarea",
        prompt: "Write the escalation note you would place into an investigation report for the timeline above.",
        placeholder: "Example: Candidate returned from focus loss and performed large paste-driven overwrites within seconds, repeating a correlated suspicious pattern that warrants escalation.",
        expectedBehavior: "Creates longer reasoning input, edits, and strong reviewer-style telemetry.",
      },
    ],
  },
  provenance: {
    id: "provenance",
    title: "Provenance Validation",
    subtitle: "Internal validation prompts designed to test post-submission external similarity analysis",
    telemetryFocus: ["Research-style answers", "Copy/paste temptation", "Minimal edits after insertion"],
    questions: [],
  },
};

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

function stableHash(value) {
  let hash = 2166136261;
  const text = String(value || "");
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function buildProvenanceValidationQuestions(sessionSeed, count = 2) {
  const seedText = String(sessionSeed || "default");
  return [...PROVENANCE_VALIDATION_QUESTION_BANK]
    .sort((left, right) => stableHash(`${seedText}:${left.id}`) - stableHash(`${seedText}:${right.id}`))
    .slice(0, Math.max(1, Math.min(count, PROVENANCE_VALIDATION_QUESTION_BANK.length)))
    .map((question) => {
      const variants = question.promptVariants?.length ? question.promptVariants : [question.prompt];
      const variantIndex = stableHash(`${seedText}:${question.id}:variant`) % variants.length;
      return {
        ...question,
        prompt: variants[variantIndex],
      };
    });
}

function buildDemoAssessment(trackId, sessionSeed = "default") {
  const track = PUBLIC_DEMO_ASSESSMENTS.find((item) => item.id === trackId) || PUBLIC_DEMO_ASSESSMENTS[0];
  const sections = track.sectionOrder.map((sectionId, sectionIndex) => {
    const section = PUBLIC_DEMO_SECTION_LIBRARY[sectionId];
    const sourceQuestions = track.isProvenanceValidation && sectionId === "provenance"
      ? buildProvenanceValidationQuestions(sessionSeed, track.questionCount || 2)
      : section.questions;
    return {
      ...section,
      order: sectionIndex + 1,
      questions: sourceQuestions.map((question, questionIndex) => ({
        ...question,
        sectionId: section.id,
        sectionTitle: section.title,
        sectionSubtitle: section.subtitle,
        displayNumber: `${sectionIndex + 1}.${questionIndex + 1}`,
      })),
    };
  });
  return {
    ...track,
    sections,
    questions: sections.flatMap((section) => section.questions),
  };
}

function getDemoQuestionAssessmentType(question) {
  if (question?.assessmentType) return question.assessmentType;
  const sectionId = String(question?.sectionId || "").toLowerCase();
  if (sectionId === "frontend") return "Frontend Debugging";
  if (sectionId === "sql") return "SQL Analysis";
  if (sectionId === "logic") return "Logical Reasoning";
  if (sectionId === "security") return "Security Investigation";
  return "Assessment Response";
}

function isDemoAnswerFilled(question, value) {
  return Boolean(String(value || "").trim());
}

function getDemoSignalToast(event) {
  const eventType = String(event?.event_type || "").toLowerCase();
  const payload = event?.payload || {};
  const state = String(payload.state || "").toLowerCase();
  const action = String(payload.action || payload.operation || "").toLowerCase();
  const changeType = String(payload.change_type || "").toLowerCase();
  const answerLength = Number(payload.answer_length || 0);
  const pasteSize = String(payload.paste_size || payload.size || "").toLowerCase();
  const idleDuration = Number(payload.duration_seconds || 0);

  if (eventType === "visibility_change" && state === "hidden") return DEMO_SIGNAL_MAP.visibility_hidden;
  if (eventType === "visibility_change" && state === "visible") return DEMO_SIGNAL_MAP.visibility_visible;
  if (eventType === "blur") return DEMO_SIGNAL_MAP.focus_loss;
  if (eventType === "focus") return DEMO_SIGNAL_MAP.focus_return;
  if (eventType === "clipboard" && action === "paste") return DEMO_SIGNAL_MAP.clipboard_paste;
  if (eventType === "clipboard" && action === "copy") return DEMO_SIGNAL_MAP.clipboard_copy;
  if (eventType === "answer_change" && changeType === "paste" && (answerLength >= 180 || pasteSize === "large")) return DEMO_SIGNAL_MAP.large_answer_insert;
  if (eventType === "idle_state" && state === "idle" && idleDuration >= 20) return DEMO_SIGNAL_MAP.idle_period;
  if (eventType === "idle_state" && state === "active") return DEMO_SIGNAL_MAP.idle_recovery;
  return null;
}

/* ── Sequence Detection ─────────────────────────────────────────────────
   Checks the recent-signal sliding window for correlated patterns.
   Returns the first matching sequence pattern, or null.                */
function detectDemoSequence(recentSignals) {
  const now = Date.now();
  const windowMs = 8000;
  const active = recentSignals.filter((s) => now - s.at < windowMs);
  const activeKeys = active.map((s) => s.key);

  for (const pattern of DEMO_SEQUENCE_PATTERNS) {
    const allPresent = pattern.keys.every((k) => activeKeys.includes(k));
    if (allPresent) {
      return {
        key: `seq_${pattern.id}`,
        group: `sequence_${pattern.id}`,
        label: pattern.label,
        severity: pattern.severity,
        category: pattern.category,
        isSequence: true,
        replacesKeys: pattern.replacesKeys,
      };
    }
  }
  return null;
}

function PublicDemoPage({ apiBaseUrl }) {
  const initialCompletion = useMemo(() => getDemoCompletionFromLocation(), []);
  const tracker = useMemo(() => ({ visited: new Set(), lastSectionId: "" }), []);
  const toastTracker = useRef({
    lastShownAt: {},
    dismissTimers: {},
    lastAnswerChangeByQuestion: {},
    recentTelemetrySignatures: {},
    recentSignals: [],
  });
  const [consented, setConsented] = useState(false);
  const [candidateName, setCandidateName] = useState(() => initialCompletion?.candidateName || "");
  const [candidateEmail, setCandidateEmail] = useState("");
  const [assessmentId, setAssessmentId] = useState(() => initialCompletion?.assessmentId || PUBLIC_DEMO_ASSESSMENTS[0].id);
  const [assessmentSessionSeed, setAssessmentSessionSeed] = useState(() => `${Date.now()}`);
  const [stage, setStage] = useState(() => (initialCompletion?.attemptId ? "submitted" : "welcome"));
  const [attemptId, setAttemptId] = useState(() => initialCompletion?.attemptId || "");
  const [candidateId, setCandidateId] = useState("");
  const [answers, setAnswers] = useState({});
  const [markedForReview, setMarkedForReview] = useState({});
  const [questionIndex, setQuestionIndex] = useState(0);
  const [timeRemaining, setTimeRemaining] = useState(PUBLIC_DEMO_ASSESSMENTS[0].durationMinutes * 60);
  const [telemetryStatus, setTelemetryStatus] = useState("Session not started");
  const [telemetryError, setTelemetryError] = useState("");
  const [submittedAt, setSubmittedAt] = useState(() => initialCompletion?.submittedAt || "");
  const [submissionMessage, setSubmissionMessage] = useState("");
  const [submissionBusy, setSubmissionBusy] = useState(false);
  const [reportReadyState, setReportReadyState] = useState(() => ({
    reportReady: Boolean(initialCompletion?.reportReady),
    provenanceReady: Boolean(initialCompletion?.provenanceReady),
    timedOut: false,
  }));
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [connectionStable, setConnectionStable] = useState(() => (typeof navigator === "undefined" ? true : navigator.onLine !== false));
  const [editorLanguageByQuestion, setEditorLanguageByQuestion] = useState({});
  const [sectionCompletion, setSectionCompletion] = useState(null);
  const [editorConsoleByQuestion, setEditorConsoleByQuestion] = useState({});
  const [customInputByQuestion, setCustomInputByQuestion] = useState({});
  const [signalToasts, setSignalToasts] = useState([]);

  const selectedAssessment = useMemo(
    () => buildDemoAssessment(assessmentId, assessmentSessionSeed),
    [assessmentId, assessmentSessionSeed],
  );
  const flatQuestions = selectedAssessment.questions;
  const totalQuestions = flatQuestions.length;
  const currentQuestion = flatQuestions[questionIndex] || flatQuestions[0];
  const currentSection = selectedAssessment.sections.find((section) => section.id === currentQuestion?.sectionId) || selectedAssessment.sections[0];
  const answeredCount = flatQuestions.filter((question) => isDemoAnswerFilled(question, answers[question.id])).length;
  const markedCount = Object.values(markedForReview).filter(Boolean).length;
  const unansweredCount = Math.max(0, totalQuestions - answeredCount);
  const progressPercent = totalQuestions > 0 ? (answeredCount / totalQuestions) * 100 : 0;
  const sectionSummaries = selectedAssessment.sections.map((section) => ({
    ...section,
    ...getDemoSectionVisual(section.id),
    answered: section.questions.filter((question) => isDemoAnswerFilled(question, answers[question.id])).length,
    marked: section.questions.filter((question) => markedForReview[question.id]).length,
    total: section.questions.length,
  }));
  const currentSectionSummary = sectionSummaries.find((section) => section.id === currentSection?.id) || sectionSummaries[0];
  const currentQuestionAnswer = answers[currentQuestion?.id] || "";
  const currentEditorLanguage = editorLanguageByQuestion[currentQuestion?.id]
    || currentQuestion?.languageOptions?.[0]
    || currentQuestion?.snippetLanguage
    || "javascript";
  const currentSectionIndex = sectionSummaries.findIndex((section) => section.id === currentSection?.id);
  const currentSectionProgress = currentSectionSummary?.total ? ((currentSectionSummary.answered || 0) / currentSectionSummary.total) * 100 : 0;
  const demoDataReady = Boolean(selectedAssessment?.sections?.length && flatQuestions.length && currentQuestion);

  const dismissSignalToast = useCallback((toastId) => {
    const timer = toastTracker.current.dismissTimers[toastId];
    if (timer) {
      window.clearTimeout(timer);
      delete toastTracker.current.dismissTimers[toastId];
    }
    setSignalToasts((prev) => prev.filter((toast) => toast.id !== toastId));
  }, []);

  const addDemoSignalToast = useCallback((toast) => {
    if (!DEMO_SIGNAL_TOASTS_ENABLED || stage !== "assessment" || !toast?.key || !toast?.label) return;
    const now = Date.now();
    const cooldownKey = toast.group || toast.key;
    const lastShownAt = toastTracker.current.lastShownAt[cooldownKey] || 0;
    if (now - lastShownAt < DEMO_SIGNAL_TOAST_COOLDOWN_MS) return;
    toastTracker.current.lastShownAt[cooldownKey] = now;

    const toastId = `${toast.key}-${now}`;
    setSignalToasts((prev) => {
      let next = [...prev];
      // If this is a sequence toast, replace weaker constituent toasts
      if (toast.isSequence && toast.replacesKeys?.length) {
        const replaceSet = new Set(toast.replacesKeys);
        next = next.filter((t) => !replaceSet.has(t.key));
      }
      next.push({ ...toast, id: toastId, createdAt: now });
      return next.slice(-DEMO_SIGNAL_TOAST_LIMIT);
    });
    toastTracker.current.dismissTimers[toastId] = window.setTimeout(() => {
      dismissSignalToast(toastId);
    }, DEMO_SIGNAL_TOAST_DISMISS_MS);
  }, [dismissSignalToast, stage]);

  useEffect(() => {
    return () => {
      Object.values(toastTracker.current.dismissTimers).forEach((timerId) => window.clearTimeout(timerId));
      toastTracker.current.dismissTimers = {};
    };
  }, []);

  useEffect(() => {
    if (stage !== "assessment" && stage !== "review") {
      setTimeRemaining(selectedAssessment.durationMinutes * 60);
    }
  }, [selectedAssessment.durationMinutes, stage]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.__PROVENANCE_TEST_MODE__ = Boolean(selectedAssessment?.isProvenanceValidation);
  }, [selectedAssessment?.isProvenanceValidation]);

  useEffect(() => {
    if (stage !== "submitted") return;
    console.info("[Demo Submit Lifecycle]", {
      event: "completion_screen_rendered",
      attemptId,
      reportReady: reportReadyState.reportReady,
      provenanceReady: reportReadyState.provenanceReady,
      timedOut: reportReadyState.timedOut,
    });
  }, [attemptId, reportReadyState.provenanceReady, reportReadyState.reportReady, reportReadyState.timedOut, stage]);

  useEffect(() => {
    function syncFullscreenState() {
      setIsFullscreen(Boolean(document.fullscreenElement));
    }

    function syncConnectionState() {
      setConnectionStable(navigator.onLine !== false);
    }

    if (typeof window === "undefined") return undefined;
    document.addEventListener("fullscreenchange", syncFullscreenState);
    window.addEventListener("online", syncConnectionState);
    window.addEventListener("offline", syncConnectionState);
    return () => {
      document.removeEventListener("fullscreenchange", syncFullscreenState);
      window.removeEventListener("online", syncConnectionState);
      window.removeEventListener("offline", syncConnectionState);
    };
  }, []);

  useEffect(() => {
    if (stage !== "assessment" && stage !== "review") return undefined;
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
    if (!DEMO_SIGNAL_TOASTS_ENABLED || stage !== "assessment" || typeof window === "undefined") return undefined;

    function handleTelemetryToast(event) {
      const toast = getDemoSignalToast(event.detail);
      const payload = event?.detail?.payload || {};
      const signature = [
        toast?.group || toast?.key || "",
        event?.detail?.event_type || "",
        payload?.state || payload?.visibility_state || payload?.action || payload?.operation || "",
        payload?.question_id || "",
      ].join("|");
      const now = Date.now();
      const lastSeenAt = toastTracker.current.recentTelemetrySignatures[signature] || 0;
      if (toast && now - lastSeenAt < 1400) return;
      if (toast) {
        toastTracker.current.recentTelemetrySignatures[signature] = now;
      }

      // Feed the sliding window for sequence detection
      if (toast) {
        toastTracker.current.recentSignals.push({ key: toast.key, at: now });
        // Prune entries older than 8 seconds
        toastTracker.current.recentSignals = toastTracker.current.recentSignals.filter(
          (s) => now - s.at < 8000,
        );

        // Check for correlated sequences before showing the individual toast
        const sequence = detectDemoSequence(toastTracker.current.recentSignals);
        if (sequence) {
          // Clear the window so the same sequence doesn't re-fire
          toastTracker.current.recentSignals = [];
          addDemoSignalToast(sequence);
          return;
        }
      }

      if (toast) addDemoSignalToast(toast);
    }

    window.addEventListener("proctoriq:telemetry-event", handleTelemetryToast);
    return () => window.removeEventListener("proctoriq:telemetry-event", handleTelemetryToast);
  }, [addDemoSignalToast, stage]);

  const emitDemoEvent = useCallback((eventType, payload = {}) => {
    if (!attemptId) return;
    void authJsonFetch(`${apiBaseUrl.replace(/\/$/, "")}/v1/events/ingest`, {
      method: "POST",
      body: {
        attempt_id: attemptId,
        candidate_id: candidateId,
        candidate_name: candidateName.trim(),
        candidate_email: candidateEmail.trim(),
        assessment_id: selectedAssessment.id,
        assessment_name: selectedAssessment.name,
        event_type: eventType,
        payload,
        occurred_at: new Date().toISOString(),
      },
    });
  }, [apiBaseUrl, attemptId, candidateId, candidateName, candidateEmail, selectedAssessment.id, selectedAssessment.name]);

  useEffect(() => {
    if (stage !== "assessment" || !currentQuestion || typeof window === "undefined" || !window.RiskTelemetry) return undefined;
    if (currentQuestion.sectionId && tracker.lastSectionId !== currentQuestion.sectionId) {
      tracker.lastSectionId = currentQuestion.sectionId;
      emitDemoEvent("section_entered", {
        section_id: currentQuestion.sectionId,
        section_title: currentSection?.title || currentQuestion.sectionId,
        question_id: currentQuestion.id,
      });
    }
    if (tracker.visited.has(currentQuestion.id)) {
      emitDemoEvent("question_revisited", {
        question_id: currentQuestion.id,
        section_id: currentQuestion.sectionId,
      });
    } else {
      tracker.visited.add(currentQuestion.id);
    }
    window.RiskTelemetry.enterQuestion(currentQuestion.id);
    return () => {
      try {
        window.RiskTelemetry.leaveQuestion(currentQuestion.id);
      } catch {
        // ignore SDK cleanup issues
      }
    };
  }, [currentQuestion, currentSection?.title, emitDemoEvent, stage, tracker]);

  useEffect(() => {
    if (timeRemaining !== 0 || (stage !== "assessment" && stage !== "review")) return;
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

  useEffect(() => {
    if (!currentQuestion?.id || currentQuestion.inputType !== "code") return;
    setEditorLanguageByQuestion((prev) => {
      if (prev[currentQuestion.id]) return prev;
      return {
        ...prev,
        [currentQuestion.id]: currentQuestion.languageOptions?.[0] || currentQuestion.snippetLanguage || "javascript",
      };
    });
  }, [currentQuestion]);

  const goToQuestion = useCallback((nextIndex) => {
    setQuestionIndex(Math.max(0, Math.min(totalQuestions - 1, nextIndex)));
  }, [totalQuestions]);

  const waitForReportReadiness = useCallback(async (targetAttemptId, { timeoutMs = 22000, intervalMs = 900 } = {}) => {
    const startedAt = Date.now();
    const reportUrlFallback = targetAttemptId?.startsWith("demo_public_")
      ? `/demo/report/${encodeURIComponent(targetAttemptId)}`
      : `/?attemptId=${encodeURIComponent(targetAttemptId)}`;
    let lastKnown = {
      reportReady: false,
      provenanceReady: false,
      timedOut: false,
      reportUrl: reportUrlFallback,
    };
    let attemptCount = 0;

    while (Date.now() - startedAt < timeoutMs) {
      attemptCount += 1;
      const elapsedMs = Date.now() - startedAt;
      const result = await authJsonFetch(`${apiBaseUrl.replace(/\/$/, "")}/v1/reports/${targetAttemptId}/status`, {
        timeoutMs: 7000,
        retries: 1,
        retryDelayMs: 500,
      });
      if (result.ok && result.data?.data) {
        const statusData = result.data.data;
        lastKnown = {
          reportReady: Boolean(statusData.report_exists),
          provenanceReady: Boolean(statusData.provenance_ready),
          timedOut: false,
          reportUrl: statusData.report_url || reportUrlFallback,
        };
        if (lastKnown.provenanceReady) {
          console.info("[Demo Submit Lifecycle]", { event: "provenance_ready", attemptId: targetAttemptId });
        }
        if (lastKnown.reportReady) {
          console.info("[Demo Submit Lifecycle]", { event: "report_ready", attemptId: targetAttemptId });
        }
        if (lastKnown.reportReady && lastKnown.provenanceReady) {
          return lastKnown;
        }
        if (lastKnown.reportReady && elapsedMs >= Math.min(timeoutMs - 2000, 9000)) {
          return lastKnown;
        }
      }
      const nextIntervalMs = Math.min(intervalMs + (attemptCount > 5 ? 250 : 0), 1600);
      await new Promise((resolve) => window.setTimeout(resolve, nextIntervalMs));
    }

    return {
      ...lastKnown,
      timedOut: true,
    };
  }, [apiBaseUrl]);

  const handleToggleFullscreen = useCallback(async () => {
    if (typeof document === "undefined") return;
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await document.documentElement.requestFullscreen();
      }
    } catch {
      // ignore browser fullscreen errors
    }
  }, []);

  const handleContinueToInstructions = useCallback(() => {
    if (!candidateName.trim() || !candidateEmail.trim()) {
      setTelemetryError("Enter a candidate name and email to begin the demo assessment.");
      return;
    }
    if (!consented) {
      setTelemetryError("Candidate consent is required before telemetry can begin.");
      return;
    }
    setTelemetryError("");
    setStage("instructions");
  }, [candidateEmail, candidateName, consented]);

  const handleStart = useCallback(() => {
    const nextAttemptId = createDemoAttemptId(candidateName, selectedAssessment.id);
    const nextCandidateId = createDemoCandidateId(candidateName, candidateEmail);
    replaceDemoCompletionInLocation(null);
    setAttemptId(nextAttemptId);
    setCandidateId(nextCandidateId);
    setAnswers({});
    setMarkedForReview({});
    setQuestionIndex(0);
    setSubmittedAt("");
    setSubmissionMessage("");
    setSubmissionBusy(false);
    setSectionCompletion(null);
    setEditorLanguageByQuestion({});
    setEditorConsoleByQuestion({});
    setCustomInputByQuestion({});
    setSignalToasts([]);
    toastTracker.current.lastShownAt = {};
    toastTracker.current.lastAnswerChangeByQuestion = {};
    toastTracker.current.recentTelemetrySignatures = {};
    toastTracker.current.recentSignals = [];
    setTelemetryError("");
    tracker.visited.clear();
    tracker.lastSectionId = "";

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
      setTelemetryStatus("Live telemetry active");
      setStage("assessment");
    } catch (error) {
      setTelemetryError(error instanceof Error ? error.message : "Unable to initialize assessment telemetry.");
    }
  }, [apiBaseUrl, candidateEmail, candidateName, selectedAssessment.id, selectedAssessment.name, tracker]);

  const handleAnswerChange = useCallback((questionId, value) => {
    const now = Date.now();
    const nextValue = String(value || "");
    const previousValue = String(answers[questionId] || "");
    const previousChange = toastTracker.current.lastAnswerChangeByQuestion[questionId] || { at: 0, length: previousValue.length, bursts: 0 };
    const lengthDelta = Math.abs(nextValue.length - previousValue.length);
    const rapidWindowMs = now - previousChange.at;
    const rapidBursts = rapidWindowMs > 0 && rapidWindowMs < 850
      ? (previousChange.bursts || 0) + 1
      : 1;
    toastTracker.current.lastAnswerChangeByQuestion[questionId] = {
      at: now,
      length: nextValue.length,
      bursts: rapidBursts,
    };

    if (lengthDelta >= 140) {
      addDemoSignalToast(DEMO_SIGNAL_MAP.large_answer_insert);
    } else if (rapidBursts >= 4) {
      addDemoSignalToast(DEMO_SIGNAL_MAP.rapid_answer_change);
    }

    setAnswers((prev) => ({ ...prev, [questionId]: value }));
    try {
      window.RiskTelemetry?.trackAnswerChange(questionId, value);
    } catch (error) {
      setTelemetryError(error instanceof Error ? error.message : "Unable to record typing telemetry.");
    }
  }, [addDemoSignalToast, answers]);

  const handleMcqKeyDown = useCallback((event, question, optionIndex) => {
    if (!question?.options?.length) return;
    const optionCount = question.options.length;
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      handleAnswerChange(question.id, question.options[optionIndex]);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowRight") {
      event.preventDefault();
      const next = (optionIndex + 1) % optionCount;
      handleAnswerChange(question.id, question.options[next]);
      return;
    }
    if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
      event.preventDefault();
      const next = (optionIndex - 1 + optionCount) % optionCount;
      handleAnswerChange(question.id, question.options[next]);
    }
  }, [handleAnswerChange]);

  const handleToggleReview = useCallback((question) => {
    setMarkedForReview((prev) => {
      const nextValue = !prev[question.id];
      emitDemoEvent("question_marked_for_review", {
        question_id: question.id,
        section_id: question.sectionId,
        marked: nextValue,
      });
      return { ...prev, [question.id]: nextValue };
    });
  }, [emitDemoEvent]);

  const handleOpenReview = useCallback(() => {
    emitDemoEvent("assessment_review_started", {
      answered_count: answeredCount,
      unanswered_count: unansweredCount,
      marked_for_review_count: markedCount,
      time_remaining_seconds: timeRemaining,
    });
    setTelemetryStatus("Reviewing before submission");
    setStage("review");
  }, [answeredCount, emitDemoEvent, markedCount, timeRemaining, unansweredCount]);

  const handleAdvanceQuestion = useCallback(() => {
    if (!currentQuestion) return;
    if (questionIndex >= totalQuestions - 1) {
      handleOpenReview();
      return;
    }

    const nextQuestion = flatQuestions[questionIndex + 1];
    if (nextQuestion?.sectionId && nextQuestion.sectionId !== currentQuestion.sectionId) {
      const completedSection = sectionSummaries.find((section) => section.id === currentQuestion.sectionId);
      const upcomingSection = sectionSummaries.find((section) => section.id === nextQuestion.sectionId);
      setSectionCompletion({
        completedSection,
        upcomingSection,
        answered: completedSection?.answered || 0,
        skipped: Math.max(0, (completedSection?.total || 0) - (completedSection?.answered || 0)),
        nextIndex: questionIndex + 1,
      });
      return;
    }

    goToQuestion(questionIndex + 1);
  }, [currentQuestion, flatQuestions, goToQuestion, handleOpenReview, questionIndex, sectionSummaries, totalQuestions]);

  const handleSubmit = useCallback(async () => {
    if (submissionBusy) return;

    const progressTimers = [];
    const submitStartedAt = typeof performance !== "undefined" ? performance.now() : Date.now();
    const clearProgressTimers = () => {
      progressTimers.forEach((timerId) => window.clearTimeout(timerId));
    };
    const queueSubmissionMessage = (message, delayMs = 0) => {
      const timerId = window.setTimeout(() => {
        setSubmissionMessage(message);
        setTelemetryStatus(message);
      }, delayMs);
      progressTimers.push(timerId);
    };

    setSubmissionBusy(true);
    setReportReadyState({ reportReady: false, provenanceReady: false, timedOut: false });
    setTelemetryError("");
    setSubmissionMessage("Submitting assessment...");
    setTelemetryStatus("Submitting assessment...");
    setStage("submitting");
    console.info("[Demo Submit Lifecycle]", { event: "submission_started", attemptId });
    queueSubmissionMessage("Analyzing behavioral signals...", 450);
    queueSubmissionMessage("Generating reviewer report...", 1400);

    try {
      const flushBeforeSubmitStartedAt = typeof performance !== "undefined" ? performance.now() : Date.now();
      const preSubmitFlush = await window.RiskTelemetry?.flush?.({ timeoutMs: 6000, settleMs: 100 });
      const flushBeforeSubmitDurationMs = (typeof performance !== "undefined" ? performance.now() : Date.now()) - flushBeforeSubmitStartedAt;

      window.RiskTelemetry?.endExam();

      const flushAfterSubmitStartedAt = typeof performance !== "undefined" ? performance.now() : Date.now();
      const finalFlush = await window.RiskTelemetry?.flush?.({ timeoutMs: 12000, settleMs: 125 });
      const flushAfterSubmitDurationMs = (typeof performance !== "undefined" ? performance.now() : Date.now()) - flushAfterSubmitStartedAt;
      console.info("[Demo Submit Lifecycle]", {
        event: "telemetry_flushed",
        attemptId,
        flushBeforeSubmitMs: Math.round(flushBeforeSubmitDurationMs),
        flushAfterSubmitMs: Math.round(flushAfterSubmitDurationMs),
      });

      if (finalFlush && finalFlush.ok === false) {
        console.warn("[Demo Submit Lifecycle]", {
          event: "telemetry_flush_timeout",
          attemptId,
          queueLength: finalFlush.queueLength ?? 0,
        });
      }

      const submittedAnswersPayload = {
        attempt_id: attemptId,
        candidate_id: candidateId,
        candidate_name: candidateName.trim(),
        candidate_email: candidateEmail.trim(),
        assessment_id: selectedAssessment.id,
        assessment_name: selectedAssessment.name,
        answers: flatQuestions
          .filter((question) => isDemoAnswerFilled(question, answers[question.id]))
          .map((question) => ({
            question_id: question.id,
            question_title: question.title,
            section_id: question.sectionId,
            section_title: question.sectionTitle,
            assessment_type: getDemoQuestionAssessmentType(question),
            input_type: question.inputType,
            answer_text: String(answers[question.id] || "").trim(),
            marked_for_review: Boolean(markedForReview[question.id]),
          })),
      };
      const provenancePersistStartedAt = typeof performance !== "undefined" ? performance.now() : Date.now();
      const provenancePersistResult = await authJsonFetch(
        `${apiBaseUrl.replace(/\/$/, "")}/v1/attempts/${attemptId}/answers`,
        {
          method: "POST",
          body: submittedAnswersPayload,
          timeoutMs: 12000,
          retries: 1,
          retryDelayMs: 800,
        },
      );
      const provenancePersistDurationMs = (typeof performance !== "undefined" ? performance.now() : Date.now()) - provenancePersistStartedAt;
      if (!provenancePersistResult.ok) {
        console.warn("[Demo Provenance Persist]", provenancePersistResult.data?.detail || "Unable to persist submitted answers for provenance analysis.");
      }

      const reportState = await waitForReportReadiness(attemptId);
      setReportReadyState(reportState);

      clearProgressTimers();
      setSubmissionMessage("Assessment submitted successfully");
      setTelemetryStatus("Assessment submitted successfully");
      console.info("[Demo Submit Timing]", {
        attemptId,
        flushBeforeSubmitMs: Math.round(flushBeforeSubmitDurationMs),
        flushAfterSubmitMs: Math.round(flushAfterSubmitDurationMs),
        provenancePersistMs: Math.round(provenancePersistDurationMs),
        pendingEventsBeforeSubmit: preSubmitFlush?.queueLength ?? 0,
        pendingEventsAfterSubmit: finalFlush?.queueLength ?? 0,
        totalSubmitMs: Math.round((typeof performance !== "undefined" ? performance.now() : Date.now()) - submitStartedAt),
      });
      const completedAt = new Date().toISOString();
      setSubmittedAt(completedAt);
      replaceDemoCompletionInLocation({
        attemptId,
        submittedAt: completedAt,
        candidateName: candidateName.trim(),
        assessmentId: selectedAssessment.id,
        reportReady: reportState.reportReady,
        provenanceReady: reportState.provenanceReady,
      });
    } catch (error) {
      clearProgressTimers();
      setSubmissionBusy(false);
      setStage("review");
      setSubmissionMessage("");
      setTelemetryError(error instanceof Error ? error.message : "Unable to submit telemetry cleanly.");
      setTelemetryStatus("Submission delayed — retry required");
      return;
    }

    setSubmissionBusy(false);
    setStage("submitted");
  }, [answers, apiBaseUrl, attemptId, candidateEmail, candidateId, candidateName, flatQuestions, markedForReview, selectedAssessment.id, selectedAssessment.name, submissionBusy, waitForReportReadiness]);

  const handleDemoRun = useCallback(() => {
    if (!currentQuestion?.id) return;
    const answerLength = String(currentQuestionAnswer || "").trim().length;
    const customInput = String(customInputByQuestion[currentQuestion.id] || "").trim();
    setEditorConsoleByQuestion((prev) => ({
      ...prev,
      [currentQuestion.id]: {
        ranAt: new Date().toISOString(),
        message: answerLength > 0
          ? `Execution preview captured locally for the ${currentEditorLanguage.toUpperCase()} draft. ${customInput ? "Custom input attached." : "No custom input was provided."}`
          : "Add a response draft before running the local execution preview.",
      },
    }));
  }, [currentEditorLanguage, currentQuestion?.id, currentQuestionAnswer, customInputByQuestion]);

  const handleOpenCandidateReport = useCallback(() => {
    if (!attemptId) return;
    window.location.assign(`/demo/report/${encodeURIComponent(attemptId)}`);
  }, [attemptId]);

  const handleOpenReviewerConsole = useCallback(() => {
    window.location.assign("/");
  }, []);

  const handleRestart = useCallback(() => {
    try {
      window.RiskTelemetry?.destroy();
    } catch {
      // ignore
    }
    replaceDemoCompletionInLocation(null);
    if (selectedAssessment?.isProvenanceValidation) {
      setAssessmentSessionSeed(`${Date.now()}`);
    }
    setTelemetryStatus("Telemetry inactive");
    setTelemetryError("");
    setAttemptId("");
    setCandidateId("");
    setAnswers({});
    setMarkedForReview({});
    setQuestionIndex(0);
    setSubmittedAt("");
    setSubmissionMessage("");
    setSubmissionBusy(false);
    setSectionCompletion(null);
    setEditorLanguageByQuestion({});
    setEditorConsoleByQuestion({});
    setCustomInputByQuestion({});
    setReportReadyState({ reportReady: false, provenanceReady: false, timedOut: false });
    setSignalToasts([]);
    toastTracker.current.lastShownAt = {};
    toastTracker.current.lastAnswerChangeByQuestion = {};
    toastTracker.current.recentTelemetrySignatures = {};
    toastTracker.current.recentSignals = [];
    setStage("welcome");
    setTimeRemaining(selectedAssessment.durationMinutes * 60);
    tracker.visited.clear();
    tracker.lastSectionId = "";
  }, [selectedAssessment?.isProvenanceValidation, selectedAssessment.durationMinutes, tracker]);

  return (
    <div className="public-demo-shell">
      <div className={`public-demo-page demo-experience stage-${stage}`}>
        <section className="public-demo-hero">
          <div className="public-demo-hero-copy">
            <span className="page-kicker">PUBLIC DEMO</span>
            <h1>Experience an enterprise ProctorIQ assessment</h1>
            <p>
              ProctorIQ converts privacy-preserving behavioral telemetry into explainable reviewer intelligence,
              showing how real assessment sessions become operational investigation workflows without webcam or audio monitoring.
            </p>
          </div>
          <div className="public-demo-hero-actions">
            <a className="demo-link-btn" href="/">Open Reviewer Console</a>
          </div>
        </section>

        <section className={`public-demo-grid${stage === "assessment" ? " assessment-stage" : ""}`}>
          <div className="public-demo-main">
            {!demoDataReady ? (
              <section className="public-demo-card public-demo-stage-card">
                <div className="public-demo-card-head">
                  <h2>Assessment setup unavailable</h2>
                  <span className="demo-status-chip warning">Retry required</span>
                </div>
                <div className="public-demo-copy-block">
                  <p>The demo assessment content could not be loaded safely. Refresh the page to reinitialize the session shell.</p>
                </div>
              </section>
            ) : null}

            {demoDataReady && stage === "welcome" && (
              <section className="public-demo-card public-demo-stage-card">
                <div className="public-demo-card-head">
                  <h2>Welcome</h2>
                  <span className="demo-status-chip neutral">Privacy-preserving telemetry</span>
                </div>
                <div className="public-demo-notice-grid">
                  <div className="public-demo-copy-block">
                    <p>
                      This demo mirrors a real enterprise assessment session. The reviewer console will receive the same
                      telemetry stream used to build explainable behavioral evidence, correlated suspicious sequences,
                      and investigation-ready reviewer workflows.
                    </p>
                    <ul className="public-demo-bullets">
                      <li>No webcam or microphone monitoring</li>
                      <li>No clipboard contents stored</li>
                      <li>No answer text stored as telemetry</li>
                      <li>Only metadata from timing, focus, typing rhythm, navigation, and clipboard behavior is analyzed</li>
                    </ul>
                  </div>
                  <div className="public-demo-telemetry-panel">
                    <div className="public-demo-mini-kicker">Telemetry inputs</div>
                    <div className="public-demo-mini-grid">
                      <div className="public-demo-mini-stat"><strong>Focus</strong><span>Blur, return, visibility cycles</span></div>
                      <div className="public-demo-mini-stat"><strong>Typing</strong><span>Bursts, pauses, recovery timing</span></div>
                      <div className="public-demo-mini-stat"><strong>Clipboard</strong><span>Metadata only, never contents</span></div>
                      <div className="public-demo-mini-stat"><strong>Navigation</strong><span>Question revisits and sequencing</span></div>
                    </div>
                  </div>
                </div>
                <div className="public-demo-consent">
                  <label className="public-demo-checkbox">
                    <input checked={consented} onChange={(event) => setConsented(event.target.checked)} type="checkbox" />
                    <span>I understand this demo analyzes behavioral telemetry metadata only and does not capture sensitive content, webcam, audio, or clipboard contents.</span>
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
                        onClick={() => {
                          setAssessmentId(assessment.id);
                          if (assessment.isProvenanceValidation) {
                            setAssessmentSessionSeed(`${Date.now()}`);
                          }
                        }}
                        type="button"
                      >
                        <span className="public-demo-assessment-icon"><Icon size={18} /></span>
                        <strong>{assessment.name}</strong>
                        {assessment.internalBadge ? <span className="demo-status-chip provenance">{assessment.internalBadge}</span> : null}
                        <small>{assessment.intro}</small>
                      </button>
                    );
                  })}
                </div>
                {telemetryError ? <div className="workflow-message workflow-message-warning">{telemetryError}</div> : null}
                <div className="public-demo-actions">
                  <button className="workflow-action-btn primary" onClick={handleContinueToInstructions} type="button">
                    <Play size={16} />
                    Continue to Instructions
                  </button>
                </div>
              </section>
            )}

            {demoDataReady && stage === "instructions" && (
              <section className="public-demo-card public-demo-stage-card">
                <div className="public-demo-card-head">
                  <div>
                    <span className="report-section-kicker">{selectedAssessment.name}</span>
                    <h2>Assessment Instructions</h2>
                  </div>
                  <div className="public-demo-head-badges">
                    {selectedAssessment.internalBadge ? <span className="demo-status-chip provenance">{selectedAssessment.internalBadge}</span> : null}
                    <span className="demo-status-chip neutral">Review before submit enabled</span>
                  </div>
                </div>
                <div className="public-demo-instructions-grid">
                  <p>
                    {selectedAssessment.intro}
                  </p>
                  <ul className="public-demo-bullets">
                    <li>{selectedAssessment.durationMinutes} minute guided demo session</li>
                    <li>{selectedAssessment.sections.length} sections and {totalQuestions} questions</li>
                    <li>Use next/back navigation and mark items for review before final submission</li>
                    <li>Telemetry is generated from timing, focus shifts, clipboard metadata, typing rhythm, and question navigation</li>
                  </ul>
                  <div className="public-demo-summary-grid single">
                    <div className="public-demo-summary-item">
                      <span>Sections</span>
                      <strong>{selectedAssessment.sections.map((section) => section.title).join(" • ")}</strong>
                    </div>
                    <div className="public-demo-summary-item">
                      <span>Submit rule</span>
                      <strong>Review screen appears before final submission</strong>
                    </div>
                    <div className="public-demo-summary-item">
                      <span>Assessment timer</span>
                      <strong>{selectedAssessment.durationMinutes} minutes</strong>
                    </div>
                  </div>
                </div>
                <div className="public-demo-actions">
                  <button className="workflow-action-btn" onClick={() => setStage("welcome")} type="button">
                    Back
                  </button>
                  <button className="workflow-action-btn primary" onClick={handleStart} type="button">
                    <Play size={16} />
                    Start Assessment
                  </button>
                </div>
              </section>
            )}

            {demoDataReady && stage === "assessment" && currentQuestion && (
              <section className="public-demo-assessment-shell immersive">
                <header className="public-demo-topbar">
                  <div className="public-demo-topbar-left">
                    <div className="public-demo-candidate-pill">
                      <span className="public-demo-candidate-avatar">{(candidateName || "PQ").slice(0, 2).toUpperCase()}</span>
                      <div>
                        <strong>{candidateName || "Demo Candidate"}</strong>
                        <small>{selectedAssessment.name}</small>
                      </div>
                    </div>
                    <div className="public-demo-compact-progress">
                      <span>Assessment Progress</span>
                      <strong>{questionIndex + 1} / {totalQuestions}</strong>
                      <div className="public-demo-progress-track slim">
                        <span style={{ width: `${((questionIndex + 1) / Math.max(1, totalQuestions)) * 100}%` }}></span>
                      </div>
                    </div>
                  </div>
                  <div className="public-demo-topbar-right">
                    <div className="public-demo-topbar-chip">
                      {connectionStable ? <Wifi size={15} /> : <WifiOff size={15} />}
                      <span>{connectionStable ? "Connection stable" : "Reconnecting"}</span>
                    </div>
                    <div className="public-demo-topbar-chip">
                      <Activity size={15} />
                      <span>Assessment integrity active</span>
                    </div>
                    <div className="public-demo-topbar-chip">
                      <CheckCheck size={15} />
                      <span>Auto-save enabled</span>
                    </div>
                    {selectedAssessment.internalBadge ? (
                      <div className="public-demo-topbar-chip provenance">
                        <Lock size={15} />
                        <span>{selectedAssessment.internalBadge}</span>
                      </div>
                    ) : null}
                    <div className="public-demo-timer premium">
                      <TimerReset size={16} />
                      <strong>{formatDuration(timeRemaining)}</strong>
                    </div>
                    <button className="public-demo-icon-btn" onClick={() => void handleToggleFullscreen()} type="button">
                      {isFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
                    </button>
                  </div>
                </header>

                <div className="public-demo-assessment-body">
                  <aside className="public-demo-assessment-sidebar">
                    <section className="public-demo-sidebar-panel">
                      <div className="public-demo-sidebar-heading">
                        <span>Section progress</span>
                        <strong>{currentSectionSummary?.label || currentSection?.title}</strong>
                      </div>
                      <div className="public-demo-section-list">
                        {sectionSummaries.map((section) => {
                          const Icon = section.icon;
                          const firstQuestionIndex = flatQuestions.findIndex((question) => question.sectionId === section.id);
                          const active = currentSection?.id === section.id;
                          return (
                            <button
                              key={section.id}
                              className={`public-demo-section-card ${active ? "active" : ""} ${section.accent || ""}`}
                              onClick={() => goToQuestion(firstQuestionIndex)}
                              type="button"
                            >
                              <span className="public-demo-section-icon"><Icon size={16} /></span>
                              <div>
                                <strong>{section.label}</strong>
                                <small>{section.subtitle}</small>
                              </div>
                              <div className="public-demo-section-stats">
                                <span>{section.estimate}</span>
                                <span>{section.answered}/{section.total}</span>
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    </section>

                    <section className="public-demo-sidebar-panel">
                      <div className="public-demo-sidebar-heading">
                        <span>Question navigator</span>
                        <strong>{answeredCount} answered</strong>
                      </div>
                      <div className="public-demo-question-strip">
                        {flatQuestions.map((question, index) => {
                          const chipState = getQuestionCompletionState(question, answers[question.id], index === questionIndex, Boolean(markedForReview[question.id]));
                          return (
                            <button
                              key={question.id}
                              className={`public-demo-question-index ${chipState}`}
                              onClick={() => goToQuestion(index)}
                              type="button"
                              title={`${question.displayNumber} · ${question.title}`}
                            >
                              {index + 1}
                            </button>
                          );
                        })}
                      </div>
                    </section>

                    <section className="public-demo-sidebar-panel subtle">
                      <div className="public-demo-sidebar-heading">
                        <span>Session overview</span>
                        <strong>{telemetryStatus}</strong>
                      </div>
                      <div className="public-demo-session-grid">
                        <div className="public-demo-session-stat">
                          <span>Attempt ID</span>
                          <strong>{attemptId}</strong>
                        </div>
                        <div className="public-demo-session-stat">
                          <span>Current section</span>
                          <strong>{currentSectionSummary?.label || currentSection?.title}</strong>
                        </div>
                        <div className="public-demo-session-stat">
                          <span>Review flags</span>
                          <strong>{markedCount}</strong>
                        </div>
                      </div>
                    </section>
                  </aside>

                  <div className="public-demo-workspace">
                    <div className={`public-demo-workspace-header ${currentSectionSummary?.accent || ""}`}>
                      <div>
                        <span className="report-section-kicker">{currentSectionSummary?.label || currentSection?.title}</span>
                        <h2>{currentQuestion.title}</h2>
                        <p>{currentSection?.subtitle}</p>
                        {currentQuestion?.category ? <small>{currentQuestion.category}</small> : null}
                      </div>
                      <div className="public-demo-workspace-meta">
                        <span>{currentQuestion.displayNumber}</span>
                        <span>{currentQuestion.difficulty}</span>
                        <span>{currentSectionSummary?.answered || 0}/{currentSectionSummary?.total || 0} complete</span>
                      </div>
                    </div>

                    <div className="public-demo-progress-track">
                      <span style={{ width: `${currentSectionProgress}%` }}></span>
                    </div>

                    {currentQuestion.inputType === "code" ? (
                      <div className="public-demo-coding-shell">
                        <section className="public-demo-problem-panel">
                          <div className="public-demo-problem-block">
                            <div className="public-demo-question-meta premium">
                              <span>{currentQuestion.displayNumber}</span>
                              <span>{currentSectionSummary?.label || currentSection?.title}</span>
                              <span>{currentQuestion.difficulty}</span>
                            </div>
                            <p>{currentQuestion.prompt}</p>
                          </div>
                          {currentQuestion.snippet ? (
                            <div className="public-demo-problem-block">
                              <div className="public-demo-problem-label">Reference context</div>
                              <pre className={`public-demo-snippet language-${currentQuestion.snippetLanguage || "text"}`}>
                                <code>{currentQuestion.snippet}</code>
                              </pre>
                            </div>
                          ) : null}
                          <div className="public-demo-problem-grid">
                            <div className="public-demo-problem-block">
                              <div className="public-demo-problem-label">Constraints</div>
                              <ul className="public-demo-bullets compact">
                                {(currentQuestion.constraints || []).map((item) => <li key={item}>{item}</li>)}
                              </ul>
                            </div>
                            <div className="public-demo-problem-block">
                              <div className="public-demo-problem-label">Examples</div>
                              <ul className="public-demo-bullets compact">
                                {(currentQuestion.examples || []).map((item) => <li key={item}>{item}</li>)}
                              </ul>
                            </div>
                          </div>
                          <div className="public-demo-tag-row">
                            {(currentQuestion.tags || []).map((tag) => <span className="public-demo-chip" key={tag}>{tag}</span>)}
                          </div>
                          <div className="public-demo-answer-signals coding">
                            <span className="public-demo-mini-kicker">Expected behavioral signals</span>
                            <div className="public-demo-chip-row">
                              {(currentSection?.telemetryFocus || []).map((item) => (
                                <span className="public-demo-chip" key={item}>{item}</span>
                              ))}
                            </div>
                            <p>{currentQuestion.expectedBehavior}</p>
                          </div>
                        </section>

                        <section className="public-demo-editor-panel">
                          <div className="public-demo-editor-toolbar">
                            <div className="public-demo-editor-toolbar-group">
                              <span className="public-demo-problem-label">Language</span>
                              <select
                                value={currentEditorLanguage}
                                onChange={(event) => setEditorLanguageByQuestion((prev) => ({ ...prev, [currentQuestion.id]: event.target.value }))}
                              >
                                {(currentQuestion.languageOptions || [currentQuestion.snippetLanguage || "javascript"]).map((lang) => (
                                  <option key={lang} value={lang}>{lang.toUpperCase()}</option>
                                ))}
                              </select>
                            </div>
                            <div className="public-demo-editor-toolbar-group status">
                              <span>Session active</span>
                              <span>Auto-save enabled</span>
                            </div>
                          </div>
                          <DemoMonacoEditor
                            language={currentEditorLanguage}
                            value={currentQuestionAnswer || currentQuestion.starterCode?.[currentEditorLanguage] || ""}
                            onChange={(nextValue) => handleAnswerChange(currentQuestion.id, nextValue)}
                          />
                          <div className="public-demo-editor-footer">
                            <div className="public-demo-editor-console">
                              <strong>Execution panel</strong>
                              <small>{editorConsoleByQuestion[currentQuestion.id]?.message || "Use this space to reason through the prompt and validate your final response before continuing."}</small>
                              <input
                                className="public-demo-custom-input"
                                value={customInputByQuestion[currentQuestion.id] || ""}
                                onChange={(event) => setCustomInputByQuestion((prev) => ({ ...prev, [currentQuestion.id]: event.target.value }))}
                                placeholder="Optional custom input for your local execution preview"
                              />
                            </div>
                            <div className="public-demo-editor-actions">
                              <button className="workflow-action-btn" onClick={() => handleToggleReview(currentQuestion)} type="button">
                                {markedForReview[currentQuestion.id] ? "Unmark Review" : "Mark for Review"}
                              </button>
                              <button className="workflow-action-btn" onClick={handleDemoRun} type="button">Run</button>
                              {questionIndex < totalQuestions - 1 ? (
                                <button className="workflow-action-btn primary" onClick={handleAdvanceQuestion} type="button">
                                  Save & Next
                                </button>
                              ) : (
                                <button className="workflow-action-btn warning" onClick={handleOpenReview} type="button">
                                  Review Before Submit
                                </button>
                              )}
                            </div>
                          </div>
                        </section>
                      </div>
                    ) : (
                      <div className="public-demo-question-stage">
                        <div className="public-demo-question-card premium">
                          <div className="public-demo-question-meta premium">
                            <span>{currentQuestion.displayNumber}</span>
                            <span>{currentSectionSummary?.label || currentSection?.title}</span>
                            <span>{currentQuestion.difficulty}</span>
                          </div>
                          <div className="public-demo-question">
                            <p>{currentQuestion.prompt}</p>
                            {currentQuestion.snippet ? (
                              <pre className={`public-demo-snippet language-${currentQuestion.snippetLanguage || "text"}`}>
                                <code>{currentQuestion.snippet}</code>
                              </pre>
                            ) : null}
                            {currentQuestion.inputType === "mcq" ? (
                              <div className="public-demo-option-list premium" role="radiogroup" aria-label={currentQuestion.title}>
                                {currentQuestion.options.map((option, optionIndex) => {
                                  const checked = answers[currentQuestion.id] === option;
                                  return (
                                    <label
                                      className={`public-demo-option premium ${checked ? "active" : ""}`}
                                      key={option}
                                      onKeyDown={(event) => handleMcqKeyDown(event, currentQuestion, optionIndex)}
                                      tabIndex={0}
                                    >
                                      <input
                                        checked={checked}
                                        name={currentQuestion.id}
                                        onChange={() => handleAnswerChange(currentQuestion.id, option)}
                                        type="radio"
                                      />
                                      <span className="public-demo-option-marker">{String.fromCharCode(65 + optionIndex)}</span>
                                      <span>{option}</span>
                                    </label>
                                  );
                                })}
                              </div>
                            ) : currentQuestion.inputType === "textarea" ? (
                              <textarea
                                rows={8}
                                value={currentQuestionAnswer}
                                onChange={(event) => handleAnswerChange(currentQuestion.id, event.target.value)}
                                placeholder={currentQuestion.placeholder}
                              />
                            ) : (
                              <input
                                value={currentQuestionAnswer}
                                onChange={(event) => handleAnswerChange(currentQuestion.id, event.target.value)}
                                placeholder={currentQuestion.placeholder}
                              />
                            )}
                          </div>
                        </div>

                        <div className="public-demo-answer-footer elevated">
                          <div className="public-demo-answer-signals">
                            <span className="public-demo-mini-kicker">Expected behavioral signals</span>
                            <div className="public-demo-chip-row">
                              {(currentSection?.telemetryFocus || []).map((item) => (
                                <span className="public-demo-chip" key={item}>{item}</span>
                              ))}
                            </div>
                            <p>{currentQuestion.expectedBehavior}</p>
                          </div>
                          <div className="public-demo-actions">
                            <button className="workflow-action-btn" onClick={() => handleToggleReview(currentQuestion)} type="button">
                              {markedForReview[currentQuestion.id] ? "Unmark Review" : "Mark for Review"}
                            </button>
                            <button className="workflow-action-btn" disabled={questionIndex === 0} onClick={() => goToQuestion(questionIndex - 1)} type="button">
                              Previous
                            </button>
                            {questionIndex < totalQuestions - 1 ? (
                              <button className="workflow-action-btn primary" onClick={handleAdvanceQuestion} type="button">
                                Save & Next
                              </button>
                            ) : (
                              <button className="workflow-action-btn warning" onClick={handleOpenReview} type="button">
                                Review Before Submit
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>

                  <aside className="public-demo-assessment-info-rail">
                    <DemoInfoRail
                      telemetryStatus={telemetryStatus}
                      candidateId={candidateId}
                      apiBaseUrl={apiBaseUrl}
                      selectedAssessment={selectedAssessment}
                      totalQuestions={totalQuestions}
                      telemetryError={telemetryError}
                    />
                  </aside>
                </div>

                {sectionCompletion ? (
                  <div className="public-demo-section-modal-backdrop">
                    <div className="public-demo-section-modal">
                      <span className="report-section-kicker">Section completed</span>
                      <h3>{sectionCompletion.completedSection?.label || sectionCompletion.completedSection?.title}</h3>
                      <p>
                        You’ve completed this section. Review your progress, then continue into the next phase of the assessment.
                      </p>
                      <div className="public-demo-summary-grid">
                        <div className="public-demo-summary-item">
                          <span>Answered</span>
                          <strong>{sectionCompletion.answered}</strong>
                        </div>
                        <div className="public-demo-summary-item">
                          <span>Skipped</span>
                          <strong>{sectionCompletion.skipped}</strong>
                        </div>
                        <div className="public-demo-summary-item">
                          <span>Upcoming</span>
                          <strong>{sectionCompletion.upcomingSection?.label || sectionCompletion.upcomingSection?.title}</strong>
                        </div>
                        <div className="public-demo-summary-item">
                          <span>Remaining Time</span>
                          <strong>{formatDuration(timeRemaining)}</strong>
                        </div>
                      </div>
                      <div className="public-demo-progress-track completion">
                        <span style={{ width: `${((sectionCompletion.nextIndex) / Math.max(1, totalQuestions)) * 100}%` }}></span>
                      </div>
                      <div className="public-demo-actions">
                        <button className="workflow-action-btn" onClick={() => setSectionCompletion(null)} type="button">
                          Stay Here
                        </button>
                        <button
                          className="workflow-action-btn primary"
                          onClick={() => {
                            goToQuestion(sectionCompletion.nextIndex);
                            setSectionCompletion(null);
                          }}
                          type="button"
                        >
                          Continue to Next Section
                        </button>
                      </div>
                    </div>
                  </div>
                ) : null}
              </section>
            )}

            {demoDataReady && stage === "review" && (
              <section className="public-demo-card public-demo-stage-card">
                <div className="public-demo-card-head">
                  <div>
                    <span className="report-section-kicker">{selectedAssessment.name}</span>
                    <h2>Review Before Submit</h2>
                  </div>
                  <span className="demo-status-chip active">Final check</span>
                </div>
                <div className="public-demo-summary-grid">
                  <div className="public-demo-summary-item">
                    <span>Answered</span>
                    <strong>{answeredCount} / {totalQuestions}</strong>
                  </div>
                  <div className="public-demo-summary-item">
                    <span>Unanswered</span>
                    <strong>{unansweredCount}</strong>
                  </div>
                  <div className="public-demo-summary-item">
                    <span>Marked for Review</span>
                    <strong>{markedCount}</strong>
                  </div>
                  <div className="public-demo-summary-item">
                    <span>Time Remaining</span>
                    <strong>{formatDuration(timeRemaining)}</strong>
                  </div>
                </div>
                <div className="public-demo-review-board">
                  {sectionSummaries.map((section) => (
                    <div className="public-demo-review-section" key={section.id}>
                      <div className="public-demo-review-head">
                        <div>
                          <strong>{section.order}. {section.title}</strong>
                          <small>{section.answered}/{section.total} answered</small>
                        </div>
                        <span>{section.marked} marked</span>
                      </div>
                      <div className="public-demo-review-list">
                        {section.questions.map((question) => {
                          const answered = isDemoAnswerFilled(question, answers[question.id]);
                          return (
                            <button
                              className={`public-demo-review-row ${answered ? "answered" : ""}`}
                              key={question.id}
                              onClick={() => {
                                goToQuestion(flatQuestions.findIndex((item) => item.id === question.id));
                                setStage("assessment");
                              }}
                              type="button"
                            >
                              <div>
                                <strong>{question.displayNumber} · {question.title}</strong>
                                <small>{question.difficulty}</small>
                              </div>
                              <div className="public-demo-review-tags">
                                {!answered ? <span className="public-demo-review-chip warning">Unanswered</span> : null}
                                {markedForReview[question.id] ? <span className="public-demo-review-chip">Marked</span> : null}
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
                <div className="public-demo-actions">
                  <button className="workflow-action-btn" onClick={() => setStage("assessment")} type="button">
                    Back to Questions
                  </button>
                  <button className="workflow-action-btn warning" disabled={submissionBusy} onClick={() => void handleSubmit()} type="button">
                    {submissionBusy ? "Submitting..." : "Submit Final Attempt"}
                  </button>
                </div>
              </section>
            )}

            {demoDataReady && stage === "submitting" && (
              <section className="public-demo-card public-demo-stage-card">
                <div className="public-demo-card-head">
                  <h2>Submitting assessment</h2>
                  <span className="demo-status-chip active">Processing</span>
                </div>
                <div className="public-demo-copy-block">
                  <p>
                    ProctorIQ is finalizing telemetry delivery, analyzing behavioral signals, and preparing the reviewer-facing investigation record.
                  </p>
                </div>
                <div className="public-demo-summary-grid">
                  <div className="public-demo-summary-item">
                    <span>Attempt ID</span>
                    <strong>{attemptId}</strong>
                  </div>
                  <div className="public-demo-summary-item">
                    <span>Current Step</span>
                    <strong>{submissionMessage || "Submitting assessment..."}</strong>
                  </div>
                  <div className="public-demo-summary-item">
                    <span>Assessment Track</span>
                    <strong>{selectedAssessment.name}</strong>
                  </div>
                  <div className="public-demo-summary-item">
                    <span>Status</span>
                    <strong>{telemetryStatus}</strong>
                  </div>
                </div>
              </section>
            )}

            {demoDataReady && stage === "submitted" && (
              <section className="public-demo-card public-demo-stage-card">
                <div className="public-demo-card-head">
                  <div>
                    <h2>Assessment Complete</h2>
                    <p>Your telemetry, behavioral analysis, and reviewer-facing investigation report were successfully generated.</p>
                  </div>
                  <span className="demo-status-chip success">
                    {reportReadyState.provenanceReady ? "Report ready" : reportReadyState.timedOut ? "Ready to review" : "Telemetry delivered"}
                  </span>
                </div>
                <div className="public-demo-copy-block">
                  <p>
                    {reportReadyState.timedOut
                      ? "The assessment has been submitted successfully. Report generation took longer than expected, but you can still open the generated investigation view and reviewer console safely."
                      : "The assessment session is now available in the reviewer workflow. Risk history, evidence, answer provenance findings, and correlated suspicious sequences can now be reviewed."}
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
                    <span>Assessment Track</span>
                    <strong>{selectedAssessment.name}</strong>
                  </div>
                  <div className="public-demo-summary-item">
                    <span>Submitted</span>
                    <strong>{formatDateTime(submittedAt)}</strong>
                  </div>
                  <div className="public-demo-summary-item">
                    <span>Submission Status</span>
                    <strong>{submissionMessage || "Assessment submitted successfully"}</strong>
                  </div>
                  <div className="public-demo-summary-item">
                    <span>Report Readiness</span>
                    <strong>{reportReadyState.provenanceReady ? "Provenance ready" : reportReadyState.reportReady ? "Report available" : "Processing fallback complete"}</strong>
                  </div>
                </div>
                <div className="public-demo-actions">
                  <button className="workflow-action-btn primary" onClick={handleOpenCandidateReport} type="button">
                    <Eye size={16} />
                    View My Candidate Report
                  </button>
                  <button className="workflow-action-btn" onClick={handleOpenReviewerConsole} type="button">
                    <ArrowUpRight size={16} />
                    Open Reviewer Console
                  </button>
                  <button className="workflow-action-btn primary" onClick={handleRestart} type="button">
                    <CheckSquare size={16} />
                    Start New Demo
                  </button>
                </div>
              </section>
            )}
          </div>

          {stage !== "assessment" ? (
            <aside className="public-demo-sidebar">
              <DemoInfoRail
                telemetryStatus={telemetryStatus}
                candidateId={candidateId}
                apiBaseUrl={apiBaseUrl}
                selectedAssessment={selectedAssessment}
                totalQuestions={totalQuestions}
                telemetryError={telemetryError}
              />
            </aside>
          ) : null}
        </section>
        {DEMO_SIGNAL_TOASTS_ENABLED && signalToasts.length ? (
          <div className="public-demo-signal-toasts" aria-live="polite" aria-label="Assessment integrity signal notifications">
            {signalToasts.map((toast) => (
              <div className={`public-demo-signal-toast ${toast.severity || "low"}`} key={toast.id}>
                <span className={`public-demo-signal-dot ${toast.severity || "low"}`}></span>
                <div>
                  <strong>{toast.label}</strong>
                  <small>{toast.category || "Integrity Signal"}{toast.isSequence ? " \u00b7 Correlated Pattern" : ""}</small>
                </div>
                <em className={`demo-toast-severity ${toast.severity || "low"}`}>
                  {(toast.severity || "low").toUpperCase()}
                </em>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default function App() {
  const normalizedPath = typeof window !== "undefined" ? window.location.pathname.replace(/\/+$/, "") : "";
  const isPublicDemoRoute = normalizedPath === "/demo";
  const publicDemoReportAttemptId = normalizedPath.startsWith("/demo/report/") ? getPublicDemoReportAttemptIdFromPath() : "";
  const isPublicDemoReportRoute = Boolean(publicDemoReportAttemptId);
  const initialReportAttemptId = (!isPublicDemoRoute && !isPublicDemoReportRoute) ? getReportAttemptIdFromLocation() : "";
  const [page, setPage] = useState(() => (initialReportAttemptId ? "report" : "dashboard"));
  const [logs, setLogs] = useState([]);
  const [cases, setCases] = useState([]);
  const [dashboardSummary, setDashboardSummary] = useState(null);
  const [reviewQueuePayload, setReviewQueuePayload] = useState(null);
  const [caseAccessError, setCaseAccessError] = useState("");
  const [selected, setSelected] = useState(() => (initialReportAttemptId ? { attempt_id: initialReportAttemptId } : null));
  const [reportAttemptId, setReportAttemptId] = useState(initialReportAttemptId);
  const [loading, setLoading] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [wsState, setWsState] = useState("paused");
  const [searchTerm, setSearchTerm] = useState("");
  const [riskFilter, setRiskFilter] = useState({ LOW: true, MEDIUM: true, HIGH: true });

  const [token, setToken] = useState(() => getStoredToken());
  const [currentUser, setCurrentUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(false);
  const [authError, setAuthError] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => getStoredSetting("riskintel_sidebar_collapsed", "false") === "true");
  const [autoRefreshInterval, setAutoRefreshInterval] = useState(() => getStoredSetting("riskintel_auto_refresh", "30"));
  const [liveUpdatesEnabled, setLiveUpdatesEnabled] = useState(() => getStoredSetting("riskintel_live_updates", "true") === "true");
  const [notificationsEnabled, setNotificationsEnabled] = useState(() => getStoredSetting("riskintel_notifications", "true") === "true");

  useEffect(() => {
    document.documentElement.dataset.theme = "dark";
    document.documentElement.classList.add("dark");
    document.documentElement.classList.remove("light");
  }, []);

  const logout = useCallback(() => {
    window.clearTimeout(reviewerRefreshTimerId);
    reviewerRefreshTimerId = null;
    setStoredToken(null);
    setToken(null);
    setCurrentUser(null);
    setCases([]);
    setReviewQueuePayload(null);
    setReportAttemptId("");
    setSelected(null);
    setPage("dashboard");
  }, []);

  const fetchLogs = useCallback(async () => {
    if (!token || !liveUpdatesEnabled) {
      setLogs([]);
      setLoading(false);
      return;
    }
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
  }, [liveUpdatesEnabled, logout, token]);

  const caseByAttemptId = useMemo(() => {
    return Object.fromEntries(cases.map((item) => [item.attempt_id, item]));
  }, [cases]);

  const reviewQueueAttemptById = useMemo(() => {
    if (!Array.isArray(reviewQueuePayload?.data)) return {};
    return Object.fromEntries(
      reviewQueuePayload.data.map((row) => [
        row.attempt_id,
        buildReportSelectionFallback({
          attempt_id: row.attempt_id,
          candidate_name: row.candidate_name,
          candidate_email: row.candidate_email,
          assessment_name: row.assessment_name,
          risk_score: row.risk_score,
          confidence: row.confidence,
          risk_level: row.risk_level,
          last_activity: row.last_activity,
          event_count: row.event_count,
          status: row.case_status,
        }),
      ]),
    );
  }, [reviewQueuePayload]);

  const resolveAttemptSelection = useCallback((target, explicitAttemptId = "") => {
    const attemptId = explicitAttemptId
      || (typeof target === "string" ? target : "")
      || target?.attempt_id
      || target?.attemptId
      || target?.item?.attempt_id
      || target?.caseRecord?.attempt_id
      || "";
    if (!attemptId) return null;
    return (
      target?.item
      || (target?.attempt_id ? target : null)
      || logs.find((item) => item.attempt_id === attemptId)
      || reviewQueueAttemptById[attemptId]
      || buildReportSelectionFallback(caseByAttemptId[attemptId], attemptId)
      || buildReportSelectionFallback(target, attemptId)
      || { attempt_id: attemptId }
    );
  }, [caseByAttemptId, logs, reviewQueueAttemptById]);

  const openReportForAttempt = useCallback((target, explicitAttemptId = "") => {
    const resolved = resolveAttemptSelection(target, explicitAttemptId);
    const attemptId = resolved?.attempt_id || explicitAttemptId || (typeof target === "string" ? target : "");
    setReportAttemptId(attemptId || "");
    setSelected(resolved);
    setPage("report");
  }, [resolveAttemptSelection]);

  const navigateToPage = useCallback((nextPage) => {
    if (nextPage !== "report") {
      setReportAttemptId("");
    }
    setPage(nextPage);
  }, []);

  const scheduleReviewerRefresh = useCallback((delayMs = 1200) => {
    if (!token) return;
    const requestedDelayMs = Math.max(0, Number(delayMs) || 0);
    const nextDueAt = Date.now() + requestedDelayMs;
    if (reviewerRefreshTimerId !== null) {
      if (reviewerRefreshDueAtMs <= nextDueAt) return;
      window.clearTimeout(reviewerRefreshTimerId);
      reviewerRefreshTimerId = null;
    }
    reviewerRefreshDueAtMs = nextDueAt;
    reviewerRefreshTimerId = window.setTimeout(() => {
      reviewerRefreshTimerId = null;
      reviewerRefreshDueAtMs = 0;
      const refreshStartedAt = typeof performance !== "undefined" ? performance.now() : Date.now();
      void Promise.allSettled([
        fetchCases(),
        fetchDashboardSummary(),
        fetchReviewQueue(),
      ]).then(() => {
        const refreshDurationMs = (typeof performance !== "undefined" ? performance.now() : Date.now()) - refreshStartedAt;
        console.info("[Reviewer Refresh Timing]", {
          durationMs: Math.round(refreshDurationMs),
          triggerDelayMs: requestedDelayMs,
        });
      });
    }, requestedDelayMs);
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
    if (!liveUpdatesEnabled) {
      setLogs([]);
    }
  }, [liveUpdatesEnabled]);

  useEffect(() => {
    setStoredSetting("riskintel_notifications", notificationsEnabled);
  }, [notificationsEnabled]);

  useEffect(() => {
    if (isPublicDemoRoute) return undefined;
    const handlePopState = () => {
      const nextAttemptId = getReportAttemptIdFromLocation();
      setReportAttemptId(nextAttemptId);
      if (nextAttemptId) {
        setPage("report");
      }
    };
    window.addEventListener("popstate", handlePopState);
    return () => {
      window.removeEventListener("popstate", handlePopState);
    };
  }, [isPublicDemoRoute]);

  useEffect(() => {
    if (isPublicDemoRoute) return;
    const activeAttemptId = page === "report" ? (selected?.attempt_id || reportAttemptId) : "";
    replaceReportAttemptIdInLocation(activeAttemptId);
  }, [isPublicDemoRoute, page, reportAttemptId, selected?.attempt_id]);

  useEffect(() => {
    if (isPublicDemoRoute) return;
    if (!reportAttemptId) return;
    if (page !== "report") return;
    if (selected?.attempt_id === reportAttemptId && selected?.candidate_name) return;
    const resolved = resolveAttemptSelection(reportAttemptId, reportAttemptId);
    if (resolved) {
      setSelected((prev) => {
        if (prev?.attempt_id === reportAttemptId && prev?.candidate_name && resolved?.candidate_name) {
          return prev;
        }
        return resolved;
      });
    }
  }, [isPublicDemoRoute, page, reportAttemptId, resolveAttemptSelection, selected?.attempt_id, selected?.candidate_name]);

  useEffect(() => {
    setStoredSetting("riskintel_sidebar_collapsed", sidebarCollapsed ? "true" : "false");
  }, [sidebarCollapsed]);

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
      if (token && liveUpdatesEnabled) void fetchLogs();
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
          if (data.type === "attempt_submitted" || data.type === "case_created_or_updated") {
            scheduleReviewerRefresh(250);
            return;
          }
          if (data.type !== "risk_update") return;

          const newLog = {
            timestamp: data.latest_event?.occurred_at || data.timeline_point?.timestamp || new Date().toISOString(),
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
          const latestEventType = String(data.latest_event?.event_type || "").toLowerCase();

          setLogs((prev) => {
            const exists = prev.some((x) => x.attempt_id === newLog.attempt_id);
            if (exists) {
              return prev.map((x) => (x.attempt_id === newLog.attempt_id ? { ...x, ...newLog } : x));
            }
            return [...prev, newLog].slice(-500);
          });

          setSelected((prev) => (prev?.attempt_id === newLog.attempt_id ? { ...prev, ...newLog } : prev || newLog));
          scheduleReviewerRefresh(
            ["exam_submitted", "assessment_submitted", "submit", "completed"].includes(latestEventType) ? 250 : 900,
          );
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
      reviewerRefreshDueAtMs = 0;
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
      if (liveUpdatesEnabled) {
        void fetchLogs();
      }
      void fetchCases();
      void fetchDashboardSummary();
      void fetchReviewQueue();
    }, seconds * 1000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [autoRefreshInterval, fetchCases, fetchDashboardSummary, fetchLogs, fetchReviewQueue, liveUpdatesEnabled, token]);

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

  if (isPublicDemoReportRoute) {
    return <PublicDemoReportPage apiBaseUrl={API_BASE_URL} attemptId={publicDemoReportAttemptId} />;
  }

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
    <div
      className="app-shell"
      style={{ "--sidebar-width": `${sidebarCollapsed ? SIDEBAR_COLLAPSED_WIDTH : SIDEBAR_EXPANDED_WIDTH}px` }}
    >
      <aside className={`sidebar${sidebarCollapsed ? " collapsed" : ""}`}>
        <div className="sidebar-top">
          <div className="sidebar-brand">
            <ProctorIQLogo className="brand-mark brand-mark-proctoriq" />
            <div className="sidebar-brand-copy">
              <h2>ProctorIQ</h2>
              <p>Assessment Integrity Console</p>
            </div>
            <button
              className="sidebar-collapse-btn"
              type="button"
              onClick={() => setSidebarCollapsed((value) => !value)}
              title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {sidebarCollapsed ? <ArrowRight size={16} /> : <ArrowLeft size={16} />}
            </button>
          </div>
        </div>

        <nav className="nav">
          <button className={page === "dashboard" ? "active" : ""} onClick={() => navigateToPage("dashboard")} title="Command Center">
            <Home size={18} />
            <span>Command Center</span>
          </button>
          <button className={page === "queue" ? "active" : ""} onClick={() => navigateToPage("queue")} title="Review Queue">
            <ListChecks size={18} />
            <span>Review Queue</span>
          </button>
          <button className={page === "report" ? "active" : ""} onClick={() => navigateToPage("report")} title="Investigation">
            <ShieldAlert size={18} />
            <span>Investigation</span>
          </button>
          <button className={page === "settings" ? "active" : ""} onClick={() => navigateToPage("settings")} title="Settings">
            <Settings size={18} />
            <span>Settings</span>
          </button>
        </nav>

        <div className="sidebar-spacer"></div>

        <div className="sidebar-footer">
          <div className={`connection-card ${wsConnected ? "online" : "offline"}`} title={wsStatusLabel}>
            <div className="connection-card-row">
              <span className="pulse"></span>
              <strong>{wsStatusLabel}</strong>
            </div>
            <small>{wsStatusDetail}</small>
          </div>

          {currentUser ? (
            <div className="sidebar-user-panel">
              <button className="sidebar-user-card" type="button" title={currentUser.full_name || currentUser.email}>
                <span className="sidebar-user-avatar">{getInitials(currentUser.full_name || currentUser.email)}</span>
                <span className="sidebar-user-copy">
                  <strong>{currentUser.full_name || currentUser.email}</strong>
                  <small>{currentUser.role}</small>
                </span>
                <ChevronDown size={16} />
              </button>
              <button className="sidebar-logout-btn" onClick={logout} type="button" title="Logout">
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
            onOpenReport={openReportForAttempt}
            setPage={navigateToPage}
            loading={loading}
            wsConnected={wsConnected}
            wsState={wsState}
            liveUpdatesEnabled={liveUpdatesEnabled}
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
              if (liveUpdatesEnabled) {
                void fetchLogs();
              }
              void fetchCases();
              void fetchReviewQueue();
            }}
            onOpenReport={openReportForAttempt}
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
            onBack={() => navigateToPage("queue")}
          />
        )}

        {page === "settings" && (
          <SettingsPage
            currentUser={currentUser}
            apiBaseUrl={API_BASE_URL}
            wsConnected={wsConnected}
            wsState={wsState}
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

function DashboardPage({ logs, cases, stats, dashboardSummary, currentUser, onOpenReport, setPage, loading, wsConnected, wsState, liveUpdatesEnabled }) {
  const [feedExpanded, setFeedExpanded] = useState(false);
  const [analyticsExpanded, setAnalyticsExpanded] = useState(false);
  const [dashboardClock, setDashboardClock] = useState(() => new Date());

  useEffect(() => {
    const timerId = window.setInterval(() => setDashboardClock(new Date()), 1000);
    return () => window.clearInterval(timerId);
  }, []);

  const recentLogs = useMemo(
    () => filterRecentItems(logs, (item) => item?.timestamp || getLastActivityAt(item)),
    [logs],
  );
  const sortedLogs = [...recentLogs].sort((a, b) => compareIso(getDashboardFeedTimestamp(b), getDashboardFeedTimestamp(a)));
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
  const fallbackFeedRows = sortedLogs.filter(isMeaningfulDashboardFeedItem).slice(0, 8);
  const fallbackReviewCases = reviewRows.filter((row) => isQueueActionableStatus(row.queueStatus)).slice(0, 5);
  const unresolvedCases = reviewRows.filter((row) => isQueueActionableStatus(row.queueStatus)).length;
  const fallbackSignalSummary = buildAggregateSignals(recentLogs);
  const fallbackLiveEventRate = getLiveEventRate(recentLogs);
  const riskTotal = Math.max(1, fallbackStats.total);
  const fallbackLastUpdated = sortedLogs[0]?.timestamp || null;
  const feedRows = liveUpdatesEnabled && Array.isArray(dashboardSummary?.recent_risk_feed) && dashboardSummary.recent_risk_feed.length
    ? dashboardSummary.recent_risk_feed
    : liveUpdatesEnabled ? fallbackFeedRows : [];
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
    { title: "Total Attempts", value: metricTotalAttempts, subtitle: "Persisted attempts", tone: "cyan", icon: <ClipboardList size={20} /> },
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

  const analyticsSummary = [
    { label: "Total Attempts", value: metricTotalAttempts },
    { label: "Medium Risk", value: metricMediumRisk },
    { label: "Live Event Rate", value: liveEventRate },
    { label: "Stream", value: wsState === "connected" ? "Connected" : wsState === "reconnecting" ? "Reconnecting" : "Paused" },
  ];

  return (
    <div className="command-center-page">
      <div className="command-header-row">
        <div className="page-title-block command-title-block">
          <span className="page-kicker">LIVE ASSESSMENT MONITORING</span>
          <h1>Command Center</h1>
          <p>Real-time assessment monitoring and risk intelligence</p>
        </div>
        <div className="command-header-meta">
          <div className="command-live-chip">
            <span className="command-live-dot"></span>
            <span>{wsConnected ? "Live monitoring" : "Stream reconnecting"}</span>
          </div>
          <div className="command-clock-card">
            <small>Local Time</small>
            <strong>{dashboardClock.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</strong>
          </div>
        </div>
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
        {liveUpdatesEnabled ? (
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
                    onClick={() => onOpenReport(item)}
                  >
                    <span className="activity-feed-time">{formatTime(item.timestamp)}</span>
                    <span className="activity-feed-avatar">{getInitials(getCandidateName(item))}</span>
                    <span className="activity-feed-copy">
                      <strong>{getCandidateName(item)}</strong>
                      <small>{getDashboardAssessmentLabel(item)}</small>
                      <p>{getDashboardStrongestReason(item)}</p>
                    </span>
                    <span className={riskClass(item.risk || item.risk_level)}>{item.risk || item.risk_level || "LOW"}</span>
                    <span className="activity-feed-relative">{formatRelativeTime(item.timestamp || item.last_activity)}</span>
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
        ) : null}

        <div className={`enterprise-panel${!liveUpdatesEnabled ? " command-primary-full" : ""}`}>
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
                <span>Candidate</span>
                <span>Risk</span>
                <span>Score</span>
                <span>Action</span>
              </div>
              {visibleReviewCases.map((row, index) => (
                <div className="review-priority-row" key={row.attemptId || row.attempt_id}>
                  <span className={`priority-index tone-${riskTone(row.risk || row.risk_level)}`}>{row.priority || index + 1}</span>
                  <span className="review-priority-candidate">
                    <span className="review-priority-avatar activity-feed-avatar">{getInitials(row.candidateName || row.candidate_name || "Unknown Candidate")}</span>
                    <span className="review-priority-candidate-copy activity-feed-copy">
                      <strong>{row.candidateName || row.candidate_name || "Unknown Candidate"}</strong>
                      <small title={row.attemptId || row.attempt_id}>{getDashboardAssessmentLabel(row)}</small>
                      <p>{getDashboardReviewStatus(row)}</p>
                    </span>
                  </span>
                  <span className="review-priority-risk-stack">
                    <span className={riskClass(row.risk || row.risk_level)}>{row.risk || row.risk_level || "LOW"}</span>
                  </span>
                  <span>{formatNumber(row.score)}</span>
                  <button
                    className="action-link-button"
                    onClick={() => onOpenReport(row)}
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

      <section className={`command-analytics-shell enterprise-panel${analyticsExpanded ? " expanded" : ""}`}>
        <div className="command-analytics-header">
          <div>
            <h3>Analytics &amp; System Overview</h3>
            <p>Risk distribution, evidence signals, system health, and detailed insights</p>
          </div>
          <button
            className="panel-header-action command-analytics-toggle"
            onClick={() => setAnalyticsExpanded((value) => !value)}
            type="button"
          >
            {analyticsExpanded ? "Collapse" : "Expand"}
          </button>
        </div>
        <div className="command-analytics-summary">
          {analyticsSummary.map((item) => (
            <div className="command-analytics-pill" key={item.label}>
              <span>{item.label}</span>
              <strong>{item.value}</strong>
            </div>
          ))}
        </div>
        {analyticsExpanded ? (
          <div className="command-bottom-grid">
            <div className="command-analytics-section">
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
            <div className="command-analytics-section">
              <PanelHeader title="Recent Evidence Signals" subtitle="Summary of recent suspicious activity" />
              <div className="signals-grid">
                <SignalStatCard title="Clipboard Copy/Paste" value={signalSummary.clipboard} tone="high" icon={<Copy size={18} />} />
                <SignalStatCard title="Tab Switch Events" value={signalSummary.tabSwitches} tone="medium" icon={<MonitorOff size={18} />} />
                <SignalStatCard title="Idle Time Spikes" value={signalSummary.idleSpikes} tone="medium" icon={<Clock3 size={18} />} />
                <SignalStatCard title="Rapid Answer Bursts" value={signalSummary.rapidBursts} tone="medium" icon={<Zap size={18} />} />
                <SignalStatCard title="Focus/Blur Events" value={signalSummary.focusBlur} tone="violet" icon={<Eye size={18} />} />
              </div>
            </div>

            <div className="command-analytics-section">
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
          </div>
        ) : null}
      </section>
    </div>
  );
}

function QueuePage({ logs, reviewQueuePayload, caseByAttemptId, caseAccessError, currentUser, onRefresh, onOpenReport }) {
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
                      onClick={() => onOpenReport(row)}
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
    let disposed = false;
    let reportRetryCount = 0;

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
        if (disposed) return;
        setLiveRisk(liveJson.data || null);
        setEvents(eventsJson.data || []);
        setRiskHistory(historyJson.data || []);
        let shouldRetryReport = false;
        setReportData((prev) => {
          if (reportJson?.status !== "success") {
            return prev;
          }
          const nextData = reportJson.data || null;
          const prevEvidence = Array.isArray(prev?.evidence_items) ? prev.evidence_items : [];
          const nextEvidence = Array.isArray(nextData?.evidence_items) ? nextData.evidence_items : [];
          const prevOverview = prev?.violation_overview_counts ? Object.keys(prev.violation_overview_counts).length : 0;
          const nextOverview = nextData?.violation_overview_counts ? Object.keys(nextData.violation_overview_counts).length : 0;
          shouldRetryReport = Boolean(
            nextData
            && nextData.attempt_status !== "ONGOING"
            && !nextData.final_risk_assessment
            && nextEvidence.length === 0
            && nextOverview === 0,
          );
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
        if (shouldRetryReport && reportRetryCount < 3) {
          reportRetryCount += 1;
          window.setTimeout(() => {
            if (!disposed) {
              void fetchReportData();
            }
          }, 1200);
        }
      } catch (err) {
        console.error("Failed to fetch report data:", err);
      } finally {
        if (!disposed) {
          setReportLoading(false);
        }
      }
    }
    void fetchReportData();
    return () => {
      disposed = true;
    };
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
  const provenanceAnalysis = reportData?.provenance_analysis || null;
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

        <InvestigationInsightsCard insights={investigationInsights} riskLevel={displayRisk} />

        <ProvenanceIntelligenceCard provenanceAnalysis={provenanceAnalysis} />

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
              label="Live Risk Feed"
              help="Enable or pause live feed fetching, websocket updates, and candidate activity rendering."
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

function DemoInfoRail({ telemetryStatus, candidateId, apiBaseUrl, selectedAssessment, totalQuestions, telemetryError }) {
  return (
    <>
      <section className="public-demo-card compact">
        <div className="public-demo-card-head compact">
          <h3>Telemetry Notice</h3>
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
          <li>No answer text stored as telemetry</li>
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
          <div className="public-demo-summary-item">
            <span>Sections</span>
            <strong>{selectedAssessment.sections.length} sections / {totalQuestions} questions</strong>
          </div>
        </div>
        {telemetryError ? <div className="workflow-message workflow-message-warning">{telemetryError}</div> : null}
      </section>
    </>
  );
}

function CandidateViolationSummary({ summary }) {
  const items = [
    ["Tab Switches", summary?.tab_switches],
    ["Blur Events", summary?.blur_events],
    ["Clipboard Events", summary?.clipboard_events],
    ["Idle Spikes", summary?.idle_spikes],
    ["Rapid Answers", summary?.rapid_answer_bursts],
  ].filter(([, value]) => value !== undefined && value !== null);

  return (
    <div className="public-demo-summary-grid">
      {items.map(([label, value]) => (
        <div className="public-demo-summary-item" key={label}>
          <span>{label}</span>
          <strong>{String(value)}</strong>
        </div>
      ))}
    </div>
  );
}

function InvestigationInsightsCard({ insights, riskLevel, title = "Most Suspicious Behaviors Observed" }) {
  return (
    <section className="investigation-card investigation-insights-card">
      <div className="report-card-head accent">
        <h3>{title}</h3>
      </div>
      <div className="investigation-insights-list">
        {(insights || []).map((item) => (
          <article className="investigation-insight-item" key={item}>
            <span className={`insight-dot ${String(riskLevel || "LOW").toLowerCase()}`}></span>
            <p>{item}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function ProvenanceIntelligenceCard({
  provenanceAnalysis,
  waitingMessage = "Waiting for post-submission answer provenance analysis.",
}) {
  return (
    <section className="investigation-card provenance-intelligence-card">
      <div className="report-card-head accent">
        <h3>Answer Provenance Intelligence</h3>
      </div>
      {provenanceAnalysis ? (
        <div className="provenance-intelligence-body">
          <div className="provenance-summary-grid">
            <article className="provenance-summary-item">
              <span>External Similarity Likelihood</span>
              <strong className={riskClass(provenanceAnalysis.external_similarity_likelihood || "LOW")}>
                {provenanceAnalysis.external_similarity_likelihood || "LOW"}
              </strong>
            </article>
            <article className="provenance-summary-item">
              <span>Confidence Score</span>
              <strong>{formatNumber(provenanceAnalysis.confidence_score, 2)}</strong>
            </article>
            <article className="provenance-summary-item">
              <span>Possible Reference Matches</span>
              <strong>{(provenanceAnalysis.possible_reference_matches || []).length}</strong>
            </article>
          </div>
          <div className="provenance-summary-copy">
            <p>{provenanceAnalysis.summary || "No meaningful reference overlap was detected in submitted answers."}</p>
            {(provenanceAnalysis.evidence_title && (provenanceAnalysis.external_similarity_likelihood === "MEDIUM" || provenanceAnalysis.external_similarity_likelihood === "HIGH")) ? (
              <div className={`provenance-evidence-banner tone-${String(provenanceAnalysis.external_similarity_likelihood || "LOW").toLowerCase()}`}>
                <strong>{provenanceAnalysis.evidence_title}</strong>
                <span>Potential external similarity source overlap should be reviewed alongside behavioral context.</span>
              </div>
            ) : null}
            {provenanceAnalysis.limitations_note ? (
              <p className="provenance-limitations-note">{provenanceAnalysis.limitations_note}</p>
            ) : null}
            {provenanceAnalysis.web_retrieval_disclaimer ? (
              <p className="provenance-limitations-note">{provenanceAnalysis.web_retrieval_disclaimer}</p>
            ) : null}
          </div>
          {(provenanceAnalysis.behavioral_correlation || []).length ? (
            <div className="provenance-correlation-list">
              <span>Behavioral Correlation</span>
              <ul>
                {provenanceAnalysis.behavioral_correlation.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="provenance-match-list">
            {(provenanceAnalysis.possible_reference_matches || []).length ? (
              provenanceAnalysis.possible_reference_matches.map((match, index) => (
                <details className="provenance-match-card" key={`${match.source_title}-${match.question_id || "candidate"}-${index}`}>
                  <summary>
                    <div>
                      <strong>{match.source_title || "Possible source match"}</strong>
                      <small>
                        {match.source_type || "Reference"}
                        {match.source_domain ? ` • ${match.source_domain}` : ""}
                        {match.question_title ? ` • ${match.question_title}` : ""}
                      </small>
                    </div>
                    <div className="provenance-match-meta">
                      <span>{match.similarity_percent}% similarity</span>
                      <span className={riskClass(match.likelihood || "LOW")}>{match.likelihood || "LOW"}</span>
                    </div>
                  </summary>
                  <div className="provenance-match-body">
                    <div className="provenance-preview-grid">
                      <article>
                        <span>Candidate Excerpt</span>
                        <p>{match.candidate_excerpt || "No candidate excerpt available."}</p>
                      </article>
                      <article>
                        <span>Reference Excerpt</span>
                        <p>{match.reference_excerpt || "No reference excerpt available."}</p>
                      </article>
                    </div>
                    <div className="provenance-match-footer">
                      <div>
                        <span>Source</span>
                        <strong>{match.source_type || "Reference"}</strong>
                      </div>
                      {match.retrieval_source ? (
                        <div>
                          <span>Retrieval Source</span>
                          <strong>{match.retrieval_source}</strong>
                        </div>
                      ) : null}
                      <div>
                        <span>Domain</span>
                        <strong>{match.source_domain || "Controlled corpus"}</strong>
                      </div>
                      {match.retrieval_confidence != null ? (
                        <div>
                          <span>Retrieval Confidence</span>
                          <strong>{formatNumber(match.retrieval_confidence, 2)}</strong>
                        </div>
                      ) : null}
                      {match.retrieval_timestamp ? (
                        <div>
                          <span>Retrieved</span>
                          <strong>{formatDateTime(match.retrieval_timestamp)}</strong>
                        </div>
                      ) : null}
                      <div>
                        <span>Match Reason</span>
                        <strong>{match.match_reason || match.confidence_label || "Observed similarity pattern"}</strong>
                      </div>
                      {match.source_url ? (
                        <a href={match.source_url} rel="noreferrer" target="_blank">
                          Reference Link
                        </a>
                      ) : null}
                    </div>
                    <div className="provenance-match-metrics">
                      <span>Token overlap {formatNumber(match.token_overlap, 2)}</span>
                      <span>Phrase overlap {formatNumber(match.phrase_overlap, 2)}</span>
                      <span>Chunk similarity {formatNumber(match.chunk_similarity, 2)}</span>
                      <span>{match.confidence_label || "Low confidence"}</span>
                    </div>
                    {(match.behavioral_correlation || []).length ? (
                      <ul className="provenance-correlation-inline">
                        {match.behavioral_correlation.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                </details>
              ))
            ) : (
              <div className="empty-state-inline">No strong reference overlaps were detected in submitted answers.</div>
            )}
          </div>
        </div>
      ) : (
        <div className="provenance-empty-state">
          <p>{waitingMessage}</p>
        </div>
      )}
    </section>
  );
}

function PublicDemoReportPage({ apiBaseUrl, attemptId }) {
  const [reportState, setReportState] = useState({ loading: true, ready: false, data: null, error: "" });
  const [isGeneratingPdf, setIsGeneratingPdf] = useState(false);
  const [pdfError, setPdfError] = useState("");
  const exportTargetRef = useRef(null);
  const exportPdfRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    let timerId = null;

    async function loadReport() {
      const startedAt = Date.now();
      let transientFailureCount = 0;
      while (!cancelled && Date.now() - startedAt < 18000) {
        const requestUrl = `${apiBaseUrl.replace(/\/$/, "")}/v1/demo/attempts/${encodeURIComponent(attemptId)}/report`;
        console.info("[Candidate Report]", {
          event: "candidate_report_fetch_started",
          attemptId,
          url: requestUrl,
        });
        const controller = new AbortController();
        const timeoutId = window.setTimeout(() => controller.abort(), 20000);
        let response;
        let payload = null;
        try {
          response = await fetch(requestUrl, {
            method: "GET",
            headers: { Accept: "application/json" },
            signal: controller.signal,
          });
          const text = await response.text();
          payload = text ? JSON.parse(text) : null;
        } catch (error) {
          console.error("[Candidate Report]", {
            event: "candidate_report_fetch_failed",
            attemptId,
            message: error instanceof Error ? error.message : "Unknown fetch error",
          });
          window.clearTimeout(timeoutId);
          transientFailureCount += 1;
          if (cancelled) return;
          if (transientFailureCount >= 3) {
            setReportState({
              loading: false,
              ready: false,
              data: null,
              error: "Backend is unavailable or waking up. Please retry shortly.",
            });
            return;
          }
          await new Promise((resolve) => {
            timerId = window.setTimeout(resolve, 1100 * transientFailureCount);
          });
          continue;
        } finally {
          window.clearTimeout(timeoutId);
        }

        if (cancelled) return;

        const hasReadyReport = Boolean(
          response?.ok
          && (
            (payload?.ready === true && payload?.data)
            || (payload?.status === "success" && payload?.data)
          )
        );
        if (hasReadyReport) {
          console.info("[Candidate Report]", {
            event: "candidate_report_fetch_success",
            attemptId,
            ready: payload?.ready,
            status: payload?.status,
          });
          setReportState({ loading: false, ready: true, data: payload.data, error: "" });
          return;
        }
        transientFailureCount = 0;

        if (response?.status === 404) {
          console.error("[Candidate Report]", {
            event: "candidate_report_fetch_failed",
            attemptId,
            message: "Report not found",
          });
          setReportState({ loading: false, ready: false, data: null, error: "This demo report is not available." });
          return;
        }

        if (response?.ok && payload?.ready === false && payload?.status === "processing") {
          setReportState((prev) => ({ ...prev, loading: true, ready: false, error: "" }));
        } else if (!response?.ok) {
          console.error("[Candidate Report]", {
            event: "candidate_report_fetch_failed",
            attemptId,
            status: response?.status || 0,
            message: payload?.detail || payload?.message || "Request failed",
          });
          setReportState({
            loading: false,
            ready: false,
            data: null,
            error: payload?.detail || payload?.message || "Backend is unavailable or waking up. Please retry shortly.",
          });
          return;
        }

        await new Promise((resolve) => {
          timerId = window.setTimeout(resolve, 1200);
        });
      }

      if (!cancelled) {
        setReportState({
          loading: false,
          ready: false,
          data: null,
          error: "Generating your report is taking longer than expected. Please refresh and try again shortly.",
        });
      }
    }

    void loadReport();
    return () => {
      cancelled = true;
      if (timerId) window.clearTimeout(timerId);
    };
  }, [apiBaseUrl, attemptId]);

  useEffect(() => {
    if (!reportState.ready || !reportState.data) return;
    console.info("[Candidate Report]", {
      event: "candidate_report_rendered",
      attemptId,
      riskLevel: reportState.data.risk_level || "LOW",
    });
  }, [attemptId, reportState.data, reportState.ready]);

  const report = reportState.data;
  const provenance = report?.answer_provenance || null;
  const riskLevel = report?.risk_level || "LOW";
  const status = report?.attempt_status || "SUBMITTED";
  const overviewItems = buildViolationOverviewFromCounts(report?.violation_summary || {});
  const candidateInsights = (report?.most_suspicious_behaviors || []).length
    ? report.most_suspicious_behaviors
    : [report?.behavioral_summary || report?.strongest_reason || "Behavioral signals were analyzed after submission."];
  const candidateEvidenceRows = Array.isArray(report?.evidence_items) ? report.evidence_items : [];
  const candidateTechnicalDetails = [
    { label: "Assessment", value: report?.assessment_name || "Assessment" },
    { label: "Attempt ID", value: report?.attempt_id || attemptId || "N/A" },
    { label: "Started", value: formatDateTime(report?.started_at) },
    { label: "Submitted", value: formatDateTime(report?.submitted_at) },
    { label: "Latest Activity", value: formatDateTime(report?.latest_event_at) },
    { label: "Event Count", value: String(report?.event_count ?? 0) },
  ];
  const candidateMetadata = [
    { label: "Assessment", value: report?.assessment_name || "Assessment" },
    { label: "Attempt ID", value: report?.attempt_id || attemptId || "N/A" },
    { label: "Submitted", value: formatDateTime(report?.submitted_at) },
    { label: "Status", value: status },
  ];
  const executiveSummaryPoints = [
    report?.summary_text,
    report?.strongest_reason ? `Strongest reason: ${report.strongest_reason}` : "",
    report?.behavioral_summary && report?.behavioral_summary !== report?.summary_text ? report.behavioral_summary : "",
  ].filter(Boolean);
  const scorebandItems = [
    { label: "Risk Level", value: riskLevel, tone: riskLevel.toLowerCase() },
    { label: "Risk Score", value: `${formatNumber(report?.risk_score, 2)} / 1.00`, tone: "neutral" },
    { label: "Confidence", value: `${formatNumber(report?.confidence, 2)} • ${getConfidenceLabel(report?.confidence)}`, tone: "neutral" },
  ];
  const handleDownloadPdf = useCallback(async () => {
    if (!reportState.ready || !exportPdfRef.current || isGeneratingPdf) return;

    setIsGeneratingPdf(true);
    setPdfError("");
    console.info("[Candidate Report]", {
      event: "pdf_export_started",
      attemptId,
    });

    try {
      const exportNode = exportPdfRef.current;
      console.info("[Candidate Report]", {
        event: "pdf_export_target_found",
        attemptId,
        found: Boolean(exportNode),
      });
      await new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
      await new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
      await new Promise((resolve) => window.setTimeout(resolve, 80));
      const targetRect = exportNode.getBoundingClientRect();
      const targetWidth = Math.round(exportNode.scrollWidth || targetRect.width || 0);
      const targetHeight = Math.round(exportNode.scrollHeight || targetRect.height || 0);
      console.info("[Candidate Report]", {
        event: "pdf_export_target_size",
        attemptId,
        width: targetWidth,
        height: targetHeight,
      });
      if (!targetWidth || !targetHeight) {
        const message = "PDF export could not render because the report surface is not ready yet. Please retry in a moment.";
        setPdfError(message);
        console.error("[Candidate Report]", {
          event: "candidate_report_pdf_failed",
          attemptId,
          message,
        });
        return;
      }
      const canvas = await html2canvas(exportNode, {
        backgroundColor: "#ffffff",
        scale: Math.min(window.devicePixelRatio || 1, 2),
        useCORS: true,
        logging: false,
        scrollX: 0,
        scrollY: 0,
        windowWidth: targetWidth,
        windowHeight: targetHeight,
        ignoreElements: (element) => element?.dataset?.pdfExclude === "true",
      });
      console.info("[Candidate Report]", {
        event: "pdf_export_canvas_created",
        attemptId,
        width: canvas.width,
        height: canvas.height,
      });
      if (!canvas.width || !canvas.height) {
        const message = "PDF export produced an empty canvas. Please retry the download.";
        setPdfError(message);
        console.error("[Candidate Report]", {
          event: "candidate_report_pdf_failed",
          attemptId,
          message,
        });
        return;
      }

      const pdf = new jsPDF({
        orientation: "p",
        unit: "pt",
        format: "a4",
        compress: true,
      });

      const pageWidth = pdf.internal.pageSize.getWidth();
      const pageHeight = pdf.internal.pageSize.getHeight();
      const margin = 28;
      const usableWidth = pageWidth - margin * 2;
      const usableHeight = pageHeight - margin * 2;
      const scaleRatio = usableWidth / canvas.width;
      const pagePixelHeight = Math.max(1, Math.floor(usableHeight / scaleRatio));

      let pageIndex = 0;
      for (let offsetY = 0; offsetY < canvas.height; offsetY += pagePixelHeight) {
        const sliceHeight = Math.min(pagePixelHeight, canvas.height - offsetY);
        const pageCanvas = document.createElement("canvas");
        pageCanvas.width = canvas.width;
        pageCanvas.height = sliceHeight;
        const pageContext = pageCanvas.getContext("2d");
        if (!pageContext) continue;
        pageContext.drawImage(
          canvas,
          0,
          offsetY,
          canvas.width,
          sliceHeight,
          0,
          0,
          canvas.width,
          sliceHeight,
        );

        if (pageIndex > 0) pdf.addPage();

        pdf.addImage(
          pageCanvas.toDataURL("image/png"),
          "PNG",
          margin,
          margin,
          usableWidth,
          sliceHeight * scaleRatio,
          undefined,
          "FAST",
        );
        pageIndex += 1;
      }

      pdf.save(`ProctorIQ_Candidate_Report_${attemptId}.pdf`);
      console.info("[Candidate Report]", {
        event: "pdf_export_saved",
        attemptId,
        pages: pageIndex,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown PDF error";
      setPdfError("Unable to generate the PDF right now. Please retry.");
      console.error("[Candidate Report]", {
        event: "candidate_report_pdf_failed",
        attemptId,
        message,
      });
    } finally {
      setIsGeneratingPdf(false);
    }
  }, [attemptId, isGeneratingPdf, reportState.ready]);

  return (
    <div className="report-scroll-container public-candidate-report-shell">
      <div className="report-toolbar public-candidate-report-toolbar" data-pdf-exclude="true">
        <div className="public-candidate-report-brand">
          <ProctorIQLogo className="brand-mark brand-mark-proctoriq" />
          <div>
            <span className="report-section-kicker">ProctorIQ</span>
            <strong>Candidate-safe investigation report</strong>
          </div>
        </div>
        {reportState.ready ? (
          <div className="report-toolbar-actions">
            <button
              className="panel-header-action"
              disabled={isGeneratingPdf}
              onClick={() => void handleDownloadPdf()}
              type="button"
            >
              <FileDown size={16} />
              {isGeneratingPdf ? "Generating PDF..." : "Download PDF Report"}
            </button>
            <a className="workflow-action-btn primary" href="/demo">Start New Demo</a>
            <a className="workflow-action-btn" href="/">Open Reviewer Console</a>
          </div>
        ) : null}
      </div>
      {pdfError ? (
        <section className="investigation-card provenance-empty-state" data-pdf-exclude="true">
          <div className="report-card-head accent">
            <h3>PDF export unavailable</h3>
          </div>
          <p>{pdfError}</p>
        </section>
      ) : null}

      <section className="investigation-page investigation-report-shell public-candidate-report-page" ref={exportTargetRef}>
        {!reportState.ready ? (
          <section className="investigation-card provenance-empty-state">
            <div className="report-card-head accent">
              <h3>{reportState.error ? "Report unavailable" : "Generating your report..."}</h3>
            </div>
            <p>{reportState.error || "Behavioral analysis, evidence sections, and answer provenance details are still being prepared for this attempt."}</p>
            {reportState.error ? (
              <div className="public-demo-actions">
                <button className="workflow-action-btn primary" onClick={() => window.location.reload()} type="button">
                  <RefreshCw size={16} />
                  Retry Report Load
                </button>
                <a className="workflow-action-btn" href="/demo">Start New Demo</a>
              </div>
            ) : null}
          </section>
        ) : (
          <>
            <InvestigationHeader
              candidateName={report?.candidate_name || "Unknown Candidate"}
              candidateEmail={report?.candidate_email || "No email available"}
              initials={getInitials(report?.candidate_name || "Unknown Candidate")}
              risk={riskLevel}
              status={status}
              finalDecision={report?.final_decision || "Integrity analysis summary"}
              summaryText={report?.summary_text || report?.behavioral_summary || "Behavioral signals were analyzed after submission."}
              strongestReason={report?.strongest_reason || "Behavioral signals were reviewed in context after submission."}
              explanation={report?.why_score_text || report?.behavioral_summary || "Potential external similarity and behavioral signals should be reviewed in context."}
              score={formatNumber(report?.risk_score, 2)}
              confidence={formatNumber(report?.confidence, 2)}
              confidenceLabel={getConfidenceLabel(report?.confidence)}
              metadataItems={candidateMetadata}
            />

            <section className="investigation-card candidate-report-executive-card">
              <div className="report-card-head accent">
                <h3>Executive Integrity Summary</h3>
              </div>
              <div className="candidate-report-executive-copy">
                <p>{report?.summary_text || report?.behavioral_summary || "Behavioral signals were analyzed after submission."}</p>
                <div className="candidate-report-summary-list">
                  {executiveSummaryPoints.map((item) => (
                    <article className="candidate-report-summary-item" key={item}>
                      <span className={`insight-dot ${String(riskLevel || "LOW").toLowerCase()}`}></span>
                      <p>{item}</p>
                    </article>
                  ))}
                </div>
              </div>
            </section>

            <section className="investigation-card candidate-report-scoreband-card">
              <div className="report-card-head">
                <h3>Risk Score & Confidence</h3>
              </div>
              <div className="candidate-report-scoreband">
                {scorebandItems.map((item) => (
                  <article className={`candidate-report-scoreband-item tone-${item.tone}`} key={item.label}>
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                  </article>
                ))}
              </div>
            </section>

            <ViolationOverview items={overviewItems} />

            <InvestigationInsightsCard insights={candidateInsights} riskLevel={riskLevel} />

            <ProvenanceIntelligenceCard provenanceAnalysis={provenance} waitingMessage="Answer provenance analysis was not available for this attempt." />

            <div className="report-main-row candidate-report-main-grid">
              <EvidenceTable rows={candidateEvidenceRows} loading={reportState.loading} />
              <div className="candidate-report-side-stack">
                <TechnicalDetails items={candidateTechnicalDetails} />
                <section className="investigation-card">
                  <div className="report-card-head">
                    <h3>Privacy-safe explanation</h3>
                  </div>
                  <div className="public-demo-copy-block">
                    <p>{report?.privacy_note || "This summary uses metadata-based integrity analysis only."}</p>
                  </div>
                </section>
              </div>
            </div>
          </>
        )}
      </section>

      {reportState.ready ? (
        <section className="candidate-report-export-root pdf-export-mode" ref={exportPdfRef} aria-hidden="true">
          <div className="pdf-report-shell">
            <header className="pdf-report-header">
              <div className="pdf-report-brand-row">
                <div className="pdf-report-brand-copy">
                  <span className="pdf-report-kicker">ProctorIQ</span>
                  <h1>Candidate Assessment Integrity Report</h1>
                  <p>
                    Candidate-safe summary of telemetry, integrity analysis, evidence grouping, and answer provenance findings.
                  </p>
                </div>
                <div className="pdf-report-badges">
                  <span className={`pdf-report-badge risk ${String(riskLevel || "LOW").toLowerCase()}`}>{riskLevel} Risk</span>
                  <span className="pdf-report-badge status">{status}</span>
                </div>
              </div>
              <div className="pdf-report-meta-grid">
                {candidateMetadata.map((item) => (
                  <div className="pdf-report-meta-item" key={item.label}>
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                  </div>
                ))}
                <div className="pdf-report-meta-item">
                  <span>Candidate</span>
                  <strong>{report?.candidate_name || "Unknown Candidate"}</strong>
                </div>
                <div className="pdf-report-meta-item">
                  <span>Candidate Email</span>
                  <strong>{report?.candidate_email || "No email available"}</strong>
                </div>
              </div>
            </header>

            <section className="pdf-report-section">
              <div className="pdf-report-section-head">
                <h2>Executive Integrity Summary</h2>
              </div>
              <p className="pdf-report-lead">{report?.summary_text || report?.behavioral_summary || "Behavioral signals were analyzed after submission."}</p>
              <div className="pdf-report-bullet-list">
                {executiveSummaryPoints.map((item) => (
                  <div className="pdf-report-bullet" key={item}>
                    <span className={`pdf-report-bullet-dot ${String(riskLevel || "LOW").toLowerCase()}`}></span>
                    <p>{item}</p>
                  </div>
                ))}
              </div>
            </section>

            <section className="pdf-report-section">
              <div className="pdf-report-section-head">
                <h2>Risk Score & Confidence</h2>
              </div>
              <div className="pdf-report-score-grid">
                {scorebandItems.map((item) => (
                  <article className={`pdf-report-score-card tone-${item.tone}`} key={item.label}>
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                  </article>
                ))}
              </div>
            </section>

            <section className="pdf-report-section">
              <div className="pdf-report-section-head">
                <h2>Violation Overview</h2>
              </div>
              <div className="pdf-report-overview-grid">
                {overviewItems.map((item) => (
                  <article className={`pdf-report-overview-card ${String(item.severity || "low").toLowerCase()}`} key={item.title}>
                    <span>{item.title}</span>
                    <strong>{item.value}</strong>
                    <small>{item.severityLabel || severityLabel(item.severity)}</small>
                  </article>
                ))}
              </div>
            </section>

            <section className="pdf-report-section">
              <div className="pdf-report-section-head">
                <h2>Most Suspicious Behaviors</h2>
              </div>
              <div className="pdf-report-bullet-list">
                {candidateInsights.map((item) => (
                  <div className="pdf-report-bullet" key={item}>
                    <span className={`pdf-report-bullet-dot ${String(riskLevel || "LOW").toLowerCase()}`}></span>
                    <p>{item}</p>
                  </div>
                ))}
              </div>
            </section>

            <section className="pdf-report-section">
              <div className="pdf-report-section-head">
                <h2>Answer Provenance Intelligence</h2>
              </div>
              {provenance ? (
                <div className="pdf-report-provenance">
                  <div className="pdf-report-score-grid">
                    <article className={`pdf-report-score-card tone-${String(provenance.external_similarity_likelihood || "LOW").toLowerCase()}`}>
                      <span>Potential Reference Overlap</span>
                      <strong>{provenance.external_similarity_likelihood || "LOW"}</strong>
                    </article>
                    <article className="pdf-report-score-card tone-neutral">
                      <span>Confidence</span>
                      <strong>{formatNumber(provenance.confidence_score, 2)}</strong>
                    </article>
                    <article className="pdf-report-score-card tone-neutral">
                      <span>Possible Matches</span>
                      <strong>{(provenance.possible_reference_matches || []).length}</strong>
                    </article>
                  </div>
                  <p className="pdf-report-lead">{provenance.summary || "No meaningful reference overlap was detected in submitted answers."}</p>
                  {(provenance.possible_reference_matches || []).map((match, index) => (
                    <article className="pdf-report-match-card" key={`${match.source_title || "match"}-${index}`}>
                      <div className="pdf-report-match-head">
                        <div>
                          <strong>{match.source_title || "Possible source match"}</strong>
                          <small>
                            {match.source_type || "Reference"}
                            {match.source_domain ? ` • ${match.source_domain}` : ""}
                            {match.question_title ? ` • ${match.question_title}` : ""}
                          </small>
                        </div>
                        <span className={`pdf-report-badge risk ${String(match.likelihood || "LOW").toLowerCase()}`}>{match.similarity_percent}% similarity</span>
                      </div>
                      <div className="pdf-report-detail-grid">
                        <div>
                          <span>Possible source match</span>
                          <strong>{match.match_reason || match.confidence_label || "Observed similarity pattern"}</strong>
                        </div>
                        {match.retrieval_source ? (
                          <div>
                            <span>Retrieval source</span>
                            <strong>{match.retrieval_source}</strong>
                          </div>
                        ) : null}
                        {match.retrieval_confidence != null ? (
                          <div>
                            <span>Retrieval confidence</span>
                            <strong>{formatNumber(match.retrieval_confidence, 2)}</strong>
                          </div>
                        ) : null}
                        {match.retrieval_timestamp ? (
                          <div>
                            <span>Retrieved</span>
                            <strong>{formatDateTime(match.retrieval_timestamp)}</strong>
                          </div>
                        ) : null}
                        {match.source_url ? (
                          <div className="span-2">
                            <span>Source URL</span>
                            <strong>{match.source_url}</strong>
                          </div>
                        ) : null}
                      </div>
                      <div className="pdf-report-preview-grid">
                        <article>
                          <span>Candidate excerpt</span>
                          <p>{match.candidate_excerpt || "No candidate excerpt available."}</p>
                        </article>
                        <article>
                          <span>Reference excerpt</span>
                          <p>{match.reference_excerpt || "No reference excerpt available."}</p>
                        </article>
                      </div>
                    </article>
                  ))}
                  {provenance.limitations_note ? <p className="pdf-report-note">{provenance.limitations_note}</p> : null}
                  {provenance.web_retrieval_disclaimer ? <p className="pdf-report-note">{provenance.web_retrieval_disclaimer}</p> : null}
                </div>
              ) : (
                <p className="pdf-report-lead">Answer provenance analysis was not available for this attempt.</p>
              )}
            </section>

            <section className="pdf-report-section">
              <div className="pdf-report-section-head">
                <h2>Evidence & Violations</h2>
              </div>
              <table className="pdf-report-table">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Severity</th>
                    <th>Count</th>
                    <th>Impact</th>
                    <th>Details</th>
                  </tr>
                </thead>
                <tbody>
                  {candidateEvidenceRows.map((row, index) => (
                    <tr key={`${row.title || row.signalType || "evidence"}-${index}`}>
                      <td>{row.title || row.signalType || "Evidence item"}</td>
                      <td>{row.severity || "LOW"}</td>
                      <td>{row.countDisplay || row.count || "0"}</td>
                      <td>{row.impactLabel || "Context required"}</td>
                      <td>{row.subtitle || row.details || row.explanation || "No additional details available."}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            <section className="pdf-report-section pdf-report-grid-two">
              <article className="pdf-report-panel">
                <div className="pdf-report-section-head">
                  <h2>Technical Details</h2>
                </div>
                <div className="pdf-report-detail-grid">
                  {candidateTechnicalDetails.map((item) => (
                    <div key={item.label}>
                      <span>{item.label}</span>
                      <strong>{item.value}</strong>
                    </div>
                  ))}
                </div>
              </article>
              <article className="pdf-report-panel">
                <div className="pdf-report-section-head">
                  <h2>Privacy-safe Explanation</h2>
                </div>
                <p className="pdf-report-lead">
                  {report?.privacy_note || "This summary uses metadata-based integrity analysis only."}
                </p>
              </article>
            </section>
          </div>
        </section>
      ) : null}
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

