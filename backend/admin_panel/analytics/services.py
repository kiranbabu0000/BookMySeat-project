"""Analytics business logic for the Admin portal.

All queries are executed with the Django ORM using aggregate/annotate calls so
they stay efficient even with 100k+ bookings. Date-range filtering always uses
half-open intervals (``start <= value < end``) on indexed datetime columns so
the database can leverage the indexes added in ``movies.0013`` /
``admin_panel.0008``.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth.models import User
from django.db.models import Count, Sum, Q, Max
from django.db.models.functions import TruncDate, TruncMonth, ExtractYear, ExtractHour, ExtractWeekDay, Coalesce
from django.utils import timezone

from movies.models import Booking, Movie, Seat, Theater
from admin_panel.models import Payment, PaymentTransaction

ZERO = Decimal('0.00')

REFUND_STATUSES = ('refund_requested', 'refunded')

RANGE_PRESETS = (
    ('today', 'Today'),
    ('yesterday', 'Yesterday'),
    ('last_7_days', 'Last 7 Days'),
    ('last_30_days', 'Last 30 Days'),
    ('last_90_days', 'Last 90 Days'),
    ('this_month', 'This Month'),
    ('previous_month', 'Previous Month'),
    ('this_year', 'This Year'),
    ('previous_year', 'Previous Year'),
    ('custom', 'Custom Range'),
)


@dataclass(frozen=True)
class DateRange:
    """A half-open time window plus the equal-length window before it.

    ``start``/``end`` are timezone-aware datetimes (``end`` exclusive). The
    previous window enables period-over-period percentage changes.
    """
    label: str
    start: datetime
    end: datetime
    prev_start: datetime
    prev_end: datetime

    @property
    def start_date(self):
        return self.start.date()

    @property
    def end_date(self):
        return (self.end - timedelta(microseconds=1)).date()

    @property
    def span_days(self):
        return (self.end - self.start).days


def _aware(d):
    return timezone.make_aware(datetime.combine(d, time.min))


def _month_start(d):
    return date(d.year, d.month, 1)


def _add_months(d, count):
    month = d.month - 1 + count
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, 1)


def resolve_range(range_key, start_date=None, end_date=None):
    """Resolve a preset/custom key into a :class:`DateRange`."""
    today = timezone.now().date()
    if range_key == 'custom':
        if not start_date or not end_date:
            range_key = 'last_30_days'
        else:
            if start_date > end_date:
                start_date, end_date = end_date, start_date
            start = _aware(start_date)
            end = _aware(end_date + timedelta(days=1))
            span = (end - start).days
            return DateRange(
                label=f'Custom ({start_date.strftime("%d %b %Y")} - {end_date.strftime("%d %b %Y")})',
                start=start,
                end=end,
                prev_start=start - timedelta(days=span),
                prev_end=start,
            )

    if range_key == 'today':
        start, end = today, today + timedelta(days=1)
        prev_start, prev_end = today - timedelta(days=1), today
        label = 'Today'
    elif range_key == 'yesterday':
        start, end = today - timedelta(days=1), today
        prev_start, prev_end = today - timedelta(days=2), today - timedelta(days=1)
        label = 'Yesterday'
    elif range_key == 'last_7_days':
        start, end = today - timedelta(days=6), today + timedelta(days=1)
        prev_start, prev_end = start - timedelta(days=7), start
        label = 'Last 7 Days'
    elif range_key == 'last_90_days':
        start, end = today - timedelta(days=89), today + timedelta(days=1)
        prev_start, prev_end = start - timedelta(days=90), start
        label = 'Last 90 Days'
    elif range_key == 'this_month':
        start, end = _month_start(today), _add_months(today, 1)
        prev_start, prev_end = _add_months(start, -1), start
        label = 'This Month'
    elif range_key == 'previous_month':
        start, end = _add_months(today, -1), _month_start(today)
        prev_start, prev_end = _add_months(start, -1), start
        label = 'Previous Month'
    elif range_key == 'this_year':
        start, end = date(today.year, 1, 1), date(today.year + 1, 1, 1)
        prev_start, prev_end = date(today.year - 1, 1, 1), start
        label = 'This Year'
    elif range_key == 'previous_year':
        start, end = date(today.year - 1, 1, 1), date(today.year, 1, 1)
        prev_start, prev_end = date(today.year - 2, 1, 1), start
        label = 'Previous Year'
    else:  # last_30_days default
        start, end = today - timedelta(days=29), today + timedelta(days=1)
        prev_start, prev_end = start - timedelta(days=30), start
        label = 'Last 30 Days'

    return DateRange(
        label=label,
        start=_aware(start),
        end=_aware(end),
        prev_start=_aware(prev_start),
        prev_end=_aware(prev_end),
    )


def choose_granularity(rng):
    days = rng.span_days
    if days <= 92:
        return 'day'
    if days <= 730:
        return 'month'
    return 'year'


def _bucket_windows(rng, granularity=None):
    """Yield ``(start_dt, end_dt, label)`` windows covering the range."""
    granularity = granularity or choose_granularity(rng)
    if granularity == 'day':
        d = rng.start_date
        while d <= rng.end_date:
            yield _aware(d), _aware(d + timedelta(days=1)), d.strftime('%d %b')
            d += timedelta(days=1)
    elif granularity == 'month':
        start = rng.start_date.replace(day=1)
        cursor = start
        while cursor < rng.end_date:
            nxt = _add_months(cursor, 1)
            yield _aware(cursor), _aware(nxt), cursor.strftime('%b %Y')
            cursor = nxt
    else:
        for y in range(rng.start.year, rng.end.year + 1):
            if y > rng.end.year:
                continue
            yield _aware(date(y, 1, 1)), _aware(date(y + 1, 1, 1)), str(y)


def _series(queryset, date_field, rng, aggregate='count', sum_field=None):
    """Return ``(labels, values, granularity)`` for a date-bucketed series."""
    granularity = choose_granularity(rng)
    windows = list(_bucket_windows(rng, granularity))
    qs = queryset.filter(**{
        f'{date_field}__gte': rng.start,
        f'{date_field}__lt': rng.end,
    })
    agg = Count('id') if aggregate == 'count' else Sum(sum_field)

    if granularity == 'day':
        grouped = qs.annotate(bucket=TruncDate(date_field)).values('bucket').annotate(v=agg)
        lookup = {row['bucket']: row['v'] for row in grouped}
        key = lambda w: w[0].date()
    elif granularity == 'month':
        grouped = qs.annotate(bucket=TruncMonth(date_field)).values('bucket').annotate(v=agg)
        lookup = {row['bucket']: row['v'] for row in grouped}
        key = lambda w: w[0].date().replace(day=1)
    else:
        grouped = qs.annotate(bucket=ExtractYear(date_field)).values('bucket').annotate(v=agg)
        lookup = {row['bucket']: row['v'] for row in grouped}
        key = lambda w: w[0].year

    labels = [w[2] for w in windows]
    values = [lookup.get(key(w), 0) for w in windows]
    return labels, values, granularity


def _distribution(queryset, group_field, sum_field=None):
    """Return a list of ``{key, count, amount}`` grouped by a column."""
    annotations = {'count': Count('id')}
    if sum_field:
        annotations['amount'] = Coalesce(Sum(sum_field), ZERO)
    qs = queryset.values(group_field).annotate(**annotations).order_by('-count')
    out = []
    for row in qs:
        entry = {
            'key': row[group_field] or 'Unknown',
            'count': row['count'],
        }
        if sum_field:
            entry['amount'] = row['amount']
        out.append(entry)
    return out


def _pct_change(current, previous):
    """Percentage change of current vs previous, or None when not computable."""
    if previous is None or previous == 0:
        return None
    return round((Decimal(current) - Decimal(previous)) / Decimal(previous) * 100, 1)


def money(value, places=2):
    value = Decimal(value or 0)
    q = Decimal('0.01') if places == 2 else Decimal('0.1')
    value = value.quantize(q, rounding=ROUND_HALF_UP)
    s = f'{value:,.{places}f}'
    return f'\u20b9{s}'


def fmt_int(value):
    return f'{int(value or 0):,}'


# ---------------------------------------------------------------------------
# Summary / overview
# ---------------------------------------------------------------------------

def summary_metrics(rng):
    """Core KPIs for the range plus period-over-period changes."""
    def bucket(bookings, r):
        confirmed = bookings.filter(status='confirmed')
        return {
            'revenue': _sum_payments(r),
            'bookings': bookings.count(),
            'confirmed': confirmed.count(),
            'cancelled': bookings.filter(status='cancelled').count(),
            'cancelled_value': bookings.filter(status='cancelled').aggregate(
                v=Coalesce(Sum('total'), ZERO))['v'],
            'new_users': User.objects.filter(date_joined__gte=r.start, date_joined__lt=r.end).count(),
            'refunds': PaymentTransaction.objects.filter(
                status__in=REFUND_STATUSES, **{f'created_at__gte': r.start, f'created_at__lt': r.end}
            ).count(),
            'refund_amount': _sum_transaction_amounts(r),
        }

    def _sum_payments(r):
        return Payment.objects.filter(status='completed', **{f'paid_at__gte': r.start, f'paid_at__lt': r.end}).aggregate(
            v=Coalesce(Sum('amount'), ZERO))['v']

    def _sum_transaction_amounts(r):
        return PaymentTransaction.objects.filter(
            status__in=REFUND_STATUSES, created_at__gte=r.start, created_at__lt=r.end
        ).aggregate(v=Coalesce(Sum('amount'), ZERO))['v']

    bookings = Booking.objects.filter(**{f'booked_at__gte': rng.start, f'booked_at__lt': rng.end})
    cur = bucket(bookings, rng)

    prev_bookings = Booking.objects.filter(**{f'booked_at__gte': rng.prev_start, f'booked_at__lt': rng.prev_end})
    prev = bucket(prev_bookings, DateRange(
        label='Previous', start=rng.prev_start, end=rng.prev_end,
        prev_start=rng.prev_start, prev_end=rng.prev_end,
    ))

    confirmed = cur['confirmed']
    revenue = Decimal(cur['revenue'])
    aov = (revenue / confirmed) if confirmed else ZERO

    return {
        'range_label': rng.label,
        'revenue': revenue,
        'revenue_fmt': money(revenue),
        'revenue_change': _pct_change(revenue, prev['revenue']),
        'bookings': cur['bookings'],
        'bookings_change': _pct_change(cur['bookings'], prev['bookings']),
        'confirmed': confirmed,
        'tickets': confirmed,
        'aov': aov,
        'aov_fmt': money(aov),
        'aov_change': _pct_change(aov, (prev['revenue'] / prev['confirmed']) if prev['confirmed'] else ZERO),
        'cancelled': cur['cancelled'],
        'cancelled_value': cur['cancelled_value'],
        'cancelled_change': _pct_change(cur['cancelled'], prev['cancelled']),
        'new_users': cur['new_users'],
        'new_users_change': _pct_change(cur['new_users'], prev['new_users']),
        'refunds': cur['refunds'],
        'refund_amount': cur['refund_amount'],
        'refund_amount_fmt': money(cur['refund_amount']),
        'refund_change': _pct_change(cur['refunds'], prev['refunds']),
    }


def overview_data(rng):
    summary = summary_metrics(rng)
    revenue_labels, revenue_values, revenue_gran = _series(
        Payment.objects.filter(status='completed'), 'paid_at', rng, 'sum', 'amount')
    booking_labels, booking_values, booking_gran = _series(
        Booking.objects.all(), 'booked_at', rng)
    recent = list(
        Booking.objects.select_related('user', 'movie', 'theater')
        .filter(**{f'booked_at__gte': rng.start, f'booked_at__lt': rng.end})
        .order_by('-booked_at')[:10]
    )
    return {
        'summary': summary,
        'revenue_series': {'labels': revenue_labels, 'values': [float(v) for v in revenue_values], 'granularity': revenue_gran},
        'bookings_series': {'labels': booking_labels, 'values': booking_values, 'granularity': booking_gran},
        'recent_bookings': [
            {
                'id': b.id,
                'booking_ref': b.booking_ref,
                'user': b.user.username if b.user else '—',
                'movie': b.movie.name if b.movie else '—',
                'theater': b.theater.name if b.theater else '—',
                'status': b.status,
                'total': str(b.total),
                'booked_at': b.booked_at.strftime('%d %b %Y, %I:%M %p'),
            }
            for b in recent
        ],
    }


# ---------------------------------------------------------------------------
# Revenue analytics
# ---------------------------------------------------------------------------

def revenue_data(rng):
    completed = Payment.objects.filter(
        status='completed', paid_at__gte=rng.start, paid_at__lt=rng.end)
    labels, values, gran = _series(completed, 'paid_at', rng, 'sum', 'amount')
    by_method = _distribution(completed, 'payment_method', 'amount')
    components = Booking.objects.filter(
        status='confirmed', **{f'booked_at__gte': rng.start, f'booked_at__lt': rng.end}
    ).aggregate(
        ticket=Coalesce(Sum('ticket_price'), ZERO),
        gst=Coalesce(Sum('gst_amount'), ZERO),
        platform_fee=Coalesce(Sum('platform_fee'), ZERO),
        misc_fee=Coalesce(Sum('misc_fee'), ZERO),
        discount=Coalesce(Sum('discount'), ZERO),
        total=Coalesce(Sum('total'), ZERO),
    )
    return {
        'summary': summary_metrics(rng),
        'series': {'labels': labels, 'values': [float(v) for v in values], 'granularity': gran},
        'by_method': [{'key': m['key'], 'count': m['count'], 'amount': float(m['amount'])} for m in by_method],
        'components': {
            'ticket': float(components['ticket']),
            'gst': float(components['gst']),
            'platform_fee': float(components['platform_fee']),
            'misc_fee': float(components['misc_fee']),
            'discount': float(components['discount']),
            'total': float(components['total']),
            'ticket_fmt': f"{components['ticket']:,.2f}",
            'gst_fmt': f"{components['gst']:,.2f}",
            'platform_fee_fmt': f"{components['platform_fee']:,.2f}",
            'misc_fee_fmt': f"{components['misc_fee']:,.2f}",
            'discount_fmt': f"{components['discount']:,.2f}",
            'total_fmt': f"{components['total']:,.2f}",
        },
    }


# ---------------------------------------------------------------------------
# Bookings analytics
# ---------------------------------------------------------------------------

def bookings_data(rng):
    in_range = Booking.objects.filter(**{f'booked_at__gte': rng.start, f'booked_at__lt': rng.end})
    labels, values, gran = _series(Booking.objects.all(), 'booked_at', rng)
    statuses = _distribution(in_range, 'status')
    weekday = _weekday_series(Booking.objects.all(), 'booked_at', rng)
    hour = _hour_series(Booking.objects.all(), 'booked_at', rng)
    total = in_range.count()
    confirmed = in_range.filter(status='confirmed').count()
    cancelled = in_range.filter(status='cancelled').count()
    cancelled_rate = round((cancelled / total * 100), 1) if total else 0.0
    return {
        'series': {'labels': labels, 'values': values, 'granularity': gran},
        'statuses': statuses,
        'weekday': weekday,
        'hour': hour,
        'total': total,
        'confirmed': confirmed,
        'cancelled': cancelled,
        'cancelled_rate': cancelled_rate,
        'cancelled_rate_fmt': f'{cancelled_rate}%',
    }


def _weekday_series(queryset, date_field, rng):
    rows = queryset.filter(**{f'{date_field}__gte': rng.start, f'{date_field}__lt': rng.end})\
        .annotate(dow=ExtractWeekDay(date_field)).values('dow').annotate(c=Count('id'))
    counts = {row['dow']: row['c'] for row in rows}
    labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    values = [counts.get((i + 1) % 7 + 1, 0) for i in range(7)]
    return {'labels': labels, 'values': values}


def _hour_series(queryset, date_field, rng):
    rows = queryset.filter(**{f'{date_field}__gte': rng.start, f'{date_field}__lt': rng.end})\
        .annotate(hour=ExtractHour(date_field)).values('hour').annotate(c=Count('id'))
    counts = {row['hour']: row['c'] for row in rows}
    labels = [f'{h:02d}:00' for h in range(24)]
    values = [counts.get(h, 0) for h in range(24)]
    return {'labels': labels, 'values': values}


# ---------------------------------------------------------------------------
# Occupancy analytics
# ---------------------------------------------------------------------------

def occupancy_data(rng):
    shows = Theater.objects.filter(time__gte=rng.start, time__lt=rng.end)
    # Three single-pass aggregates instead of one query with two distinct
    # joins: the seats x bookings join fan-out blows up at 100k bookings.
    # theater__in=<queryset> keeps the filtered show list in a subquery, so
    # no SQLite bound-variable limit is hit for large theater sets.
    seat_counts = dict(
        Seat.objects.filter(theater__in=shows)
        .values('theater_id')
        .annotate(c=Count('id'))
        .values_list('theater_id', 'c')
    )
    book_counts = dict(
        Booking.objects.filter(
            theater__in=shows,
            status='confirmed',
            booked_at__gte=rng.start,
            booked_at__lt=rng.end,
        )
        .values('theater_id')
        .annotate(c=Count('id'))
        .values_list('theater_id', 'c')
    )
    info = {
        t['id']: t
        for t in shows.values('id', 'name', 'movie__name')
    }

    total_seats = sum(seat_counts.values())
    occupied = sum(book_counts.values())

    theater_rows = []
    for tid, t in info.items():
        total = seat_counts.get(tid, 0) or 0
        bk = book_counts.get(tid, 0) or 0
        theater_rows.append({
            'id': tid,
            'name': t['name'],
            'movie': t['movie__name'] or '—',
            'total_seats': total,
            'booked_seats': bk,
            'rate': round((bk / total * 100), 1) if total else 0.0,
        })
    theater_rows.sort(key=lambda r: r['booked_seats'], reverse=True)
    return {
        'shows': len(info),
        'total_seats': total_seats,
        'booked_seats': occupied,
        'occupancy_rate': round((occupied / total_seats * 100), 1) if total_seats else 0.0,
        'occupancy_rate_fmt': f"{round((occupied / total_seats * 100), 1) if total_seats else 0.0}%",
        'per_theater': theater_rows[:15],
    }


# ---------------------------------------------------------------------------
# Movie analytics
# ---------------------------------------------------------------------------

def movies_data(rng):
    base = Movie.objects.filter(
        booking__booked_at__gte=rng.start, booking__booked_at__lt=rng.end
    )
    top_revenue = list(
        base.annotate(
            bookings=Count('booking', filter=Q(booking__status='confirmed')),
            revenue=Coalesce(Sum('booking__total', filter=Q(booking__status='confirmed')), ZERO),
        ).order_by('-revenue')[:10]
    )
    top_bookings = list(
        Movie.objects.filter(
            booking__booked_at__gte=rng.start, booking__booked_at__lt=rng.end
        ).annotate(
            bookings=Count('booking', filter=Q(booking__status='confirmed')),
            revenue=Coalesce(Sum('booking__total', filter=Q(booking__status='confirmed')), ZERO),
        ).order_by('-bookings')[:10]
    )
    all_movies = Movie.objects.annotate(
        bookings=Count('booking', filter=Q(booking__status='confirmed', booking__booked_at__gte=rng.start, booking__booked_at__lt=rng.end)),
        revenue=Coalesce(Sum('booking__total', filter=Q(booking__status='confirmed', booking__booked_at__gte=rng.start, booking__booked_at__lt=rng.end)), ZERO),
    ).order_by('-bookings')
    total_revenue = all_movies.aggregate(v=Coalesce(Sum('revenue'), ZERO))['v']
    movies_with_bookings = all_movies.filter(bookings__gt=0).count()

    def _rows(items):
        return [
            {
                'id': m.id,
                'name': m.name,
                'bookings': m.bookings,
                'revenue': float(m.revenue),
                'share': round((float(m.revenue) / float(total_revenue) * 100), 1) if total_revenue else 0.0,
            }
            for m in items
        ]

    return {
        'top_by_revenue': _rows(top_revenue),
        'top_by_bookings': _rows(top_bookings),
        'total_revenue': float(total_revenue),
        'total_revenue_fmt': f'{float(total_revenue):,.2f}',
        'movies_with_bookings': movies_with_bookings,
        'active_movies': Movie.objects.filter(status='now_showing').count(),
        'upcoming_movies': Movie.objects.filter(status='coming_soon').count(),
    }


# ---------------------------------------------------------------------------
# Theater analytics
# ---------------------------------------------------------------------------

def theaters_data(rng):
    shows = Theater.objects.filter(time__gte=rng.start, time__lt=rng.end)
    top = list(
        shows.annotate(
            bookings=Count(
                'booking',
                filter=Q(
                    booking__status='confirmed',
                    booking__booked_at__gte=rng.start,
                    booking__booked_at__lt=rng.end,
                ),
            ),
            revenue=Coalesce(Sum(
                'booking__total',
                filter=Q(
                    booking__status='confirmed',
                    booking__booked_at__gte=rng.start,
                    booking__booked_at__lt=rng.end,
                ),
            ), ZERO),
        ).order_by('-revenue')[:10]
    )
    rows = []
    for t in top:
        rows.append({
            'id': t.id,
            'name': t.name,
            'movie': t.movie.name if t.movie_id else '—',
            'bookings': t.bookings,
            'revenue': float(t.revenue),
        })
    total_shows = shows.count()
    total_revenue = sum(t['revenue'] for t in rows)
    return {
        'theaters': rows,
        'total_shows': total_shows,
        'total_theaters': Theater.objects.values('name').distinct().count(),
        'cancelled_shows': shows.filter(status='cancelled').count(),
        'avg_revenue_show': round(total_revenue / total_shows, 2) if total_shows else 0.0,
        'avg_revenue_show_fmt': f'{total_revenue / total_shows:,.2f}' if total_shows else '0.00',
    }


# ---------------------------------------------------------------------------
# Peak booking analytics
# ---------------------------------------------------------------------------

def peak_data(rng):
    confirmed = Booking.objects.filter(status='confirmed')
    hour = _hour_series(confirmed, 'booked_at', rng)
    weekday = _weekday_series(confirmed, 'booked_at', rng)
    labels, values, gran = _series(confirmed, 'booked_at', rng)
    matrix = _hour_weekday_matrix(confirmed, 'booked_at', rng)
    peak_hour = max(range(24), key=lambda h: hour['values'][h])
    peak_weekday = max(range(7), key=lambda i: weekday['values'][i])
    return {
        'hour': hour,
        'weekday': weekday,
        'series': {'labels': labels, 'values': values, 'granularity': gran},
        'matrix': matrix,
        'peak_hour': peak_hour,
        'peak_hour_label': f'{peak_hour:02d}:00',
        'peak_hour_count': hour['values'][peak_hour],
        'peak_weekday': weekday['labels'][peak_weekday],
        'peak_weekday_count': weekday['values'][peak_weekday],
    }


def _hour_weekday_matrix(queryset, date_field, rng):
    rows = queryset.filter(**{f'{date_field}__gte': rng.start, f'{date_field}__lt': rng.end})\
        .annotate(hour=ExtractHour(date_field), dow=ExtractWeekDay(date_field))\
        .values('hour', 'dow').annotate(c=Count('id'))
    matrix = [[0] * 24 for _ in range(7)]
    for row in rows:
        weekday_index = (row['dow'] + 5) % 7
        matrix[weekday_index][row['hour']] = row['c']
    return {'weekdays': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], 'matrix': matrix}


# ---------------------------------------------------------------------------
# Payment analytics
# ---------------------------------------------------------------------------

def payments_data(rng):
    payments = Payment.objects.filter(**{f'paid_at__gte': rng.start, f'paid_at__lt': rng.end})
    transactions = PaymentTransaction.objects.filter(**{f'created_at__gte': rng.start, f'created_at__lt': rng.end})
    payment_statuses = _distribution(payments, 'status', 'amount')
    tx_statuses = _distribution(transactions, 'status', 'amount')
    methods = _distribution(payments, 'payment_method', 'amount')
    total_tx = transactions.count()
    captured = transactions.filter(status='captured').count()
    failed = transactions.filter(status='failed').count()
    labels, values, gran = _series(transactions.filter(status='captured'), 'created_at', rng, 'count')
    return {
        'payment_statuses': [{'key': s['key'], 'count': s['count'], 'amount': float(s['amount'])} for s in payment_statuses],
        'tx_statuses': [{'key': s['key'], 'count': s['count'], 'amount': float(s['amount'])} for s in tx_statuses],
        'methods': [{'key': m['key'], 'count': m['count'], 'amount': float(m['amount'])} for m in methods],
        'series': {'labels': labels, 'values': values, 'granularity': gran},
        'total_transactions': total_tx,
        'captured': captured,
        'failed': failed,
        'success_rate': round((captured / total_tx * 100), 1) if total_tx else 0.0,
        'success_rate_fmt': f"{round((captured / total_tx * 100), 1) if total_tx else 0.0}%",
        'failure_rate': round((failed / total_tx * 100), 1) if total_tx else 0.0,
        'failure_rate_fmt': f"{round((failed / total_tx * 100), 1) if total_tx else 0.0}%",
    }


# ---------------------------------------------------------------------------
# Refund analytics
# ---------------------------------------------------------------------------

def refunds_data(rng):
    refunds = PaymentTransaction.objects.filter(status__in=REFUND_STATUSES)
    labels, values, gran = _series(refunds, 'created_at', rng, 'sum', 'amount')
    detail = list(
        refunds.filter(**{f'created_at__gte': rng.start, f'created_at__lt': rng.end})
        .select_related('user', 'reservation')
        .order_by('-created_at')[:100]
    )
    statuses = _distribution(refunds.filter(**{f'created_at__gte': rng.start, f'created_at__lt': rng.end}), 'status', 'amount')
    captured_total = PaymentTransaction.objects.filter(
        status='captured', created_at__gte=rng.start, created_at__lt=rng.end
    ).aggregate(v=Coalesce(Sum('amount'), ZERO))['v']
    refund_total = refunds.filter(created_at__gte=rng.start, created_at__lt=rng.end).aggregate(
        v=Coalesce(Sum('amount'), ZERO))['v']
    refund_count = refunds.filter(created_at__gte=rng.start, created_at__lt=rng.end).count()
    rate = round((float(refund_total) / float(captured_total) * 100), 2) if captured_total else 0.0
    avg_refund = (refund_total / refund_count) if refund_count else ZERO
    return {
        'series': {'labels': labels, 'values': [float(v) for v in values], 'granularity': gran},
        'statuses': [{'key': s['key'], 'count': s['count'], 'amount': float(s['amount'])} for s in statuses],
        'count': refund_count,
        'amount': float(refund_total),
        'amount_fmt': money(refund_total),
        'rate': rate,
        'rate_fmt': f'{rate}%',
        'avg_fmt': money(avg_refund),
        'details': [
            {
                'id': t.id,
                'gateway_order_id': t.gateway_order_id or str(t.id),
                'user': t.user.username if t.user else '—',
                'amount': str(t.amount),
                'status': t.status,
                'method': t.method or '—',
                'created_at': t.created_at.strftime('%d %b %Y, %I:%M %p'),
                'reservation': t.reservation.token[:12] if t.reservation and t.reservation.token else '—',
            }
            for t in detail
        ],
    }


# ---------------------------------------------------------------------------
# User analytics
# ---------------------------------------------------------------------------

def users_data(rng):
    labels, values, gran = _series(User.objects.all(), 'date_joined', rng)
    top = list(
        User.objects.annotate(
            bookings=Count('booking', filter=Q(booking__status='confirmed', booking__booked_at__gte=rng.start, booking__booked_at__lt=rng.end)),
            spend=Coalesce(Sum('booking__total', filter=Q(booking__status='confirmed', booking__booked_at__gte=rng.start, booking__booked_at__lt=rng.end)), ZERO),
            last_booking=Max('booking__booked_at', filter=Q(booking__booked_at__gte=rng.start, booking__booked_at__lt=rng.end)),
        ).order_by('-bookings')[:10]
    )
    return {
        'series': {'labels': labels, 'values': values, 'granularity': gran},
        'total_users': User.objects.count(),
        'active_users': User.objects.filter(is_active=True).count(),
        'staff_users': User.objects.filter(is_staff=True).count(),
        'new_in_range': User.objects.filter(date_joined__gte=rng.start, date_joined__lt=rng.end).count(),
        'top': [
            {
                'id': u.id,
                'username': u.username,
                'email': u.email or '—',
                'date_joined': u.date_joined.strftime('%d %b %Y'),
                'bookings': u.bookings,
                'spend': float(u.spend),
                'last_booking': u.last_booking.strftime('%d %b %Y') if u.last_booking else '—',
            }
            for u in top
        ],
    }


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

def export_sections(area, rng):
    """Return a list of ``(sheet_title, headers, rows)`` for a report area."""
    if area == 'overview':
        s = summary_metrics(rng)
        d = overview_data(rng)
        revenue_series = [
            [d['revenue_series']['labels'][i], f"{d['revenue_series']['values'][i]:,.2f}"]
            for i in range(len(d['revenue_series']['labels']))
        ]
        booking_series = [
            [d['bookings_series']['labels'][i], d['bookings_series']['values'][i]]
            for i in range(len(d['bookings_series']['labels']))
        ]
        return [
            ('Summary', ['Metric', 'Value'], [
                ['Revenue', money(s['revenue'])],
                ['Total bookings', s['bookings']],
                ['Confirmed bookings', s['confirmed']],
                ['Cancelled bookings', s['cancelled']],
                ['Cancellation value', money(s['cancelled_value'])],
                ['Average order value', s['aov_fmt']],
                ['New users', s['new_users']],
                ['Refunds', s['refunds']],
                ['Refund amount', s['refund_amount_fmt']],
            ]),
            ('Revenue by period', ['Period', 'Revenue'], revenue_series),
            ('Bookings by period', ['Period', 'Bookings'], booking_series),
        ]
    if area == 'revenue':
        d = revenue_data(rng)
        series = [
            [d['series']['labels'][i], f"{d['series']['values'][i]:,.2f}"]
            for i in range(len(d['series']['labels']))
        ]
        return [
            ('Revenue by period', ['Period', 'Revenue'], series),
            ('By payment method', ['Method', 'Count', 'Amount'], [
                [m['key'], m['count'], f"{m['amount']:,.2f}"] for m in d['by_method']
            ]),
            ('Revenue components', ['Component', 'Amount'], [
                ['Ticket', f"{d['components']['ticket']:,.2f}"],
                ['GST', f"{d['components']['gst']:,.2f}"],
                ['Platform fee', f"{d['components']['platform_fee']:,.2f}"],
                ['Misc fee', f"{d['components']['misc_fee']:,.2f}"],
                ['Discount', f"{d['components']['discount']:,.2f}"],
                ['Total', f"{d['components']['total']:,.2f}"],
            ]),
        ]
    if area == 'bookings':
        d = bookings_data(rng)
        series = [
            [d['series']['labels'][i], d['series']['values'][i]]
            for i in range(len(d['series']['labels']))
        ]
        return [
            ('Bookings by period', ['Period', 'Bookings'], series),
            ('By status', ['Status', 'Count'], [[s['key'], s['count']] for s in d['statuses']]),
            ('By weekday', ['Weekday', 'Bookings'], [
                [d['weekday']['labels'][i], d['weekday']['values'][i]] for i in range(7)
            ]),
        ]
    if area == 'occupancy':
        d = occupancy_data(rng)
        return [
            ('Overall', ['Metric', 'Value'], [
                ['Shows', d['shows']],
                ['Total seats', d['total_seats']],
                ['Occupied seats', d['booked_seats']],
                ['Occupancy rate', f"{d['occupancy_rate']}%"],
            ]),
            ('By theater', ['Theater', 'Movie', 'Total seats', 'Booked seats', 'Occupancy %'], [
                [t['name'], t['movie'], t['total_seats'], t['booked_seats'], f"{t['rate']}%"]
                for t in d['per_theater']
            ]),
        ]
    if area == 'movies':
        d = movies_data(rng)
        return [
            ('Top by revenue', ['Movie', 'Bookings', 'Revenue', 'Share %'], [
                [m['name'], m['bookings'], f"{m['revenue']:,.2f}", m['share']] for m in d['top_by_revenue']
            ]),
            ('Top by bookings', ['Movie', 'Bookings', 'Revenue', 'Share %'], [
                [m['name'], m['bookings'], f"{m['revenue']:,.2f}", m['share']] for m in d['top_by_bookings']
            ]),
        ]
    if area == 'theaters':
        d = theaters_data(rng)
        return [
            ('Top theaters', ['Theater', 'Movie', 'Bookings', 'Revenue'], [
                [t['name'], t['movie'], t['bookings'], f"{t['revenue']:,.2f}"] for t in d['theaters']
            ]),
        ]
    if area == 'peak':
        d = peak_data(rng)
        return [
            ('By hour', ['Hour', 'Bookings'], [
                [d['hour']['labels'][h], d['hour']['values'][h]] for h in range(24)
            ]),
            ('By weekday', ['Weekday', 'Bookings'], [
                [d['weekday']['labels'][i], d['weekday']['values'][i]] for i in range(7)
            ]),
        ]
    if area == 'payments':
        d = payments_data(rng)
        return [
            ('Payment status', ['Status', 'Count', 'Amount'], [
                [s['key'], s['count'], f"{s['amount']:,.2f}"] for s in d['payment_statuses']
            ]),
            ('Transaction status', ['Status', 'Count', 'Amount'], [
                [s['key'], s['count'], f"{s['amount']:,.2f}"] for s in d['tx_statuses']
            ]),
            ('By method', ['Method', 'Count', 'Amount'], [
                [m['key'], m['count'], f"{m['amount']:,.2f}"] for m in d['methods']
            ]),
        ]
    if area == 'refunds':
        d = refunds_data(rng)
        detail = [
            [t['id'], t['gateway_order_id'], t['user'], t['reservation'], t['method'], t['status'], t['amount'], t['created_at']]
            for t in d['details']
        ]
        return [
            ('Summary', ['Metric', 'Value'], [
                ['Refunds', d['count']],
                ['Refund amount', d['amount_fmt']],
                ['Refund rate', f"{d['rate']}%"],
            ]),
            ('Refund transactions', ['ID', 'Order ID', 'User', 'Reservation', 'Method', 'Status', 'Amount', 'Date'], detail),
        ]
    if area == 'users':
        d = users_data(rng)
        series = [
            [d['series']['labels'][i], d['series']['values'][i]]
            for i in range(len(d['series']['labels']))
        ]
        return [
            ('New users by period', ['Period', 'New users'], series),
            ('Top users', ['User', 'Email', 'Joined', 'Bookings', 'Spend', 'Last booking'], [
                [u['username'], u['email'], u['date_joined'], u['bookings'], f"{u['spend']:,.2f}", u['last_booking']]
                for u in d['top']
            ]),
        ]
    return []


DANGEROUS_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def _sanitize_cell(value):
    if isinstance(value, str) and value.startswith(DANGEROUS_PREFIXES):
        return "'" + value
    return value


def csv_bytes(sections):
    """Serialize export sections into CSV bytes (UTF-8 with BOM for Excel)."""
    import csv
    import io
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    first = True
    for title, headers, rows in sections:
        if not first:
            writer.writerow([])
        writer.writerow([_sanitize_cell(title)])
        writer.writerow([_sanitize_cell(h) for h in headers])
        for row in rows:
            writer.writerow([_sanitize_cell(cell) for cell in row])
        first = False
    return ('\ufeff' + buffer.getvalue()).encode('utf-8')


def xlsx_bytes(sections):
    """Serialize export sections into a minimal, standards-compliant XLSX.

    Uses only the Python stdlib (zipfile + XML), so no third-party dependency
    is needed on the Vercel build.
    """
    import re
    import zipfile
    from xml.sax.saxutils import escape

    def sheet_name(title):
        name = re.sub(r'[\[\]\*:/\?\\]', '', title)[:31]
        return name or 'Sheet'

    valid_sheets = [(sheet_name(t), h, r) for t, h, r in sections]
    sheet_names = []
    for name, _, _ in valid_sheets:
        base, i = name, 1
        while name in sheet_names:
            suffix = str(i)
            name = base[: 31 - len(suffix)] + suffix
            i += 1
        sheet_names.append(name)
    valid_sheets = [
        (sheet_names[i], valid_sheets[i][1], valid_sheets[i][2])
        for i in range(len(valid_sheets))
    ]

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + ''.join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(valid_sheets) + 1)
        ) + '</Types>'
    )

    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>'
        + ''.join(
            f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, (name, _, _) in enumerate(valid_sheets, start=1)
        ) + '</sheets></workbook>'
    )

    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + ''.join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(valid_sheets) + 1)
        ) +
        '<Relationship Id="rId{0}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        .format(len(valid_sheets) + 1) + '</Relationships>'
    )

    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font>'
        '</fonts>'
        '<fills count="2">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '</fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '</cellXfs>'
        '</styleSheet>'
    )

    def cell(ref, value, style=0):
        if isinstance(value, bool):
            v = '1' if value else '0'
        elif isinstance(value, (int, float, Decimal)):
            v = str(float(value))
        else:
            text = escape(str(value or ''))
            return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'
        return f'<c r="{ref}" s="{style}"><v>{v}</v></c>'

    def column_ref(index):
        letters = ''
        index += 1
        while index:
            index, rem = divmod(index - 1, 26)
            letters = chr(65 + rem) + letters
        return letters

    def worksheet(idx, headers, rows):
        body = []
        body.append(f'<row r="1">' + ''.join(
            cell(f'{column_ref(c)}1', value, style=1) for c, value in enumerate(headers)
        ) + '</row>')
        for r, row in enumerate(rows, start=2):
            body.append(f'<row r="{r}">' + ''.join(
                cell(f'{column_ref(c)}{r}', value) for c, value in enumerate(row)
            ) + '</row>')
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<dimension ref="A1:{column_ref(len(headers) - 1) if headers else "A"}{len(rows) + 1}"/>'
            f'<sheetData>{"".join(body)}</sheetData></worksheet>'
        )

    import io
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', content_types)
        zf.writestr('_rels/.rels', rels)
        zf.writestr('xl/workbook.xml', workbook)
        zf.writestr('xl/_rels/workbook.xml.rels', workbook_rels)
        zf.writestr('xl/styles.xml', styles)
        for i, (_, headers, rows) in enumerate(valid_sheets, start=1):
            zf.writestr(f'xl/worksheets/sheet{i}.xml', worksheet(i, headers, rows))
    return buffer.getvalue()
