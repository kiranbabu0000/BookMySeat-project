"""Real-Time Showtime Validation & Late-Entry Warning — 15 required scenarios.

Covers the dynamic show status (UPCOMING / LATE ENTRY / EXPIRED), the
configurable late-entry window, Asia/Kolkata date filtering (midnight
boundary), server-side enforcement at every booking/payment entry point,
frontend warning surfacing, and the admin dynamic status display.

Reference implementation: ``movies/showtime.py``.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from admin_panel.models import AdminProfile

from .models import Movie, Seat, Theater
from .services import ReservationError, confirm_booking, create_reservation
from .showtime import (
    day_range_utc,
    late_entry_window,
    show_bookable,
    show_status,
    show_status_info,
)
from .testutils import DEMO_RAZORPAY


def _fixed_now():
    return timezone.make_aware(datetime(2026, 8, 16, 10, 0, 0))


def _make_movie(name='Showtime Test Movie', duration=120):
    return Movie.objects.create(
        name=name, rating=7.5, cast='Actor', status='now_showing', duration=duration
    )


def _make_show(movie, started_minutes_ago=None, at=None, name='PVR Showtime'):
    if at is not None:
        show_time = at
    elif started_minutes_ago is not None:
        show_time = timezone.now() - timedelta(minutes=started_minutes_ago)
    else:
        show_time = timezone.now() + timedelta(hours=5)
    return Theater.objects.create(
        name=name, movie=movie, time=show_time, ticket_price=Decimal('250.00')
    )


def _add_seats(show, count=4):
    return [Seat.objects.create(theater=show, seat_number=f'B{i + 1}') for i in range(count)]


class ShowtimeStatusCoreTests(TestCase):
    """Scenarios 1-8: status computation, boundary rules, duration cap, config."""

    def setUp(self):
        self.movie = _make_movie()
        self.now = _fixed_now()

    def test_01_upcoming_before_start(self):
        show = _make_show(self.movie, at=self.now + timedelta(hours=2))
        self.assertEqual(show_status(show, now=self.now), 'upcoming')
        self.assertTrue(show_bookable(show, now=self.now))
        info = show_status_info(show, now=self.now)
        self.assertEqual(info['started_minutes_ago'], 0)
        self.assertTrue(info['bookable'])

    def test_02_exactly_at_start_is_late_entry(self):
        show = _make_show(self.movie, at=self.now)
        self.assertEqual(show_status(show, now=self.now), 'late_entry')
        self.assertTrue(show_bookable(show, now=self.now))

    def test_03_ten_minutes_after_start_is_late_entry(self):
        show = _make_show(self.movie, at=self.now - timedelta(minutes=10))
        self.assertEqual(show_status(show, now=self.now), 'late_entry')
        info = show_status_info(show, now=self.now)
        self.assertEqual(info['started_minutes_ago'], 10)
        self.assertTrue(info['bookable'])

    def test_04_exact_late_entry_boundary_is_expired(self):
        show = _make_show(self.movie, at=self.now - timedelta(minutes=30))
        self.assertEqual(late_entry_window(), 30)
        self.assertEqual(show_status(show, now=self.now), 'expired')

    def test_05_after_late_entry_window_is_expired_and_not_bookable(self):
        show = _make_show(self.movie, at=self.now - timedelta(minutes=45))
        self.assertEqual(show_status(show, now=self.now), 'expired')
        self.assertFalse(show_bookable(show, now=self.now))
        from .showtime import assert_show_bookable

        with self.assertRaises(ReservationError):
            assert_show_bookable(show, now=self.now)

    def test_06_movie_end_time_caps_late_entry_window(self):
        movie = _make_movie(duration=25)
        show = _make_show(movie, at=self.now - timedelta(minutes=30))
        self.assertEqual(show_status(show, now=self.now), 'expired')

    def test_07_no_duration_uses_window_only(self):
        movie = _make_movie(duration=None)
        within = _make_show(movie, at=self.now - timedelta(minutes=10))
        self.assertEqual(show_status(within, now=self.now), 'late_entry')
        at_boundary = _make_show(movie, at=self.now - timedelta(minutes=30))
        self.assertEqual(show_status(at_boundary, now=self.now), 'expired')

    def test_08_late_entry_window_is_configurable(self):
        show = _make_show(self.movie, at=self.now - timedelta(minutes=20))
        self.assertEqual(show_status(show, now=self.now), 'late_entry')
        with override_settings(LATE_ENTRY_WINDOW_MINUTES=15):
            self.assertEqual(late_entry_window(), 15)
            self.assertEqual(show_status(show, now=self.now), 'expired')


class ShowtimeDateAndListTests(TestCase):
    """Scenarios 9-11: IST midnight boundary, theater_list, book_seats."""

    def setUp(self):
        self.movie = _make_movie()
        self.user = User.objects.create_user('viewer', 'viewer@example.com', 'pw123')

    def test_09_midnight_show_appears_on_its_ist_day_tab(self):
        ist_today = timezone.localdate()
        ist_tomorrow = ist_today + timedelta(days=1)
        midnight_show = _make_show(
            self.movie, at=timezone.make_aware(
                datetime.combine(ist_tomorrow, datetime.min.time()).replace(hour=0, minute=30),
                timezone.get_current_timezone(),
            ),
            name='Midnight Hall',
        )
        local_day_start, _ = day_range_utc(ist_tomorrow)
        self.assertGreaterEqual(midnight_show.time, local_day_start)
        self.assertEqual(timezone.localtime(midnight_show.time).date(), ist_tomorrow)

        response = self._get(date=ist_tomorrow.isoformat())
        self.assertContains(response, 'Midnight Hall')
        response = self._get(date=ist_today.isoformat())
        self.assertNotContains(response, 'Midnight Hall')

    def test_10_theater_list_shows_late_entry_with_warning_hides_expired(self):
        late = _make_show(self.movie, started_minutes_ago=5, name='Late Hall')
        expired = _make_show(self.movie, started_minutes_ago=45, name='Dead Hall')
        _add_seats(late)
        _add_seats(expired)

        response = self._get()
        self.assertContains(response, 'Late Hall')
        self.assertContains(response, 'Late entry')
        self.assertNotContains(response, 'Dead Hall')

    def test_11_book_seats_warns_on_late_entry_and_redirects_when_expired(self):
        self.client.force_login(self.user)
        late = _make_show(self.movie, started_minutes_ago=5, name='Late Hall')
        _add_seats(late)
        response = self.client.get(reverse('book_seats', args=[late.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This show has already started')

        expired = _make_show(self.movie, started_minutes_ago=45, name='Dead Hall')
        _add_seats(expired)
        response = self.client.get(reverse('book_seats', args=[expired.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('theater_list', args=[self.movie.id]), response.url)

    def _get(self, **params):
        return self.client.get(reverse('theater_list', args=[self.movie.id]), params)


class ShowtimeBackendEnforcementTests(TestCase):
    """Scenarios 12-14: services + payments reject expired shows server-side."""

    def setUp(self):
        self.user = User.objects.create_user('alice', 'alice@example.com', 'password123')
        self.movie = _make_movie()

    def test_12_create_reservation_allows_late_entry_and_rejects_expired(self):
        late = _make_show(self.movie, started_minutes_ago=5, name='Late Hall')
        late_seats = _add_seats(late)
        reservation = create_reservation(self.user, late.id, [late_seats[0].id])
        self.assertEqual(reservation.status, 'active')

        expired = _make_show(self.movie, started_minutes_ago=45, name='Dead Hall')
        expired_seats = _add_seats(expired)
        with self.assertRaises(ReservationError):
            create_reservation(self.user, expired.id, [expired_seats[0].id])

    def test_13_confirm_booking_allows_late_entry_and_rejects_expired(self):
        late = _make_show(self.movie, name='Late Hall')
        late_seats = _add_seats(late)
        reservation = create_reservation(self.user, late.id, [late_seats[0].id])
        Theater.objects.filter(pk=late.pk).update(
            time=timezone.now() - timedelta(minutes=5)
        )
        _, bookings = confirm_booking(self.user, reservation.token)
        self.assertEqual(len(bookings), 1)
        self.assertEqual(bookings[0].status, 'confirmed')

        expired = _make_show(self.movie, name='Dead Hall')
        expired_seats = _add_seats(expired)
        reservation = create_reservation(self.user, expired.id, [expired_seats[0].id])
        Theater.objects.filter(pk=expired.pk).update(
            time=timezone.now() - timedelta(minutes=45)
        )
        with self.assertRaises(ReservationError):
            confirm_booking(self.user, reservation.token)

    @DEMO_RAZORPAY
    def test_14_payment_page_and_start_checkout_respect_showtime(self):
        from . import payments

        late = _make_show(self.movie, name='Late Hall')
        late_seats = _add_seats(late)
        reservation = create_reservation(self.user, late.id, [late_seats[0].id])
        Theater.objects.filter(pk=late.pk).update(
            time=timezone.now() - timedelta(minutes=5)
        )
        self.client.force_login(self.user)
        page = self.client.get(reverse('payment_page', args=[reservation.token]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'This show has already started')
        _tx, checkout = payments.start_checkout(self.user, reservation.token)
        self.assertTrue(checkout['demo'])

        expired = _make_show(self.movie, name='Dead Hall')
        expired_seats = _add_seats(expired)
        reservation = create_reservation(self.user, expired.id, [expired_seats[0].id])
        Theater.objects.filter(pk=expired.pk).update(
            time=timezone.now() - timedelta(minutes=45)
        )
        with self.assertRaises(ReservationError):
            payments.start_checkout(self.user, reservation.token)
        response = self.client.get(reverse('payment_page', args=[reservation.token]))
        self.assertEqual(response.status_code, 302)


class ShowtimeAdminStatusTests(TestCase):
    """Scenario 15: admin show list renders dynamic showtime status."""

    def setUp(self):
        self.movie = _make_movie()
        self.admin = User.objects.create_user(
            username='admin', password='adminpass123', is_staff=True
        )
        AdminProfile.objects.create(user=self.admin, role='admin', is_active=True)
        self.client.post('/admin-login/', {
            'username': 'admin', 'password': 'adminpass123'
        })

    def test_15_admin_show_list_shows_dynamic_status(self):
        upcoming = _make_show(self.movie, started_minutes_ago=-120, name='Upcoming Hall')
        late = _make_show(self.movie, started_minutes_ago=10, name='Late Hall')
        expired = _make_show(self.movie, started_minutes_ago=45, name='Dead Hall')
        _add_seats(upcoming)
        _add_seats(late)
        _add_seats(expired)

        response = self.client.get(reverse('admin_show_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'UPCOMING')
        self.assertContains(response, 'LIVE · LATE ENTRY')
        self.assertContains(response, 'EXPIRED')
        self.assertContains(response, 'Started 10 min ago')

        statuses = [s.status_info['status'] for s in response.context['shows']]
        self.assertIn('upcoming', statuses)
        self.assertIn('late_entry', statuses)
        self.assertIn('expired', statuses)
