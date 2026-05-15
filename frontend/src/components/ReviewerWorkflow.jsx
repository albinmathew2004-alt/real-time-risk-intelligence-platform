import { useState } from "react";
import { AlertTriangle, Check, History, NotebookPen, ShieldCheck, UserPlus } from "lucide-react";

export default function ReviewerWorkflow({
  caseStatus,
  assignedReviewer,
  caseId,
  workflowNote,
  onWorkflowNoteChange,
  workflowBusy,
  workflowMessage,
  caseAccessError,
  actionHistory,
  onAssign,
  onAddNote,
  onEscalate,
  onConfirmRisk,
  onFalsePositive,
}) {
  const [showHistory, setShowHistory] = useState(false);

  return (
    <section className="investigation-card reviewer-workflow-card">
      <div className="report-card-head accent">
        <h3>Reviewer Workflow</h3>
        <button className="history-toggle-btn" onClick={() => setShowHistory((prev) => !prev)} type="button">
          <History size={16} />
          {showHistory ? "Hide History" : "View History"}
        </button>
      </div>

      <div className="reviewer-status-panel">
        <span className="report-section-kicker">Case Status</span>
        <div className="reviewer-status-row">
          <strong className={`reviewer-case-pill ${String(caseStatus || "NEW").toLowerCase()}`}>{caseStatus || "NEW"}</strong>
          <p>Assigned to: {assignedReviewer}</p>
        </div>
        {caseId ? <small>Case #{caseId}</small> : <small>Case record is still loading.</small>}
      </div>

      {caseAccessError ? <div className="workflow-message workflow-message-warning">{caseAccessError}</div> : null}

      <div className="reviewer-actions-shell">
        <span className="report-section-kicker">Actions</span>
        <div className="reviewer-action-grid reviewer-action-grid-top">
          <button className="workflow-action-btn primary" disabled={!caseId || workflowBusy} onClick={onAssign} type="button">
            <UserPlus size={16} />
            Assign To Me
          </button>
          <button
            className="workflow-action-btn"
            disabled={!caseId || workflowBusy || !workflowNote.trim()}
            onClick={onAddNote}
            type="button"
          >
            <NotebookPen size={16} />
            Add Note
          </button>
        </div>

        <textarea
          className="reviewer-note-input"
          placeholder="Add a concise reviewer note to support the audit trail."
          value={workflowNote}
          onChange={(event) => onWorkflowNoteChange(event.target.value)}
          rows={4}
          disabled={!caseId || workflowBusy}
        />

        <div className="reviewer-action-grid">
          <button className="workflow-action-btn danger" disabled={!caseId || workflowBusy} onClick={onEscalate} type="button">
            <AlertTriangle size={16} />
            Escalate
          </button>
          <button className="workflow-action-btn warning" disabled={!caseId || workflowBusy} onClick={onConfirmRisk} type="button">
            <ShieldCheck size={16} />
            Confirm Risk
          </button>
          <button className="workflow-action-btn success" disabled={!caseId || workflowBusy} onClick={onFalsePositive} type="button">
            <Check size={16} />
            Mark False Positive
          </button>
        </div>
      </div>

      {workflowMessage ? <div className="workflow-message">{workflowMessage}</div> : null}

      {showHistory ? (
        <div className="reviewer-history-shell">
          {actionHistory.length === 0 ? (
            <div className="timeline-empty">Reviewer actions will appear here once the case workflow starts.</div>
          ) : (
            <div className="reviewer-history-list">
              {actionHistory.map((action) => {
                const actor = action.reviewer_name || action.reviewer_email || `Reviewer ${action.reviewer_id}`;
                const transition = action.new_status
                  ? `${action.previous_status || "NONE"} -> ${action.new_status}`
                  : action.previous_status || action.action_type;
                return (
                  <article className="reviewer-history-item" key={action.id}>
                    <div className="reviewer-history-head">
                      <strong>{action.action_type}</strong>
                      <span>{transition}</span>
                    </div>
                    <p>{action.comment || "No reviewer comment provided."}</p>
                    <small>
                      {actor} • {action.created_at}
                    </small>
                  </article>
                );
              })}
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}
