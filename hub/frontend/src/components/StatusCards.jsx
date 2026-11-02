function StatusCard({ title, value, degraded }) {
  return (
    <article className={`card${degraded ? " card--degraded" : ""}`}>
      <p>{title}</p>
      <strong>{value}</strong>
    </article>
  );
}

function formatServiceNow(sn) {
  if (!sn) return "unknown";
  if (typeof sn === "string") return sn;
  return sn.reachable ? "reachable" : "unreachable";
}

export function StatusCards({ summary, integrations, deps }) {
  const unavailable = deps?.unavailable || [];
  const snowDegraded = unavailable.includes("servicenow");
  const kafkaDegraded = unavailable.includes("kafka");

  const incidentValue = snowDegraded
    ? "unavailable"
    : String(summary.open_incidents ?? 0);

  return (
    <section className="grid">
      <StatusCard title="Agent Runtime" value={summary.agent_status || "unknown"} />
      <StatusCard title="Edge Site" value={summary.site || "edge-site-01"} />
      <StatusCard
        title="Open Incidents"
        value={incidentValue}
        degraded={snowDegraded || kafkaDegraded}
      />
      <StatusCard
        title="MCP Availability"
        value={`${integrations.up || 0}/${integrations.total || 0} up`}
      />
      <StatusCard
        title="ServiceNow"
        value={snowDegraded ? "unavailable" : formatServiceNow(summary.servicenow)}
        degraded={snowDegraded}
      />
      <StatusCard title="Hub Cluster" value={summary.cluster || "hub"} />
    </section>
  );
}
