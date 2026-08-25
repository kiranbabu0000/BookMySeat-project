from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone

from admin_panel.models import AdminProfile

from .otp import generate_and_store
from .middleware import JUST_LOGGED_OUT_FLAG, JUST_LOGGED_OUT_WINDOW
from movies.models import EmailOutbox
from movies.testutils import DEMO_RAZORPAY


class OTPHelpersTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='alice', email='alice@example.com', password='password123'
        )

    def tearDown(self):
        cache.clear()

    def test_mask_email(self):
        from .otp import mask_email
        self.assertEqual(mask_email('alice@example.com'), 'a***@example.com')

    def test_generate_and_store_roundtrip(self):
        otp = generate_and_store(self.user)
        self.assertEqual(len(otp), 6)
        self.assertTrue(otp.isdigit())

    def test_verify_accepts_correct_code(self):
        otp = generate_and_store(self.user)
        ok, msg = __import__('users.otp', fromlist=['verify']).verify(self.user.id, otp)
        self.assertTrue(ok)
        self.assertEqual(msg, 'ok')

    def test_verify_rejects_wrong_code_and_counts_attempts(self):
        otp = generate_and_store(self.user)
        from .otp import verify, remaining_attempts
        ok, _ = verify(self.user.id, '000000')
        self.assertFalse(ok)
        self.assertEqual(remaining_attempts(self.user.id), 4)
        ok, _ = verify(self.user.id, otp)
        self.assertTrue(ok)

    def test_verify_expires_when_code_gone(self):
        generate_and_store(self.user)
        cache.clear()
        from .otp import verify
        ok, msg = verify(self.user.id, '123456')
        self.assertFalse(ok)
        self.assertIn('expired', msg)

    def test_resend_capped_at_absolute_limit(self):
        from .otp import (
            OTP_MAX_RESENDS, _cooldown_key, can_resend, mark_resend, resend_count,
        )
        generate_and_store(self.user)
        self.assertTrue(can_resend(self.user.id))
        for _ in range(OTP_MAX_RESENDS):
            self.assertTrue(can_resend(self.user.id))
            mark_resend(self.user.id)
            cache.delete(_cooldown_key(self.user.id))
        self.assertEqual(resend_count(self.user.id), OTP_MAX_RESENDS)
        self.assertFalse(can_resend(self.user.id))

    def test_reset_resend_count_starts_new_flow_at_zero(self):
        from .otp import can_resend, mark_resend, resend_count, reset_resend_count
        for _ in range(3):
            mark_resend(self.user.id)
        self.assertEqual(resend_count(self.user.id), 3)
        reset_resend_count(self.user.id)
        self.assertEqual(resend_count(self.user.id), 0)
        self.assertTrue(can_resend(self.user.id))


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class LoginFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.user = User.objects.create_user(
            username='bob', email='bob@example.com', password='password123'
        )

    def tearDown(self):
        cache.clear()

    def test_valid_credentials_login_directly_without_otp(self):
        response = self.client.post(reverse('login'), {
            'username': 'bob', 'password': 'password123',
        })
        self.assertRedirects(response, '/')
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.id)
        self.assertEqual(len(mail.outbox), 0)
        self.assertNotIn('otp_user_id', self.client.session)

    def test_login_honors_next_url(self):
        response = self.client.post(reverse('login'), {
            'username': 'bob', 'password': 'password123', 'next': '/profile/',
        })
        self.assertRedirects(response, '/profile/')

    def test_wrong_password_rejected(self):
        response = self.client.post(reverse('login'), {
            'username': 'bob', 'password': 'wrongpass',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(len(mail.outbox), 0)

    def test_admin_account_blocked_from_customer_login(self):
        admin = User.objects.create_superuser(
            username='root', email='root@example.com', password='rootpass123'
        )
        AdminProfile.objects.create(user=admin, role='admin', is_active=True)
        response = self.client.post(reverse('login'), {
            'username': 'root', 'password': 'rootpass123',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(response, 'admin portal')
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_user_cannot_login(self):
        User.objects.create_user(
            username='pending', email='pending@example.com',
            password='password123', is_active=False,
        )
        response = self.client.post(reverse('login'), {
            'username': 'pending', 'password': 'password123',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(response, 'correct username and password')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class RegisterOtpFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []

    def tearDown(self):
        cache.clear()

    def _register_post(self):
        return self.client.post(reverse('register'), {
            'username': 'carol',
            'email': 'carol@example.com',
            'password1': 'Str0ngPass!',
            'password2': 'Str0ngPass!',
        })

    def _get_code(self, user_id):
        from .otp import _otp_key
        return cache.get(_otp_key(user_id))

    def test_register_creates_inactive_user_and_sends_otp(self):
        response = self._register_post()
        self.assertRedirects(response, reverse('register_otp'))
        user = User.objects.get(username='carol')
        self.assertFalse(user.is_active)
        outbox = EmailOutbox.objects.filter(recipient='carol@example.com').first()
        self.assertIsNotNone(outbox)
        self.assertIn('verify your email', outbox.subject.lower())
        self.assertRegex(outbox.plain_body, r'\b\d{6}\b')
        self.assertEqual(self.client.session.get('otp_user_id'), user.id)

    def test_register_otp_page_requires_started_flow(self):
        response = self.client.get(reverse('register_otp'))
        self.assertRedirects(response, reverse('register'))

    def _payload(self, username='carol', email='carol@example.com', password='Str0ngPass!'):
        return {
            'username': username,
            'email': email,
            'password1': password,
            'password2': password,
        }

    def _assert_register_rejected(self, payload, message):
        response = self.client.post(reverse('register'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, message)
        self.assertEqual(
            EmailOutbox.objects.count(), 0,
            'no OTP email may be enqueued for an invalid registration',
        )
        self.assertNotIn('otp_user_id', self.client.session)
        return response

    def test_duplicate_name_rejected_before_otp(self):
        User.objects.create_user(
            username='Carol', email='carol.old@example.com', password='Str0ngPass!'
        )
        self._assert_register_rejected(
            self._payload(username='carol', email='brand.new@example.com'),
            'This name is already registered.',
        )
        self.assertFalse(User.objects.filter(username='carol').exists())

    def test_duplicate_email_rejected_before_otp(self):
        User.objects.create_user(
            username='alice', email='carol@example.com', password='Str0ngPass!'
        )
        self._assert_register_rejected(
            self._payload(username='carol', email='CAROL@Example.com'),
            'An account with this email already exists.',
        )
        self.assertFalse(User.objects.filter(username='carol').exists())

    def test_weak_password_rejected_before_otp(self):
        self._assert_register_rejected(
            self._payload(username='carol', email='carol@example.com', password='12345678'),
            'password',
        )
        self.assertFalse(User.objects.filter(username='carol').exists())

    def test_password_similar_to_name_rejected_before_otp(self):
        self._assert_register_rejected(
            self._payload(username='kiran', email='kiran@example.com', password='Kiran123'),
            'password',
        )
        self.assertFalse(User.objects.filter(username='kiran').exists())

    def test_register_otp_page_masks_email(self):
        self._register_post()
        response = self.client.get(reverse('register_otp'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'c***@example.com')

    def test_correct_otp_activates_account_and_logs_in(self):
        self._register_post()
        user = User.objects.get(username='carol')
        code = self._get_code(user.id)
        response = self.client.post(reverse('register_otp'), {'otp': code})
        self.assertRedirects(response, reverse('profile'))
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(int(self.client.session['_auth_user_id']), user.id)
        self.assertNotIn('otp_user_id', self.client.session)

    def test_unverified_user_cannot_login(self):
        self._register_post()
        user = User.objects.get(username='carol')
        self.assertFalse(user.is_active)
        response = self.client.post(reverse('login'), {
            'username': 'carol', 'password': 'Str0ngPass!',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(
            EmailOutbox.objects.filter(recipient='carol@example.com').count(), 1
        )

    def test_wrong_otp_keeps_user_inactive(self):
        self._register_post()
        response = self.client.post(reverse('register_otp'), {'otp': '000000'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Incorrect code')
        user = User.objects.get(username='carol')
        self.assertFalse(user.is_active)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_exhausting_attempts_redirects_to_register(self):
        self._register_post()
        for _ in range(5):
            self.client.post(reverse('register_otp'), {'otp': '000000'})
        response = self.client.post(reverse('register_otp'), {'otp': '000000'})
        self.assertRedirects(response, reverse('register'))
        self.assertNotIn('otp_user_id', self.client.session)

    def test_expired_otp_redirects_to_register(self):
        self._register_post()
        cache.clear()
        response = self.client.post(reverse('register_otp'), {'otp': '123456'})
        self.assertRedirects(response, reverse('register'))
        self.assertNotIn('otp_user_id', self.client.session)

    def test_resend_sends_new_code(self):
        self._register_post()
        user = User.objects.get(username='carol')
        first = self._get_code(user.id)
        response = self.client.post(reverse('register_otp_resend'))
        self.assertRedirects(response, reverse('register_otp'))
        self.assertEqual(
            EmailOutbox.objects.filter(recipient='carol@example.com').count(), 2
        )
        self.assertNotEqual(self._get_code(user.id), first)

    def test_resend_without_flow_redirects_to_register(self):
        response = self.client.post(reverse('register_otp_resend'))
        self.assertRedirects(response, reverse('register'))

    def test_resend_blocked_once_limit_reached(self):
        from .otp import OTP_MAX_RESENDS, _cooldown_key
        self._register_post()
        user = User.objects.get(username='carol')
        base = EmailOutbox.objects.filter(recipient='carol@example.com').count()
        for _ in range(OTP_MAX_RESENDS):
            cache.delete(_cooldown_key(user.id))
            response = self.client.post(reverse('register_otp_resend'))
            self.assertRedirects(response, reverse('register_otp'))
        self.assertEqual(
            EmailOutbox.objects.filter(recipient='carol@example.com').count(),
            base + OTP_MAX_RESENDS,
        )
        cache.delete(_cooldown_key(user.id))
        response = self.client.post(reverse('register_otp_resend'), follow=True)
        self.assertRedirects(response, reverse('register_otp'))
        self.assertContains(response, 'limit for resend requests')
        self.assertEqual(
            EmailOutbox.objects.filter(recipient='carol@example.com').count(),
            base + OTP_MAX_RESENDS,
        )


class LoggedOutGuardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='dave', email='dave@example.com', password='password123'
        )

    def _login(self):
        self.client.post(reverse('login'), {
            'username': 'dave', 'password': 'password123',
        })

    def _logout(self):
        return self.client.post(reverse('logout'))

    def test_back_button_after_logout_lands_on_home(self):
        self._login()
        self.assertEqual(self.client.get(reverse('profile')).status_code, 200)
        self._logout()
        response = self.client.get(reverse('profile'))
        self.assertRedirects(response, '/')

    def test_only_first_protected_page_after_logout_goes_home(self):
        self._login()
        self._logout()
        self.client.get(reverse('profile'))
        response = self.client.get(reverse('profile'))
        self.assertRedirects(response, reverse('login') + '?next=/profile/')

    def test_public_page_after_logout_not_redirected(self):
        self._login()
        self._logout()
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)

    def test_flag_cleared_on_next_login(self):
        self._login()
        self._logout()
        self._login()
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_without_logout_goes_to_login(self):
        response = self.client.get(reverse('profile'))
        self.assertRedirects(response, reverse('login') + '?next=/profile/')

    def test_expired_flag_goes_to_login(self):
        self._login()
        self._logout()
        session = self.client.session
        session[JUST_LOGGED_OUT_FLAG] = (
            timezone.now() - JUST_LOGGED_OUT_WINDOW - timezone.timedelta(minutes=1)
        ).isoformat()
        session.save()
        response = self.client.get(reverse('profile'))
        self.assertRedirects(response, reverse('login') + '?next=/profile/')


@DEMO_RAZORPAY
class ProfilePendingPaymentsTests(TestCase):
    """Dashboard surfaces active reservations so users can retry payment."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', email='alice@example.com', password='password123'
        )
        from movies.tests import _make_categories_and_prices, _make_show
        from movies.models import SeatCategory, ShowPrice

        self.movie, self.show, self.seats = _make_show()
        _make_categories_and_prices(self.show)
        self.client.force_login(self.user)

    def _reserve(self):
        from movies.services import create_reservation

        return create_reservation(self.user, self.show.id, [self.seats[0].id])

    def test_pending_reservation_shows_complete_payment_button(self):
        reservation = self._reserve()
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pending Payments')
        self.assertContains(response, 'Complete Payment')
        self.assertContains(
            response, reverse('payment_page', args=[reservation.token])
        )

    def test_paid_reservation_disappears_from_pending(self):
        from movies.payments import start_checkout, verify_and_confirm

        reservation = self._reserve()
        tx, checkout = start_checkout(self.user, reservation.token)
        verify_and_confirm(
            self.user, reservation.token,
            gateway_order_id=tx.gateway_order_id,
            gateway_payment_id='pay_DEMO_pending',
            gateway_signature=checkout['demo_signature'],
            method='upi',
        )
        response = self.client.get(reverse('profile'))
        self.assertContains(response, 'Nothing pending')
        self.assertNotContains(
            response, reverse('payment_page', args=[reservation.token])
        )


@DEMO_RAZORPAY
class ProfileBookingCardsTests(TestCase):
    """Profile shows one transaction-level card per reservation with
    View / Share / Invoice / Cancel actions."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', email='alice@example.com', password='password123'
        )
        from movies.tests import _make_categories_and_prices, _make_show

        self.movie, self.show, self.seats = _make_show()
        _make_categories_and_prices(self.show)
        self.client.force_login(self.user)

    def _confirm(self, *seat_ids):
        from movies.services import create_reservation

        from movies.payments import start_checkout, verify_and_confirm

        reservation = create_reservation(
            self.user, self.show.id, list(seat_ids)
        )
        tx, checkout = start_checkout(self.user, reservation.token)
        verify_and_confirm(
            self.user, reservation.token,
            gateway_order_id=tx.gateway_order_id,
            gateway_payment_id='pay_DEMO_pending',
            gateway_signature=checkout['demo_signature'],
            method='upi',
        )
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, 'booked')
        return reservation

    def test_profile_renders_transaction_booking_card(self):
        reservation = self._confirm(self.seats[0].id, self.seats[1].id)
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, reservation.booking_ref)
        self.assertContains(response, '2 Tickets')
        self.assertContains(response, 'M-Ticket Valid')
        self.assertContains(
            response, reverse('download_ticket', args=[reservation.booking_ref])
        )
        self.assertContains(
            response, reverse('booking_invoice', args=[reservation.booking_ref])
        )
        self.assertContains(response, 'btn-share')
        self.assertContains(response, 'data-wa-link=')
        self.assertContains(response, 'cancelBookingModal1')
        self.assertContains(
            response, reverse('cancel_booking_ref', args=[reservation.booking_ref])
        )

    def test_cancel_modal_flow_cancels_transaction(self):
        reservation = self._confirm(self.seats[0].id, self.seats[1].id)
        response = self.client.post(
            reverse('cancel_booking_ref', args=[reservation.booking_ref])
        )
        self.assertRedirects(response, reverse('profile'))

        reservation.refresh_from_db()
        self.assertEqual(reservation.status, 'cancelled')
        from movies.models import Booking

        self.assertEqual(
            Booking.objects.filter(reservation=reservation, status='cancelled').count(),
            2,
        )
        self.seats[0].refresh_from_db()
        self.seats[1].refresh_from_db()
        self.assertFalse(self.seats[0].is_booked)
        self.assertFalse(self.seats[1].is_booked)

        response = self.client.get(reverse('profile'))
        self.assertContains(response, 'booking-ticket--cancelled')
        self.assertContains(response, 'has been cancelled')
        self.assertNotContains(
            response, reverse('download_ticket', args=[reservation.booking_ref])
        )
        self.assertNotContains(
            response, reverse('cancel_booking_ref', args=[reservation.booking_ref])
        )

    def test_booking_in_the_past_has_no_cancel_button(self):
        reservation = self._confirm(self.seats[0].id)
        self.show.time = timezone.now() - timezone.timedelta(hours=2)
        self.show.save(update_fields=['time'])
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reservation.booking_ref)
        self.assertNotContains(
            response, reverse('cancel_booking_ref', args=[reservation.booking_ref])
        )
