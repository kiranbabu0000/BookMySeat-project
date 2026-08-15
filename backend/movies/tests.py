from io import BytesIO
import threading
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import datetime, time, timedelta

from admin_panel.models import Notification, Review, ReviewHelpful

from .models import Movie, Theater, Seat, SeatCategory, ShowPrice, Reservation, ReservedSeat, Booking, Wishlist, EmailOutbox
from .testutils import DEMO_RAZORPAY
from .services import (
    ReservationError,
    cancel_booking,
    category_for_seat,
    confirm_booking,
    create_reservation,
    get_pricing_config,
    gst_rate_for,
    gst_slabs,
    modify_reservation,
    release_expired_reservations,
    release_reservation,
    reservation_pricing,
    seat_price,
    seat_states_for_show,
    validate_coupon,
)


def _make_show(seat_count=15):
    movie = Movie.objects.create(
        name='Race Test Movie', rating=7.5, cast='Actor', status='now_showing'
    )
    show = Theater.objects.create(
        name='PVR Test',
        movie=movie,
        time=timezone.now() + timedelta(hours=5),
    )
    seats = [
        Seat.objects.create(theater=show, seat_number=f'A{i + 1}')
        for i in range(seat_count)
    ]
    return movie, show, seats


def _make_categories_and_prices(show):
    silver = SeatCategory.objects.create(
        name='SILVER', row_start='A', row_end='C', display_order=1
    )
    gold = SeatCategory.objects.create(
        name='GOLD', row_start='D', row_end='G', display_order=2
    )
    platinum = SeatCategory.objects.create(
        name='PLATINUM', row_start='H', row_end='Z', display_order=3
    )
    ShowPrice.objects.create(theater=show, category=silver, price=Decimal('180.00'))
    ShowPrice.objects.create(theater=show, category=gold, price=Decimal('250.00'))
    ShowPrice.objects.create(theater=show, category=platinum, price=Decimal('320.00'))
    return silver, gold, platinum


class ReservationServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', 'alice@example.com', 'password123')
        self.other = User.objects.create_user('bob', 'bob@example.com', 'password123')
        self.movie, self.show, self.seats = _make_show()

    def test_create_reservation_holds_seats(self):
        reservation = create_reservation(
            self.user, self.show.id, [self.seats[0].id, self.seats[1].id]
        )
        self.assertEqual(reservation.status, 'active')
        self.assertEqual(reservation.payment_status, 'pending')
        self.assertEqual(set(reservation.seat_numbers), {'A1', 'A2'})
        states = seat_states_for_show(self.show)
        self.assertEqual(states[str(self.seats[0].id)], 'reserved')
        self.assertEqual(states[str(self.seats[2].id)], 'available')

    def test_reservation_hold_is_five_minutes(self):
        from .models import RESERVATION_HOLD_SECONDS

        self.assertEqual(RESERVATION_HOLD_SECONDS, 300)
        reservation = create_reservation(
            self.user, self.show.id, [self.seats[0].id]
        )
        expected = timezone.now() + timedelta(seconds=RESERVATION_HOLD_SECONDS)
        self.assertLessEqual(
            abs((reservation.expires_at - expected).total_seconds()), 5
        )

    def test_create_reservation_reuses_existing_active_reservation(self):
        first = create_reservation(self.user, self.show.id, [self.seats[0].id])
        second = create_reservation(self.user, self.show.id, [self.seats[0].id])
        self.assertEqual(first.token, second.token)
        self.assertEqual(
            Reservation.objects.filter(user=self.user).count(), 1
        )

    def test_create_reservation_rejects_seat_change_on_active_hold(self):
        # Requesting different seats while an active hold exists must fail
        # loudly instead of silently returning the hold for other seats.
        create_reservation(self.user, self.show.id, [self.seats[0].id])
        with self.assertRaises(ReservationError):
            create_reservation(self.user, self.show.id, [self.seats[1].id])

    def test_seat_held_by_another_user_is_rejected(self):
        create_reservation(self.user, self.show.id, [self.seats[0].id])
        with self.assertRaises(ReservationError):
            create_reservation(self.other, self.show.id, [self.seats[0].id])

    def test_expired_reservation_is_lazily_released(self):
        first = create_reservation(self.user, self.show.id, [self.seats[0].id])
        Reservation.objects.filter(pk=first.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        second = create_reservation(self.other, self.show.id, [self.seats[0].id])
        self.assertEqual(second.user, self.other)
        self.assertEqual(set(second.seat_numbers), {'A1'})
        first.refresh_from_db()
        self.assertEqual(first.status, 'expired')

    def test_release_expired_reservations_cleans_everything(self):
        r1 = create_reservation(self.user, self.show.id, [self.seats[0].id])
        r2 = create_reservation(self.other, self.show.id, [self.seats[1].id])
        Reservation.objects.filter(pk=r1.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        Reservation.objects.filter(pk=r2.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        count = release_expired_reservations()
        self.assertEqual(count, 2)
        self.assertEqual(Reservation.objects.filter(status='expired').count(), 2)
        self.assertEqual(ReservedSeat.objects.count(), 0)

    def test_modify_reservation_adds_and_removes_and_refreshes_expiry(self):
        reservation = create_reservation(
            self.user, self.show.id, [self.seats[0].id, self.seats[1].id]
        )
        old_expiry = reservation.expires_at
        modify_reservation(
            self.user,
            reservation.token,
            add_seat_ids=[self.seats[2].id],
            remove_seat_ids=[self.seats[0].id],
        )
        reservation.refresh_from_db()
        self.assertEqual(set(reservation.seat_numbers), {'A2', 'A3'})
        self.assertGreater(reservation.expires_at, old_expiry)

    def test_modify_reservation_cannot_take_another_users_seat(self):
        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        create_reservation(self.other, self.show.id, [self.seats[1].id])
        with self.assertRaises(ReservationError):
            modify_reservation(
                self.user, reservation.token, add_seat_ids=[self.seats[1].id], remove_seat_ids=[]
            )

    def test_modify_reservation_must_keep_at_least_one_seat(self):
        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        with self.assertRaises(ReservationError):
            modify_reservation(
                self.user, reservation.token, add_seat_ids=[], remove_seat_ids=[self.seats[0].id]
            )

    def test_confirm_booking_creates_bookings_and_payments(self):
        reservation = create_reservation(
            self.user, self.show.id, [self.seats[0].id, self.seats[1].id]
        )
        reservation, bookings = confirm_booking(
            self.user, reservation.token, transaction_id='TXN-1'
        )
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, 'booked')
        self.assertEqual(reservation.payment_status, 'completed')
        self.assertGreater(reservation.total_amount, 0)
        self.assertEqual(
            reservation.total_amount,
            reservation.subtotal_amount
            + reservation.convenience_fee
            + reservation.gst_amount,
        )
        from admin_panel.models import Payment

        self.assertEqual(len(bookings), 2)
        self.assertTrue(all(b.booking_ref for b in bookings))
        self.assertEqual(len({b.booking_ref for b in bookings}), 2)
        self.assertEqual(
            Payment.objects.filter(booking__in=bookings, status='completed').count(), 2
        )
        self.seats[0].refresh_from_db()
        self.assertTrue(self.seats[0].is_booked)
        self.assertEqual(ReservedSeat.objects.filter(reservation=reservation).count(), 0)
        self.assertEqual(seat_states_for_show(self.show)[str(self.seats[0].id)], 'booked')

    def test_confirm_booking_is_idempotent_guard_against_double_charge(self):
        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        confirm_booking(self.user, reservation.token, transaction_id='TXN-1')
        with self.assertRaises(ReservationError):
            confirm_booking(self.user, reservation.token, transaction_id='TXN-2')

    def test_seat_price_falls_back_to_show_price_without_catalog(self):
        self.assertEqual(seat_price(self.show, 'A1'), self.show.ticket_price)
        self.assertEqual(seat_price(self.show, 'A1', []), self.show.ticket_price)
        self.assertIsNone(category_for_seat('A1', []))

    def test_seat_price_uses_per_show_category_catalog(self):
        silver, gold, platinum = _make_categories_and_prices(self.show)
        self.assertEqual(category_for_seat('A1'), silver)
        self.assertEqual(category_for_seat('C10'), silver)
        self.assertEqual(category_for_seat('D1'), gold)
        self.assertEqual(category_for_seat('H1'), platinum)
        self.assertEqual(category_for_seat('Z9'), platinum)
        self.assertEqual(seat_price(self.show, 'A1'), Decimal('180.00'))
        self.assertEqual(seat_price(self.show, 'D1'), Decimal('250.00'))
        self.assertEqual(seat_price(self.show, 'H1'), Decimal('320.00'))
        self.assertEqual(seat_price(self.show, 'A1', [], {}), self.show.ticket_price)

    def test_seat_price_is_per_show_not_global(self):
        silver, _, _ = _make_categories_and_prices(self.show)
        _, other_show, _ = _make_show()
        self.assertEqual(seat_price(self.show, 'A1'), Decimal('180.00'))
        self.assertEqual(seat_price(other_show, 'A1'), other_show.ticket_price)

    def test_reservation_pricing_breaks_down_fee_gst_and_total(self):
        reservation = create_reservation(
            self.user, self.show.id, [self.seats[0].id, self.seats[1].id]
        )
        pricing = reservation_pricing(reservation)
        self.assertEqual(pricing['subtotal'], reservation_pricing(reservation)['subtotal'])
        self.assertEqual(pricing['platform_fee'], Decimal('10.00'))
        self.assertEqual(pricing['misc_fee'], Decimal('2.50'))
        self.assertEqual(pricing['convenience_fee'], Decimal('12.50'))
        self.assertEqual(pricing['gst_rate'], Decimal('0.00'))
        self.assertEqual(pricing['gst'], Decimal('0.00'))
        self.assertEqual(
            pricing['total'],
            pricing['subtotal'] + pricing['convenience_fee'] + pricing['gst'],
        )

    def test_gst_slab_selection_and_fee_breakdown(self):
        from admin_panel.models import GSTSlab, PricingConfig

        PricingConfig.objects.create(
            pk=1,
            platform_fee_per_ticket=Decimal('5.00'),
            misc_fee_per_booking=Decimal('2.50'),
        )
        GSTSlab.objects.create(
            min_amount=Decimal('0.00'), max_amount=Decimal('500.00'),
            rate=Decimal('5.00'), display_order=1,
        )
        GSTSlab.objects.create(
            min_amount=Decimal('500.01'), max_amount=None,
            rate=Decimal('18.00'), display_order=2,
        )

        self.assertEqual(gst_rate_for(Decimal('499.00')), Decimal('5.00'))
        self.assertEqual(gst_rate_for(Decimal('500.00')), Decimal('5.00'))
        self.assertEqual(gst_rate_for(Decimal('512.50')), Decimal('18.00'))
        self.assertEqual(len(gst_slabs()), 2)

        config = get_pricing_config()
        self.assertEqual(config['platform_fee_per_ticket'], Decimal('5.00'))
        self.assertEqual(config['misc_fee_per_booking'], Decimal('2.50'))

        reservation = create_reservation(
            self.user, self.show.id, [self.seats[0].id, self.seats[1].id]
        )
        pricing = reservation_pricing(reservation)
        self.assertEqual(pricing['subtotal'], Decimal('500.00'))
        self.assertEqual(pricing['platform_fee'], Decimal('10.00'))
        self.assertEqual(pricing['misc_fee'], Decimal('2.50'))
        self.assertEqual(pricing['convenience_fee'], Decimal('12.50'))
        self.assertEqual(pricing['gst_rate'], Decimal('18.00'))
        self.assertAlmostEqual(pricing['gst'], Decimal('92.25'), places=2)
        self.assertEqual(pricing['total'], Decimal('604.75'))

    def test_booking_snapshots_preserve_pricing_at_confirmation(self):
        _make_categories_and_prices(self.show)
        reservation = create_reservation(
            self.user, self.show.id, [self.seats[0].id, self.seats[1].id]
        )
        reservation, bookings = confirm_booking(
            self.user, reservation.token, transaction_id='TXN-SNAP'
        )
        self.assertEqual(len(bookings), 2)
        for booking in bookings:
            self.assertEqual(booking.seat_category, 'SILVER')
            self.assertEqual(booking.ticket_price, Decimal('180.00'))
            self.assertEqual(booking.platform_fee, Decimal('5.00'))
            self.assertEqual(booking.misc_fee, Decimal('1.25'))
            self.assertEqual(booking.gst_rate, Decimal('0.00'))
            self.assertEqual(booking.gst_amount, Decimal('0.00'))
        reservation.refresh_from_db()
        self.assertEqual(reservation.platform_fee, Decimal('10.00'))
        self.assertEqual(reservation.misc_fee, Decimal('2.50'))
        self.assertEqual(reservation.gst_rate, Decimal('0.00'))

    def test_show_price_change_does_not_affect_confirmed_booking(self):
        from admin_panel.models import Payment

        _make_categories_and_prices(self.show)
        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        reservation, bookings = confirm_booking(
            self.user, reservation.token, transaction_id='TXN-IMM'
        )
        booking = bookings[0]
        ShowPrice.objects.filter(
            theater=self.show, category__name='SILVER'
        ).update(price=Decimal('500.00'))
        booking.refresh_from_db()
        self.assertEqual(booking.ticket_price, Decimal('180.00'))
        payment = Payment.objects.get(booking=booking)
        self.assertEqual(payment.amount, Decimal('187.50'))
        self.assertEqual(
            Payment.objects.filter(booking=booking).aggregate(
                total=Sum('amount')
            )['total'],
            booking.total,
        )

    def test_coupon_percent_discount_applied(self):
        from admin_panel.models import Coupon

        coupon = Coupon.objects.create(
            code='BOOK10',
            discount_percent=10,
            max_uses=100,
            min_order_amount=Decimal('100.00'),
            valid_from=timezone.now() - timedelta(days=1),
            valid_to=timezone.now() + timedelta(days=1),
            is_active=True,
        )
        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        pricing = reservation_pricing(reservation, coupon_code='BOOK10')
        self.assertEqual(pricing['coupon'], coupon)
        self.assertGreater(pricing['discount'], 0)
        self.assertEqual(pricing['total'], pricing['subtotal'] + pricing['convenience_fee'] + pricing['gst'] - pricing['discount'])

    def test_coupon_flat_discount_and_min_order(self):
        from admin_panel.models import Coupon

        Coupon.objects.create(
            code='FLAT50',
            discount_amount=50,
            max_uses=100,
            min_order_amount=Decimal('1000.00'),
            valid_from=timezone.now() - timedelta(days=1),
            valid_to=timezone.now() + timedelta(days=1),
            is_active=True,
        )
        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        subtotal = reservation_pricing(reservation)['subtotal']
        with self.assertRaises(ReservationError):
            validate_coupon('FLAT50', subtotal)
        with self.assertRaises(ReservationError):
            reservation_pricing(reservation, coupon_code='FLAT50')

    def test_coupon_usage_limit_enforced(self):
        from admin_panel.models import Coupon

        coupon = Coupon.objects.create(
            code='LIMIT1',
            discount_percent=10,
            max_uses=1,
            used_count=1,
            min_order_amount=Decimal('0.00'),
            valid_from=timezone.now() - timedelta(days=1),
            valid_to=timezone.now() + timedelta(days=1),
            is_active=True,
        )
        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        subtotal = reservation_pricing(reservation)['subtotal']
        with self.assertRaises(ReservationError):
            validate_coupon('LIMIT1', subtotal)
        with self.assertRaises(ReservationError):
            reservation_pricing(reservation, coupon_code='LIMIT1')

    def test_coupon_marked_used_after_confirmation(self):
        from admin_panel.models import Coupon, Payment

        coupon = Coupon.objects.create(
            code='ONCE',
            discount_percent=5,
            max_uses=5,
            used_count=0,
            min_order_amount=Decimal('0.00'),
            valid_from=timezone.now() - timedelta(days=1),
            valid_to=timezone.now() + timedelta(days=1),
            is_active=True,
        )
        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        confirm_booking(self.user, reservation.token, transaction_id='TXN-C', coupon_code='ONCE')
        coupon.refresh_from_db()
        self.assertEqual(coupon.used_count, 1)
        reservation.refresh_from_db()
        self.assertEqual(reservation.coupon_code, 'ONCE')
        self.assertGreater(reservation.discount_amount, 0)

    def test_cannot_book_after_show_started(self):
        show = Theater.objects.create(
            name='Late Show',
            movie=self.movie,
            time=timezone.now() - timedelta(minutes=5),
        )
        seat = Seat.objects.create(theater=show, seat_number='A1')
        with self.assertRaises(ReservationError):
            create_reservation(self.user, show.id, [seat.id])

    def test_cancel_booking_before_show_refunds_and_frees_seat(self):
        from admin_panel.models import Payment

        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        reservation, bookings = confirm_booking(
            self.user, reservation.token, transaction_id='TXN-C'
        )
        booking = bookings[0]
        cancel_booking(self.user, booking.id)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'cancelled')
        self.seats[0].refresh_from_db()
        self.assertFalse(self.seats[0].is_booked)
        self.assertEqual(
            Payment.objects.filter(booking_id=booking.id, status='refunded').count(), 1
        )
        self.assertEqual(seat_states_for_show(self.show)[str(self.seats[0].id)], 'available')

    def test_cancel_booking_rejected_after_show_started(self):
        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        reservation, bookings = confirm_booking(self.user, reservation.token)
        Theater.objects.filter(pk=self.show.id).update(
            time=timezone.now() - timedelta(minutes=1)
        )
        with self.assertRaises(ReservationError):
            cancel_booking(self.user, bookings[0].id)

    def test_cancel_booking_rejects_foreign_user(self):
        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        reservation, bookings = confirm_booking(self.user, reservation.token)
        with self.assertRaises(ReservationError):
            cancel_booking(self.other, bookings[0].id)

    def test_confirm_rejects_expired(self):
        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        Reservation.objects.filter(pk=reservation.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        with self.assertRaises(ReservationError):
            confirm_booking(self.user, reservation.token)

    def test_release_reservation_frees_seats_for_reuse(self):
        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        release_reservation(self.user, reservation.token)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, 'cancelled')
        self.assertEqual(ReservedSeat.objects.count(), 0)
        self.assertEqual(
            seat_states_for_show(self.show)[str(self.seats[0].id)], 'available'
        )
        create_reservation(self.user, self.show.id, [self.seats[0].id])

    def test_foreign_user_cannot_touch_reservation(self):
        reservation = create_reservation(self.user, self.show.id, [self.seats[0].id])
        with self.assertRaises(ReservationError):
            release_reservation(self.other, reservation.token)
        with self.assertRaises(ReservationError):
            confirm_booking(self.other, reservation.token)

    def test_seat_limit_enforced(self):
        ids = [s.id for s in self.seats[:13]]
        with self.assertRaises(ReservationError):
            create_reservation(self.user, self.show.id, ids)

    def test_revision_bumped_on_state_change(self):
        before = Theater.objects.get(pk=self.show.id).seat_revision
        create_reservation(self.user, self.show.id, [self.seats[0].id])
        after = Theater.objects.get(pk=self.show.id).seat_revision
        self.assertEqual(after, before + 1)


@DEMO_RAZORPAY
class ReservationApiTests(TestCase):
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

    def _pay(self, token):
        start = self.client.post(
            reverse('payment_start', args=[token]),
            data={'coupon_code': ''},
            content_type='application/json',
        )
        self.assertEqual(start.status_code, 200)
        checkout = start.json()['checkout']
        self.assertTrue(checkout['demo'])
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

    def test_reserve_api_holds_seats(self):
        response = self._reserve(self.seats[0].id)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['ok'])
        self.assertEqual(len(body['reservation']['seats']), 1)

    def test_reserve_api_conflict_for_held_seat(self):
        self._reserve(self.seats[0].id)
        self.client.force_login(self.other)
        response = self._reserve(self.seats[0].id)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()['ok'])

    def test_seat_selection_page_renders_tier_pricing(self):
        response = self.client.get(reverse('book_seats', args=[self.show.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SILVER')
        self.assertContains(response, 'seat--available')
        self.assertContains(response, 'data-price=')

    def test_status_api_reports_states_and_etag(self):
        self._reserve(self.seats[0].id)
        url = reverse('seat_status', args=[self.show.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['seats'][str(self.seats[0].id)], 'reserved')
        self.assertIsNotNone(body['reservation'])
        etag = response['ETag']
        not_modified = self.client.get(url, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(not_modified.status_code, 304)

    def test_confirm_api_is_not_exposed(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        response = self.client.post(
            '/movies/api/reservation/{}/confirm/'.format(reservation['token']),
            data={'transaction_id': 'TXN-API'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        self.seats[0].refresh_from_db()
        self.assertFalse(self.seats[0].is_booked)

    def test_payment_cannot_be_confirmed_without_charge_step(self):
        from admin_panel.models import Payment

        reservation = self._reserve(self.seats[0].id).json()['reservation']
        response = self.client.post(
            '/movies/reservation/{}/payment/process/'.format(reservation['token']),
            data={'action': 'success', 'transaction_id': 'TXN-FAKE'},
        )
        self.assertEqual(response.status_code, 404)
        self.seats[0].refresh_from_db()
        self.assertFalse(self.seats[0].is_booked)
        self.assertFalse(Payment.objects.filter(transaction_id='TXN-FAKE').exists())

    def test_release_api_frees_seat(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        response = self.client.post(
            reverse('api_reservation_release', args=[reservation['token']]),
            data='{}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.seats[0].refresh_from_db()
        self.assertFalse(self.seats[0].is_booked)

    def test_payment_page_guards_owner_and_expiry(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        url = reverse('payment_page', args=[reservation['token']])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pay')

        self.client.force_login(self.other)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('profile'))

    def test_demo_checkout_confirms_booking(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        token = reservation['token']
        page = self.client.get(reverse('payment_page', args=[token]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Secure Checkout')
        verify = self._pay(token)
        self.assertEqual(verify.status_code, 200)
        body = verify.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['confirmation_url'], reverse('booking_confirmation', args=[token]))
        self.seats[0].refresh_from_db()
        self.assertTrue(self.seats[0].is_booked)

    def test_payment_verify_rejects_unverified_callback(self):
        from admin_panel.models import Payment

        reservation = self._reserve(self.seats[0].id).json()['reservation']
        response = self.client.post(
            reverse('payment_verify', args=[reservation['token']]),
            data={
                'razorpay_order_id': 'order_FAKE',
                'razorpay_payment_id': 'pay_FAKE',
                'razorpay_signature': 'deadbeef',
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.seats[0].refresh_from_db()
        self.assertFalse(self.seats[0].is_booked)
        self.assertFalse(Payment.objects.filter(booking__reservation__token=reservation['token']).exists())

    def test_booking_confirmation_page_accessible_to_owner(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])
        response = self.client.get(
            reverse('booking_confirmation', args=[reservation['token']])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Booking Confirmed')

    def test_ticket_page_accessible_to_owner(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])
        booking = Booking.objects.get(reservation__token=reservation['token'])
        response = self.client.get(reverse('download_ticket', args=[booking.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, booking.booking_ref)
        self.client.force_login(self.other)
        response = self.client.get(reverse('download_ticket', args=[booking.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Not Your Ticket')

    def test_ticket_page_accessible_by_booking_ref(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])
        reservation_obj = Reservation.objects.get(token=reservation['token'])
        self.assertTrue(reservation_obj.booking_ref)
        response = self.client.get(
            reverse('download_ticket', args=[reservation_obj.booking_ref])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reservation_obj.booking_ref)
        self.assertContains(response, 'data:image/png;base64,')

    def test_ticket_has_scannable_qr(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])
        booking = Booking.objects.get(reservation__token=reservation['token'])
        response = self.client.get(reverse('download_ticket', args=[booking.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data:image/png;base64,')
        self.assertContains(response, booking.booking_ref)

    def test_ticket_hidden_after_cancellation(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])
        booking = Booking.objects.get(reservation__token=reservation['token'])
        cancel_booking(self.user, booking.id)
        response = self.client.get(reverse('download_ticket', args=[booking.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('profile'))

    def test_invoice_page_renders(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])
        booking = Booking.objects.get(reservation__token=reservation['token'])
        response = self.client.get(reverse('booking_invoice', args=[booking.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'INV-{}'.format(booking.booking_ref))
        self.assertContains(response, 'Total Paid')
        self.assertContains(response, booking.movie.name)

    def test_invoice_hidden_from_other_users(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])
        booking = Booking.objects.get(reservation__token=reservation['token'])
        self.client.force_login(self.other)
        response = self.client.get(reverse('booking_invoice', args=[booking.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('profile'))

    def test_ticket_pdf_endpoint_returns_pdf(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])
        reservation_obj = Reservation.objects.get(token=reservation['token'])
        response = self.client.get(reverse('ticket_pdf', args=[reservation_obj.booking_ref]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_ticket_pdf_hidden_from_other_users(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])
        reservation_obj = Reservation.objects.get(token=reservation['token'])
        self.client.force_login(self.other)
        response = self.client.get(reverse('ticket_pdf', args=[reservation_obj.booking_ref]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('profile'))

    def test_viewing_ticket_records_download_history(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])
        reservation_obj = Reservation.objects.get(token=reservation['token'])
        self.client.get(reverse('download_ticket', args=[reservation_obj.booking_ref]))
        self.client.get(reverse('ticket_pdf', args=[reservation_obj.booking_ref]))
        from movies.models import TicketDownload
        history = TicketDownload.objects.filter(user=self.user)
        self.assertEqual(history.count(), 2)
        self.assertEqual(history.first().booking_ref, reservation_obj.booking_ref)

    def test_verify_ticket_qr_accepts_signed_payload(self):
        import json as _json
        from movies.qr import build_qr_payload

        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])
        reservation_obj = Reservation.objects.get(token=reservation['token'])
        payload = build_qr_payload(
            reservation_obj.booking_ref,
            reservation_obj.show.movie.name,
            reservation_obj.show.name,
            ['A1'],
        )
        response = self.client.post(
            reverse('verify_ticket_qr'),
            data=_json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['valid'])

        tampered = dict(payload)
        tampered['booking_id'] = 'BMS-TAMPERED'
        response = self.client.post(
            reverse('verify_ticket_qr'),
            data=_json.dumps(tampered),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['valid'])

    def test_verify_ticket_qr_is_one_time(self):
        import json as _json
        from movies.qr import build_qr_payload

        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])
        reservation_obj = Reservation.objects.get(token=reservation['token'])
        payload = build_qr_payload(
            reservation_obj.booking_ref,
            reservation_obj.show.movie.name,
            reservation_obj.show.name,
            ['A1'],
        )
        url = reverse('verify_ticket_qr')
        first = self.client.post(
            url, data=_json.dumps(payload), content_type='application/json'
        )
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()['valid'])
        self.assertTrue(first.json()['scanned'])

        reservation_obj.refresh_from_db()
        self.assertIsNotNone(reservation_obj.scanned_at)
        self.assertEqual(reservation_obj.scan_count, 1)

        second = self.client.post(
            url, data=_json.dumps(payload), content_type='application/json'
        )
        self.assertEqual(second.status_code, 200)
        body = second.json()
        self.assertFalse(body['valid'])
        self.assertTrue(body['used'])
        self.assertEqual(body['reason'], 'already_scanned')

        reservation_obj.refresh_from_db()
        self.assertEqual(reservation_obj.scan_count, 1)

    def test_confirmation_email_attaches_ticket_pdf_with_qr(self):
        from django.core import mail
        from django.core.management import call_command

        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])

        # Emails are enqueued asynchronously — drain the outbox like the worker.
        self.assertTrue(EmailOutbox.objects.filter(status='pending').exists(),
                        'a confirmation email must be enqueued after payment')
        self.assertEqual(mail.outbox, [], 'email must NOT be sent synchronously')
        call_command('process_email_outbox', verbosity=0)

        self.assertTrue(mail.outbox, 'expected a confirmation email to be sent')
        message = mail.outbox[0]
        pdf_attachments = [
            a for a in message.attachments
            if isinstance(a, tuple) and len(a) >= 3
            and a[0].startswith('ticket_') and a[2] == 'application/pdf'
        ]
        self.assertTrue(pdf_attachments, 'confirmation email must carry a PDF ticket')
        self.assertIn('Booking Confirmed', message.subject)
        pdf_bytes = pdf_attachments[0][1]
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))
        html_parts = [a[0] for a in message.alternatives if a[1] == 'text/html']
        self.assertTrue(html_parts, 'confirmation email must include an HTML body')
        self.assertIn(
            'data:image/png;base64,', html_parts[0],
            'HTML must embed the QR inline as a data URI, not an attachment',
        )
        self.assertNotIn(
            'cid:qr_ticket', html_parts[0],
            'no leftover Content-ID reference once the QR is embedded',
        )
        self.assertEqual(
            EmailOutbox.objects.filter(status='sent').count(), 1,
            'the enqueued message must be marked sent after delivery',
        )

    def test_cancel_booking_via_web_redirects_to_profile(self):
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        self._pay(reservation['token'])
        booking = Booking.objects.get(reservation__token=reservation['token'])
        response = self.client.post(reverse('cancel_booking', args=[booking.id]))
        self.assertRedirects(response, reverse('profile'))
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'cancelled')
        self.seats[0].refresh_from_db()
        self.assertFalse(self.seats[0].is_booked)

    def test_cancel_booking_ref_cancels_entire_transaction(self):
        reservation_data = self._reserve(
            self.seats[0].id, self.seats[1].id
        ).json()['reservation']
        self._pay(reservation_data['token'])
        reservation = Reservation.objects.get(token=reservation_data['token'])
        self.assertEqual(reservation.status, 'booked')
        response = self.client.post(
            reverse('cancel_booking_ref', args=[reservation.booking_ref])
        )
        self.assertRedirects(response, reverse('profile'))
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, 'cancelled')
        for booking in Booking.objects.filter(reservation=reservation):
            self.assertEqual(booking.status, 'cancelled')
        self.seats[0].refresh_from_db()
        self.seats[1].refresh_from_db()
        self.assertFalse(self.seats[0].is_booked)
        self.assertFalse(self.seats[1].is_booked)

    def test_coupon_validate_api(self):
        from admin_panel.models import Coupon

        Coupon.objects.create(
            code='SAVE10',
            discount_percent=10,
            max_uses=100,
            min_order_amount=Decimal('0.00'),
            valid_from=timezone.now() - timedelta(days=1),
            valid_to=timezone.now() + timedelta(days=1),
            is_active=True,
        )
        reservation = self._reserve(self.seats[0].id).json()['reservation']
        response = self.client.get(
            reverse('api_coupon_validate'),
            {'code': 'SAVE10', 'token': reservation['token']},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['coupon_code'], 'SAVE10')
        bad = self.client.get(
            reverse('api_coupon_validate'),
            {'code': 'NOPE', 'token': reservation['token']},
        )
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(bad.json()['ok'])

    def test_cleanup_api_requires_staff(self):
        response = self.client.post(reverse('api_cleanup_expired'))
        self.assertEqual(response.status_code, 403)
        self.user.is_staff = True
        self.user.save()
        response = self.client.post(reverse('api_cleanup_expired'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])


class ReservationConcurrencyTests(TransactionTestCase):
    """Race two users against the same single seat.

    Exactly one reservation may win; the loser must either see the seat held
    or be stopped by the DB-level OneToOne backstop (converted to a clean
    ReservationError). No double booking is possible either way.
    """

    def setUp(self):
        self.movie, self.show, self.seats = _make_show()
        self.users = [
            User.objects.create_user(f'racer{i}', f'r{i}@example.com', 'password123')
            for i in range(4)
        ]

    def test_single_seat_race(self):
        results = []

        def race(user, seat_id):
            # SQLite has no select_for_update, so losing writers hit
            # "table is locked" and must retry until the winner commits.
            import time

            from django.db.utils import OperationalError

            for _attempt in range(20):
                try:
                    create_reservation(user, self.show.id, [seat_id])
                    results.append(user.id)
                    return
                except ReservationError:
                    return
                except OperationalError:
                    time.sleep(0.1)

        seat_id = self.seats[0].id
        threads = [
            threading.Thread(target=race, args=(user, seat_id)) for user in self.users
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        winners = [uid for uid in results]
        self.assertEqual(len(winners), 1)
        active = Reservation.objects.filter(
            reserved_seats__seat_id=seat_id, status='active'
        ).count()
        self.assertEqual(active, 1)
        reserved_rows = ReservedSeat.objects.filter(seat_id=seat_id).count()
        self.assertEqual(reserved_rows, 1)


class MovieRestoreVisibilityTests(TestCase):
    def test_restoring_archived_movie_reenables_public_visibility(self):
        image = SimpleUploadedFile(
            'poster.jpg',
            BytesIO(b'fake-image-data').getvalue(),
            content_type='image/jpeg'
        )
        movie = Movie.objects.create(
            name='Restore Test Movie',
            image=image,
            rating=7.5,
            cast='Actor',
            duration=120,
            status='archived',
            show_on_homepage=False,
            is_deleted=True,
        )

        user = User.objects.create_superuser('admin', 'admin@example.com', 'password123')
        self.client.force_login(user)
        session = self.client.session
        session['admin_user_id'] = user.id
        session['is_admin_authenticated'] = True
        session.save()

        response = self.client.post(reverse('admin_movie_restore', args=[movie.pk]))

        movie.refresh_from_db()
        self.assertFalse(movie.is_deleted)
        self.assertTrue(movie.show_on_homepage)
        self.assertNotIn(movie.status, ['archived', 'hidden'])
        self.assertRedirects(response, reverse('admin_movie_list'))


class WishlistAndNotificationsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('customer', 'customer@example.com', 'password123')
        self.movie = Movie.objects.create(
            name='Wishlist Test Movie', rating=8.0, cast='Actor',
            status='now_showing', show_on_homepage=True,
        )

    def _login(self):
        self.client.force_login(self.user)

    def test_toggle_wishlist_adds_and_removes(self):
        self._login()
        url = reverse('toggle_wishlist', args=[self.movie.pk])
        self.client.post(url)
        self.assertTrue(Wishlist.objects.filter(user=self.user, movie=self.movie).exists())
        self.client.post(url)
        self.assertFalse(Wishlist.objects.filter(user=self.user, movie=self.movie).exists())

    def test_toggle_wishlist_requires_login(self):
        response = self.client.post(reverse('toggle_wishlist', args=[self.movie.pk]))
        self.assertNotEqual(response.status_code, 200)

    def test_wishlist_page_lists_saved_movies(self):
        self._login()
        Wishlist.objects.create(user=self.user, movie=self.movie)
        response = self.client.get(reverse('wishlist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.movie.name)

    def test_toggle_twice_returns_to_removed(self):
        self._login()
        Wishlist.objects.create(user=self.user, movie=self.movie)
        url = reverse('toggle_wishlist', args=[self.movie.pk])
        self.client.post(url)
        self.assertEqual(Wishlist.objects.filter(user=self.user, movie=self.movie).count(), 0)
        self.client.post(url)
        self.assertEqual(Wishlist.objects.filter(user=self.user, movie=self.movie).count(), 1)

    def test_movie_detail_shows_wishlist_state(self):
        self._login()
        self.client.post(reverse('toggle_wishlist', args=[self.movie.pk]))
        response = self.client.get(reverse('movie_detail', args=[self.movie.pk]))
        self.assertContains(response, 'In Wishlist')

    def test_notifications_page_and_mark_all_read(self):
        self._login()
        Notification.objects.create(
            user=self.user, title='Hello', message='Test message',
            notification_type='info', is_read=False,
        )
        response = self.client.get(reverse('my_notifications'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Hello')
        self.client.post(reverse('my_notifications'))
        self.assertTrue(
            Notification.objects.filter(user=self.user, is_read=True).exists()
        )

    def test_mark_single_notification_read(self):
        self._login()
        notification = Notification.objects.create(
            user=self.user, title='Single', message='Msg',
            notification_type='info', is_read=False,
        )
        self.client.post(reverse('mark_notification_read', args=[notification.pk]))
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_other_users_cannot_mark_my_notification_read(self):
        self._login()
        other = User.objects.create_user('other', 'other@example.com', 'password123')
        notification = Notification.objects.create(
            user=other, title='Secret', message='Msg',
            notification_type='info', is_read=False,
        )
        response = self.client.post(reverse('mark_notification_read', args=[notification.pk]))
        self.assertEqual(response.status_code, 404)

    def test_recently_viewed_tracks_movie(self):
        self.client.get(reverse('movie_detail', args=[self.movie.pk]))
        session = self.client.session
        self.assertIn(self.movie.pk, session.get('recently_viewed', []))

    def test_home_shows_recently_viewed(self):
        self.client.get(reverse('movie_detail', args=[self.movie.pk]))
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Recently Viewed')
        self.assertContains(response, self.movie.name)


class ReviewHelpfulTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user('author', 'author@example.com', 'password123')
        self.voter = User.objects.create_user('voter', 'voter@example.com', 'password123')
        self.movie = Movie.objects.create(
            name='Helpful Test Movie', rating=8.0, cast='Actor', status='now_showing'
        )
        self.review = Review.objects.create(
            movie=self.movie, user=self.author, rating=5, comment='Great film!',
            is_approved=True,
        )

    def test_helpful_vote_adds_and_toggles(self):
        self.client.force_login(self.voter)
        url = reverse('helpful_review', args=[self.review.pk])
        self.client.post(url)
        self.assertTrue(
            ReviewHelpful.objects.filter(review=self.review, user=self.voter).exists()
        )
        self.client.post(url)
        self.assertFalse(
            ReviewHelpful.objects.filter(review=self.review, user=self.voter).exists()
        )

    def test_helpful_requires_login(self):
        response = self.client.post(reverse('helpful_review', args=[self.review.pk]))
        self.assertNotEqual(response.status_code, 200)

    def test_cannot_vote_on_own_review(self):
        self.client.force_login(self.author)
        self.client.post(reverse('helpful_review', args=[self.review.pk]))
        self.assertFalse(
            ReviewHelpful.objects.filter(review=self.review, user=self.author).exists()
        )


class TheaterListDateTests(TestCase):
    """Multi-day show booking: theater_list date tabs and filters."""

    def setUp(self):
        self.movie = Movie.objects.create(
            name='Date Test Movie', rating=7.0, cast='Cast', status='now_showing'
        )
        self.today = timezone.now().date()
        self.tomorrow = self.today + timedelta(days=1)
        self.day3 = self.today + timedelta(days=3)
        # Today: one upcoming, one already-started show.
        self.ava = self._mk('Ava Hall', timezone.now() + timedelta(minutes=30))
        self.beta = self._mk('Beta Hall', timezone.now() - timedelta(minutes=30))
        # Tomorrow and +3 days: fixed slots.
        self.gamma = self._mk('Gamma Hall', self._dt(self.tomorrow, 10, 30))
        self.delta = self._mk('Delta Hall', self._dt(self.day3, 18, 30))

    def _dt(self, day, hour, minute):
        return timezone.make_aware(datetime.combine(day, time(hour, minute)))

    def _mk(self, name, dt, screen='Screen 1'):
        return Theater.objects.create(
            name=name, movie=self.movie, time=dt, screen_name=screen,
            ticket_price=Decimal('200.00'),
        )

    def _get(self, **params):
        return self.client.get(reverse('theater_list', args=[self.movie.id]), params)

    def test_default_shows_only_upcoming_today_shows(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ava Hall')
        self.assertNotContains(response, 'Beta Hall')
        self.assertNotContains(response, 'Gamma Hall')
        self.assertNotContains(response, 'Delta Hall')

    def test_date_param_filters_to_that_day(self):
        response = self._get(date=self.tomorrow.isoformat())
        self.assertContains(response, 'Gamma Hall')
        self.assertNotContains(response, 'Ava Hall')
        self.assertNotContains(response, 'Delta Hall')

    def test_third_day_tab_has_shows(self):
        response = self._get(date=self.day3.isoformat())
        self.assertContains(response, 'Delta Hall')
        self.assertNotContains(response, 'Ava Hall')

    def test_invalid_date_falls_back_to_today(self):
        response = self._get(date='not-a-date')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ava Hall')
        self.assertNotContains(response, 'Gamma Hall')

    def test_date_outside_window_falls_back_to_today(self):
        response = self._get(date=(self.today + timedelta(days=40)).isoformat())
        self.assertContains(response, 'Ava Hall')

    def test_date_tabs_rendered(self):
        response = self._get()
        self.assertContains(response, 'id="dateTabs"')
        self.assertContains(response, 'Today')
        self.assertContains(response, 'date-tab')
        self.assertEqual(response.context['show_dates'][0]['is_selected'], True)
        self.assertEqual(len(response.context['show_dates']), 4)

    def test_ajax_returns_partial_html_and_count(self):
        response = self.client.get(
            reverse('theater_list', args=[self.movie.id]),
            {'date': self.tomorrow.isoformat()},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['count'], 1)
        self.assertIn('Gamma Hall', body['html'])
        self.assertNotIn('Ava Hall', body['html'])

    def test_date_with_no_shows_renders_empty_state(self):
        no_show_date = self.today + timedelta(days=2)
        response = self._get(date=no_show_date.isoformat())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No shows on')
        self.assertNotContains(response, 'Ava Hall')

    def test_book_seats_redirects_past_show(self):
        user = User.objects.create_user('tester', 't@example.com', 'pw123')
        self.client.force_login(user)
        response = self.client.get(reverse('book_seats', args=[self.beta.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn('theaters', response.url)

    def test_book_seats_allows_upcoming_show(self):
        user = User.objects.create_user('tester', 't@example.com', 'pw123')
        self.client.force_login(user)
        response = self.client.get(reverse('book_seats', args=[self.ava.id]))
        self.assertEqual(response.status_code, 200)
