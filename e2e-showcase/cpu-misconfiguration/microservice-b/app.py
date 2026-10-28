import logging
import os
import time

from flask import Flask, jsonify, request
from pythonjsonlogger.json import JsonFormatter

app = Flask(__name__)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger("compute-service")

PORT = int(os.environ.get("PORT", "8080"))


def check_prime(n):
    if n < 2:
        return False
    for i in range(2, int(n**0.5) + 1):
        if n % i == 0:
            return False
    return True


def count_primes(n):
    start_time = time.time()
    prime_count = 0

    for num in range(3, n + 1):
        if check_prime(num):
            prime_count += 1

    duration = time.time() - start_time
    return prime_count, duration


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "service": "compute-service",
        "timestamp": time.time(),
    }), 200


@app.route("/process", methods=["POST"])
def process():
    start_time = time.time()

    try:
        data = request.get_json() or {}
        n = data.get("n", 30000)

        logger.info(
            f"Processing request - counting primes up to {n}",
            extra={"n": n},
        )

        prime_count, cpu_duration = count_primes(n)

        duration = time.time() - start_time
        logger.info(
            f"Request completed in {duration:.2f}s",
            extra={"n": n, "prime_count": prime_count, "processing_time": cpu_duration},
        )

        return jsonify({
            "status": "success",
            "message": "Processing completed",
            "processing_time": cpu_duration,
            "prime_count": prime_count,
        }), 200

    except Exception as e:
        logger.error(f"Error processing request: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.errorhandler(404)
def not_found(error):
    return jsonify({"status": "error", "message": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal error: {error}")
    return jsonify({"status": "error", "message": "Internal server error"}), 500


if __name__ == "__main__":
    logger.info(f"Starting Compute Service on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
