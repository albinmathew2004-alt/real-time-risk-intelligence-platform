export default function InvestigationHeader({
  candidateName,
  candidateEmail,
  initials,
  risk,
  status,
  finalDecision,
  summaryText,
  strongestReason,
  explanation,
  score,
  confidence,
  confidenceLabel,
}) {
  return (
    <section className="investigation-card investigation-header-card">
      <div className="investigation-header-grid">
        <div className="investigation-identity">
          <div className="investigation-avatar">{initials}</div>
          <div className="investigation-identity-copy">
            <div className="investigation-pill-row">
              <span className={`report-badge report-badge-risk ${String(risk || "LOW").toLowerCase()}`}>{risk || "LOW"} RISK</span>
              <span className={`report-badge report-badge-status ${String(status || "ONGOING").toLowerCase()}`}>{status || "ONGOING"}</span>
            </div>
            <h2>{candidateName}</h2>
            <p>{candidateEmail}</p>
          </div>
        </div>

        <div className="investigation-summary">
          <span className="report-section-kicker">Final Decision Summary</span>
          <h3>{finalDecision}</h3>
          <p>{summaryText}</p>
          <div className="investigation-strongest-reason">Strongest reason: {strongestReason}</div>
          <div className="investigation-why-box">
            <span className="report-section-kicker">Why This Score?</span>
            <p>{explanation}</p>
          </div>
        </div>

        <div className="investigation-scoreboard">
          <div className="investigation-score-item">
            <span>Risk Level</span>
            <strong className={`risk-ring ${String(risk || "LOW").toLowerCase()}`}>{risk || "LOW"}</strong>
          </div>
          <div className="investigation-score-item">
            <span>Risk Score</span>
            <strong className="score-value">
              {score} <small>/ 1.00</small>
            </strong>
          </div>
          <div className="investigation-score-item">
            <span>Confidence</span>
            <strong className="confidence-value">{confidence}</strong>
            <small>{confidenceLabel}</small>
          </div>
        </div>
      </div>
    </section>
  );
}
