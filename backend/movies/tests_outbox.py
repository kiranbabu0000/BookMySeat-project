"""Async email outbox tests: non-blocking enqueue + worker delivery/retry.

The booking flow must never wait for SMTP — confirmation emails are persisted
to ``EmailOutbox`` and delivered by ``process_email_outbox``. These tests cover
the enqueue step, worker delivery, exponential-backoff retries, permanent
failure after ``max_attempts``, reclaiming messages stuck mid-delivery, and the
``runserver`` auto-start of the worker.
"""
import time
from datetime import timedelta

from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from admin_panel.models import Notification

from .models import EmailOutbox
from .notifications import backoff_delay
from .testutils import DEMO_RAZORPAY
from .tests import _make_categories_and_prices, _make_show
from django.contrib.auth.models import User


class FailingEmailBackend(BaseEmailBackend):
    """Simulates a down SMTP server so delivery fails."""

    def send_messages(self, email_messages):
        raise OSError('SMTP connection refused')


def _make_message(max_attempts=6):
    return EmailOutbox.objects.create(
        recipient='patron@example.com',
        subject='Booking confirmed — Test Movie',
        plain_body='Hi,\nYour booking is confirmed.',
        html_body='<p>Hi,<br>Your booking is confirmed.</p>',
        pdf_filename='ticket_BMSTEST.pdf',
        pdf_attachment=b'%PDF-1.4-test-bytes',
        max_attempts=max_attempts,
        next_attempt_at=timezone.now() - timedelta(minutes=1),
    )


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailOutboxWorkerTests(TestCase):

    def test_backoff_delay_is_exponential_and_capped(self):
        self.assertEqual(backoff_delay(0), 60)
        self.assertEqual(backoff_delay(1), 60)
        self.assertEqual(backoff_delay(2), 120)
        self.assertEqual(backoff_delay(3), 240)
        self.assertEqual(backoff_delay(4), 480)
        self.assertEqual(backoff_delay(12), 86400)

    def test_worker_delivers_pending_messages_with_pdf(self):
        message = _make_message()
        call_command('process_email_outbox', verbosity=0)
        message.refresh_from_db()
        self.assertEqual(message.status, 'sent')
        self.assertEqual(message.attempts, 1)
        self.assertIsNotNone(message.sent_at)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ['patron@example.com'])
        self.assertIn('Booking confirmed', sent.subject)
        pdf = [a for a in sent.attachments if a[2] == 'application/pdf']
        self.assertEqual(len(pdf), 1)
        self.assertEqual(pdf[0][1], b'%PDF-1.4-test-bytes')

    def test_failure_is_retried_with_exponential_backoff(self):
        message = _make_message()
        with override_settings(EMAIL_BACKEND='movies.tests_outbox.FailingEmailBackend'):
            call_command('process_email_outbox', verbosity=0)
        message.refresh_from_db()
        self.assertEqual(message.status, 'pending', 'failed sends must stay pending')
        self.assertEqual(message.attempts, 1)
        self.assertIn('SMTP connection refused', message.last_error)
        self.assertGreater(
            message.next_attempt_at, timezone.now() + timedelta(seconds=50),
            'first retry must be scheduled ~60s in the future',
        )
        self.assertEqual(len(mail.outbox), 0)

        EmailOutbox.objects.filter(pk=message.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )
        call_command('process_email_outbox', verbosity=0)
        message.refresh_from_db()
        self.assertEqual(message.status, 'sent')
        self.assertEqual(message.attempts, 2)
        self.assertEqual(len(mail.outbox), 1)

    def test_max_attempts_marks_message_permanently_failed(self):
        message = _make_message(max_attempts=2)
        with override_settings(EMAIL_BACKEND='movies.tests_outbox.FailingEmailBackend'):
            call_command('process_email_outbox', verbosity=0)
            EmailOutbox.objects.filter(pk=message.pk).update(
                next_attempt_at=timezone.now() - timedelta(seconds=1)
            )
            call_command('process_email_outbox', verbosity=0)
        message.refresh_from_db()
        self.assertEqual(message.status, 'failed')
        self.assertEqual(message.attempts, 2)
        self.assertEqual(len(mail.outbox), 0)

    def test_worker_reclaims_messages_stuck_in_processing(self):
        stuck = EmailOutbox.objects.create(
            recipient='stuck@example.com',
            subject='Stuck message',
            plain_body='Never delivered.',
            status='processing',
            locked_at=timezone.now() - timedelta(hours=2),
            next_attempt_at=timezone.now() - timedelta(minutes=1),
        )
        call_command('process_email_outbox', verbosity=0)
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, 'sent')
        self.assertEqual(len(mail.outbox), 1)

    def test_worker_ignores_future_and_failed_messages(self):
        future = EmailOutbox.objects.create(
            recipient='later@example.com',
            subject='Not due yet',
            plain_body='Body',
            next_attempt_at=timezone.now() + timedelta(hours=1),
        )
        failed = EmailOutbox.objects.create(
            recipient='dead@example.com',
            subject='Dead',
            plain_body='Body',
            status='failed',
            next_attempt_at=timezone.now() - timedelta(minutes=1),
        )
        call_command('process_email_outbox', verbosity=0)
        future.refresh_from_db()
        failed.refresh_from_db()
        self.assertEqual(future.status, 'pending')
        self.assertEqual(failed.status, 'failed')
        self.assertEqual(len(mail.outbox), 0)


@DEMO_RAZORPAY
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BookingEnqueueTests(TestCase):
    """The booking request must enqueue, never send, and never block on SMTP."""

    def setUp(self):
        self.user = User.objects.create_user('alice', 'alice@example.com', 'password123')
        self.movie, self.show, self.seats = _make_show()
        _make_categories_and_prices(self.show)
        self.client.force_login(self.user)

    def _reserve(self, *seat_ids):
        from django.urls import reverse
        return self.client.post(
            reverse('api_reserve'),
            data={'show_id': self.show.id, 'seats': [str(s) for s in seat_ids]},
            content_type='application/json',
        )

    def _pay(self, token):
        from django.urls import reverse
        start = self.client.post(
            reverse('payment_start', args=[token]),
            data={'coupon_code': ''},
            content_type='application/json',
        )
        self.assertEqual(start.status_code, 200)
        checkout = start.json()['checkout']
        return self.client.post(
            reverse('payment_verify', args=[token]),
            data={
                'razorpay_order_id': checkout['order_id'],
                'razorpay_payment_id': 'pay_DEMO_pending',
                'razorpay_signature': checkout['demo_signature'],
                'payment_method': 'upi',
                'demo': 'true',
            },
            content_type='application/json',
        )

    def test_booking_enqueues_email_without_sending_synchronously(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])

        pending = EmailOutbox.objects.filter(status='pending')
        self.assertEqual(pending.count(), 1, 'exactly one confirmation email enqueued')
        message = pending.first()
        self.assertEqual(message.recipient, 'alice@example.com')
        self.assertIn('Booking confirmed', message.subject)
        self.assertTrue(
            message.pdf_attachment.startswith(b'%PDF'),
            'the enqueued message must carry a generated PDF ticket',
        )
        self.assertTrue(
            message.qr_image.startswith(b'\x89PNG'),
            'the enqueued message must carry an inline QR PNG',
        )
        self.assertIn(
            'cid:qr_ticket', message.html_body,
            'the HTML must reference the QR by Content-ID, not a data URI',
        )
        self.assertEqual(mail.outbox, [], 'email must not be sent inside the booking request')
        self.assertTrue(
            Notification.objects.filter(user=self.user, title='Booking confirmed').exists(),
            'in-app notification must still be created synchronously',
        )

        call_command('process_email_outbox', verbosity=0)
        self.assertEqual(len(mail.outbox), 1, 'worker must deliver the enqueued email')
        self.assertEqual(EmailOutbox.objects.filter(status='sent').count(), 1)

    def test_booking_without_email_skips_enqueue_but_keeps_notification(self):
        no_email = User.objects.create_user('bob', '', 'password123')
        self.client.force_login(no_email)
        reservation = self._reserve(self.seats[1].id).json()['reservation']
        self._pay(reservation['token'])

        self.assertEqual(
            EmailOutbox.objects.count(), 0,
            'no confirmation email can be enqueued when the user has no address',
        )
        self.assertEqual(mail.outbox, [])
        self.assertTrue(
            Notification.objects.filter(user=no_email, title='Booking confirmed').exists(),
            'in-app notification must still be created without an email address',
        )


class RunserverEmailWorkerTests(TestCase):
    """`python manage.py runserver` must auto-start the email outbox worker."""

    def test_runserver_resolves_to_movies_command(self):
        from django.core.management import get_commands
        self.assertEqual(
            get_commands()['runserver'], 'movies',
            'movies.runserver must override django.contrib.staticfiles runserver '
            'so the email worker auto-starts locally',
        )


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class RunserverEmailWorkerDeliveryTests(TransactionTestCase):
    """The runserver worker thread must actually drain pending messages.

    TransactionTestCase (real commits) so the worker thread's own database
    connection sees the enqueued message.
    """

    def test_start_email_worker_thread_delivers_pending_messages(self):
        from movies.management.commands.runserver import _start_email_worker

        message = _make_message()
        thread, stop_event = _start_email_worker(sleep=0.1)
        try:
            deadline = time.time() + 15
            while time.time() < deadline:
                message.refresh_from_db()
                if message.status == 'sent':
                    break
                time.sleep(0.05)
            message.refresh_from_db()
            self.assertEqual(message.status, 'sent', 'worker thread must deliver')
            self.assertEqual(message.attempts, 1)
            self.assertEqual(len(mail.outbox), 1)
            self.assertEqual(mail.outbox[0].to, ['patron@example.com'])
        finally:
            stop_event.set()
            thread.join(timeout=5)
