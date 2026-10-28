import { useState } from "react";

const SCENARIOS = [
  { id: "crashloop", label: "Trigger CrashLoop Demo" },
  { id: "oom", label: "Trigger OOM Demo" },
  { id: "lightspeed", label: "Trigger Lightspeed Demo" },
  { id: "escalation", label: "Trigger Escalation Demo" },
  { id: "cpu-misconfiguration", label: "Trigger CPU Misconfiguration Demo" },
];
const CPU_DEMO_SCENARIO = "cpu-misconfiguration";
const CPU_DEMO_SUMMARY =
  "microservice-a request queue is backing up; incident investigation is queued.";

export function DemoTrigger({ baseUrl }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  async function trigger(scenario) {
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const url = baseUrl
        ? `${baseUrl}/api/demo/trigger`
        : "/api/demo/trigger";
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(
          body.detail || body.message || `Demo trigger failed (${res.status})`
        );
      }
      const data = await res.json();
      setResult(data);
    } catch (err) {
      setError(err.message || "Demo trigger failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="panel">
      <h2>Demo Mode</h2>
      <p className="meta">
        Run a controlled E2E simulation and follow results across AAP,
        ServiceNow, Slack, and Langfuse.
      </p>
      <div className="demo-actions">
        {SCENARIOS.map((s) => (
          <button
            key={s.id}
            disabled={loading}
            onClick={() => trigger(s.id)}
          >
            {s.label}
          </button>
        ))}
      </div>
      {error && <p className="demo-error">{error}</p>}
      {result && (
        <div className="demo-result">
          <p>
            <strong>Incident ID:</strong>{" "}
            <code>{result.incident_id || "n/a"}</code>
          </p>
          <p>
            <strong>Scenario:</strong> <code>{result.scenario}</code> · Topic:{" "}
            <code>{result.topic}</code> · Offset:{" "}
            <code>{result.kafka_offset}</code>
          </p>
          <p>
            <strong>Target:</strong>{" "}
            <code>{result.site} / {result.namespace} / {result.workload}</code>
          </p>
          {result.event_message && (
            <p>
              <strong>{result.scenario === CPU_DEMO_SCENARIO ? "Summary:" : "Event:"}</strong>{" "}
              {result.scenario === CPU_DEMO_SCENARIO
                ? CPU_DEMO_SUMMARY
                : result.event_message}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
