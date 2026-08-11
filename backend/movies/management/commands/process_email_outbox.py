"""Drain the database-backed email outbox.

Confirmation emails are enqueued asynchronously during booking; this command is
the worker that actually delivers them. Run it as a cron job (single run) or as
a persistent process (``--loop``). Failed deliveries are retried automatically
with exponential backoff up to ``EMAIL_OUTBOX_MAX_ATTEMPTS``.

Examples:
    python manage.py process_email_outbox            # send everything due now
    python manage.py process_email_outbox --loop      # run as a worker forever
    python manage.py process_email_outbox --limit 50  # cap deliveries per run
"""
import time
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from movies.models import EmailOutbox
from movies.notifications import backoff_delay, send_outbox_message


class Command(BaseCommand):
    help = 'Deliver pending emails from the async outbox with automatic retries.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--loop', action='store_true',
            help='Run continuously as a worker (Ctrl+C to stop).',
        )
        parser.add_argument(
            '--limit', type=int, default=100,
            help='Maximum messages to attempt per run (default: 100).',
        )
        parser.add_argument(
            '--sleep', type=float, default=5.0,
            help='Seconds between polls in --loop mode (default: 5).',
        )

    def handle(self, *args, **options):
        loop = options['loop']
        limit = max(1, options['limit'])
        sleep = max(0.1, options['sleep'])
        verbosity = options.get('verbosity', 1)
        if verbosity > 0:
            self.stdout.write(
                self.style.SUCCESS('Email outbox worker started (loop={}, limit={}).'.format(
                    loop, limit))
            )
        try:
            while True:
                sent, failed = self._deliver_due(limit)
                if sent or failed:
                    self.stdout.write(
                        'Delivered {} message(s), {} failure(s).'.format(sent, failed)
                    )
                if not loop:
                    break
                time.sleep(sleep)
        except KeyboardInterrupt:
            if verbosity > 0:
                self.stdout.write(self.style.WARNING('\nWorker stopped.'))

    def _claim_due(self, limit, now):
        """Atomically claim up to ``limit`` due messages and return their pks.

        Pending rows are flipped to ``processing`` so concurrent workers never
        deliver the same message twice. Rows stuck in ``processing`` for a long
        time (e.g. a crashed worker) are reclaimed automatically.
        """
        stuck_before = now - timedelta(
            seconds=getattr(settings, 'EMAIL_OUTBOX_CLAIM_MAX_AGE', 600)
        )
        due = EmailOutbox.objects.filter(
            status='pending',
            next_attempt_at__lte=now,
        )
        stuck = EmailOutbox.objects.filter(
            status='processing',
            locked_at__isnull=False,
            locked_at__lte=stuck_before,
        )
        ids = list(due.values_list('pk', flat=True)[:limit]) + \
            list(stuck.values_list('pk', flat=True)[:limit])
        ids = list(dict.fromkeys(ids))[:limit]
        if not ids:
            return []
        EmailOutbox.objects.filter(pk__in=ids).update(
            status='processing', locked_at=now
        )
        return ids

    def _deliver_due(self, limit):
        now = timezone.now()
        ids = []
        try:
            with transaction.atomic():
                ids = self._claim_due(limit, now)
        except Exception as exc:  # noqa: BLE001 - keep the worker alive
            self.stderr.write(self.style.ERROR(
                'Could not claim outbox rows: {}'.format(exc)))
            return 0, 0

        sent = failed = 0
        for pk in ids:
            try:
                outbox = EmailOutbox.objects.get(pk=pk)
            except EmailOutbox.DoesNotExist:
                continue
            try:
                ok = send_outbox_message(outbox)
            except Exception:  # noqa: BLE001 - defensive, send() records errors itself
                ok = False
            attempts = outbox.attempts + 1
            max_attempts = outbox.max_attempts or getattr(
                settings, 'EMAIL_OUTBOX_MAX_ATTEMPTS', 6
            )
            if ok:
                EmailOutbox.objects.filter(pk=pk).update(
                    status='sent',
                    attempts=attempts,
                    sent_at=timezone.now(),
                    last_error='',
                    locked_at=None,
                )
                sent += 1
            elif attempts >= max_attempts:
                EmailOutbox.objects.filter(pk=pk).update(
                    status='failed',
                    attempts=attempts,
                    next_attempt_at=None,
                    locked_at=None,
                )
                failed += 1
                self.stderr.write(self.style.ERROR(
                    'Email to {} permanently failed after {} attempt(s): {}'.format(
                        outbox.recipient, attempts, outbox.last_error or 'unknown error')
                ))
            else:
                next_at = timezone.now() + timedelta(
                    seconds=backoff_delay(attempts)
                )
                EmailOutbox.objects.filter(pk=pk).update(
                    status='pending',
                    attempts=attempts,
                    next_attempt_at=next_at,
                    locked_at=None,
                )
        return sent, failed
