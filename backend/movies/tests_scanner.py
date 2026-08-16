"""Ticket QR scanner tests.

Covers the admin-only validation API, one-time claiming, the scan-history
audit trail, explicit outcome states (unpaid / cancelled / invalid) and
concurrent double-scan safety.
"""
import json
import threading
import time
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.utils import OperationalError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from admin_panel.models import AdminPermission, AdminProfile
from .models import Booking, Reservation, TicketScan
from .payments import start_checkout, verify_and_confirm
from .qr import build_qr_payload
from .services import create_reservation
from .tests import _make_categories_and_prices, _make_show
from .testutils import DEMO_RAZORPAY
from .ticket_scan import scan_ticket


def _admin_session(client, user):
    client.force_login(user)
    session = client.session
    session['admin_user_id'] = user.id
    session['is_admin_authenticated'] = True
    session['admin_login_time'] = str(timezone.now())
    session.save()


def _confirmed_reservation(user, show, seats):
    reservation = create_reservation(user, show.id, [s.id for s in seats])
    tx, checkout = start_checkout(user, reservation.token)
    verify_and_confirm(
        user, reservation.token,
        gateway_order_id=tx.gateway_order_id,
        gateway_payment_id='pay_DEMO_pending',
        gateway_signature=checkout['demo_signature'],
        method='upi', demo=True,
    )
    reservation.refresh_from_db()
    return reservation


def _payload_for(reservation):
    return build_qr_payload(
        reservation.booking_ref,
        reservation.show.movie.name,
        reservation.show.name,
        ['A1'],
    )


@DEMO_RAZORPAY
class ScannerAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', 'alice@example.com', 'password123')
        self.admin = User.objects.create_superuser('admin', 'admin@example.com', 'password123')
        self.staff = User.objects.create_user('staff', 'staff@example.com', 'password123', is_staff=True)
        self.movie, self.show, self.seats = _make_show()
        _make_categories_and_prices(self.show)

    def _staff_profile(self, permission=False):
        profile = AdminProfile.objects.create(user=self.staff, role='staff', is_active=True)
        if permission:
            AdminPermission.objects.create(
                admin_profile=profile, module='ticket', can_view=True
            )
        return profile

    def test_scanner_page_requires_admin_login(self):
        response = self.client.get(reverse('admin_ticket_scanner'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/admin-login/'))

    def test_scanner_page_renders_for_superadmin(self):
        _admin_session(self.client, self.admin)
        response = self.client.get(reverse('admin_ticket_scanner'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Camera Scanner')

    def test_scan_history_requires_admin_login(self):
        response = self.client.get(reverse('admin_ticket_scan_history'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/admin-login/'))

    def test_staff_without_ticket_permission_denied(self):
        self._staff_profile(permission=False)
        _admin_session(self.client, self.staff)
        response = self.client.get(reverse('admin_ticket_scanner'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('admin_dashboard'))

    def test_staff_with_ticket_permission_allowed(self):
        self._staff_profile(permission=True)
        _admin_session(self.client, self.staff)
        response = self.client.get(reverse('admin_ticket_scanner'))
        self.assertEqual(response.status_code, 200)

    def test_scan_api_requires_admin_login(self):
        payload = build_qr_payload('BMS-NOPE', 'Movie', 'Theatre', ['A1'])
        response = self.client.post(
            reverse('admin_ticket_scan_api'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/admin-login/'))


@DEMO_RAZORPAY
class TicketScanApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', 'alice@example.com', 'password123')
        self.admin = User.objects.create_superuser('admin', 'admin@example.com', 'password123')
        self.movie, self.show, self.seats = _make_show()
        _make_categories_and_prices(self.show)
        _admin_session(self.client, self.admin)

    def _scan_api(self, payload):
        return self.client.post(
            reverse('admin_ticket_scan_api'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_scan_admits_valid_ticket_and_records_history(self):
        reservation = _confirmed_reservation(self.user, self.show, self.seats[:1])
        response = self._scan_api(_payload_for(reservation))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['valid'])
        self.assertTrue(body['scanned'])

        reservation.refresh_from_db()
        self.assertIsNotNone(reservation.scanned_at)
        self.assertEqual(reservation.scan_count, 1)

        scan = TicketScan.objects.filter(booking_ref=reservation.booking_ref).first()
        self.assertIsNotNone(scan)
        self.assertEqual(scan.result, 'admitted')
        self.assertEqual(scan.scanned_by_id, self.admin.id)
        self.assertEqual(scan.movie, reservation.show.movie.name)
        self.assertEqual(scan.theatre, reservation.show.name)
        self.assertEqual(scan.seats, 'A1')

    def test_scan_is_one_time(self):
        reservation = _confirmed_reservation(self.user, self.show, self.seats[:1])
        payload = _payload_for(reservation)
        first = self._scan_api(payload)
        self.assertTrue(first.json()['scanned'])

        second = self._scan_api(payload)
        body = second.json()
        self.assertFalse(body['valid'])
        self.assertTrue(body['used'])
        self.assertEqual(body['reason'], 'already_scanned')

        reservation.refresh_from_db()
        self.assertEqual(reservation.scan_count, 1)
        results = list(TicketScan.objects.filter(booking_ref=reservation.booking_ref).values_list('result', flat=True))
        self.assertEqual(sorted(results), ['admitted', 'already_scanned'])

    def test_scan_rejects_tampered_payload(self):
        reservation = _confirmed_reservation(self.user, self.show, self.seats[:1])
        payload = _payload_for(reservation)
        tampered = dict(payload)
        tampered['booking_id'] = 'BMS-TAMPERED'
        response = self._scan_api(tampered)
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body['valid'])
        self.assertEqual(body['reason'], 'invalid_signature')

        reservation.refresh_from_db()
        self.assertIsNone(reservation.scanned_at)
        self.assertEqual(
            TicketScan.objects.filter(booking_ref='BMS-TAMPERED', result='invalid').count(), 1
        )

    def test_scan_rejects_unpaid_reservation(self):
        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        reservation.booking_ref = 'BMSUNPAID01'
        reservation.save(update_fields=['booking_ref'])
        response = self._scan_api(_payload_for(reservation))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body['valid'])
        self.assertEqual(body['reason'], 'unpaid')
        reservation.refresh_from_db()
        self.assertIsNone(reservation.scanned_at)
        self.assertEqual(
            TicketScan.objects.filter(booking_ref='BMSUNPAID01', result='unpaid').count(), 1
        )

    def test_scan_rejects_cancelled_reservation(self):
        reservation = _confirmed_reservation(self.user, self.show, self.seats[:1])
        reservation.status = 'cancelled'
        reservation.save(update_fields=['status'])
        response = self._scan_api(_payload_for(reservation))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body['valid'])
        self.assertEqual(body['reason'], 'cancelled')
        self.assertIsNone(reservation.scanned_at)

    def test_scan_rejects_cancelled_legacy_booking(self):
        Booking.objects.create(
            user=self.user,
            seat=self.seats[0],
            movie=self.movie,
            theater=self.show,
            status='cancelled',
            booking_ref='BMSCANCEL01',
            total=Decimal('250.00'),
        )
        payload = build_qr_payload('BMSCANCEL01', self.movie.name, self.show.name, ['A1'])
        response = self._scan_api(payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body['valid'])
        self.assertEqual(body['reason'], 'cancelled')

    def test_scan_unknown_reference_not_found(self):
        payload = build_qr_payload('BMS-NOEXIST', self.movie.name, self.show.name, ['A1'])
        response = self._scan_api(payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body['valid'])
        self.assertEqual(body['reason'], 'not_found')
        self.assertEqual(
            TicketScan.objects.filter(booking_ref='BMS-NOEXIST', result='not_found').count(), 1
        )

    def test_scan_history_page_renders_and_filters(self):
        reservation = _confirmed_reservation(self.user, self.show, self.seats[:1])
        self._scan_api(_payload_for(reservation))
        self._scan_api(_payload_for(reservation))

        response = self.client.get(reverse('admin_ticket_scan_history'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reservation.booking_ref)

        admitted_only = self.client.get(
            reverse('admin_ticket_scan_history'), {'result': 'admitted'}
        )
        self.assertContains(admitted_only, reservation.booking_ref)

        cancelled_only = self.client.get(
            reverse('admin_ticket_scan_history'), {'result': 'cancelled'}
        )
        self.assertNotContains(cancelled_only, reservation.booking_ref)

    def test_public_gate_api_still_works_and_records_history(self):
        reservation = _confirmed_reservation(self.user, self.show, self.seats[:1])
        response = self.client.post(
            reverse('verify_ticket_qr'),
            data=json.dumps(_payload_for(reservation)),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['valid'])
        self.assertEqual(
            TicketScan.objects.filter(
                booking_ref=reservation.booking_ref,
                result='admitted',
                scanned_by__isnull=True,
            ).count(), 1
        )


@DEMO_RAZORPAY
class ScannerConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', 'alice@example.com', 'password123')
        self.movie, self.show, self.seats = _make_show()
        _make_categories_and_prices(self.show)
        self.reservation = _confirmed_reservation(self.user, self.show, self.seats[:1])

    def test_concurrent_double_scan_admits_exactly_once(self):
        payload = _payload_for(self.reservation)
        results = []
        lock = threading.Lock()

        def scan():
            for _attempt in range(20):
                try:
                    response = scan_ticket(payload)
                    with lock:
                        results.append(json.loads(response.content.decode('utf-8')))
                    return
                except OperationalError:
                    time.sleep(0.1)

        threads = [threading.Thread(target=scan) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        admitted = [r for r in results if r.get('valid') and r.get('scanned')]
        used = [r for r in results if r.get('used') and r.get('reason') == 'already_scanned']
        self.assertEqual(len(admitted), 1)
        self.assertEqual(len(used), 1)
        self.reservation.refresh_from_db()
        self.assertIsNotNone(self.reservation.scanned_at)
        self.assertEqual(self.reservation.scan_count, 1)
