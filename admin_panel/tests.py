from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from .models import AdminProfile, Genre, Payment
from movies.models import Movie, Theater, Seat, Booking, SeatCategory, ShowPrice


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
        self.assertFalse(Booking.objects.filter(id=booking.id).exists())
        self.assertFalse(Seat.objects.get(pk=self.seat.id).is_booked)
        self.assertGreater(Theater.objects.get(pk=self.show.id).seat_revision, before)
