import { useState } from "react";

function confidenceBadge(confidence) {
  if (confidence >= 0.9) return "high";
  if (confidence >= 0.7) return "medium";
  return "low";
}

function formatCategory(category) {
  if (!category) return "";
  if (category === "rf_filter_fault") return "RF Filter Fault";
  return category.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function hasEnrichment(anomaly) {
  return !anomaly.incident_status || anomaly.incident_status.stage === "rca_explanation";
}

function RemediationBadge({ status }) {
  if (status === "completed") {
    return <span className="remediation-badge completed">✅ Remediated</span>;
  }
  if (status === "failed") {
    return <span className="remediation-badge failed">❌ Remediation failed</span>;
  }
  return null;
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
                  Anomaly detection confidence: {(a.ad_confidence * 100).toFixed(0)}%
                </span>
                <span className="anomaly-cell">
                  Incident {a.incident_id} · Zone {a.zone}
                </span>
                <RemediationBadge status={a.remediation_status} />
              </header>
              {a.incident_status?.outcome === "failed" && (
                <p className="demo-error" role="status">
                  {a.incident_status.stage}: {a.incident_status.error_reason} · {new Date(a.incident_status.timestamp).toLocaleString()}
                </p>
              )}
              <p className="anomaly-detail">
                Application: {a.application}{hasEnrichment(a) && " · 128×18 KPI window"}
                {a.root_cause_category && (
                  <span className="category-badge"> · ML category: {formatCategory(a.root_cause_category)}</span>
                )}
              </p>
              <p className="anomaly-detail">
                Mantis RCA: {a.ml_class || "unavailable"}
                {a.ml_class && a.ml_confidence != null && (
                  <> · RCA confidence: {a.ml_confidence}</>
                )}
                {a.remediation_template_name && (
                  <> · Job: {a.remediation_template_name}</>
                )}
                {a.remediation_job_status && (
                  <> · Job outcome: {a.remediation_job_status}</>
                )}
                {a.remediation_job_id && (
                  <> · Job ID: {a.remediation_job_id}</>
                )}
              </p>
              {a.remediation_output_summary && (
                <p className="anomaly-detail">Job result: {a.remediation_output_summary}</p>
              )}
              {a.remediation_status === "failed" && a.remediation_failure_stage && (
                <p className="demo-error" role="status">
                  {a.remediation_failure_stage}: {a.remediation_failure_reason || "AAP failed"}
                  {a.remediation_timestamp && <> · {new Date(a.remediation_timestamp).toLocaleString()}</>}
                </p>
              )}
              {hasEnrichment(a) && <div className="anomaly-grid">
                <div>
                  <span className="anomaly-label">LLM Explanation</span>
                  <p>{a.root_cause || "unavailable"}</p>
                </div>
                <div>
                  <span className="anomaly-label">Recommended Fix</span>
                  <p>{a.recommended_fix || "unavailable"}</p>
                </div>
              </div>}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
