# gunicorn.conf.py - Gunicorn Configuration for Emergency NLP System

import os

# Server socket
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
backlog = 2048

# Worker processes
workers = int(os.environ.get('WEB_CONCURRENCY', 2))
worker_class = "sync"
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 100

# Timeout settings
timeout = 120
keepalive = 2
graceful_timeout = 30

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "Lomba"

# Server mechanics
daemon = False
pidfile = None
user = None
group = None
tmp_upload_dir = None

# SSL (if needed)
keyfile = None
certfile = None

# Application
preload_app = True
enable_stdio_inheritance = True

# Worker process lifecycle
def on_starting(server):
    server.log.info("Emergency NLP System starting up")

def on_reload(server):
    server.log.info("Emergency NLP System reloading")

def when_ready(server):
    server.log.info("Emergency NLP System ready to serve requests")

def on_exit(server):
    server.log.info("Emergency NLP System shutting down")

def worker_exit(server, worker):
    server.log.info("Worker %s exited", worker.pid)