"""In-process email outbox drainer for WSGI servers (Gunicorn on Render).

Confirmation/OTP emails are enqueued to ``movies.EmailOutbox`` during requests;
this module delivers them from a background daemon thread so emails are actually
sent without a separate (paid) cron worker. The claim logic in
``process_email_outbox`` flips rows to ``processing`` atomically, so running
this thread in every web worker never delivers the same message twice.
"""
import io
import logging
import threading
import time

logger = logging.getLogger(__name__)

POLL_SECONDS = 30
BATCH_LIMIT = 50
THREAD_NAME = 'bms-email-outbox'


def _drain():
    from movies.management.commands.process_email_outbox import Command

    command = Command(stdout=io.StringIO(), stderr=io.StringIO())
    while True:
        try:
            command._deliver_due(BATCH_LIMIT)
        except Exception as exc:  # noqa: BLE001 - never kill the web process
            logger.warning('email outbox drainer error: %s', exc)
        time.sleep(POLL_SECONDS)


def start_outbox_worker():
    """Start the drainer thread exactly once per process."""
    if any(t.name == THREAD_NAME and t.is_alive() for t in threading.enumerate()):
        return
    thread = threading.Thread(target=_drain, name=THREAD_NAME, daemon=True)
    thread.start()
    logger.info('Email outbox drainer thread started.')
