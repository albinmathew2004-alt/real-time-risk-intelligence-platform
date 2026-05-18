import { useMemo, useState } from "react";
import { AlertTriangle, ArrowLeftRight, ClipboardList, Clock3, Eye, Gauge, ShieldAlert, X, Zap } from "lucide-react";

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

export default function EvidenceTable({ rows }) {
  const [showTimeline, setShowTimeline] = useState(false);

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
        <div className="evidence-timeline-overlay" role="dialog" aria-modal="true">
          <div className="evidence-timeline-panel">
            <div className="evidence-timeline-head">
              <div>
                <span className="report-section-kicker">Full Evidence Timeline</span>
                <h3>All Evidence Signals</h3>
              </div>
              <button className="evidence-timeline-close" onClick={() => setShowTimeline(false)} type="button" aria-label="Close evidence timeline">
                <X size={18} />
              </button>
            </div>

            <div className="evidence-timeline-list">
              {rankedRows.map((row) => {
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
                          <p>{row.signalType || row.key}</p>
                        </div>
                      </div>
                      <span className={`severity-chip ${String(row.severity || "low").toLowerCase()}`}>{row.severity}</span>
                    </div>
                    <p className="evidence-timeline-explanation">{row.subtitle}</p>
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
