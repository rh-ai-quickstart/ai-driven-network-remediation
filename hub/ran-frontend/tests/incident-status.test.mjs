import assert from "node:assert/strict";
import { test } from "node:test";
import { renderIncidentCard } from "./render-incident-card.mjs";

test("classification failure appears on its incident card without a job", () => {
  const html = renderIncidentCard({
    incident_id: "failed-001",
    zone: "A",
    application: "Twitch",
    ad_confidence: 0.94,
    incident_status: {
      stage: "rca_classification",
      outcome: "failed",
      error_reason: "invalid RCA class index",
      timestamp: "2026-10-22T10:30:00+00:00",
    },
  });

  assert.match(html, /Incident failed-001/);
  assert.match(html, /rca_classification: invalid RCA class index/);
  assert.doesNotMatch(html, /Job outcome:|ML category:|128×18 KPI window/);
});

test("LLM failure and completed cell recovery share one incident card", () => {
  const html = renderIncidentCard({
    incident_id: "failed-llm-001", zone: "A", application: "Twitch", ad_confidence: 0.94,
    ml_class: "Antenna Failure", ml_confidence: 0.9, root_cause_category: "cell_failure",
    root_cause: "", recommended_fix: "",
    remediation_status: "completed", remediation_template_name: "ran-cell-recovery", remediation_job_status: "successful",
    incident_status: { stage: "rca_explanation", outcome: "failed", error_reason: "LLM unavailable", timestamp: "2026-10-22T10:30:00+00:00" },
  });

  assert.match(html, /rca_explanation: LLM unavailable/);
  assert.match(html, /ML category: Cell Failure/);
  assert.match(html, /Job: ran-cell-recovery/);
  assert.match(html, /Job outcome: successful/);
  assert.match(html, /LLM Explanation<\/span><p>unavailable<\/p>/);
  assert.match(html, /Recommended Fix<\/span><p>unavailable<\/p>/);
});

test("RF filter fault card shows the simulated job result under its incident ID", () => {
  const html = renderIncidentCard({
    incident_id: "rf-filter-001", zone: "A", application: "File", ad_confidence: 0.94,
    ml_class: "Faulty RF Filters (Temporal)", ml_confidence: 0.87,
    root_cause_category: "rf_filter_fault", remediation_status: "completed",
    remediation_template_name: "ran-rf-filter-response", remediation_job_status: "successful",
    remediation_job_id: "42",
    remediation_output_summary: "RF filter response simulation successful; no RAN device changed",
  });

  assert.match(html, /Incident rf-filter-001/);
  assert.match(html, /Mantis RCA: Faulty RF Filters \(Temporal\)/);
  assert.match(html, /ML category: RF Filter Fault/);
  assert.match(html, /Job: ran-rf-filter-response/);
  assert.match(html, /Job outcome: successful/);
  assert.match(html, /RF filter response simulation successful; no RAN device changed/);
});

for (const label of ["High Network Congestion (Gradual Buildup)", "High Network Congestion (Sudden Spike)"]) {
  test(`${label} card keeps its model label and shows the shared load-balance result`, () => {
    const html = renderIncidentCard({
      incident_id: "congestion-001", zone: "A", application: "File", ad_confidence: 0.94,
      ml_class: label, ml_confidence: 0.9, root_cause_category: "congestion",
      remediation_status: "completed", remediation_template_name: "ran-load-balance",
      remediation_job_id: "42", remediation_job_status: "successful",
      remediation_output_summary: "Load balancing simulation successful; no RAN device changed",
    });

    assert.match(html, new RegExp(`Mantis RCA: ${label.replace(/[()]/g, "\\$&")}`));
    assert.match(html, /ML category: Congestion/);
    assert.match(html, /Job: ran-load-balance/);
    assert.match(html, /Job outcome: successful/);
    assert.match(html, /Load balancing simulation successful; no RAN device changed/);
  });
}

test("AAP failure shows attempted template, job, stage, reason, and time", () => {
  const html = renderIncidentCard({
    incident_id: "failed-aap-001", zone: "A", application: "Twitch", ad_confidence: 0.94,
    ml_class: "Antenna Failure", root_cause_category: "cell_failure",
    remediation_status: "failed", remediation_template_name: "ran-cell-recovery",
    remediation_job_id: "42", remediation_job_status: "failed",
    remediation_failure_stage: "aap_job", remediation_failure_reason: "Simulated AAP job failure",
    remediation_timestamp: "2026-10-22T10:30:00+00:00",
  });

  assert.match(html, /ML category: Cell Failure/);
  assert.match(html, /Job: ran-cell-recovery/);
  assert.match(html, /Job outcome: failed/);
  assert.match(html, /Job ID: 42/);
  assert.match(html, /aap_job: Simulated AAP job failure/);
  assert.match(html, /Remediation failed/);
});
