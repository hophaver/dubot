"""HTTP server on port 3000 that serves GET /status from web/status.json."""
import http.server
import json
import os
import threading

PORT = 3000
STATUS_JSON_PATH = os.path.join("web", "status.json")


class StatusHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.rstrip("/") or "/"
        if path == "/status":
            self._serve_status()
        else:
            self.send_error(404, "Not Found")

    def _serve_status(self):
        try:
            if os.path.isfile(STATUS_JSON_PATH):
                with open(STATUS_JSON_PATH, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                body = json.dumps({"error": "Status not yet generated. Run /status once."}).encode()
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # quiet by default


def start_status_server():
    """Start the status HTTP server in a daemon thread."""
    server = http.server.HTTPServer(("", PORT), StatusHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Status server: http://localhost:{PORT}/status")
