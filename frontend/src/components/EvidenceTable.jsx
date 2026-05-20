import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeftRight, ClipboardList, Clock3, Eye, Gauge, Keyboard, Search, ShieldAlert, SlidersHorizontal, X, Zap } from "lucide-react";

const ICONS = {
  score: Gauge,
  high_risk_score: Gauge,
  clipboard: ClipboardList,
  clipboard_activity: ClipboardList,
  tab_switches: ArrowLeftRight,
  tab_switching: ArrowLeftRight,
  blur_events: Eye,
  focus_blur: Eye,
  idle_time: Clock3,
  idle_spike: Clock3,
  rapid_answer_bursts: Zap,
  rapid_answer_burst: Zap,
  suspicious_sequences: ShieldAlert,
  suspicious_sequence: ShieldAlert,
  typing_behavior: Keyboard,
  typing_behavior_anomalies: Keyboard,
  fallback: AlertTriangle,
};

function severityRank(severity) {
  if (severity === "HIGH") return 3;
  if (severity === "MEDIUM") return 2;
  return 1;
}

function formatTimestamp(value) {
  if (!value) return "Not available";
  try {
    return new Date(value).toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return value;
  }
}

function impactLabel(severity) {
  if (severity === "HIGH") return "High";
  if (severity === "MEDIUM") return "Medium";
  return "Low";
}

function evidencePriority(row) {
  if (row.key === "suspicious_sequence" || row.key === "suspicious_sequences") return 3;
  if (row.key === "high_risk_score" || row.key === "score") return 2;
  return 0;
}

function normalizeSignalType(value) {
  return String(value || "")
    .trim()
    .toUpperCase()
    .replace(/[\s-]+/g, "_");
}

function signalTypeOption(row) {
  const key = normalizeSignalType(row.signalType || row.key);
  if (key.includes("CLIPBOARD")) return "CLIPBOARD";
  if (
    key.includes("CORRELATED")
    || key.includes("FOCUS_LOSS_PATTERN")
    || key.includes("SUBMISSION_AFTER_PASTE")
    || key.includes("PASTE_WITHOUT_TYPING")
    || key.includes("IDLE_TO_BURST")
    || key.includes("TAB_SWITCH_TO_ANSWER_CHANGE")
    || key.includes("AFTER_FOCUS_LOSS")
  ) return "CORRELATED_PATTERN";
  if (key.includes("TAB")) return "TAB_SWITCHING";
  if (key.includes("FOCUS") || key.includes("BLUR")) return "FOCUS_BLUR";
  if (key.includes("IDLE")) return "IDLE";
  if (key.includes("RAPID")) return "RAPID_ANSWER";
  if (key.includes("SUSPICIOUS")) return "SUSPICIOUS_SEQUENCE";
  if (key.includes("TYPING")) return "TYPING";
  return "OTHER";
}

function signalTypeLabel(option) {
  if (option === "CLIPBOARD") return "Clipboard";
  if (option === "CORRELATED_PATTERN") return "Correlated Pattern";
  if (option === "TAB_SWITCHING") return "Tab Switching";
  if (option === "FOCUS_BLUR") return "Focus Blur";
  if (option === "IDLE") return "Idle";
  if (option === "RAPID_ANSWER") return "Rapid Answer";
  if (option === "SUSPICIOUS_SEQUENCE") return "Suspicious Sequence";
  if (option === "TYPING") return "Typing";
  return "Other";
}

function sortTimelineRows(rows, sortMode) {
  return [...rows].sort((a, b) => {
    const aTime = a.lastSeen ? new Date(a.lastSeen).getTime() : 0;
    const bTime = b.lastSeen ? new Date(b.lastSeen).getTime() : 0;
    const aCount = Number(a.count || 0);
    const bCount = Number(b.count || 0);

    if (sortMode === "recent") {
      if (bTime !== aTime) return bTime - aTime;
      return bCount - aCount;
    }

    if (sortMode === "count") {
      if (bCount !== aCount) return bCount - aCount;
      return bTime - aTime;
    }

    const severityDelta = severityRank(b.severity) - severityRank(a.severity);
    if (severityDelta !== 0) return severityDelta;
    if (bTime !== aTime) return bTime - aTime;
    if (bCount !== aCount) return bCount - aCount;

    const impactDelta = Number(b.relatedEventCount || 0) - Number(a.relatedEventCount || 0);
    if (impactDelta !== 0) return impactDelta;

    const priorityDelta = evidencePriority(b) - evidencePriority(a);
    if (priorityDelta !== 0) return priorityDelta;

    return String(a.title || "").localeCompare(String(b.title || ""));
  });
}

export default function EvidenceTable({ rows }) {
  const [showTimeline, setShowTimeline] = useState(false);
  const [severityFilter, setSeverityFilter] = useState("ALL");
  const [typeFilter, setTypeFilter] = useState("ALL");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortMode, setSortMode] = useState("severity");

  const rankedRows = useMemo(() => {
    return [...rows].sort((a, b) => {
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
  }, [rows]);

  const topRows = rankedRows.slice(0, 5);
  const filteredTimelineRows = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();
    const filteredRows = rankedRows.filter((row) => {
      const severityMatch = severityFilter === "ALL" || String(row.severity || "").toUpperCase() === severityFilter;
      const typeMatch = typeFilter === "ALL" || signalTypeOption(row) === typeFilter;

      if (!severityMatch || !typeMatch) return false;

      if (!normalizedQuery) return true;

      const haystack = [
        row.title,
        row.subtitle,
        row.explanation,
        row.details,
        row.signalType,
        row.key,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

      return haystack.includes(normalizedQuery);
    });

    return sortTimelineRows(filteredRows, sortMode);
  }, [rankedRows, searchQuery, severityFilter, sortMode, typeFilter]);

  useEffect(() => {
    if (!showTimeline) return undefined;

    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        setShowTimeline(false);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [showTimeline]);

  return (
    <>
      <section className="investigation-card evidence-card-shell">
        <div className="report-card-head danger">
          <h3>Evidence & Violations</h3>
          <button className="history-toggle-btn" onClick={() => setShowTimeline(true)} type="button">
            View Full Evidence Timeline
          </button>
        </div>

        {rankedRows.length === 0 ? (
          <div className="timeline-empty">No high-signal evidence has been generated for this attempt yet.</div>
        ) : (
          <div className="evidence-table-shell">
            <div className="evidence-table-head">
              <span>Type</span>
              <span>Severity</span>
              <span>Count</span>
              <span>Impact</span>
            </div>
            <div className="evidence-table-body">
              {topRows.map((row) => {
                const Icon = ICONS[row.key] || ICONS.fallback;
                return (
                  <article className="evidence-table-row" key={`${row.key}-${row.title}`}>
                    <div className="evidence-type-cell">
                      <span className={`evidence-icon-chip ${String(row.severity || "low").toLowerCase()}`}>
                        <Icon size={16} />
                      </span>
                      <div>
                        <strong>{row.title}</strong>
                        <p>{row.subtitle}</p>
                      </div>
                    </div>
                    <div className="evidence-severity-cell">
                      <span className={`severity-chip ${String(row.severity || "low").toLowerCase()}`}>{row.severity}</span>
                    </div>
                    <div className="evidence-count-cell">{row.countDisplay || row.count || "0"}</div>
                    <div className="evidence-impact-cell">
                      <i className={String(row.severity || "low").toLowerCase()}></i>
                      <span>{row.impactLabel || impactLabel(row.severity)}</span>
                    </div>
                  </article>
                );
              })}
            </div>
            <div className="evidence-table-footer">
              <span>Showing top 5 of {rankedRows.length} evidence signals</span>
              <button className="evidence-footer-action" onClick={() => setShowTimeline(true)} type="button">
                View Full Evidence Timeline
              </button>
            </div>
          </div>
        )}
      </section>

      {showTimeline ? (
        <div
          className="evidence-timeline-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Full evidence timeline"
          onClick={() => setShowTimeline(false)}
        >
          <div className="evidence-timeline-panel" onClick={(event) => event.stopPropagation()}>
            <div className="evidence-timeline-head">
              <div>
                <span className="report-section-kicker">Full Evidence Timeline</span>
                <h3>All Evidence Signals</h3>
              </div>
              <button className="evidence-timeline-close" onClick={() => setShowTimeline(false)} type="button" aria-label="Close evidence timeline">
                <X size={18} />
              </button>
            </div>

            <div className="evidence-timeline-controls">
              <label className="evidence-timeline-search">
                <Search size={15} />
                <input
                  aria-label="Search evidence timeline"
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="Search type, title, or explanation"
                  type="search"
                  value={searchQuery}
                />
              </label>

              <div className="evidence-timeline-filters">
                <div className="timeline-filter-group">
                  <span><SlidersHorizontal size={14} /> Severity</span>
                  <select onChange={(event) => setSeverityFilter(event.target.value)} value={severityFilter}>
                    <option value="ALL">All</option>
                    <option value="HIGH">High</option>
                    <option value="MEDIUM">Medium</option>
                    <option value="LOW">Low</option>
                  </select>
                </div>

                <div className="timeline-filter-group">
                  <span>Type</span>
                  <select onChange={(event) => setTypeFilter(event.target.value)} value={typeFilter}>
                    <option value="ALL">All</option>
                    <option value="CLIPBOARD">Clipboard</option>
                    <option value="CORRELATED_PATTERN">Correlated Pattern</option>
                    <option value="TAB_SWITCHING">Tab Switching</option>
                    <option value="FOCUS_BLUR">Focus Blur</option>
                    <option value="IDLE">Idle</option>
                    <option value="RAPID_ANSWER">Rapid Answer</option>
                    <option value="SUSPICIOUS_SEQUENCE">Suspicious Sequence</option>
                    <option value="TYPING">Typing</option>
                  </select>
                </div>

                <div className="timeline-filter-group">
                  <span>Sort</span>
                  <select onChange={(event) => setSortMode(event.target.value)} value={sortMode}>
                    <option value="severity">Severity</option>
                    <option value="recent">Most Recent</option>
                    <option value="count">Count</option>
                  </select>
                </div>
              </div>
            </div>

            <div className="evidence-timeline-list">
              {filteredTimelineRows.length === 0 ? (
                <div className="timeline-empty">No evidence signals available.</div>
              ) : filteredTimelineRows.map((row) => {
                const Icon = ICONS[row.key] || ICONS.fallback;
                return (
                  <article className="evidence-timeline-item" key={`timeline-${row.key}-${row.title}`}>
                    <div className="evidence-timeline-top">
                      <div className="evidence-type-cell">
                        <span className={`evidence-icon-chip ${String(row.severity || "low").toLowerCase()}`}>
                          <Icon size={16} />
                        </span>
                        <div>
                          <strong>{row.title}</strong>
                          <p>{signalTypeLabel(signalTypeOption(row))}</p>
                        </div>
                      </div>
                      <span className={`severity-chip ${String(row.severity || "low").toLowerCase()}`}>{row.severity}</span>
                    </div>
                    <p className="evidence-timeline-explanation">{row.subtitle || row.explanation || row.details || "No explanation available."}</p>
                    <div className="evidence-timeline-meta">
                      <span>Count: {row.countDisplay || row.count || "0"}</span>
                      <span>Related Events: {row.relatedEventCount ?? 0}</span>
                      <span>First Seen: {formatTimestamp(row.firstSeen)}</span>
                      <span>Last Seen: {formatTimestamp(row.lastSeen)}</span>
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
