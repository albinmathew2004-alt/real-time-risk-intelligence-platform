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
  focus_loss_rate: Focus,
};

export default function ViolationOverview({ items }) {
  return (
    <section className="investigation-card">
      <div className="report-card-head">
        <h3>Violation Overview</h3>
      </div>
      <div className="violation-overview-grid">
        {items.map((item) => {
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
    </section>
  );
}
