"""Benchmark the Admin Analytics queries at scale and print the report.

Wired to the data produced by ``seed_analytics`` (100k+ bookings by default).
The command:

1. prints dataset sizes,
2. runs ``EXPLAIN QUERY PLAN`` on the exact SQL Django generates for the core
   analytics queries to prove the added composite indexes are being used,
3. times every analytics area function (services are called directly, bypassing
   the 5-minute view cache so each measurement is a real DB round-trip),
4. compares the optimized ORM aggregate against a naive Python loop that loads
   every row into memory,
5. measures the same range query with and without the ``(status, booked_at)``
   index (dropped inside a transaction, then rolled back) to quantify the
   index's contribution.

Usage:
    python manage.py benchmark_analytics
    python manage.py benchmark_analytics --runs 3
"""
import time
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Count, Sum, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from admin_panel.analytics import services as analytics
from admin_panel.models import Payment
from movies.models import Booking, Theater

AGGREGATE_INDEXES = (
    ('movies_booking (status, booked_at)', 'idx_movies_booking_status_booked'),
    ('movies_booking (booked_at)', 'idx_movies_booking_booked_at'),
    ('admin_panel_payment (status, paid_at)', 'idx_admin_panel_payment_status_paid'),
    ('admin_panel_paymenttransaction (status, created_at)',
     'idx_admin_panel_paymenttransaction_status_created'),
    ('movies_booking (movie, booked_at)', 'idx_movies_booking_movie_booked'),
    ('movies_booking (theater, booked_at)', 'idx_movies_booking_theater_booked'),
)


def _ms(seconds):
    return f'{seconds * 1000:,.1f} ms'


def _timeit(fn, runs=3):
    best = None
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        elapsed = time.perf_counter() - start
        best = elapsed if best is None else min(best, elapsed)
    return best


class Command(BaseCommand):
    help = 'Measure analytics query performance and print index evidence'

    def add_arguments(self, parser):
        parser.add_argument('--runs', type=int, default=3,
                            help='Runs per measurement; the best is reported')

    def handle(self, *args, **options):
        runs = options['runs']
        if connection.vendor != 'sqlite':
            self.stdout.write(self.style.WARNING(
                'benchmark_analytics is a SQLite-only dev tool '
                '(it uses EXPLAIN QUERY PLAN / sqlite_master). '
                'Skipping on {}; run it locally against db.sqlite3.'.format(
                    connection.vendor
                )
            ))
            return
        self._print_sizes()

        print('\n=== 1. EXPLAIN QUERY PLAN (with indexes) ===')
        self._explain_plans()

        print('\n=== 2. Analytics area timings (best of %d) ===' % runs)
        self._time_areas(runs)

        print('\n=== 3. ORM aggregate vs naive Python loop ===')
        self._compare_naive(runs)

        print('\n=== 4. Same range query WITH vs WITHOUT the (status, booked_at) index ===')
        self._index_impact(runs)

    # ------------------------------------------------------------------
    def _print_sizes(self):
        print('Dataset sizes')
        print(f'  Booking rows          : {Booking.objects.count():,}')
        print(f'  Payment rows          : {Payment.objects.count():,}')
        print(f'  Theater rows          : {Theater.objects.count():,}')

    # ------------------------------------------------------------------
    def _explain_plans(self):
        now = timezone.now()
        start = now - timedelta(days=90)
        queries = [
            ('Count bookings in 90-day range',
             Booking.objects.filter(booked_at__gte=start).query),
            ('Confirmed bookings in range + day bucket (bookings trend)',
             Booking.objects.filter(booked_at__gte=start, booked_at__lt=now, status='confirmed')
             .annotate(bucket=TruncDate('booked_at')).values('bucket')
             .annotate(v=Count('id')).query),
            ('Top movies by revenue in range',
             Booking.objects.filter(booked_at__gte=start, booked_at__lt=now)
             .annotate(revenue=Sum('total', filter=Q(status='confirmed')))
             .values('movie_id').annotate(
                 bookings=Count('id', filter=Q(status='confirmed'))).query),
            ('Occupancy per theater (confirmed seats in range)',
             Theater.objects.filter(time__gte=start, time__lt=now)
             .annotate(total_seats=Count('seats', distinct=True),
                       booked_seats=Count('booking', distinct=True,
                                          filter=Q(booking__status='confirmed',
                                                   booking__booked_at__gte=start,
                                                   booking__booked_at__lt=now)))
             .values('id', 'name').query),
            ('Sum of completed payments in range (revenue KPI)',
             Payment.objects.filter(status='completed', paid_at__gte=start, paid_at__lt=now)
             .query),
        ]
        with connection.cursor() as cursor:
            for title, qs_query in queries:
                sql, params = qs_query.sql_with_params()
                print(f'\n  {title}')
                cursor.execute('EXPLAIN QUERY PLAN ' + sql, params)
                for row in cursor.fetchall():
                    print(f'    {row[3]}')

    # ------------------------------------------------------------------
    def _time_areas(self, runs):
        areas = [
            ('overview', lambda: analytics.overview_data(rng)),
            ('revenue', lambda: analytics.revenue_data(rng)),
            ('bookings', lambda: analytics.bookings_data(rng)),
            ('occupancy', lambda: analytics.occupancy_data(rng)),
            ('movies', lambda: analytics.movies_data(rng)),
            ('theaters', lambda: analytics.theaters_data(rng)),
            ('peak', lambda: analytics.peak_data(rng)),
            ('payments', lambda: analytics.payments_data(rng)),
            ('refunds', lambda: analytics.refunds_data(rng)),
            ('users', lambda: analytics.users_data(rng)),
        ]
        rng = analytics.resolve_range('last_30_days')
        results = []
        for name, fn in areas:
            elapsed = _timeit(fn, runs)
            results.append((name, elapsed))
            print(f'  {name:<12} {_ms(elapsed)}')
        total = sum(e for _, e in results)
        print(f'  {"TOTAL (10 areas)":<12} {_ms(total)}')

    # ------------------------------------------------------------------
    def _compare_naive(self, runs):
        rng = analytics.resolve_range('last_30_days')

        def orm_revenue():
            return Payment.objects.filter(status='completed', paid_at__gte=rng.start,
                                          paid_at__lt=rng.end).aggregate(
                v=Sum('amount'))['v']

        def naive_revenue():
            total = Decimal('0.00')
            for p in Payment.objects.filter(status='completed', paid_at__gte=rng.start,
                                            paid_at__lt=rng.end).values_list('amount', flat=True):
                total += Decimal(p or 0)
            return total

        t_orm = _timeit(orm_revenue, runs)
        t_naive = _timeit(naive_revenue, runs)
        print(f'  ORM aggregate (SELECT SUM ... GROUP) : {_ms(t_orm)}')
        print(f'  Naive loop  (load all rows into Python): {_ms(t_naive)}')
        if t_naive > 0:
            print(f'  Speed-up                                : {t_naive / t_orm:,.1f}x')

    # ------------------------------------------------------------------
    def _index_impact(self, runs):
        rng = analytics.resolve_range('last_30_days')

        def q():
            return Booking.objects.filter(
                status='confirmed', booked_at__gte=rng.start, booked_at__lt=rng.end
            ).count()

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='movies_booking' "
                "AND name LIKE 'movies_book_status%'")
            rows = cursor.fetchall()
        index_name = rows[0][0] if rows else None
        if not index_name:
            print('  (status, booked_at) index not found; skipping.')
            return

        print(f'  Dropping real index {index_name} inside a transaction (rolls back after)...')
        t_indexed = _timeit(q, runs)
        print(f'  WITH    (status, booked_at) index : {_ms(t_indexed)}')
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(f'DROP INDEX {index_name}')
                cursor.execute(
                    "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM movies_booking "
                    f"WHERE status = 'confirmed' AND booked_at >= '{rng.start.isoformat(' ')}' "
                    f"AND booked_at < '{rng.end.isoformat(' ')}'")
                plan = [r[3] for r in cursor.fetchall()]
            t_scanned = _timeit(q, runs)
            print(f'  WITHOUT index (full table scan)     : {_ms(t_scanned)}')
            print(f'  Plan without index: {plan[0]}')
            transaction.set_rollback(True)
        if t_scanned > 0:
            print(f'  Speed-up from the index              : {t_scanned / t_indexed:,.1f}x')
