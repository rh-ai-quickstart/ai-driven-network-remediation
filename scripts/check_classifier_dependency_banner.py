"""Live local RCA -> BFF -> React banner smoke check.

Requires the ran-rca-service and ran-chatbot-service dev virtualenvs and
ran-frontend node_modules. Run from the repo root with
`python3 scripts/check_classifier_dependency_banner.py`.
"""

import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Predictor(BaseHTTPRequestHandler):
    ready = False

    def do_GET(self):
        ready = self.ready and self.path == "/ready"
        body = json.dumps({"ready": ready, "task": "classify"}).encode()
        self.send_response(200 if ready else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def wait_for_service(port):
    for _ in range(80):
        try:
            return get(port, "/health")
        except (OSError, TimeoutError):
            time.sleep(0.1)
    raise RuntimeError(f"Service on port {port} did not start")


def start(component, app, port, extra):
    component_dir = ROOT / "hub" / component
    env = dict(os.environ, PYTHONPATH=f"{component_dir / 'src'}:{ROOT / 'hub/shared/src'}", **extra)
    return subprocess.Popen(
        [str(component_dir / ".venv/bin/python"), "-m", "uvicorn", app, "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def render(deps):
    script = ROOT / "hub/ran-frontend/tests/render-dependency-banner.mjs"
    return subprocess.run(
        ["node", str(script)], input=json.dumps(deps), text=True, capture_output=True, check=True,
    ).stdout


def main():
    predictor_port, rca_port, bff_port = free_port(), free_port(), free_port()
    predictor = ThreadingHTTPServer(("127.0.0.1", predictor_port), Predictor)
    threading.Thread(target=predictor.serve_forever, daemon=True).start()
    processes = []
    try:
        processes.append(start("ran-rca-service", "ran_rca_service.server:app", rca_port, {
            "KAFKA_CONSUMER_ENABLED": "false",
            "CLASSIFY_INFERENCE_URL": f"http://127.0.0.1:{predictor_port}/v1/classify",
        }))
        processes.append(start("ran-chatbot-service", "ran_chatbot_service:app", bff_port, {
            "KAFKA_BOOTSTRAP": "127.0.0.1:1",
            "RCA_READY_URL": f"http://127.0.0.1:{rca_port}/ready",
        }))
        wait_for_service(rca_port)
        wait_for_service(bff_port)

        down_rca = get(rca_port, "/ready")
        down_deps = get(bff_port, "/api/anomalies")[1]
        Predictor.ready = True
        up_rca = get(rca_port, "/ready")
        up_deps = get(bff_port, "/api/anomalies")[1]

        assert down_rca == (503, {"ready": False, "reason": "classifier"}), down_rca
        assert up_rca == (200, {"ready": True}), up_rca
        assert "rca_service" in down_deps["_deps"]["unavailable"]
        assert "rca_service" not in up_deps["_deps"].get("unavailable", [])
        assert down_deps["anomalies"] == up_deps["anomalies"] == []
        assert 'role="alert"' in render(down_deps["_deps"])
        assert "rca_service" in render(down_deps["_deps"])
        assert "rca_service" not in render(up_deps["_deps"])
        print("PASS: live RCA outage reaches the banner and clears after recovery; no incident status event")
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        predictor.shutdown()


if __name__ == "__main__":
    main()
