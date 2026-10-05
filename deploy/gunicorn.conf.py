"""One-server MVP; only Caddy can reach this loopback listener."""

import os

if os.environ.get("DJANGO_DEBUG") != "0":
    raise RuntimeError("Set DJANGO_DEBUG=0 before starting the production server.")

bind = f"127.0.0.1:{int(os.environ.get('FUINOISE_PORT', '8001'))}"
workers = 1
worker_class = "gthread"
threads = 4
timeout = 120
graceful_timeout = 150
max_requests = 2000
max_requests_jitter = 200
forwarded_allow_ips = "127.0.0.1,::1"
secure_scheme_headers = {"X-FORWARDED-PROTO": "https"}
accesslog = "-"
errorlog = "-"
# Path only: OAuth callback query strings, headers, and request bodies are omitted.
access_log_format = "%(h)s %(m)s %(U)s %(s)s %(L)s"
capture_output = True
