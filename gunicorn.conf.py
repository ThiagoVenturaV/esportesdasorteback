"""
Gunicorn configuration for AWS EC2 (t3.small — 2 vCPUs, 2 GiB RAM).
"""

import multiprocessing
import os

# Bind to localhost; Nginx will reverse-proxy.
bind = "127.0.0.1:8000"

# t3.small has 2 vCPUs → 2*2+1 = 5 workers
workers = int(os.getenv("GUNICORN_WORKERS", min(multiprocessing.cpu_count() * 2 + 1, 5)))
worker_class = "uvicorn.workers.UvicornWorker"

# Timeouts
timeout = 120
graceful_timeout = 30
keepalive = 5

# Logging
accesslog = "/var/log/gunicorn/access.log"
errorlog = "/var/log/gunicorn/error.log"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")

# Security
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190

# Preload app for shared memory (connection pool etc.)
preload_app = True
