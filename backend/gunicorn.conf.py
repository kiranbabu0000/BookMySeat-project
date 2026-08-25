"""Gunicorn configuration for BookMySeat on Render Free Tier.

Render Free Tier gives 512 MB RAM and one shared vCPU.  Two sync workers
fit comfortably; a third would risk OOM-kills on cold starts when all
workers spin up simultaneously and load Django + its ORM.
"""
import os
import multiprocessing

# Workers ---------------------------------------------------------------
# (2 * cpu_count) + 1 is the standard formula but on Render's shared vCPU
# that gives 3 workers which is tight on 512 MB.  2 workers keep memory
# usage under ~350 MB even under load.
workers = int(os.environ.get('GUNICORN_WORKERS', '2'))

# Worker class -----------------------------------------------------------
# Sync workers are the safest choice for a Django WSGI app with DB-backed
# sessions and blocking email delivery.  No async framework is used.
worker_class = 'sync'

# Timeouts ---------------------------------------------------------------
# Render's reverse-proxy kills idle connections after 30 s.  Keep-alive
# must be shorter so Gunicorn closes the socket before the proxy does,
# preventing broken-pipe noise in logs.
timeout = 30
graceful_timeout = 30
keepalive = 5

# Max-requests recycling -------------------------------------------------
# Periodically restart workers to reclaim any leaked memory.  Jitter
# prevents all workers from restarting at the same instant.
max_requests = 1000
max_requests_jitter = 50

# Logging ----------------------------------------------------------------
accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')

# Server mechanics -------------------------------------------------------
preload_app = False
forwarded_allow_ips = '*'
proxy_protocol = False

# Bind -------------------------------------------------------------------
# Render injects PORT; fall back to 8000 for local testing.
bind = '0.0.0.0:{}'.format(os.environ.get('PORT', '8000'))
