import {
  Activity,
  ArrowLeftRight,
  ClipboardList,
  Clock3,
  Focus,
  Keyboard,
  ShieldAlert,
  TimerReset,
  Zap,
} from "lucide-react";

const ICONS = {
  clipboard: ClipboardList,
  tab_switches: ArrowLeftRight,
  blur_events: Activity,
  idle_time: Clock3,
  rapid_answer_bursts: Zap,
  suspicious_sequences: ShieldAlert,
  keystroke_anomalies: Keyboard,
  typing_behavior_anomalies: Keyboard,
  focus_loss_rate: Focus,
};

const EMPTY_CANDIDATE_MESSAGE = "No reportable candidate-facing integrity signals were observed.";

function hasReportableSignal(item) {
  if (!item) return false;
  const signalValue = Number(item.signalValue);
  if (Number.isFinite(signalValue)) return signalValue > 0;

  const displayValue = String(item.value ?? "").trim();
  if (!displayValue) return false;
  return !["0", "0%", "00:00", "0:00", "not available"].includes(displayValue.toLowerCase());
}

export function getVisibleViolationOverviewItems(items = [], hideZeroSignals = false) {
  const safeItems = Array.isArray(items) ? items : [];
  return hideZeroSignals ? safeItems.filter(hasReportableSignal) : safeItems;
}

export default function ViolationOverview({ items, hideZeroSignals = false }) {
  const visibleItems = getVisibleViolationOverviewItems(items, hideZeroSignals);

  return (
    <section className="investigation-card violation-overview-section">
      <div className="report-card-head">
        <h3>Violation Overview</h3>
      </div>
      {visibleItems.length || !hideZeroSignals ? (
        <div className="violation-overview-grid">
          {visibleItems.map((item) => {
            const Icon = ICONS[item.key] || TimerReset;
            return (
              <article className="violation-overview-card" key={item.key}>
                <div className={`violation-overview-icon ${String(item.severity || "low").toLowerCase()}`}>
                  <Icon size={20} />
                </div>
                <div className="violation-overview-copy">
                  <span>{item.title}</span>
                  <small>{item.subtitle}</small>
                </div>
                <strong>{item.value}</strong>
                <em className={String(item.severity || "low").toLowerCase()}>{item.severityLabel}</em>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="violation-overview-empty">{EMPTY_CANDIDATE_MESSAGE}</div>
      )}
    </section>
  );
}

export { EMPTY_CANDIDATE_MESSAGE };
