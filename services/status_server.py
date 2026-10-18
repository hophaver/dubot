"""HTTP server on port 3000: GET /status returns live bot + host status (used by the home dashboard)."""
import http.server
import json
import math
import socket
import threading
import time

import psutil

from utils.paths import root_path
from utils.system_monitor import format_uptime

PORT = 3000
# Snapshot still written by the /status command; the HTTP endpoint is live and no longer reads it.
STATUS_JSON_PATH = root_path("web", "status.json")
_client = None


def _live_status():
    """Build the status payload from the running client. Cheap: no blocking CPU sampling."""
    from config import is_bot_awake
    from integrations import PERMANENT_ADMIN
    from models import model_manager

    client = _client
    online = bool(client and client.is_ready() and not client.is_closed())
    latency = getattr(client, "latency", None) if client else None
    bot_uptime_sec = time.time() - client.start_time if client else 0
    model_info = model_manager.get_user_model_info(PERMANENT_ADMIN)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {
        "online": online,
        "awake": is_bot_awake(),
        "latency_ms": round(latency * 1000) if latency is not None and math.isfinite(latency) else None,
        "guilds": len(client.guilds) if online else None,
        "user": str(client.user) if online and client.user else None,
        "bot_uptime_sec": round(bot_uptime_sec, 1),
        "bot_uptime": format_uptime(bot_uptime_sec),
        "model": f"{model_info.get('model', 'default')} ({model_info.get('provider', 'local')})",
        "system": {
            "hostname": socket.gethostname(),
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_used": memory.used // (1024 ** 2),
            "memory_total": memory.total // (1024 ** 2),
            "memory_percent": memory.percent,
            "disk_percent": disk.percent,
            "uptime": format_uptime(time.time() - psutil.boot_time()),
        },
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


class StatusHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/status":
            try:
                self._send(200, _live_status())
            except Exception as e:
                self._send(500, {"error": str(e), "online": False})
        else:
            self._send(404, {"error": "Not Found"})

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # quiet by default


class _ReusableHTTPServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_status_server(client=None):
    """Start the status HTTP server in a daemon thread. `client` is the discord client to report on."""
    global _client
    _client = client
    psutil.cpu_percent(interval=None)  # prime the counter so the first reading is meaningful
    try:
        server = _ReusableHTTPServer(("", PORT), StatusHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"Status server: http://localhost:{PORT}/status")
    except OSError as e:
        print(f"⚠️ Status server failed to start (port {PORT} in use?): {e}")
