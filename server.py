from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import hashlib
import hmac
import json
import os
import re
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
FORCE_HTTPS = os.getenv("FORCE_HTTPS", "0").lower() in {"1", "true", "yes"}
DASHBOARD_AUTH_TOKEN = os.getenv("DASHBOARD_AUTH_TOKEN", "").strip()
ALLOWED_ORIGINS = {
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,https://localhost:8000").split(",")
    if origin.strip()
}
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 60
RATE_LIMIT_BUCKETS = {}

METRICS = {
    "overview": {
        "guilds": 58,
        "members": 4821,
        "uptime": "12h 44m",
        "latency": "41ms",
        "status": "System nominal",
        "chart": [42, 64, 58, 75, 82, 76, 90, 87],
        "modules": [
            {"name": "Support", "status": "Healthy", "load": 92, "color": "#67e8f9"},
            {"name": "Security", "status": "Protected", "load": 89, "color": "#34d399"},
            {"name": "Games", "status": "Online", "load": 74, "color": "#8b5cf6"},
            {"name": "Economy", "status": "Stable", "load": 81, "color": "#fbbf24"},
            {"name": "AI", "status": "Ready", "load": 68, "color": "#fb7185"}
        ]
    },
    "security": {
        "guilds": 58,
        "members": 4821,
        "uptime": "12h 44m",
        "latency": "38ms",
        "status": "Threat scan clear",
        "chart": [60, 66, 72, 68, 83, 89, 91, 97],
        "modules": [
            {"name": "Anti-nuke", "status": "Armed", "load": 96, "color": "#34d399"},
            {"name": "Audit", "status": "Running", "load": 88, "color": "#67e8f9"},
            {"name": "Lockdown", "status": "Standby", "load": 42, "color": "#fbbf24"},
            {"name": "Whitelist", "status": "Synced", "load": 80, "color": "#8b5cf6"}
        ]
    },
    "support": {
        "guilds": 58,
        "members": 4821,
        "uptime": "12h 44m",
        "latency": "46ms",
        "status": "Ticket flow steady",
        "chart": [54, 59, 63, 78, 80, 85, 92, 88],
        "modules": [
            {"name": "Tickets", "status": "Balanced", "load": 91, "color": "#67e8f9"},
            {"name": "Claims", "status": "Open", "load": 78, "color": "#8b5cf6"},
            {"name": "Logs", "status": "Archive", "load": 83, "color": "#34d399"},
            {"name": "Mediation", "status": "High", "load": 74, "color": "#fbbf24"}
        ]
    },
    "games": {
        "guilds": 58,
        "members": 4821,
        "uptime": "12h 44m",
        "latency": "54ms",
        "status": "Game ring active",
        "chart": [48, 57, 61, 56, 68, 74, 72, 77],
        "modules": [
            {"name": "Games", "status": "Live", "load": 86, "color": "#8b5cf6"},
            {"name": "Hangman", "status": "Ready", "load": 70, "color": "#67e8f9"},
            {"name": "Trivia", "status": "Queued", "load": 64, "color": "#34d399"},
            {"name": "Roulette", "status": "Cool", "load": 51, "color": "#fb7185"}
        ]
    },
    "economy": {
        "guilds": 58,
        "members": 4821,
        "uptime": "12h 44m",
        "latency": "43ms",
        "status": "Economy stable",
        "chart": [50, 60, 66, 73, 80, 76, 87, 94],
        "modules": [
            {"name": "Shop", "status": "Open", "load": 82, "color": "#fbbf24"},
            {"name": "XP", "status": "Engaged", "load": 87, "color": "#67e8f9"},
            {"name": "Daily", "status": "Awarding", "load": 76, "color": "#34d399"},
            {"name": "Leaderboard", "status": "Live", "load": 91, "color": "#8b5cf6"}
        ]
    }
}


def sanitize_text(value, max_length=200):
    if value is None:
        return ""
    cleaned = str(value)
    cleaned = re.sub(r"[\x00-\x1F\x7F]", "", cleaned)
    cleaned = cleaned.strip()
    return cleaned[:max_length]


def trim_api_response(data):
    if isinstance(data, dict):
        return {key: trim_api_response(value) for key, value in data.items() if key not in {"password", "token", "secret", "api_key", "authorization"}}
    if isinstance(data, list):
        return [trim_api_response(item) for item in data]
    if isinstance(data, str):
        return sanitize_text(data, 4000)
    return data


def check_rate_limit(client_ip):
    now = time.time()
    bucket = RATE_LIMIT_BUCKETS.setdefault(client_ip, [])
    bucket[:] = [stamp for stamp in bucket if now - stamp < RATE_LIMIT_WINDOW]
    if len(bucket) >= RATE_LIMIT_MAX:
        return False
    bucket.append(now)
    return True


def set_security_headers(handler):
    handler.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: https:; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; connect-src 'self' http://localhost:8000 https://localhost:8000; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none';")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
    handler.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    handler.send_header("Cache-Control", "no-store")
    if FORCE_HTTPS:
        handler.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Dashboard-Token")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def is_valid_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        if origin in ALLOWED_ORIGINS:
            return True
        if not ALLOWED_ORIGINS:
            return origin.startswith("http://localhost") or origin.startswith("https://localhost")
        return False

    def enforce_dashboard_auth(self):
        if not DASHBOARD_AUTH_TOKEN:
            return True
        provided = self.headers.get("Authorization", "").strip()
        if provided.startswith("Bearer "):
            token = provided.split(" ", 1)[1].strip()
        else:
            token = self.headers.get("X-Dashboard-Token", "").strip()
        if not hmac.compare_digest(token, DASHBOARD_AUTH_TOKEN):
            return False
        return True

    def ensure_https(self):
        if not FORCE_HTTPS:
            return True
        if self.headers.get("X-Forwarded-Proto") == "https":
            return True
        if self.server.server_address[0] in {"127.0.0.1", "::1", "localhost"}:
            return True
        if self.headers.get("Host", "").startswith("localhost"):
            return True
        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        client_ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
        if not check_rate_limit(client_ip):
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            set_security_headers(self)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Rate limit exceeded."}).encode("utf-8"))
            return

        if not self.is_valid_origin():
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            set_security_headers(self)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Forbidden origin."}).encode("utf-8"))
            return

        if not self.ensure_https():
            self.send_response(301)
            self.send_header("Location", f"https://{self.headers.get('Host', 'localhost')}{self.path}")
            set_security_headers(self)
            self.end_headers()
            return

        if parsed.path == "/api/metrics":
            if not self.enforce_dashboard_auth():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                set_security_headers(self)
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Authentication required."}).encode("utf-8"))
                return
            sanitized = trim_api_response(METRICS)
            payload = json.dumps(sanitized, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            set_security_headers(self)
            self.end_headers()
            self.wfile.write(payload)
            return

        if parsed.path in {"/", "/index.html"}:
            self.path = "/dashboard.html"
        elif not re.fullmatch(r"/[A-Za-z0-9._/-]+", parsed.path):
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            set_security_headers(self)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Invalid path."}).encode("utf-8"))
            return
        return super().do_GET()


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    with ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler) as httpd:
        print(f"Dashboard running at http://localhost:{port}/dashboard.html")
        httpd.serve_forever()
