# Edge Fast-Path Healer

Spoke-local OOM detection and safe nginx restart for the ADNR demo.

Two console scripts share this package:

- `edge-fast-path-watcher` polls pods and POSTs remediation events
- `edge-fast-path-runner` receives events and patches the target Deployment

See `docs/edge-fast-path-healer.md` for the operator runbook, and
`docs/FAST-PATH-HEALER-DEMO-SCRIPT.md` for the spoke OOM demo (heal locally, hub skips AAP).
