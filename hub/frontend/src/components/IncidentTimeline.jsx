import { useState } from "react";

function stageClass(stage) {
  if (stage === "Auto-Remediated") return "up";
  if (stage === "Escalated") return "down";
  return "warn";
}

export function IncidentTimeline({ movie }) {
  const [open, setOpen] = useState(false);
  const items = movie || [];

  return (
    <section className="panel">
      <div className="panel-title-row">
        <h2>Incident Timeline</h2>
        <button
          type="button"
          className="toggle-btn"
          onClick={() => setOpen((prev) => !prev)}
        >
          {open ? "Collapse" : "Expand"}
        </button>
      </div>
      <p className="meta">
        Most recent incidents with remediation stage and artifacts.
      </p>
      {open ? (
        <div className="movie-list">
          {items.length === 0 ? (
            <p className="meta">No incident events yet.</p>
          ) : (
            items.map((event) => (
              <article
                className="movie-card"
                key={`${event.timestamp}-${event.incident_id}`}
              >
                <div className="movie-head">
                  <h3>{event.title || event.failure_type || "Incident"}</h3>
                  <span className={`pill ${stageClass(event.stage)}`}>
                    {event.stage}
                  </span>
                </div>
                <p className="meta">
                  {event.timestamp
                    ? new Date(event.timestamp).toLocaleString()
                    : "n/a"}{" "}
                  · Incident: {event.incident_id || "n/a"}
                </p>
                {event.summary && <p>{event.summary}</p>}
                <p className="meta">
                  {event.artifacts?.lightspeed_conversation_id && (
                    <>Lightspeed Conversation: {event.artifacts.lightspeed_conversation_id} · </>
                  )}
                  AAP Job: {event.artifacts?.aap_job_id || "n/a"} · SNOW:{" "}
                  {event.artifacts?.servicenow_ticket || "n/a"} · Trace:{" "}
                  {event.artifacts?.langfuse_trace_id || "n/a"}
                </p>
                {event.badges && event.badges.length > 0 && (
                  <div className="tag-row">
                    {event.badges.map((tag) => (
                      <span key={tag} className="tag">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </article>
            ))
          )}
        </div>
      ) : (
        <p className="meta">
          Panel collapsed. Click Expand to view the incident timeline.
        </p>
      )}
    </section>
  );
}
