import { useState } from "react";

function confidenceBadge(confidence) {
  if (confidence >= 0.9) return "high";
  if (confidence >= 0.7) return "medium";
  return "low";
}

export function AnomalyTable({ anomalies, baseUrl, onCleared }) {
  const [clearing, setClearing] = useState(false);
  const [error, setError] = useState("");
  const hasAnomalies = Boolean(anomalies && anomalies.length > 0);

  async function clearAnomalies() {
    setClearing(true);
    setError("");
    try {
      const url = baseUrl ? `${baseUrl}/api/anomalies` : "/api/anomalies";
      const res = await fetch(url, { method: "DELETE" });
      if (!res.ok) {
        throw new Error(`Clear failed (${res.status})`);
      }
      await onCleared?.();
    } catch (err) {
      setError(err.message || "Failed to clear anomalies");
    } finally {
      setClearing(false);
    }
  }

  return (
    <section className="panel">
      <div className="panel-title-row">
        <h2>Recent Anomalies</h2>
        <button
          type="button"
          className="toggle-btn"
          onClick={clearAnomalies}
          disabled={clearing || !hasAnomalies}
        >
          {clearing ? "Clearing..." : "Clear"}
        </button>
      </div>
      {error && <p className="demo-error">{error}</p>}
      {!hasAnomalies ? (
        <p className="empty-state">
          No RAN anomalies detected yet. This panel updates automatically as new
          readings are processed by the ML detector.
        </p>
      ) : (
        <div className="anomaly-list">
          {anomalies.map((a, idx) => (
            <article key={`${a.incident_id}-${idx}`} className="anomaly-card">
              <header>
                <span className={`anomaly-type-pill confidence-${confidenceBadge(a.ad_confidence)}`}>
                  AD {(a.ad_confidence * 100).toFixed(0)}%
                </span>
                <span className="anomaly-cell">
                  Incident {a.incident_id} · Zone {a.zone}
                </span>
              </header>
              <p className="anomaly-detail">
                Application: {a.application} · 128×18 KPI window
              </p>
              <div className="anomaly-grid">
                <div>
                  <span className="anomaly-label">Root Cause</span>
                  <p>{a.root_cause || "n/a"}</p>
                </div>
                <div>
                  <span className="anomaly-label">Recommended Fix</span>
                  <p>{a.recommended_fix || "n/a"}</p>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
