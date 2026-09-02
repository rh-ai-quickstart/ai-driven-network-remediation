import { useState } from "react";

const SCENARIOS = [
  { id: "antenna_failure", label: "Antenna Failure" },
  { id: "high_congestion_sudden", label: "High Congestion (Sudden)" },
  { id: "co_channel_interference_severe", label: "Co-Channel Interference" },
  { id: "doppler_shift_severe", label: "Doppler Shift (Severe)" },
  { id: "normal_traffic", label: "Normal Traffic (No Anomaly)" },
];

export function DemoTrigger({ baseUrl, onTriggered }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  async function trigger(scenario) {
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const url = baseUrl ? `${baseUrl}/api/demo/trigger` : "/api/demo/trigger";
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.error || body.detail || body.message || `Demo trigger failed (${res.status})`);
      }
      setResult(body);
      onTriggered?.();
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
        Inject a public 5G lab trace (TelecomTS) into the live ML detection
        pipeline and watch it get diagnosed.
      </p>
      <div className="demo-actions">
        {SCENARIOS.map((s) => (
          <button key={s.id} type="button" disabled={loading} onClick={() => trigger(s.id)}>
            {s.label}
          </button>
        ))}
      </div>
      {error && <p className="demo-error">{error}</p>}
      {result && (
        <div className="demo-result">
          <p>
            <strong>Incident:</strong> <code>{result.incident_id}</code> ·
            Scenario: <code>{result.scenario}</code>
          </p>
          <p>
            <strong>Topic:</strong> <code>{result.topic}</code> · Offset:{" "}
            <code>{result.kafka_offset}</code>
          </p>
          <p>
            Ask the chat about incident {result.incident_id} in about 15-20
            seconds — it'll show up in the anomaly table once root cause
            analysis finishes.
          </p>
        </div>
      )}
    </section>
  );
}
