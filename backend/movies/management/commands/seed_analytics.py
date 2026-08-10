"""Bulk-seed the database with benchmark bookings for the Admin Analytics dashboard.

Generates N bookings (default 100,000) plus one Seat, one Payment, one
Reservation and one PaymentTransaction per booking using ``bulk_create`` so the
whole seed runs in a few batches instead of one query per row.

Data is spread across the trailing 365 days with a realistic time-of-day
distribution (evenings are busier) so every analytics range preset (today,
last 7/30/90 days, this month/year, custom) has data to aggregate.

Usage:
    python manage.py seed_analytics --bookings 100000
    python manage.py seed_analytics --bookings 10000 --theaters 40 --seats 250
    python manage.py seed_analytics --flush        # remove benchmark data only

Everything the command creates is tagged with a ``bench-`` marker so ``--flush``
can delete only benchmark rows and never touch real application data.
"""
from datetime import timedelta
from decimal import Decimal
from random import Random

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from movies.models import Booking, Movie, Reservation, Seat, Theater
from admin_panel.models import Payment, PaymentTransaction

CHUNK = 5000
MARKER = 'bench-'
BOOKING_REF_PREFIX = 'BENCH'


def _backdate(model_table, column, key_col, keys, datetimes, chunk=CHUNK):
    """Override ``auto_now_add`` columns that ``bulk_create`` overwrites with now().

    Uses raw ``UPDATE ... SET col = ? WHERE key = ?`` via ``executemany`` so the
    backdate pass runs in a handful of batched statements instead of one
    UPDATE per row. ``keys`` must be unique (booking_ref / token / order id).
    """
    if not keys:
        return
    # Django's SQLite cursor converts %s -> ?, PostgreSQL uses %s natively, so
    # %s placeholders work on both backends.
    sql = f'UPDATE {model_table} SET {column} = %s WHERE {key_col} = %s'
    with connection.cursor() as cursor:
        for i in range(0, len(keys), chunk):
            # isoformat(" ") matches Django's adapt_datetimefield_value format;
            # the T-separated form breaks Django's typecast_timestamp().
            rows = [(dt.isoformat(sep=' '), k)
                    for k, dt in zip(keys[i:i + chunk], datetimes[i:i + chunk])]
            cursor.executemany(sql, rows)

# Evening-heavy distribution of booking hours (index = hour 0..23, value = weight).
HOUR_WEIGHTS = (
    0, 0, 0, 1, 2, 3, 5, 7, 9, 10, 12, 13, 14, 13, 12, 11, 12, 15, 18, 20, 19, 15, 9, 4,
)
HOUR_POOL = [h for h, w in enumerate(HOUR_WEIGHTS) for _ in range(w)]

STATUS_RATIOS = (('confirmed', 90), ('cancelled', 10))
REFUND_RATIO = 3   # percent of confirmed bookings also refunded


def _decimal(value):
    return Decimal(str(value)).quantize(Decimal('0.01'))


class Command(BaseCommand):
    help = 'Bulk-generate benchmark bookings, payments and transactions'

    def add_arguments(self, parser):
        parser.add_argument('--bookings', type=int, default=100000)
        parser.add_argument('--users', type=int, default=2000)
        parser.add_argument('--movies', type=int, default=25)
        parser.add_argument('--theaters', type=int, default=200)
        parser.add_argument('--seats-per-theater', type=int, default=500)
        parser.add_argument('--seed', type=int, default=42)
        parser.add_argument('--flush', action='store_true',
                            help='Delete previously seeded benchmark data and exit')

    def handle(self, *args, **options):
        if options['flush']:
            return self._flush()
        # One big write transaction: bulk_inserts + backdate passes commit once
        # instead of fsyncing per statement (orders of magnitude faster).
        with transaction.atomic():
            self._seed(options)
        self.stdout.write(self.style.SUCCESS('Benchmark seed complete.'))

    # ------------------------------------------------------------------
    def _flush(self):
        users = User.objects.filter(username__startswith=MARKER)
        uids = list(users.values_list('id', flat=True))
        tx_ids = list(PaymentTransaction.objects.filter(user_id__in=uids).values_list('id', flat=True))
        reservations = Reservation.objects.filter(user_id__in=uids)
        rids = list(reservations.values_list('id', flat=True))
        bookings = Booking.objects.filter(booking_ref__startswith=BOOKING_REF_PREFIX)
        bids = list(bookings.values_list('id', flat=True))

        # SQLite caps bound variables per statement at ~999; chunk every IN(...).
        def chunked(model, ids, field='pk'):
            for i in range(0, len(ids), 900):
                model.objects.filter(**{f'{field}__in': ids[i:i + 900]}).delete()

        chunked(Payment, bids, 'booking_id')
        chunked(PaymentTransaction, tx_ids)
        chunked(Reservation, rids)
        chunked(Booking, bids)
        Seat.objects.filter(theater__name__startswith=MARKER).delete()
        Theater.objects.filter(name__startswith=MARKER).delete()
        Movie.objects.filter(name__startswith=MARKER).delete()
        User.objects.filter(id__in=uids).delete()

        self.stdout.write(self.style.SUCCESS(
            f'Removed benchmark seed: {len(bids)} bookings, {len(tx_ids)} transactions.'))

    # ------------------------------------------------------------------
    def _seed(self, options):
        n = options['bookings']
        rng = Random(options['seed'])
        now = timezone.now()

        self.stdout.write('Creating benchmark users ...')
        users = [User(username=f'{MARKER}user{i:05d}') for i in range(options['users'])]
        User.objects.bulk_create(users, ignore_conflicts=True)
        users = list(User.objects.filter(username__startswith=MARKER).order_by('id'))

        self.stdout.write('Creating benchmark movies ...')
        movies = [Movie(name=f'{MARKER}Movie {i+1}', rating='7.5', status='now_showing')
                  for i in range(options['movies'])]
        Movie.objects.bulk_create(movies, ignore_conflicts=True)
        movies = list(Movie.objects.filter(name__startswith=MARKER).order_by('id'))

        self.stdout.write('Creating benchmark theaters and seats ...')
        theater_count = options['theaters']
        seats_per_theater = options['seats_per_theater']
        shows = []
        seats = []
        for t in range(theater_count):
            movie = movies[t % len(movies)]
            show = Theater(
                name=f'{MARKER}Theater {t+1}',
                movie=movie,
                time=now - timedelta(days=1),
                ticket_price=_decimal(150 + (t % 8) * 25),
            )
            shows.append(show)
        Theater.objects.bulk_create(shows)
        shows = list(Theater.objects.filter(name__startswith=MARKER).order_by('id'))
        rows = (seats_per_theater // 20) or 1
        cols = (seats_per_theater // rows) or 1
        for t, show in enumerate(shows):
            for i in range(seats_per_theater):
                row, col = divmod(i, cols)
                seats.append(Seat(
                    theater=show,
                    seat_number=f'{chr(65 + row % 26)}{col + 1:02d}',
                ))
        for i in range(0, len(seats), CHUNK):
            Seat.objects.bulk_create(seats[i:i + CHUNK], ignore_conflicts=True)
        seats = list(Seat.objects.filter(theater__name__startswith=MARKER).order_by('id'))
        if len(seats) < n:
            raise SystemExit(
                f'Not enough benchmark seats ({len(seats)}) for {n} bookings; '
                'increase --seats-per-theater / --theaters.')

        self.stdout.write(f'Bulk-creating {n} bookings (in chunks of {CHUNK}) ...')
        self._bulk_bookings(users, movies, shows, seats[:n], now, rng)
        self.stdout.write(self.style.SUCCESS(
            f'Created {n} bookings across {len(shows)} theaters / {len(movies)} movies.'))

    # ------------------------------------------------------------------
    def _bulk_bookings(self, users, movies, shows, seats, now, rng):
        n = len(seats)
        booked_at = [now - timedelta(days=rng.randint(0, 364), hours=0)
                     for _ in range(n)]
        for i in range(n):
            hour = HOUR_POOL[rng.randrange(len(HOUR_POOL))]
            base = booked_at[i].replace(hour=hour, minute=rng.randint(0, 59), second=0, microsecond=0)
            if base > now:
                base = now - timedelta(seconds=rng.randint(0, 3600))
            booked_at[i] = base

        statuses = [rng.choices(('confirmed', 'cancelled'), weights=[90, 10])[0]
                    for _ in range(n)]

        tickets = [_decimal(rng.randint(120, 360)) for _ in range(n)]
        totals = [_decimal(round(float(t) * 1.18, 2)) for t in tickets]
        run = rng.randint(0, 9999)
        refs = [f'{BOOKING_REF_PREFIX}{run:04d}{i:08d}' for i in range(n)]

        bookings = [
            Booking(
                user=users[i % len(users)],
                seat=seats[i],
                movie=movies[i % len(movies)],
                theater=shows[i % len(shows)],
                status=statuses[i],
                booking_ref=refs[i],
                booked_at=booked_at[i],
                seat_category='Standard',
                ticket_price=tickets[i],
                gst_rate=_decimal('18.00'),
                gst_amount=_decimal(round(float(tickets[i]) * 0.18, 2)),
                platform_fee=_decimal('5.00'),
                misc_fee=_decimal('2.50'),
                discount=0,
                total=totals[i],
            )
            for i in range(n)
        ]
        for i in range(0, n, CHUNK):
            Booking.objects.bulk_create(bookings[i:i + CHUNK])
        _backdate('movies_booking', 'booked_at', 'booking_ref', refs, booked_at)
        bookings = list(Booking.objects.filter(booking_ref__startswith=BOOKING_REF_PREFIX)
                        .order_by('id'))

        self.stdout.write('Bulk-creating reservations ...')
        reservations = [
            Reservation(
                token=f'{MARKER}tok{i:016x}'[:64].ljust(64, 'x'),
                booking_ref=bookings[i].booking_ref,
                ticket_count=1,
                user=bookings[i].user,
                show=bookings[i].theater,
                status='booked' if bookings[i].status == 'confirmed' else 'cancelled',
                payment_status='completed',
                subtotal_amount=tickets[i],
                total_amount=totals[i],
                created_at=booked_at[i],
                expires_at=booked_at[i] + timedelta(minutes=10),
            )
            for i in range(n)
        ]
        tokens = [r.token for r in reservations]
        for i in range(0, n, CHUNK):
            Reservation.objects.bulk_create(reservations[i:i + CHUNK])
        _backdate('movies_reservation', 'created_at', 'token', tokens, booked_at)
        reservations = list(Reservation.objects.filter(token__startswith=MARKER).order_by('id'))

        self.stdout.write('Bulk-creating payments ...')
        payments = []
        for i in range(n):
            confirmed = bookings[i].status == 'confirmed'
            refunded = confirmed and rng.randint(1, 100) <= REFUND_RATIO
            payments.append(Payment(
                booking=bookings[i],
                amount=totals[i],
                payment_method='upi' if i % 3 else 'card',
                transaction_id=f'{BOOKING_REF_PREFIX}PAY{run:04d}{i:08d}',
                status='refunded' if refunded else ('completed' if confirmed else 'failed'),
                paid_at=booked_at[i],
            ))
        for i in range(0, n, CHUNK):
            Payment.objects.bulk_create(payments[i:i + CHUNK])
        # Backdate by booking_id (indexed) - transaction_id has no index and a
        # per-row scan of the payments table would be quadratic at 100k rows.
        _backdate('admin_panel_payment', 'paid_at', 'booking_id',
                  [b.id for b in bookings], booked_at)

        self.stdout.write('Bulk-creating payment transactions ...')
        transactions = []
        order_ids = []
        for i in range(n):
            confirmed = bookings[i].status == 'confirmed'
            refunded = confirmed and rng.randint(1, 100) <= REFUND_RATIO
            order_id = f'{BOOKING_REF_PREFIX}ORD{run:04d}{i:08d}'
            order_ids.append(order_id)
            transactions.append(PaymentTransaction(
                reservation=reservations[i],
                user=bookings[i].user,
                gateway_order_id=order_id,
                amount=totals[i],
                status='refunded' if refunded else ('captured' if confirmed else 'failed'),
                method='upi' if i % 3 else 'card',
                created_at=booked_at[i],
                refunded_at=booked_at[i] + timedelta(days=1) if refunded else None,
            ))
        for i in range(0, n, CHUNK):
            PaymentTransaction.objects.bulk_create(transactions[i:i + CHUNK])
        _backdate('admin_panel_paymenttransaction', 'created_at', 'gateway_order_id', order_ids, booked_at)

        self.stdout.write(self.style.SUCCESS(
            'Linked %d payments, %d reservations and %d transactions.'
            % (n, n, n)))
