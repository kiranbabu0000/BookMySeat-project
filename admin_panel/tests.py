from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from .models import AdminProfile, Genre, Payment, PaymentTransaction, Theatre, Screen, Show
from movies.models import Movie, Theater, Seat, Booking, Reservation, ReservedSeat, SeatCategory, ShowPrice


class LoginSeparationTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username='customer', password='customerpass123')
        self.admin = User.objects.create_user(
            username='admin', password='adminpass123', is_staff=True)
        AdminProfile.objects.create(user=self.admin, role='admin', is_active=True)
        self.super = User.objects.create_superuser(
            username='super', password='superpass123')

    def _admin_login(self, client, username='admin', password='adminpass123'):
        return client.post('/admin-login/', {
            'username': username, 'password': password})

    def test_customer_credentials_rejected_on_admin_login(self):
        client = self.client
        response = client.post('/admin-login/', {
            'username': 'customer', 'password': 'customerpass123'})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(client.session.get('admin_user_id'))
        self.assertFalse(client.session.get('is_admin_authenticated'))

    def test_admin_credentials_rejected_on_customer_login(self):
        client = self.client
        response = client.post('/login/', {
            'username': 'admin', 'password': 'adminpass123'})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(client.session.get('_auth_user_id'))

    def test_admin_login_creates_admin_session_not_customer_session(self):
        client = self.client
        response = self._admin_login(client)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.session.get('admin_user_id'), self.admin.id)
        self.assertIsNone(client.session.get('_auth_user_id'))
        self.assertTrue(client.session.get('is_admin_authenticated'))

    def test_customer_login_cannot_access_admin_portal(self):
        client = self.client
        self.assertTrue(client.login(username='customer', password='customerpass123'))
        response = client.get('/dashboard/')
        self.assertRedirects(response, '/admin-login/')
        self.assertFalse(client.session.get('is_admin_authenticated'))

    def test_anonymous_cannot_access_admin_portal(self):
        client = self.client
        response = client.get('/admin-movies/')
        self.assertRedirects(response, '/admin-login/')

    def test_django_style_login_cannot_access_admin_portal(self):
        client = self.client
        self.assertTrue(client.login(username='admin', password='adminpass123'))
        response = client.get('/dashboard/')
        self.assertRedirects(response, '/admin-login/')

    def test_admin_login_cannot_access_customer_profile(self):
        client = self.client
        self._admin_login(client)
        response = client.get('/profile/')
        self.assertRedirects(response, '/login/?next=/profile/')

    def test_admin_logout_keeps_customer_session(self):
        client = self.client
        self.assertTrue(client.login(username='customer', password='customerpass123'))
        self._admin_login(client)
        client.post('/admin-logout/')
        self.assertIsNone(client.session.get('admin_user_id'))
        self.assertFalse(client.session.get('is_admin_authenticated'))
        self.assertEqual(int(client.session.get('_auth_user_id')), self.customer.id)

    def test_customer_logout_keeps_admin_session(self):
        client = self.client
        self._admin_login(client)
        self.assertTrue(client.login(username='customer', password='customerpass123'))
        client.post('/logout/')
        self.assertIsNone(client.session.get('_auth_user_id'))
        self.assertEqual(client.session.get('admin_user_id'), self.admin.id)

    def test_superuser_without_profile_can_login_to_portal(self):
        client = self.client
        response = self._admin_login(client, username='super', password='superpass123')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.session.get('admin_user_id'), self.super.id)


class AdminCrashPathTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='adminpass123', is_staff=True)
        AdminProfile.objects.create(user=self.admin, role='admin', is_active=True)
        self.super = User.objects.create_superuser(
            username='super', password='superpass123')
        self.movie = Movie.objects.create(
            name='Crash Test Movie', rating=7.5, cast='Actor', duration=120,
            status='now_showing')
        self.show = Theater.objects.create(
            name='PVR', movie=self.movie,
            time=timezone.now() + timedelta(days=1), ticket_price=Decimal('250.00'))
        self.seat = Seat.objects.create(theater=self.show, seat_number='A1')

    def _admin_login(self, client, username='admin', password='adminpass123'):
        return client.post('/admin-login/', {
            'username': username, 'password': password})

    def test_seat_management_invalid_rows_do_not_crash(self):
        client = self.client
        self._admin_login(client)
        response = client.post('/seats/', {
            'action': 'generate_seats',
            'theater_id': self.show.id,
            'rows': 'abc',
            'seats_per_row': '10',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Seat.objects.filter(theater=self.show).count(), 1)

    def test_seat_management_excessive_rows_are_rejected(self):
        client = self.client
        self._admin_login(client)
        response = client.post('/seats/', {
            'action': 'generate_seats',
            'theater_id': self.show.id,
            'rows': '5000',
            'seats_per_row': '5000',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Seat.objects.filter(theater=self.show).count(), 1)

    def test_pricing_show_edit_with_no_categories_does_not_crash(self):
        client = self.client
        self._admin_login(client, username='super', password='superpass123')
        response = client.post(reverse('admin_pricing_show_edit', args=[self.show.id]), {})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ShowPrice.objects.filter(theater=self.show).count(), 0)

    def test_pricing_config_bad_slab_max_does_not_crash(self):
        client = self.client
        self._admin_login(client, username='super', password='superpass123')
        response = client.post(reverse('admin_pricing_config'), {
            'action': 'save_slabs',
            'slab_id': ['', ''],
            'slab_min': ['0', '100'],
            'slab_max': ['abc', ''],
            'slab_rate': ['5', '18'],
            'slab_order': ['1', '2'],
        })
        self.assertEqual(response.status_code, 302)

    def test_genre_create_slug_collision_does_not_crash(self):
        Genre.objects.create(name='Action', slug='action')
        client = self.client
        self._admin_login(client)
        response = client.post(reverse('admin_genre_add'), {'name': 'action'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Genre.objects.count(), 1)

    def test_admin_booking_cancel_bumps_revision_and_frees_seat(self):
        booking = Booking.objects.create(
            user=self.super, seat=self.seat, movie=self.movie, theater=self.show)
        Payment.objects.create(
            booking=booking, amount=Decimal('250.00'), status='completed')
        before = Theater.objects.get(pk=self.show.id).seat_revision
        client = self.client
        self._admin_login(client)
        response = client.post(reverse('admin_booking_cancel', args=[booking.id]))
        self.assertEqual(response.status_code, 302)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'cancelled')
        self.assertFalse(Seat.objects.get(pk=self.seat.id).is_booked)
        self.assertGreater(Theater.objects.get(pk=self.show.id).seat_revision, before)

    def test_admin_booking_reserve_creates_unique_refs_and_payment(self):
        Seat.objects.create(theater=self.show, seat_number='A2')
        Seat.objects.create(theater=self.show, seat_number='A3')
        client = self.client
        self._admin_login(client)
        response = client.post(reverse('admin_booking_reserve'), {
            'user': self.super.id,
            'movie': self.movie.id,
            'show': self.show.id,
            'seat_count': '2',
        })
        self.assertEqual(response.status_code, 302)
        bookings = list(Booking.objects.filter(theater=self.show).order_by('id'))
        self.assertEqual(len(bookings), 2)
        self.assertTrue(all(b.booking_ref for b in bookings))
        self.assertEqual(
            len({b.booking_ref for b in bookings}), 2,
            'booking_ref must be unique per seat',
        )
        self.assertTrue(all(b.status == 'confirmed' for b in bookings))
        self.assertEqual(
            Payment.objects.filter(booking__in=bookings, status='completed').count(), 2)

    def test_admin_booking_reserve_rejects_wrong_movie_for_show(self):
        other_movie = Movie.objects.create(
            name='Other Movie', rating=6.0, cast='X', duration=100,
            status='now_showing')
        client = self.client
        self._admin_login(client)
        response = client.post(reverse('admin_booking_reserve'), {
            'user': self.super.id,
            'movie': other_movie.id,
            'show': self.show.id,
            'seat_count': '1',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Booking.objects.count(), 0)
        self.assertFalse(Seat.objects.get(pk=self.seat.id).is_booked)

    def test_admin_booking_reserve_respects_selected_show(self):
        other_show = Theater.objects.create(
            name='INOX', movie=self.movie,
            time=timezone.now() + timedelta(days=1), ticket_price=Decimal('300.00'))
        Seat.objects.create(theater=other_show, seat_number='B1')
        client = self.client
        self._admin_login(client)
        response = client.post(reverse('admin_booking_reserve'), {
            'user': self.super.id,
            'movie': self.movie.id,
            'show': other_show.id,
            'seat_count': '1',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Booking.objects.filter(theater=other_show).count(), 1)
        self.assertEqual(Booking.objects.filter(theater=self.show).count(), 0)

    def test_admin_booking_modify_rejects_cross_theater_seat(self):
        other_show = Theater.objects.create(
            name='INOX', movie=self.movie,
            time=timezone.now() + timedelta(days=1), ticket_price=Decimal('300.00'))
        other_seat = Seat.objects.create(theater=other_show, seat_number='B1')
        booking = Booking.objects.create(
            user=self.super, seat=self.seat, movie=self.movie, theater=self.show)
        client = self.client
        self._admin_login(client)
        response = client.post(reverse('admin_booking_modify', args=[booking.id]), {
            'new_seat': other_seat.id,
        })
        self.assertEqual(response.status_code, 302)
        booking.refresh_from_db()
        self.assertEqual(booking.seat_id, self.seat.id)
        self.assertTrue(Seat.objects.get(pk=other_seat.id).is_booked is False)

    def test_admin_booking_modify_swaps_seat_within_show(self):
        replacement = Seat.objects.create(theater=self.show, seat_number='A2')
        booking = Booking.objects.create(
            user=self.super, seat=self.seat, movie=self.movie, theater=self.show)
        client = self.client
        self._admin_login(client)
        response = client.post(reverse('admin_booking_modify', args=[booking.id]), {
            'new_seat': replacement.id,
        })
        self.assertEqual(response.status_code, 302)
        booking.refresh_from_db()
        self.assertEqual(booking.seat_id, replacement.id)
        self.assertFalse(Seat.objects.get(pk=self.seat.id).is_booked)
        self.assertTrue(Seat.objects.get(pk=replacement.id).is_booked)

    def test_admin_booking_resend_confirmation_does_not_crash(self):
        booking = Booking.objects.create(
            user=self.super, seat=self.seat, movie=self.movie, theater=self.show)
        client = self.client
        self._admin_login(client)
        response = client.post(
            reverse('admin_booking_resend', args=[booking.id]))
        self.assertEqual(response.status_code, 302)

    def test_admin_booking_reserve_skips_seats_held_by_customer_reservation(self):
        held_seat = Seat.objects.create(theater=self.show, seat_number='A2')
        Seat.objects.create(theater=self.show, seat_number='A3')
        reservation = Reservation.objects.create(
            token='held-token-1', user=self.super, show=self.show,
            status='active', payment_status='pending',
            expires_at=timezone.now() + timedelta(minutes=2),
        )
        ReservedSeat.objects.create(reservation=reservation, seat=held_seat)
        client = self.client
        self._admin_login(client)
        response = client.post(reverse('admin_booking_reserve'), {
            'user': self.super.id,
            'movie': self.movie.id,
            'show': self.show.id,
            'seat_count': '2',
        })
        self.assertEqual(response.status_code, 302)
        booked = list(
            Booking.objects.filter(theater=self.show)
            .values_list('seat_id', flat=True)
        )
        self.assertEqual(len(booked), 2)
        self.assertNotIn(held_seat.id, booked)
        self.assertFalse(Seat.objects.get(pk=held_seat.id).is_booked)

    def test_admin_booking_reserve_records_fees_and_gst(self):
        Seat.objects.create(theater=self.show, seat_number='A2')
        client = self.client
        self._admin_login(client)
        response = client.post(reverse('admin_booking_reserve'), {
            'user': self.super.id,
            'movie': self.movie.id,
            'show': self.show.id,
            'seat_count': '1',
        })
        self.assertEqual(response.status_code, 302)
        booking = Booking.objects.get(theater=self.show)
        self.assertGreater(booking.total, booking.ticket_price)
        self.assertEqual(booking.total, booking.platform_fee + booking.misc_fee + booking.ticket_price)
        payment = Payment.objects.get(booking=booking)
        self.assertEqual(payment.amount, booking.total)

    def test_admin_payment_refund_only_refunds_own_captured_payments(self):
        reservation = Reservation.objects.create(
            token='refund-token-1', user=self.super, show=self.show,
            status='booked', payment_status='completed',
            expires_at=timezone.now() + timedelta(minutes=2),
        )
        booking = Booking.objects.create(
            user=self.super, seat=self.seat, movie=self.movie,
            theater=self.show, reservation=reservation, booking_ref='BMSREFTEST1')
        other_seat = Seat.objects.create(theater=self.show, seat_number='A2')
        other_booking = Booking.objects.create(
            user=self.super, seat=other_seat, movie=self.movie,
            theater=self.show, reservation=reservation, booking_ref='BMSREFTEST2')
        paid_at = timezone.now() - timedelta(hours=2)
        Payment.objects.create(
            booking=booking, amount=Decimal('250.00'), status='completed',
            transaction_id='pay_XYZ')
        Payment.objects.create(
            booking=other_booking, amount=Decimal('250.00'), status='completed',
            transaction_id='pay_OTHER')
        Payment.objects.filter(transaction_id__in=['pay_XYZ', 'pay_OTHER']).update(paid_at=paid_at)
        tx = PaymentTransaction.objects.create(
            reservation=reservation, user=self.super,
            gateway_order_id='order_1', gateway_payment_id='pay_XYZ',
            amount=Decimal('250.00'), status='captured', is_demo=True,
            captured_at=timezone.now())
        client = self.client
        self._admin_login(client, username='super', password='superpass123')
        response = client.post(
            reverse('admin_payment_refund', args=[tx.id]))
        self.assertEqual(response.status_code, 302)
        tx.refresh_from_db()
        self.assertEqual(tx.status, 'refunded')
        self.assertEqual(Payment.objects.get(booking=booking).status, 'refunded')
        self.assertEqual(Payment.objects.get(booking=other_booking).status, 'completed')
        self.assertEqual(
            Payment.objects.get(booking=booking).paid_at, paid_at,
            'refund must not rewrite the original payment timestamp')

    def test_admin_booking_modify_updates_payment_amount(self):
        replacement = Seat.objects.create(theater=self.show, seat_number='A2')
        booking = Booking.objects.create(
            user=self.super, seat=self.seat, movie=self.movie, theater=self.show)
        Payment.objects.create(
            booking=booking, amount=Decimal('250.00'), status='completed')
        client = self.client
        self._admin_login(client)
        response = client.post(reverse('admin_booking_modify', args=[booking.id]), {
            'new_seat': replacement.id,
        })
        self.assertEqual(response.status_code, 302)
        booking.refresh_from_db()
        self.assertEqual(booking.seat_id, replacement.id)
        payment = Payment.objects.get(booking=booking)
        self.assertEqual(payment.amount, booking.total)
        self.assertGreater(booking.total, Decimal('250.00'))


class MovieDeletionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='adminpass123', is_staff=True)
        AdminProfile.objects.create(user=self.admin, role='admin', is_active=True)
        self.movie = Movie.objects.create(
            name='Avengers', rating=8.0, cast='Heroes', duration=150,
            status='now_showing')
        self.running_show = Theater.objects.create(
            name='PVR', movie=self.movie,
            time=timezone.now() + timedelta(days=2), ticket_price=Decimal('250.00'))
        self.running_seat = Seat.objects.create(
            theater=self.running_show, seat_number='A1')
        self._admin_login(self.client)

    def _admin_login(self, client, username='admin', password='adminpass123'):
        return client.post('/admin-login/', {
            'username': username, 'password': password})

    def _hard_delete(self, movie_id):
        return self.client.post(
            reverse('admin_movie_delete', args=[movie_id]),
            {'action': 'hard_delete'})

    def test_movie_with_booking_on_running_show_cannot_be_hard_deleted(self):
        Booking.objects.create(
            user=self.admin, seat=self.running_seat, movie=self.movie,
            theater=self.running_show)
        response = self._hard_delete(self.movie.id)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Movie.objects.filter(id=self.movie.id).exists())

    def test_movie_can_be_hard_deleted_after_tickets_cancelled_even_with_leftover_reservation(self):
        booking = Booking.objects.create(
            user=self.admin, seat=self.running_seat, movie=self.movie,
            theater=self.running_show)
        Reservation.objects.create(
            token='leftover-res-token', user=self.admin, show=self.running_show,
            status='booked', payment_status='completed',
            expires_at=timezone.now() + timedelta(days=1))
        booking.delete()
        response = self._hard_delete(self.movie.id)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Movie.objects.filter(id=self.movie.id).exists())

    def test_movie_with_booking_only_on_past_show_can_be_hard_deleted(self):
        past_show = Theater.objects.create(
            name='INOX', movie=self.movie,
            time=timezone.now() - timedelta(days=1), ticket_price=Decimal('250.00'))
        past_seat = Seat.objects.create(theater=past_show, seat_number='B1')
        Booking.objects.create(
            user=self.admin, seat=past_seat, movie=self.movie, theater=past_show)
        response = self._hard_delete(self.movie.id)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Movie.objects.filter(id=self.movie.id).exists())

    def test_removal_list_allows_delete_after_tickets_cancelled(self):
        booking = Booking.objects.create(
            user=self.admin, seat=self.running_seat, movie=self.movie,
            theater=self.running_show)
        Reservation.objects.create(
            token='leftover-res-token-2', user=self.admin, show=self.running_show,
            status='booked', payment_status='completed',
            expires_at=timezone.now() + timedelta(days=1))
        response = self.client.get(reverse('admin_movie_removal'))
        self.assertNotContains(
            response,
            f'action="/admin-movies/{self.movie.id}/delete/"')
        booking.delete()
        response = self.client.get(reverse('admin_movie_removal'))
        self.assertContains(
            response,
            f'action="/admin-movies/{self.movie.id}/delete/"')


class ShowTheaterSyncTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='adminpass123', is_staff=True)
        AdminProfile.objects.create(user=self.admin, role='admin', is_active=True)
        self.customer = User.objects.create_user(
            username='customer', password='customerpass123')
        self.movie = Movie.objects.create(
            name='Sync Test Movie', rating=8.0, cast='Actor',
            status='now_showing', show_on_homepage=True)
        self.theatre = Theatre.objects.create(
            name='PVR Nexus', city='Bengaluru')
        self.screen = Screen.objects.create(
            theatre=self.theatre, name='Screen 1', capacity=60)
        self.show_date = (timezone.now() + timedelta(days=1)).date()
        self.show_time = '14:30'

    def _admin_login(self, client):
        return client.post('/admin-login/', {
            'username': 'admin', 'password': 'adminpass123'})

    def _show_payload(self, **overrides):
        payload = {
            'movie': self.movie.id,
            'theatre': self.theatre.id,
            'screen': self.screen.id,
            'date': self.show_date.isoformat(),
            'time': self.show_time,
            'ticket_price': '250.00',
            'status': 'active',
        }
        payload.update(overrides)
        return payload

    def test_creating_show_creates_bookable_theater_with_seats(self):
        self._admin_login(self.client)
        response = self.client.post(reverse('admin_show_add'), self._show_payload())
        self.assertRedirects(response, reverse('admin_show_list'))
        show = Show.objects.get(movie=self.movie, theatre=self.theatre)
        self.assertIsNotNone(show.theater_id)
        theater = show.theater
        self.assertEqual(theater.name, self.theatre.name)
        self.assertEqual(theater.movie_id, self.movie.id)
        self.assertEqual(theater.ticket_price, Decimal('250.00'))
        self.assertEqual(theater.status, 'active')
        self.assertEqual(theater.screen_name, 'Screen 1')
        self.assertEqual(theater.time.date(), self.show_date)
        self.assertEqual(theater.time.time().strftime('%H:%M'), self.show_time)
        self.assertGreater(theater.seats.count(), 0)

        public = self.client.get(reverse('theater_list', args=[self.movie.id]))
        self.assertContains(public, self.theatre.name)

        self.client.force_login(self.customer)
        booking_page = self.client.get(reverse('book_seats', args=[theater.id]))
        self.assertEqual(booking_page.status_code, 200)

    def test_updating_show_syncs_linked_theater(self):
        self._admin_login(self.client)
        self.client.post(reverse('admin_show_add'), self._show_payload())
        show = Show.objects.get(movie=self.movie, theatre=self.theatre)
        new_date = (timezone.now() + timedelta(days=3)).date()
        self.client.post(reverse('admin_show_edit', args=[show.id]), self._show_payload(
            date=new_date.isoformat(), time='18:45', ticket_price='300.00'))
        show.refresh_from_db()
        theater = show.theater
        self.assertEqual(theater.time.date(), new_date)
        self.assertEqual(theater.time.time().strftime('%H:%M'), '18:45')
        self.assertEqual(theater.ticket_price, Decimal('300.00'))

    def test_toggle_status_syncs_linked_theater_and_hides_from_public(self):
        self._admin_login(self.client)
        self.client.post(reverse('admin_show_add'), self._show_payload())
        show = Show.objects.get(movie=self.movie, theatre=self.theatre)
        theater_id = show.theater_id
        self.client.post(reverse('admin_show_toggle_status', args=[show.id]))
        show.refresh_from_db()
        theater = Theater.objects.get(pk=theater_id)
        self.assertEqual(theater.status, 'sold_out')
        self.assertEqual(show.status, 'sold_out')

        self.client.post(reverse('admin_show_toggle_status', args=[show.id]))
        self.client.post(reverse('admin_show_toggle_status', args=[show.id]))
        theater = Theater.objects.get(pk=theater_id)
        self.assertEqual(theater.status, 'cancelled')

        public = self.client.get(reverse('theater_list', args=[self.movie.id]))
        self.assertNotContains(public, self.theatre.name)

        self.client.force_login(self.customer)
        self.assertEqual(
            self.client.get(reverse('book_seats', args=[theater_id])).status_code,
            404,
        )

    def test_bulk_cancel_syncs_linked_theaters(self):
        self._admin_login(self.client)
        screen2 = Screen.objects.create(
            theatre=self.theatre, name='Screen 2', capacity=40)
        self.client.post(reverse('admin_show_add'), self._show_payload())
        self.client.post(reverse('admin_show_add'), self._show_payload(
            screen=screen2.id, time='20:00'))
        self.assertEqual(Theater.objects.filter(status='active').count(), 2)
        self.client.post(reverse('admin_show_bulk_action'), {
            'movie': self.movie.id,
        })
        self.assertEqual(Theater.objects.filter(status='active').count(), 0)
        self.assertEqual(Theater.objects.filter(status='cancelled').count(), 2)
        self.assertEqual(Show.objects.filter(status='cancelled').count(), 2)

    def test_delete_show_with_bookings_keeps_theater_cancelled(self):
        self._admin_login(self.client)
        self.client.post(reverse('admin_show_add'), self._show_payload())
        show = Show.objects.get(movie=self.movie, theatre=self.theatre)
        theater = show.theater
        seat = theater.seats.first()
        Booking.objects.create(
            user=self.customer, seat=seat, movie=self.movie, theater=theater)
        self.client.post(reverse('admin_show_delete', args=[show.id]))
        self.assertFalse(Show.objects.filter(id=show.id).exists())
        theater.refresh_from_db()
        self.assertEqual(theater.status, 'cancelled')
        self.assertTrue(Booking.objects.filter(theater=theater).exists())

    def test_delete_show_without_bookings_deletes_theater(self):
        self._admin_login(self.client)
        self.client.post(reverse('admin_show_add'), self._show_payload())
        show = Show.objects.get(movie=self.movie, theatre=self.theatre)
        theater_id = show.theater_id
        self.client.post(reverse('admin_show_delete', args=[show.id]))
        self.assertFalse(Show.objects.filter(id=show.id).exists())
        self.assertFalse(Theater.objects.filter(id=theater_id).exists())

    def test_non_active_theater_hidden_from_public_booking(self):
        Theater.objects.create(
            name='Cancelled INOX', movie=self.movie,
            time=timezone.now() + timedelta(days=1),
            ticket_price=Decimal('250.00'), status='cancelled')
        public = self.client.get(reverse('theater_list', args=[self.movie.id]))
        self.assertNotContains(public, 'Cancelled INOX')
        self.client.force_login(self.customer)
        self.assertFalse(
            Theater.objects.filter(movie=self.movie, status='active').exists()
        )
