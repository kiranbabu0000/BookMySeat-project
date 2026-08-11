"""runserver that also runs the email outbox worker in the background.

Booking confirmation emails are enqueued to ``EmailOutbox`` and delivered by the
``process_email_outbox`` worker. This command subclasses Django's runserver and
auto-starts that worker as a daemon thread, so `python manage.py runserver`
alone keeps confirmation emails flowing — no separate terminal or command.

The worker polls the outbox every ``--email-poll`` seconds (default 5) and stops
with the server. Production deployments should keep running the worker as its
own process or cron job (see render.yaml).
"""
import logging
import threading

from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticRunserverCommand,
)
from django.core.management import call_command

logger = logging.getLogger(__name__)

EMAIL_POLL_SECONDS = 5.0


class Command(StaticRunserverCommand):
    help = 'Starts a lightweight development server and the email outbox worker.'

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--email-poll', type=float, default=EMAIL_POLL_SECONDS,
            help='Seconds between email-worker polls (default: %(default)s).',
        )

    def inner_run(self, *args, **options):
        poll = max(0.5, options.get('email_poll', EMAIL_POLL_SECONDS))
        _start_email_worker(sleep=poll)
        self.stdout.write(
            self.style.SUCCESS(
                'Email outbox worker started (polls every {}s).'.format(poll)
            )
        )
        return super().inner_run(*args, **options)


def _email_worker_loop(stop_event, sleep):
    """Drain the email outbox until stop_event is set."""
    while not stop_event.is_set():
        try:
            call_command('process_email_outbox', verbosity=0)
        except Exception:  # noqa: BLE001 - keep the worker alive across crashes
            logger.exception('Email outbox worker crashed; retrying in %.1fs.', sleep)
        stop_event.wait(sleep)


def _start_email_worker(sleep=EMAIL_POLL_SECONDS):
    """Start the email worker as a daemon thread; returns (thread, stop_event).

    Daemon so it never blocks server shutdown. The returned stop_event lets
    tests (and callers) stop the loop cleanly.
    """
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_email_worker_loop,
        args=(stop_event, sleep),
        name='email-outbox-worker',
        daemon=True,
    )
    thread.start()
    return thread, stop_event
