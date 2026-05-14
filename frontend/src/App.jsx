import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Clock3,
  Copy,
  Eye,
  Gauge,
  Home,
  ListChecks,
  Mail,
  MonitorOff,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Timer,
  User,
  Zap,
  CheckCircle2,
  CircleDot,
} from "lucide-react";

import "./App.css";

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
  if (!value) return "—";
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
  if (!value) return "—";
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
  if (seconds === null || seconds === undefined) return "—";
  const total = Number(seconds);
  if (!Number.isFinite(total) || total <= 0) return "—";

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
      } catch (err) {
        console.error("WS parse error:", err);
      }
    };

    return () => {
      window.clearTimeout(timeoutId);
      ws.close();
    };
  }, [fetchCases, fetchLogs, token]);

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
          <h3>Loading session…</h3>
          <p>Verifying your access token.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div>
          <div className="brand-mark"><ShieldAlert size={24} /></div>
          <h2>RiskIntel</h2>
          <p>Assessment Integrity Console</p>
          {currentUser && (
            <div className="user-pill">
              <div>
                <strong>{currentUser.full_name || currentUser.email}</strong>
                <small>{currentUser.email} • {currentUser.role}</small>
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
        </nav>

        <button className="refresh-btn" onClick={fetchLogs}>
          <RefreshCw size={16} /> Refresh Data
        </button>
      </aside>

      <main className="main">
        {page === "dashboard" && (
          <DashboardPage
            stats={stats}
            filteredLogs={filteredLogs}
            setSelected={setSelected}
            setPage={setPage}
            loading={loading}
            wsConnected={wsConnected}
          />
        )}

        {page === "queue" && (
          <QueuePage
            filteredLogs={filteredLogs}
            caseByAttemptId={caseByAttemptId}
            caseAccessError={caseAccessError}
            searchTerm={searchTerm}
            setSearchTerm={setSearchTerm}
            riskFilter={riskFilter}
            setRiskFilter={setRiskFilter}
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
          />
        )}
      </main>
    </div>
  );
}

function DashboardPage({ stats, filteredLogs, setSelected, setPage, loading, wsConnected }) {
  const sorted = [...filteredLogs].sort((a, b) => compareIso(b?.timestamp, a?.timestamp));
  const activeAttempts = sorted.filter((x) => getExamStatus(x) === "ONGOING").slice(0, 8);
  const completedAttempts = sorted.filter((x) => getExamStatus(x) === "COMPLETED").slice(0, 8);
  const highRisk = sorted.filter((x) => (x.risk || "LOW") === "HIGH").slice(0, 8);
  const mediumRisk = sorted.filter((x) => (x.risk || "LOW") === "MEDIUM").slice(0, 8);

  return (
    <>
      <PageHeader
        eyebrow="Live assessment monitoring"
        title="Risk Command Center"
        subtitle="Track active attempts, risk movement, and candidates requiring review."
        status={loading ? "SYNCING" : wsConnected ? "LIVE" : "OFFLINE"}
      />

      <section className="metrics-grid">
        <MetricCard title="Total Attempts" value={stats.total} icon={<Activity />} />
        <MetricCard title="Ongoing Exams" value={stats.ongoing} tone="ongoing" icon={<Activity />} />
        <MetricCard title="High Risk" value={stats.high} tone="high" icon={<AlertTriangle />} />
        <MetricCard title="Medium Risk" value={stats.medium} tone="medium" icon={<AlertTriangle />} />
        <MetricCard title="Low Risk" value={stats.low} tone="low" icon={<ShieldCheck />} />
        <MetricCard title="Avg Confidence" value={formatNumber(stats.avgConfidence)} icon={<Activity />} />
      </section>

      <section className="dashboard-grid">
        <AttemptPanel
          title="Active Exams"
          subtitle="Attempts still receiving activity"
          emptyText="No active exams in the current dataset."
          items={activeAttempts}
          onOpen={(item) => {
            setSelected(item);
            setPage("report");
          }}
        />

        <AttemptPanel
          title="Completed Exams"
          subtitle="Attempts that submitted the exam"
          emptyText="No completed exams found."
          items={completedAttempts}
          onOpen={(item) => {
            setSelected(item);
            setPage("report");
          }}
        />

        <AttemptPanel
          title="High Risk Cases"
          subtitle="Urgent evaluator review recommended"
          emptyText="No high risk cases right now."
          items={highRisk}
          onOpen={(item) => {
            setSelected(item);
            setPage("report");
          }}
        />

        <AttemptPanel
          title="Medium Review Cases"
          subtitle="Manual review recommended"
          emptyText="No medium review cases right now."
          items={mediumRisk}
          onOpen={(item) => {
            setSelected(item);
            setPage("report");
          }}
        />
      </section>
    </>
  );
}

function QueuePage({ filteredLogs, caseByAttemptId, caseAccessError, searchTerm, setSearchTerm, riskFilter, setRiskFilter, setSelected, setPage }) {
  const sorted = [...filteredLogs].reverse();

  return (
    <>
      <PageHeader
        eyebrow="Investigator workflow"
        title="Review Queue"
        subtitle="Understand candidate status without opening every report."
        status={`${filteredLogs.length} attempts`}
      />

      <section className="control-panel">
        <div className="search-box">
          <Search size={18} />
          <input
            type="text"
            placeholder="Search candidate, email, or attempt ID..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>
        <div className="filter-group">
          {["LOW", "MEDIUM", "HIGH"].map((risk) => (
            <button
              key={risk}
              className={`filter-chip ${risk.toLowerCase()} ${riskFilter[risk] ? "active" : ""}`}
              onClick={() => setRiskFilter((prev) => ({ ...prev, [risk]: !prev[risk] }))}
            >
              {risk}
            </button>
          ))}
        </div>
      </section>

      <section className="panel queue-panel">
        <PanelTitle title="Candidate Attempts" subtitle="Case status, score, and latest activity" />

        {caseAccessError && <div className="workflow-message workflow-message-warning">{caseAccessError}</div>}

        <div className="queue-table">
          <div className="queue-header">
            <span>Candidate</span>
            <span>Assessment</span>
            <span>Case Status</span>
            <span>Risk</span>
            <span>Score</span>
            <span>Last Activity</span>
            <span>Events</span>
            <span>Action</span>
          </div>

          {sorted.length === 0 ? (
            <EmptyState text="No attempts found for the selected filters." />
          ) : (
            sorted.slice(0, 150).map((item, index) => {
              const examStatus = getExamStatus(item);
              const caseRecord = caseByAttemptId[item.attempt_id];
              const caseStatus = caseRecord?.status || "NEW";
              const lastAt = getLastActivityAt(item);
              const duration = getAttemptDurationSeconds(item);
              const eventCount = getEventCount(item);
              return (
                <button
                  key={`${item.attempt_id}-${index}`}
                  className="queue-row"
                  onClick={() => {
                    setSelected(item);
                    setPage("report");
                  }}
                >
                  <span className="candidate-cell">
                    <strong>{getCandidateName(item)}</strong>
                    <small>{getCandidateEmail(item)}</small>
                    <small className="mono">{item.attempt_id}</small>
                    <small className="muted">Exam: {examStatus}</small>
                  </span>
                  <span className="assessment-cell">
                    <strong>{getAssessmentName(item)}</strong>
                    <small className="muted">{item.assessment_id || "—"}</small>
                  </span>
                  <span className={caseStatusClass(caseStatus)}>{caseStatus}</span>
                  <span className={riskClass(item.risk)}>{item.risk || "LOW"}</span>
                  <span className="score-cell">{formatNumber(item.combined_score)}</span>
                  <span className="activity-cell">
                    <strong>{formatDateTime(lastAt)}</strong>
                    <small>{getLatestActivity(item)}</small>
                    <small className="muted">Duration: {formatDuration(duration)}</small>
                  </span>
                  <span className="events-cell">{eventCount === null ? "—" : eventCount}</span>
                  <span className="view-report"><Eye size={16} /> View</span>
                </button>
              );
            })
          )}
        </div>
      </section>
    </>
  );
}

function ReportPage({ selected, selectedCase, caseAccessError, token, onUnauthorized, onCaseAction }) {
  const [events, setEvents] = useState([]);
  const [riskHistory, setRiskHistory] = useState([]);
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
        const [eventsRes, historyRes] = await Promise.all([
          fetch(`${EVENTS_URL}/${selected.attempt_id}`, {
            headers: token ? { Authorization: `Bearer ${token}` } : undefined,
          }),
          fetch(`${RISK_HISTORY_URL}/${selected.attempt_id}`, {
            headers: token ? { Authorization: `Bearer ${token}` } : undefined,
          }),
        ]);

        if (eventsRes.status === 401 || historyRes.status === 401) {
          onUnauthorized();
          return;
        }

        const eventsJson = await eventsRes.json();
        const historyJson = await historyRes.json();
        setEvents(eventsJson.data || []);
        setRiskHistory(historyJson.data || []);
      } catch (err) {
        console.error("Failed to fetch report data:", err);
      } finally {
        setReportLoading(false);
      }
    }
    fetchReportData();
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

  const evidence = buildEvidence(selected);
  const candidateName = selected.candidate_name || events[0]?.candidate_name || riskHistory[0]?.candidate_name || "Unknown Candidate";
  const candidateEmail = selected.candidate_email || events[0]?.candidate_email || riskHistory[0]?.candidate_email || "No email available";
  const assessmentName = selected.assessment_name || events[0]?.assessment_name || riskHistory[0]?.assessment_name || "Assessment";
  const status = getExamStatus(selected, events);

  const violations = events.filter((e) =>
    ["clipboard", "visibility_change", "idle_state", "question_answer", "exam_started", "exam_submitted"].includes(e.event_type)
  );

  const firstEvent = events[0]?.occurred_at || selected.timestamp;
  const lastEvent = events[events.length - 1]?.occurred_at || selected.timestamp;
  const durationSeconds = getAttemptDurationSeconds(selected);
  const eventCount = getEventCount(selected) ?? (events.length ? events.length : null);
  const activeCase = caseDetail || selectedCase;
  const caseStatus = activeCase?.status || "NEW";
  const assignedReviewer = activeCase?.assigned_reviewer_name || activeCase?.assigned_reviewer_email || "Unassigned";
  const actionHistory = Array.isArray(activeCase?.actions) ? activeCase.actions : [];
  const riskTimeline = [...riskHistory].reverse();

  return (
    <>
      <PageHeader
        eyebrow="Candidate investigation"
        title={candidateName}
        subtitle={decisionText(selected.risk)}
        status={selected.risk || "LOW"}
      />

      <section className={`report-shell report-${riskTone(selected.risk)}`}>
        <div className="report-hero">
          <div className="candidate-main">
            <div className="candidate-avatar"><User size={28} /></div>
            <div>
              <div className="case-topline">
                <span className={riskClass(selected.risk)}>{selected.risk || "LOW"}</span>
                <span className={statusClass(status)}>{status}</span>
              </div>
              <h2>{candidateName}</h2>
              <p><Mail size={15} /> {candidateEmail}</p>
            </div>
          </div>

          <div className="recommendation-card">
            <span>Final Decision Summary</span>
            <strong>{decisionText(selected.risk)}</strong>
            <p>{reportSummary(selected)}</p>
            <div className="decision-metrics">
              <div>
                <span>Risk</span>
                <strong>{selected.risk || "LOW"}</strong>
              </div>
              <div>
                <span>Score</span>
                <strong>{formatNumber(selected.combined_score)}</strong>
              </div>
              <div>
                <span>Confidence</span>
                <strong>{formatNumber(selected.confidence)}</strong>
              </div>
            </div>
          </div>
        </div>

        <div className="section-heading">
          <h3>Candidate Details</h3>
          <span>{assessmentName}</span>
        </div>

        <div className="report-meta-grid">
          <MiniStat label="Assessment" value={assessmentName} />
          <MiniStat label="Attempt ID" value={selected.attempt_id} />
          <MiniStat label="Case Status" value={caseStatus} />
          <MiniStat label="Assigned To" value={assignedReviewer} />
          <MiniStat label="Started" value={formatDateTime(firstEvent)} />
          <MiniStat label="Last Activity" value={formatDateTime(lastEvent)} />
          <MiniStat label="Duration" value={formatDuration(durationSeconds)} />
          <MiniStat label="Events" value={eventCount === null ? "—" : eventCount} />
          <MiniStat label="Confidence" value={formatNumber(selected.confidence)} />
          <MiniStat label="Combined Score" value={formatNumber(selected.combined_score)} />
        </div>

        <div className="section-heading">
          <h3>Evidence & Violations</h3>
          <span>{evidence.length} evidence item{evidence.length === 1 ? "" : "s"}</span>
        </div>

        <div className="evidence-grid">
          {evidence.map((item, index) => <EvidenceCard item={item} key={`${item.title}-${index}`} />)}
        </div>

        <ViolationTable events={violations} />

        <div className="section-heading">
          <h3>Case Workflow</h3>
          <span>{activeCase ? `Case #${activeCase.id}` : "Loading case..."}</span>
        </div>

        <section className="workflow-panel">
          <div className="workflow-header">
            <div>
              <span className="workflow-label">Current state</span>
              <strong className={caseStatusClass(caseStatus)}>{caseStatus}</strong>
            </div>
            <div>
              <span className="workflow-label">Assignment</span>
              <strong>{assignedReviewer}</strong>
            </div>
            <div>
              <span className="workflow-label">Action log</span>
              <strong>{activeCase?.action_count ?? 0}</strong>
            </div>
          </div>

          {caseAccessError && <div className="workflow-message workflow-message-warning">{caseAccessError}</div>}

          <div className="workflow-note-row">
            <label htmlFor="reviewer-note">Reviewer note</label>
            <textarea
              id="reviewer-note"
              value={workflowNote}
              onChange={(e) => setWorkflowNote(e.target.value)}
              placeholder="Add a concise audit note for this case..."
              rows={4}
              disabled={!activeCase || workflowBusy}
            />
          </div>

          <div className="workflow-actions">
            <button disabled={!activeCase || workflowBusy} onClick={() => void runCaseAction("assign", {})}>Assign to me</button>
            <button disabled={!activeCase || workflowBusy || !workflowNote.trim()} onClick={() => void runCaseAction("notes", { comment: workflowNote })}>Add note</button>
            <button disabled={!activeCase || workflowBusy} onClick={() => void runCaseAction("transition", { new_status: "ESCALATED", comment: "Escalated from investigation console" })}>Escalate</button>
            <button disabled={!activeCase || workflowBusy} onClick={() => void runCaseAction("transition", { new_status: "CLEARED", comment: "Marked cleared from investigation console" })}>Clear</button>
            <button disabled={!activeCase || workflowBusy} onClick={() => void runCaseAction("transition", { new_status: "CONFIRMED_RISK", comment: "Confirmed risk from investigation console" })}>Confirm risk</button>
            <button disabled={!activeCase || workflowBusy} onClick={() => void runCaseAction("transition", { new_status: "FALSE_POSITIVE", comment: "Marked false positive from investigation console" })}>Mark false positive</button>
          </div>

          {workflowMessage && <div className="workflow-message">{workflowMessage}</div>}

          <div className="workflow-history">
            <div className="workflow-history-head">
              <span className="workflow-label">Action history</span>
              <strong>{actionHistory.length ? `${actionHistory.length} entries` : "No reviewer actions yet"}</strong>
            </div>
            {actionHistory.length === 0 ? (
              <div className="timeline-empty">Reviewer actions will appear here as the case is worked.</div>
            ) : (
              <div className="workflow-history-list">
                {actionHistory.map((action) => {
                  const actor = action.reviewer_name || action.reviewer_email || `Reviewer ${action.reviewer_id}`;
                  const transitionLabel = action.new_status
                    ? `${action.previous_status || "NONE"} → ${action.new_status}`
                    : action.previous_status || action.action_type;
                  return (
                    <div className="workflow-history-item" key={action.id}>
                      <div>
                        <strong>{action.action_type}</strong>
                        <small>{actor}</small>
                      </div>
                      <div>
                        <strong>{transitionLabel}</strong>
                        <small>{formatDateTime(action.created_at)}</small>
                      </div>
                      <p>{action.comment || "No reviewer comment provided."}</p>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </section>

        <div className="section-heading">
          <h3>Risk History</h3>
          <span>{reportLoading ? "Loading..." : `${riskTimeline.length} update${riskTimeline.length === 1 ? "" : "s"}`}</span>
        </div>

        {riskTimeline.length === 0 ? (
          <div className="timeline-empty">No risk history points are available for this attempt yet.</div>
        ) : (
          <div className="risk-history-list">
            {riskTimeline.map((item) => (
              <div className="risk-history-item" key={item.id}>
                <div className="risk-history-top">
                  <strong className={riskClass(item.risk)}>{item.risk}</strong>
                  <small>{formatDateTime(item.timestamp)}</small>
                </div>
                <div className="risk-history-metrics">
                  <span>Score {formatNumber(item.combined_score)}</span>
                  <span>Confidence {formatNumber(item.confidence)}</span>
                </div>
                <p>{item.reason || "No scoring explanation available."}</p>
              </div>
            ))}
          </div>
        )}

        <details className="advanced-details">
          <summary>Advanced Technical Details</summary>
          <pre>{JSON.stringify({ selected, events, riskHistory }, null, 2)}</pre>
        </details>
      </section>
    </>
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
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" />

          {error && <div className="login-error">{error}</div>}

          <button
            className="login-btn"
            disabled={loading || !email || !password}
            onClick={() => onLogin({ email, password })}
          >
            {loading ? "Signing in…" : "Sign In"}
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
                  <small>{getAssessmentName(item)} • {item.attempt_id}</small>
                </div>

                <div className="attempt-right">
                  <div className="attempt-score">{formatNumber(item.combined_score)}</div>
                  <div className="attempt-meta">{formatDateTime(lastAt)} • {formatDuration(duration)}</div>
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
