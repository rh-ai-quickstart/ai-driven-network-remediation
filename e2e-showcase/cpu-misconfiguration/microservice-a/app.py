import logging
import os
import threading
import time
from collections import deque

import requests
import urllib3
from flask import Flask, jsonify, request
from pythonjsonlogger.json import JsonFormatter

app = Flask(__name__)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger("queue-service")

MICROSERVICE_B_URL = os.environ.get("MICROSERVICE_B_URL", "http://microservice-b:8080")
MICROSERVICE_B_SITE_ID = os.environ.get("MICROSERVICE_B_SITE_ID", "")
MICROSERVICE_B_VERIFY_SSL = os.environ.get("MICROSERVICE_B_VERIFY_SSL", "false").lower() == "true"
PORT = int(os.environ.get("PORT", "8080"))
MAX_QUEUE_SIZE = int(os.environ.get("MAX_QUEUE_SIZE", "100"))

if not MICROSERVICE_B_VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Suffix naming microservice-b's cluster so forwarded logs let the agent retarget
# remediation to the right spoke (e.g. "on edge-site-02").
_SITE_SUFFIX = f" on {MICROSERVICE_B_SITE_ID}" if MICROSERVICE_B_SITE_ID else ""

message_queue = deque()
queue_lock = threading.Lock()


def process_queue_worker():
    logger.info("Queue worker thread started")

    while True:
        item = None

        with queue_lock:
            if len(message_queue) > 0:
                item = message_queue.popleft()

        if item:
            queue_depth = len(message_queue)
            try:
                logger.info(
                    "Processing message from queue",
                    extra={"queue_depth": queue_depth},
                )

                start_time = time.time()
                response = requests.post(
                    f"{MICROSERVICE_B_URL}/process",
                    json={"data": item["message"], "n": item["n"]},
                    timeout=120,
                    verify=MICROSERVICE_B_VERIFY_SSL,
                )
                latency = time.time() - start_time

                if response.status_code == 200:
                    if latency > 0.8:
                        logger.warning(
                            f"microservice-b latency is {latency:.2f}s{_SITE_SUFFIX} — "
                            "microservice-b response time is degraded, possible CPU contention or resource exhaustion",
                            extra={"queue_depth": queue_depth, "latency": latency},
                        )
                    else:
                        logger.info(
                            f"microservice-b compute primes latency: {latency:.2f}s",
                            extra={"queue_depth": queue_depth, "latency": latency},
                        )
                else:
                    logger.warning(
                        f"Microservice B returned status {response.status_code}",
                        extra={"queue_depth": queue_depth},
                    )

            except requests.exceptions.Timeout:
                logger.error(
                    f"Timeout calling Microservice B{_SITE_SUFFIX} (exceeded 120s)",
                    extra={"queue_depth": queue_depth},
                )
            except requests.exceptions.ConnectionError:
                logger.error(
                    f"Connection error calling Microservice B at {MICROSERVICE_B_URL}{_SITE_SUFFIX}",
                    extra={"queue_depth": queue_depth},
                )
                time.sleep(1)
            except Exception as e:
                logger.error(
                    f"Error processing message: {e}",
                    extra={"queue_depth": queue_depth},
                )
        else:
            time.sleep(0.1)


@app.route("/health", methods=["GET"])
def health():
    with queue_lock:
        queue_size = len(message_queue)

    return jsonify({
        "status": "healthy",
        "service": "queue-service",
        "queue_depth": queue_size,
        "timestamp": time.time(),
    }), 200


@app.route("/enqueue", methods=["POST"])
def enqueue():
    try:
        data = request.get_json() or {}
        message = data.get("message", f"message_{int(time.time())}")
        n = data.get("n", 30000)

        with queue_lock:
            current_queue_size = len(message_queue)

            if current_queue_size >= MAX_QUEUE_SIZE:
                logger.warning(
                    f"Queue is full ({current_queue_size}/{MAX_QUEUE_SIZE})",
                    extra={"queue_depth": current_queue_size},
                )
                return jsonify({
                    "status": "error",
                    "message": "Queue is full",
                    "queue_depth": current_queue_size,
                    "max_queue_size": MAX_QUEUE_SIZE,
                }), 503

            message_queue.append({"message": message, "n": n})
            new_queue_size = len(message_queue)

        logger.info(
            f"Message enqueued. Queue depth: {new_queue_size}",
            extra={"queue_depth": new_queue_size},
        )

        return jsonify({
            "status": "success",
            "message": "Message enqueued",
            "queue_depth": new_queue_size,
        }), 200

    except Exception as e:
        logger.error(f"Error enqueueing message: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/queue/status", methods=["GET"])
def queue_status():
    with queue_lock:
        queue_size = len(message_queue)

    return jsonify({
        "queue_depth": queue_size,
        "max_queue_size": MAX_QUEUE_SIZE,
        "microservice_b_url": MICROSERVICE_B_URL,
    }), 200


@app.errorhandler(404)
def not_found(error):
    return jsonify({"status": "error", "message": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal error: {error}")
    return jsonify({"status": "error", "message": "Internal server error"}), 500


if __name__ == "__main__":
    logger.info(
        f"Starting Queue Service on port {PORT}",
        extra={"microservice_b_url": MICROSERVICE_B_URL, "max_queue_size": MAX_QUEUE_SIZE},
    )

    worker_thread = threading.Thread(target=process_queue_worker, daemon=True)
    worker_thread.start()

    app.run(host="0.0.0.0", port=PORT, debug=False)
