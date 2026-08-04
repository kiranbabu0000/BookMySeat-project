"""Payment lifecycle tests: checkout, verified callbacks, webhooks, refunds.

The test environment has no Razorpay keys, so the gateway runs in demo mode
(RAZORPAY_DEMO_MODE default) unless a test explicitly mocks real mode.
"""
import hmac
import hashlib
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from admin_panel.models import Coupon, Payment, PaymentTransaction, PricingConfig
from . import gateway
from .models import Reservation
from .payments import (
    PaymentError,
    cancel_stale_orders,
    handle_webhook,
    record_failure,
    refund_reservation_transactions,
    refund_transaction,
    start_checkout,
    verify_and_confirm,
)
from .services import (
    ReservationError,
    cancel_booking,
    confirm_booking,
    create_reservation,
    release_reservation,
)
from .tests import _make_categories_and_prices, _make_show

WEBHOOK_SECRET = 'test-webhook-secret'


def _signed_body(payload):
    body = __import__('json').dumps(payload).encode('utf-8')
    digest = hmac.new(
        WEBHOOK_SECRET.encode('utf-8'), body, hashlib.sha256
    ).hexdigest()
    return body, digest


class PaymentServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', 'alice@example.com', 'password123')
        self.other = User.objects.create_user('bob', 'bob@example.com', 'password123')
        self.movie, self.show, self.seats = _make_show()
        _make_categories_and_prices(self.show)
        self.reservation = create_reservation(
            self.user, self.show.id, [self.seats[0].id, self.seats[1].id]
        )

    def test_start_checkout_creates_demo_order(self):
        tx, checkout = start_checkout(self.user, self.reservation.token)
        self.assertTrue(gateway.demo_mode())
        self.assertEqual(tx.status, 'created')
        self.assertTrue(tx.gateway_order_id.startswith('order_DEMO'))
        self.assertTrue(tx.is_demo)
        self.assertEqual(checkout['order_id'], tx.gateway_order_id)
        self.assertTrue(checkout['demo'])
        self.assertTrue(checkout['demo_signature'])
        self.assertGreater(checkout['amount'], 0)

    def test_start_checkout_is_idempotent(self):
        tx1, _ = start_checkout(self.user, self.reservation.token)
        tx2, _ = start_checkout(self.user, self.reservation.token)
        self.assertEqual(tx1.pk, tx2.pk)
        self.assertEqual(PaymentTransaction.objects.filter(reservation=self.reservation).count(), 1)

    def test_start_checkout_binds_coupon(self):
        Coupon.objects.create(
            code='SAVE10', discount_percent=10, max_uses=100,
            min_order_amount=Decimal('0.00'),
            valid_from=timezone.now() - timedelta(days=1),
            valid_to=timezone.now() + timedelta(days=1), is_active=True,
        )
        tx, checkout = start_checkout(self.user, self.reservation.token, coupon_code='SAVE10')
        self.assertEqual(tx.coupon_code, 'SAVE10')
        from .services import reservation_pricing
        undiscounted = reservation_pricing(self.reservation)['total']
        self.assertLess(checkout['amount'], gateway.paise_from_decimal(undiscounted))

    def test_start_checkout_rejects_expired_reservation(self):
        Reservation.objects.filter(pk=self.reservation.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        with self.assertRaises(ReservationError):
            start_checkout(self.user, self.reservation.token)

    def test_start_checkout_rejects_other_user(self):
        with self.assertRaises(ReservationError):
            start_checkout(self.other, self.reservation.token)

    def test_start_checkout_rejects_completed_reservation(self):
        confirm_booking(self.user, self.reservation.token, transaction_id='TXN-DONE')
        with self.assertRaises(ReservationError):
            start_checkout(self.user, self.reservation.token)

    def test_demo_verify_confirms_booking(self):
        tx, checkout = start_checkout(self.user, self.reservation.token)
        payment_id = 'pay_DEMO_pending'
        reservation, bookings = verify_and_confirm(
            self.user, self.reservation.token,
            gateway_order_id=tx.gateway_order_id,
            gateway_payment_id=payment_id,
            gateway_signature=checkout['demo_signature'],
            method='upi', demo=True,
        )
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, 'booked')
        self.assertEqual(len(bookings), 2)
        self.seats[0].refresh_from_db()
        self.assertTrue(self.seats[0].is_booked)
        tx.refresh_from_db()
        self.assertEqual(tx.status, 'captured')
        self.assertEqual(tx.gateway_payment_id, payment_id)
        self.assertEqual(
            Payment.objects.filter(booking__reservation=reservation, status='completed').count(), 2
        )

    def test_demo_verify_rejects_wrong_signature(self):
        tx, _checkout = start_checkout(self.user, self.reservation.token)
        with self.assertRaises(PaymentError):
            verify_and_confirm(
                self.user, self.reservation.token,
                gateway_order_id=tx.gateway_order_id,
                gateway_payment_id='pay_DEMO_bad',
                gateway_signature='deadbeef',
                method='upi', demo=True,
            )
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'active')
        self.seats[0].refresh_from_db()
        self.assertFalse(self.seats[0].is_booked)

    def test_demo_verify_rejects_unknown_order(self):
        with self.assertRaises(PaymentError):
            verify_and_confirm(
                self.user, self.reservation.token,
                gateway_order_id='order_DEMO0000000000000000',
                gateway_payment_id='pay_DEMO_x',
                gateway_signature='x', method='upi', demo=True,
            )

    def test_verify_rejects_another_users_order(self):
        tx, _checkout = start_checkout(self.user, self.reservation.token)
        with self.assertRaises(ReservationError):
            verify_and_confirm(
                self.other, self.reservation.token,
                gateway_order_id=tx.gateway_order_id,
                gateway_payment_id='pay_DEMO_pending',
                gateway_signature='x', method='upi', demo=True,
            )

    def test_verify_rejects_amount_change_after_start(self):
        tx, checkout = start_checkout(self.user, self.reservation.token)
        PricingConfig.objects.filter(pk=1).update(
            platform_fee_per_ticket=Decimal('99.00'),
            misc_fee_per_booking=Decimal('2.50'),
        )
        with self.assertRaises(PaymentError):
            verify_and_confirm(
                self.user, self.reservation.token,
                gateway_order_id=tx.gateway_order_id,
                gateway_payment_id='pay_DEMO_pending',
                gateway_signature=checkout['demo_signature'],
                method='upi', demo=True,
            )

    def test_verify_is_idempotent(self):
        tx, checkout = start_checkout(self.user, self.reservation.token)
        kwargs = dict(
            gateway_order_id=tx.gateway_order_id,
            gateway_payment_id='pay_DEMO_pending',
            gateway_signature=checkout['demo_signature'],
            method='upi', demo=True,
        )
        r1, bookings1 = verify_and_confirm(self.user, self.reservation.token, **kwargs)
        r2, bookings2 = verify_and_confirm(self.user, self.reservation.token, **kwargs)
        self.assertEqual([b.pk for b in bookings1], [b.pk for b in bookings2])
        self.assertEqual(
            Payment.objects.filter(booking__reservation=r1).count(), 2
        )
        self.assertEqual(
            PaymentTransaction.objects.filter(reservation=self.reservation).count(), 1
        )

    def test_record_failure_keeps_seats_and_allows_retry(self):
        tx, _ = start_checkout(self.user, self.reservation.token)
        record_failure(
            self.user, self.reservation.token,
            gateway_order_id=tx.gateway_order_id,
            gateway_payment_id='pay_DEMO_failed',
            failure_reason='Simulated failure',
        )
        tx.refresh_from_db()
        self.assertEqual(tx.status, 'failed')
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.payment_status, 'failed')
        self.assertEqual(self.reservation.status, 'active')
        self.seats[0].refresh_from_db()
        self.assertFalse(self.seats[0].is_booked)

        retry, checkout = start_checkout(self.user, self.reservation.token)
        self.assertNotEqual(retry.pk, tx.pk)
        verify_and_confirm(
            self.user, self.reservation.token,
            gateway_order_id=retry.gateway_order_id,
            gateway_payment_id='pay_DEMO_pending',
            gateway_signature=checkout['demo_signature'],
            method='upi', demo=True,
        )
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'booked')

    def test_refund_transaction_demo(self):
        tx, checkout = start_checkout(self.user, self.reservation.token)
        verify_and_confirm(
            self.user, self.reservation.token,
            gateway_order_id=tx.gateway_order_id,
            gateway_payment_id='pay_DEMO_pending',
            gateway_signature=checkout['demo_signature'],
            method='upi', demo=True,
        )
        tx.refresh_from_db()
        refund_transaction(tx, reason='Cancelled by user')
        tx.refresh_from_db()
        self.assertEqual(tx.status, 'refunded')
        self.assertTrue(tx.refund_id.startswith('ref_DEMO'))
        self.assertIsNotNone(tx.refunded_at)

    def test_cancel_booking_refunds_transactions(self):
        tx, checkout = start_checkout(self.user, self.reservation.token)
        _reservation, _bookings = verify_and_confirm(
            self.user, self.reservation.token,
            gateway_order_id=tx.gateway_order_id,
            gateway_payment_id='pay_DEMO_pending',
            gateway_signature=checkout['demo_signature'],
            method='upi', demo=True,
        )
        cancel_booking(self.user, _bookings[0].id)
        tx.refresh_from_db()
        self.assertEqual(tx.status, 'refunded')
        remaining = Payment.objects.filter(booking__reservation=self.reservation)
        self.assertEqual(remaining.count(), 2)
        statuses = set(remaining.values_list('status', flat=True))
        self.assertEqual(statuses, {'completed', 'refunded'})
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'booked')

    def test_refund_reservation_transactions_best_effort(self):
        tx, checkout = start_checkout(self.user, self.reservation.token)
        verify_and_confirm(
            self.user, self.reservation.token,
            gateway_order_id=tx.gateway_order_id,
            gateway_payment_id='pay_DEMO_pending',
            gateway_signature=checkout['demo_signature'],
            method='upi', demo=True,
        )
        self.reservation.refresh_from_db()
        count = refund_reservation_transactions(self.reservation)
        self.assertEqual(count, 1)

    def test_cancel_stale_orders(self):
        start_checkout(self.user, self.reservation.token)
        Reservation.objects.filter(pk=self.reservation.pk).update(status='expired')
        cancelled = cancel_stale_orders()
        self.assertEqual(cancelled, 1)
        tx = PaymentTransaction.objects.get(reservation=self.reservation)
        self.assertEqual(tx.status, 'cancelled')


@override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_SECRET)
class PaymentWebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', 'alice@example.com', 'password123')
        self.movie, self.show, self.seats = _make_show()
        _make_categories_and_prices(self.show)
        self.reservation = create_reservation(
            self.user, self.show.id, [self.seats[0].id]
        )
        self.tx, self.checkout = start_checkout(self.user, self.reservation.token)

    def test_webhook_rejects_bad_signature(self):
        body, _sig = _signed_body({'event': 'payment.captured'})
        with self.assertRaises(PaymentError):
            handle_webhook(body, 'forged-signature')

    def test_webhook_rejects_invalid_json(self):
        with self.assertRaises(PaymentError):
            handle_webhook(b'not-json', _signed_body({})[1])

    def test_webhook_payment_captured_confirms_booking(self):
        amount = gateway.paise_from_decimal(self.tx.amount)
        body, sig = _signed_body({
            'event': 'payment.captured',
            'payload': {
                'payment': {
                    'entity': {
                        'id': 'pay_WEBHOOK_1',
                        'order_id': self.tx.gateway_order_id,
                        'amount': amount,
                        'method': 'upi',
                    }
                }
            },
        })
        handle_webhook(body, sig)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'booked')
        self.seats[0].refresh_from_db()
        self.assertTrue(self.seats[0].is_booked)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, 'captured')
        self.assertEqual(self.tx.gateway_payment_id, 'pay_WEBHOOK_1')

    def test_webhook_payment_captured_is_idempotent(self):
        amount = gateway.paise_from_decimal(self.tx.amount)
        payload = {
            'event': 'payment.captured',
            'payload': {
                'payment': {
                    'entity': {
                        'id': 'pay_WEBHOOK_1',
                        'order_id': self.tx.gateway_order_id,
                        'amount': amount,
                        'method': 'upi',
                    }
                }
            },
        }
        handle_webhook(*_signed_body(payload))
        handle_webhook(*_signed_body(payload))
        self.assertEqual(
            Payment.objects.filter(booking__reservation=self.reservation).count(), 1
        )
        self.assertEqual(PaymentTransaction.objects.filter(reservation=self.reservation).count(), 1)

    def test_webhook_unknown_order_ignored(self):
        body, sig = _signed_body({
            'event': 'payment.captured',
            'payload': {
                'payment': {'entity': {'id': 'pay_X', 'order_id': 'order_UNKNOWN', 'amount': 100}}
            },
        })
        handle_webhook(body, sig)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'active')

    def test_webhook_amount_mismatch_raises(self):
        body, sig = _signed_body({
            'event': 'payment.captured',
            'payload': {
                'payment': {
                    'entity': {
                        'id': 'pay_WEBHOOK_1',
                        'order_id': self.tx.gateway_order_id,
                        'amount': 1,
                    }
                }
            },
        })
        with self.assertRaises(PaymentError):
            handle_webhook(body, sig)

    def test_webhook_payment_failed(self):
        body, sig = _signed_body({
            'event': 'payment.failed',
            'payload': {
                'payment': {
                    'entity': {
                        'id': 'pay_WEBHOOK_FAIL',
                        'order_id': self.tx.gateway_order_id,
                        'error_description': 'Card declined',
                    }
                }
            },
        })
        handle_webhook(body, sig)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, 'failed')
        self.assertEqual(self.tx.failure_reason, 'Card declined')
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.payment_status, 'failed')
        self.assertEqual(self.reservation.status, 'active')

    def test_webhook_refund_by_payment_id(self):
        _reservation, _bookings = verify_and_confirm(
            self.user, self.reservation.token,
            gateway_order_id=self.tx.gateway_order_id,
            gateway_payment_id='pay_DEMO_pending',
            gateway_signature=self.checkout['demo_signature'],
            method='upi', demo=True,
        )
        self.tx.refresh_from_db()
        body, sig = _signed_body({
            'event': 'refund.created',
            'payload': {
                'refund': {
                    'entity': {
                        'id': 'ref_WEBHOOK_1',
                        'payment_id': 'pay_DEMO_pending',
                        'status': 'processed',
                    }
                }
            },
        })
        handle_webhook(body, sig)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, 'refunded')
        self.assertEqual(self.tx.refund_id, 'ref_WEBHOOK_1')

    def test_webhook_http_endpoint(self):
        amount = gateway.paise_from_decimal(self.tx.amount)
        body, sig = _signed_body({
            'event': 'payment.captured',
            'payload': {
                'payment': {
                    'entity': {
                        'id': 'pay_WEBHOOK_1',
                        'order_id': self.tx.gateway_order_id,
                        'amount': amount,
                        'method': 'upi',
                    }
                }
            },
        })
        url = reverse('payment_webhook')
        response = self.client.post(
            url, data=body, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)
        bad = self.client.post(
            url, data=body, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE='forged',
        )
        self.assertEqual(bad.status_code, 400)


@patch('movies.gateway.demo_mode', return_value=False)
class PaymentRealGatewayTests(TestCase):
    """Exercise the real (non-demo) code path with a mocked gateway."""

    def setUp(self):
        self.user = User.objects.create_user('alice', 'alice@example.com', 'password123')
        self.movie, self.show, self.seats = _make_show()
        _make_categories_and_prices(self.show)
        self.reservation = create_reservation(
            self.user, self.show.id, [self.seats[0].id]
        )

    @patch('movies.gateway.create_order',
           return_value={'id': 'order_REAL_1'})
    def test_start_checkout_creates_real_order(self, mock_create_order, mock_demo):
        tx, checkout = start_checkout(self.user, self.reservation.token)
        self.assertFalse(tx.is_demo)
        self.assertEqual(tx.gateway_order_id, 'order_REAL_1')
        self.assertEqual(checkout['order_id'], 'order_REAL_1')
        self.assertFalse(checkout['demo'])
        self.assertEqual(checkout['key'], '')
        mock_create_order.assert_called_once()
        self.assertEqual(mock_create_order.call_args[1]['receipt'],
                         'BMS-{}'.format(self.reservation.token[:10].upper()))

    @patch('movies.gateway.fetch_payment')
    @patch('movies.gateway.verify_payment_signature', return_value=True)
    @patch('movies.gateway.create_order',
           return_value={'id': 'order_REAL_1'})
    def test_real_verify_confirms_booking(self, mock_create_order, mock_verify,
                                          mock_fetch, mock_demo):
        tx, _checkout = start_checkout(self.user, self.reservation.token)
        mock_fetch.return_value = {
            'order_id': tx.gateway_order_id,
            'amount': gateway.paise_from_decimal(tx.amount),
            'status': 'captured',
        }
        reservation, bookings = verify_and_confirm(
            self.user, self.reservation.token,
            gateway_order_id=tx.gateway_order_id,
            gateway_payment_id='pay_REAL_1',
            gateway_signature='real-signature',
            method='upi', demo=False,
        )
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, 'booked')
        self.assertEqual(len(bookings), 1)
        mock_verify.assert_called_once()
        tx.refresh_from_db()
        self.assertEqual(tx.status, 'captured')

    @patch('movies.gateway.fetch_payment')
    @patch('movies.gateway.verify_payment_signature', return_value=True)
    @patch('movies.gateway.create_order',
           return_value={'id': 'order_REAL_1'})
    def test_real_verify_rejects_wrong_order_on_payment(self, mock_create_order,
                                                        mock_verify, mock_fetch,
                                                        mock_demo):
        tx, _checkout = start_checkout(self.user, self.reservation.token)
        mock_fetch.return_value = {
            'order_id': 'order_SOMEONE_ELSE',
            'amount': gateway.paise_from_decimal(tx.amount),
            'status': 'captured',
        }
        with self.assertRaises(PaymentError):
            verify_and_confirm(
                self.user, self.reservation.token,
                gateway_order_id=tx.gateway_order_id,
                gateway_payment_id='pay_REAL_1',
                gateway_signature='real-signature',
                method='upi', demo=False,
            )

    @patch('movies.gateway.fetch_payment')
    @patch('movies.gateway.verify_payment_signature', return_value=True)
    @patch('movies.gateway.create_order',
           return_value={'id': 'order_REAL_1'})
    def test_real_verify_rejects_amount_mismatch(self, mock_create_order,
                                                 mock_verify, mock_fetch,
                                                 mock_demo):
        tx, _checkout = start_checkout(self.user, self.reservation.token)
        mock_fetch.return_value = {
            'order_id': tx.gateway_order_id,
            'amount': gateway.paise_from_decimal(tx.amount) + 1,
            'status': 'captured',
        }
        with self.assertRaises(PaymentError):
            verify_and_confirm(
                self.user, self.reservation.token,
                gateway_order_id=tx.gateway_order_id,
                gateway_payment_id='pay_REAL_1',
                gateway_signature='real-signature',
                method='upi', demo=False,
            )

    @patch('movies.gateway.create_order',
           return_value={'id': 'order_REAL_1'})
    def test_demo_verify_fails_when_gateway_is_real(self, mock_create_order, mock_demo):
        tx, _checkout = start_checkout(self.user, self.reservation.token)
        with self.assertRaises(PaymentError):
            verify_and_confirm(
                self.user, self.reservation.token,
                gateway_order_id=tx.gateway_order_id,
                gateway_payment_id='pay_DEMO_pending',
                gateway_signature='demo-forged',
                method='upi', demo=True,
            )


class PaymentApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', 'alice@example.com', 'password123')
        self.other = User.objects.create_user('bob', 'bob@example.com', 'password123')
        self.movie, self.show, self.seats = _make_show()
        _make_categories_and_prices(self.show)
        self.client.force_login(self.user)

    def _reserve(self, *seat_ids):
        return self.client.post(
            reverse('api_reserve'),
            data={'show_id': self.show.id, 'seats': [str(s) for s in seat_ids]},
            content_type='application/json',
        )

    def _start(self, token):
        return self.client.post(
            reverse('payment_start', args=[token]),
            data='{}', content_type='application/json',
        )

    def _verify(self, token, order_id, payment_id, signature):
        return self.client.post(
            reverse('payment_verify', args=[token]),
            data={
                'razorpay_order_id': order_id,
                'razorpay_payment_id': payment_id,
                'razorpay_signature': signature,
                'payment_method': 'upi',
                'demo': 'true',
            },
            content_type='application/json',
        )

    def test_full_demo_flow(self):
        token = self._reserve(self.seats[0].id).json()['reservation']['token']
        start = self._start(token)
        self.assertEqual(start.status_code, 200)
        checkout = start.json()['checkout']
        self.assertTrue(checkout['demo'])

        verify = self._verify(
            token, checkout['order_id'], 'pay_DEMO_pending',
            checkout['demo_signature'],
        )
        self.assertEqual(verify.status_code, 200)
        body = verify.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['confirmation_url'], reverse('booking_confirmation', args=[token]))

        self.seats[0].refresh_from_db()
        self.assertTrue(self.seats[0].is_booked)
        self.assertEqual(
            Payment.objects.filter(booking__reservation__token=token).count(), 1
        )

    def test_start_requires_login(self):
        token = self._reserve(self.seats[0].id).json()['reservation']['token']
        self.client.logout()
        response = self._start(token)
        self.assertEqual(response.status_code, 302)

    def test_start_rejects_expired_reservation(self):
        token = self._reserve(self.seats[0].id).json()['reservation']['token']
        Reservation.objects.filter(token=token).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        response = self._start(token)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()['ok'])

    def test_verify_rejects_another_users_order(self):
        token = self._reserve(self.seats[0].id).json()['reservation']['token']
        checkout = self._start(token).json()['checkout']
        self.client.force_login(self.other)
        response = self._verify(
            token, checkout['order_id'], 'pay_DEMO_pending', 'forged'
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()['ok'])

    def test_failed_api_records_failure_then_retry_succeeds(self):
        token = self._reserve(self.seats[0].id).json()['reservation']['token']
        checkout = self._start(token).json()['checkout']
        failed = self.client.post(
            reverse('payment_failed', args=[token]),
            data={
                'razorpay_order_id': checkout['order_id'],
                'razorpay_payment_id': 'pay_DEMO_failed',
                'error': 'Simulated failure',
                'payment_method': 'upi',
            },
            content_type='application/json',
        )
        self.assertEqual(failed.status_code, 200)
        self.assertTrue(failed.json()['ok'])
        tx = PaymentTransaction.objects.get(gateway_order_id=checkout['order_id'])
        self.assertEqual(tx.status, 'failed')

        retry = self._start(token)
        self.assertEqual(retry.status_code, 200)
        checkout2 = retry.json()['checkout']
        self.assertNotEqual(checkout2['order_id'], checkout['order_id'])
        verify = self._verify(
            token, checkout2['order_id'], 'pay_DEMO_pending',
            checkout2['demo_signature'],
        )
        self.assertEqual(verify.status_code, 200)
        self.seats[0].refresh_from_db()
        self.assertTrue(self.seats[0].is_booked)

    def test_simulate_payment_failure_keeps_seats(self):
        token = self._reserve(self.seats[0].id).json()['reservation']['token']
        response = self.client.post(
            reverse('simulate_payment', args=[token]),
            data={'action': 'fail', 'transaction_id': 'TXN-X'},
        )
        self.assertRedirects(response, reverse('payment_page', args=[token]))
        self.seats[0].refresh_from_db()
        self.assertFalse(self.seats[0].is_booked)


@override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_SECRET)
class PaymentAdminTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', 'alice@example.com', 'password123')
        self.admin = User.objects.create_superuser('admin', 'admin@example.com', 'password123')
        self.movie, self.show, self.seats = _make_show()
        _make_categories_and_prices(self.show)
        self.reservation = create_reservation(
            self.user, self.show.id, [self.seats[0].id]
        )
        tx, checkout = start_checkout(self.user, self.reservation.token)
        verify_and_confirm(
            self.user, self.reservation.token,
            gateway_order_id=tx.gateway_order_id,
            gateway_payment_id='pay_DEMO_pending',
            gateway_signature=checkout['demo_signature'],
            method='upi', demo=True,
        )
        self.reservation.refresh_from_db()

        self.client.force_login(self.admin)
        session = self.client.session
        session['admin_user_id'] = self.admin.id
        session['is_admin_authenticated'] = True
        session.save()

    def test_payment_list_page_renders(self):
        response = self.client.get(reverse('admin_payment_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'pay_DEMO_pending')

    def test_payment_detail_page_renders(self):
        tx = PaymentTransaction.objects.get(reservation=self.reservation)
        response = self.client.get(reverse('admin_payment_detail', args=[tx.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, tx.gateway_order_id)

    def test_payment_refund_action(self):
        tx = PaymentTransaction.objects.get(reservation=self.reservation)
        response = self.client.post(
            reverse('admin_payment_refund', args=[tx.id]),
            data={'reason': 'Admin initiated refund'},
        )
        self.assertEqual(response.status_code, 302)
        tx.refresh_from_db()
        self.assertEqual(tx.status, 'refunded')
        self.assertEqual(
            Payment.objects.filter(
                booking__reservation=self.reservation, status='refunded'
            ).count(), 1
        )

    def test_dashboard_shows_revenue(self):
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Total Revenue')
