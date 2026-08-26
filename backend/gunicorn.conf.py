"""Gunicorn configuration for BookMySeat on Render Free Tier.

Render Free Tier gives 512 MB RAM and one shared vCPU.  A single worker
with two threads keeps memory under ~200 MB even under load, leaving
headroom for the OS, database connections, WhiteNoise, and the email
outbox daemon thread.  Two sync workers caused OOM on 512 MB instances.
"""
import os

# Workers ---------------------------------------------------------------
# 1 worker + 2 threads is the most memory-efficient safe configuration
# for Django on a 512 MB instance.  Django is thread-safe; the ORM,
# WhiteNoise, and the email outbox worker all handle concurrency correctly.
# Two sync workers doubled memory usage to ~350-400 MB, leaving almost no
# headroom and triggering OOM kills on cold starts.
workers = 1
threads = int(os.environ.get('GUNICORN_THREADS', '2'))

# Worker class -----------------------------------------------------------
# Threaded worker allows handling 2 concurrent requests per worker.
# Sync workers are still the base; threads provide concurrency within
# the single worker process without the memory overhead of multiple
# pre-forked processes.
worker_class = 'threaded'

# Timeouts ---------------------------------------------------------------
# Render's reverse proxy kills idle connections after ~30 s.  The Django
# timeout is generous (120 s) to accommodate heavy admin views (dashboard,
# movie removal analytics) that run multiple aggregation queries.  A 30 s
# timeout caused worker timeouts on those pages, producing 502 errors.
timeout = 120
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
