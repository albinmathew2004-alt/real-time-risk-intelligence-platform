import { AlertTriangle, ArrowLeftRight, ClipboardList, Clock3, Eye, Gauge, ShieldAlert, Zap } from "lucide-react";

const ICONS = {
  score: Gauge,
  clipboard: ClipboardList,
  tab_switches: ArrowLeftRight,
  blur_events: Eye,
  idle_time: Clock3,
  rapid_answer_bursts: Zap,
  suspicious_sequences: ShieldAlert,
  fallback: AlertTriangle,
};

export default function EvidenceTable({ rows }) {
  return (
    <section className="investigation-card evidence-card-shell">
      <div className="report-card-head danger">
        <h3>Evidence & Violations</h3>
      </div>

      {rows.length === 0 ? (
        <div className="timeline-empty">No high-signal evidence has been generated for this attempt yet.</div>
      ) : (
        <div className="evidence-table-shell">
          <div className="evidence-table-head">
            <span>Type</span>
            <span>Severity</span>
            <span>Details</span>
          </div>
          <div className="evidence-table-body">
            {rows.map((row) => {
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
                  <div className="evidence-details-cell">{row.details}</div>
                </article>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
