import json

from edge_fast_path_healer.logging_util import log_event


def test_log_event_writes_json(capsys):
    log_event("runner", action="restart", result="success", site_id="edge-01")
    line = capsys.readouterr().out.strip()
    payload = json.loads(line)
    assert payload["component"] == "runner"
    assert payload["action"] == "restart"
    assert payload["result"] == "success"
    assert payload["site_id"] == "edge-01"
    assert "timestamp" in payload
