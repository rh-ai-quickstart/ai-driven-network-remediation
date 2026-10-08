import { useMemo, useState } from "react";

/**
 * Derive a severity level from AD confidence.
 *   >= 0.9  → "red"
 *   <  0.9  → "yellow"
 *   absent  → "green" (handled by the caller)
 */
function severity(confidence) {
  return confidence >= 0.9 ? "red" : "yellow";
}

function remediationIcon(status) {
  if (status === "completed") return "✅";
  if (status === "failed") return "❌";
  return null;
}

/**
 * Known TelecomTS zones and applications. The grid always shows the full
 * matrix so that "not listed in the anomaly topics" shows as green, per the
 * ticket requirement. Any new zone/app that appears in anomaly data but isn't
 * in these lists is included automatically.
 */
const KNOWN_ZONES = ["A", "B", "C"];
const KNOWN_APPS = ["File", "Twitch", "YouTube"];

/**
 * Build a lookup Map keyed by "zone::application" from the flat anomaly list,
 * keeping only the latest (first in the already-newest-first array) per pair.
 * Returns the full set of zones and apps (known + any extras from data).
 */
function buildGrid(anomalies) {
  const zoneSet = new Set(KNOWN_ZONES);
  const appSet = new Set(KNOWN_APPS);
  const lookup = new Map();

  for (const a of anomalies) {
    const zone = a.zone || "Unknown";
    const app = a.application || "Unknown";
    zoneSet.add(zone);
    appSet.add(app);

    const key = `${zone}::${app}`;
    if (!lookup.has(key)) {
      lookup.set(key, a);
    }
  }

  const zones = [...zoneSet].sort();
  const apps = [...appSet].sort();
  return { zones, apps, lookup };
}

function CellDetail({ anomaly, onClose }) {
  if (!anomaly) return null;

  return (
    <div className="cell-detail-overlay" onClick={onClose}>
      <div className="cell-detail-card" onClick={(e) => e.stopPropagation()}>
        <button className="cell-detail-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h3>
          Zone {anomaly.zone} · {anomaly.application}
        </h3>
        <div className="cell-detail-grid">
          <div>
            <span className="cell-detail-label">Incident</span>
            <p>{anomaly.incident_id}</p>
          </div>
          <div>
            <span className="cell-detail-label">AD Confidence</span>
            <p>{(anomaly.ad_confidence * 100).toFixed(1)}%</p>
          </div>
          <div>
            <span className="cell-detail-label">Root Cause</span>
            <p>{anomaly.root_cause || "n/a"}</p>
          </div>
          <div>
            <span className="cell-detail-label">Recommended Fix</span>
            <p>{anomaly.recommended_fix || "n/a"}</p>
          </div>
          {anomaly.remediation_status && (
            <div>
              <span className="cell-detail-label">Remediation</span>
              <p>
                {anomaly.remediation_status === "completed"
                  ? "✅ Completed"
                  : "❌ Failed"}
                {anomaly.remediation_job_id && (
                  <span className="cell-detail-job">
                    {" "}
                    (job {anomaly.remediation_job_id})
                  </span>
                )}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function CellBandGrid({ anomalies }) {
  const [selected, setSelected] = useState(null);
  const { zones, apps, lookup } = useMemo(
    () => buildGrid(anomalies || []),
    [anomalies],
  );

  return (
    <section className="panel">
      <h2>Cell &amp; Band Overview</h2>
      <p className="meta">
        Zone × Application grid — green means no anomaly detected. Click a
        red or yellow cell for details.
      </p>

      <div className="cbg-scroll">
          <table className="cbg-table">
            <thead>
              <tr>
                <th className="cbg-corner">Zone ╲ App</th>
                {apps.map((app) => (
                  <th key={app} className="cbg-col-header">
                    {app}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {zones.map((zone) => (
                <tr key={zone}>
                  <td className="cbg-row-header">Zone {zone}</td>
                  {apps.map((app) => {
                    const key = `${zone}::${app}`;
                    const anomaly = lookup.get(key);
                    const level = anomaly
                      ? severity(anomaly.ad_confidence)
                      : "green";
                    const icon = anomaly
                      ? remediationIcon(anomaly.remediation_status)
                      : null;
                    const title = anomaly
                      ? `Incident ${anomaly.incident_id} — AD ${(anomaly.ad_confidence * 100).toFixed(0)}%`
                      : "No anomaly";

                    return (
                      <td
                        key={key}
                        className={`cbg-cell cbg-${level}`}
                        title={title}
                        role={anomaly ? "button" : undefined}
                        tabIndex={anomaly ? 0 : undefined}
                        onClick={() => anomaly && setSelected(anomaly)}
                        onKeyDown={(e) =>
                          anomaly &&
                          (e.key === "Enter" || e.key === " ") &&
                          setSelected(anomaly)
                        }
                      >
                        {icon && (
                          <span className="cbg-icon">{icon}</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

      <div className="cbg-legend">
        <span className="cbg-legend-item">
          <span className="cbg-swatch cbg-green" /> Healthy
        </span>
        <span className="cbg-legend-item">
          <span className="cbg-swatch cbg-yellow" /> Anomaly (&lt;90% confidence)
        </span>
        <span className="cbg-legend-item">
          <span className="cbg-swatch cbg-red" /> Anomaly (≥90% confidence)
        </span>
      </div>

      {selected && (
        <CellDetail anomaly={selected} onClose={() => setSelected(null)} />
      )}
    </section>
  );
}
