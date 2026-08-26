from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum, Q, Avg, Max, Min, Exists, OuterRef, Subquery, F
from django.db.models.functions import TruncMonth, TruncDate, Coalesce
from django.utils import timezone
from django.utils.text import slugify
from django.core.paginator import Paginator
from django.utils.decorators import method_decorator
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View, TemplateView, DetailView
from django.urls import reverse_lazy, reverse
from django.http import JsonResponse, HttpResponseRedirect
from django.views.decorators.http import require_POST
import json
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote_plus

import sys
import django
import secrets
import logging
from movies.models import Movie, Theater, Seat, Booking, Reservation, ReservedSeat, SeatCategory, ShowPrice, TicketScan
from movies.services import ReservationError, create_walkin_bookings, pricing_for_seats
from movies.showtime import day_range_utc, show_status_info, showtime_zone
from movies.ticket_scan import scan_ticket

logger = logging.getLogger('admin_panel')
from .models import Genre, Language, CastMember, Theatre, Screen, Show, Trailer, MovieImage, AdminProfile, AdminPermission, AuditLog, Coupon, Notification, Review, Payment, PaymentTransaction, GSTSlab, PricingConfig
from .forms import (
    AdminLoginForm, MovieForm, GenreForm, LanguageForm, CastMemberForm,
    TheatreForm, ScreenForm, ShowForm, TrailerForm, MovieImageForm,
    BookingSearchForm, PaymentSearchForm, StaffCreateForm, StaffUpdateForm, AdminProfileForm,
    AdminPermissionForm, CouponForm, NotificationForm, ReviewForm,
    ReserveBookingForm, RefundForm, AdminProfileSelfEditForm, AdminUserSelfEditForm
)
from .decorators import admin_session_required, AdminSessionMixin, permission_required, clear_admin_session
from bookmyseat.ratelimit import is_locked_out, login_failed, login_succeeded, remaining_attempts
from .services import (
    BookingTransaction,
    hide_theater_for_show,
    sync_theater_from_show,
    group_bookings_into_transactions,
)


def admin_login_view(request):
    if request.session.get('is_admin_authenticated'):
        return redirect('admin_dashboard')

    if request.method == 'POST':
        form = AdminLoginForm(request.POST)
        username = form.data.get('username', '').strip()
        if is_locked_out('admin', request, username):
            messages.error(
                request,
                'Too many failed attempts. Please wait a few minutes and try again.',
            )
            return render(request, 'admin/login.html', {'form': form})
        if form.is_valid():
            password = form.cleaned_data['password']
            user = authenticate(request, username=username, password=password)
            if user is not None:
                if not user.is_active:
                    messages.error(request, 'This account has been deactivated.')
                    return render(request, 'admin/login.html', {'form': form})
                is_admin = user.is_superuser or AdminProfile.objects.filter(user=user, is_active=True).exists()
                if not is_admin:
                    messages.error(request, 'This account is not an admin account. Please use the customer login instead.')
                    return render(request, 'admin/login.html', {'form': form})
                login_succeeded('admin', request, username)
                # Rotate the session key so a pre-authentication session id
                # captured by an attacker cannot be reused (session fixation).
                request.session.cycle_key()
                clear_admin_session(request)
                request.session['admin_user_id'] = user.id
                request.session['is_admin_authenticated'] = True
                request.session['admin_login_time'] = str(timezone.now())
                request.session['admin_session_id'] = request.session.session_key
                request.session['admin_ip_address'] = request.META.get('REMOTE_ADDR')
                request.session['admin_user_agent'] = request.META.get('HTTP_USER_AGENT', '')
                AuditLog.objects.create(
                    user=user,
                    action='Admin Login',
                    module='Auth',
                    ip_address=request.META.get('REMOTE_ADDR')
                )
                return redirect('admin_dashboard')
            else:
                login_failed('admin', request, username)
                remaining = remaining_attempts('admin', request, username)
                if remaining:
                    messages.error(
                        request,
                        'Invalid username or password. {} attempt(s) remaining.'.format(remaining),
                    )
                else:
                    messages.error(
                        request,
                        'Too many failed attempts. Please wait a few minutes and try again.',
                    )
    else:
        form = AdminLoginForm()
    return render(request, 'admin/login.html', {'form': form})


def admin_logout_view(request):
    if request.method != 'POST':
        return redirect('admin_dashboard')
    admin_user_id = request.session.get('admin_user_id')
    if admin_user_id:
        try:
            user = User.objects.get(pk=admin_user_id)
        except (User.DoesNotExist, ValueError, TypeError):
            user = None
        if user is not None:
            AuditLog.objects.create(
                user=user,
                action='Admin Logout',
                module='Auth',
                ip_address=request.META.get('REMOTE_ADDR')
            )
    clear_admin_session(request)
    return redirect('admin_login')


def _dashboard_pct(current, previous):
    """Percentage change of ``current`` vs ``previous`` (None when undefined)."""
    if previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


def _dashboard_occupancy(day):
    """Return (occupancy_pct, booked_seats, total_seats) for shows on ``day``.

    Filters the show time with an index-friendly half-open range instead of
    ``theater__time__date``: the date-cast lookup wraps the column in a
    timezone function so no index can be used and every Seat row of every
    show gets joined and cast. On a seeded database (hundreds of thousands of
    seat rows) that made each call take seconds and the dashboard tens of
    seconds; the range form drives off the indexed ``Theater.time`` column.
    """
    start, end = day_range_utc(day)
    seats = Seat.objects.filter(theater__time__gte=start, theater__time__lt=end)
    total = seats.count()
    booked = seats.filter(is_booked=True).count()
    if total == 0:
        return 0, booked, total
    return round(booked / total * 100), booked, total


def _dashboard_occupancy_series(days):
    """Per-day occupancy for the last ``days`` theatre-local days.

    Returns ``{date: (occupancy_pct, booked_seats, total_seats)}`` computed in
    a SINGLE grouped query (one indexed range scan instead of one full
    join-per-day). Days without shows are simply absent from the mapping.
    """
    today = timezone.localdate()
    window_start = day_range_utc(today - timedelta(days=days - 1))[0]
    window_end = day_range_utc(today)[1]
    rows = (
        Seat.objects.filter(
            theater__time__gte=window_start, theater__time__lt=window_end,
        )
        .annotate(day=TruncDate('theater__time'))
        .values('day')
        .annotate(
            total=Count('id'),
            booked=Count('id', filter=Q(is_booked=True)),
        )
    )
    out = {}
    for row in rows:
        total, booked = row['total'], row['booked']
        pct = round(booked / total * 100) if total else 0
        day = row['day']
        out[day if isinstance(day, date) else day.date()] = (pct, booked, total)
    return out


def _dashboard_active_show_counts(days):
    """Active-show count per theatre-local day for the last ``days`` days.

    One grouped query replaces what used to be one ``time__date`` cast query
    per sparkline point.
    """
    today = timezone.localdate()
    window_start = day_range_utc(today - timedelta(days=days - 1))[0]
    window_end = day_range_utc(today)[1]
    rows = (
        Theater.objects.filter(
            time__gte=window_start, time__lt=window_end, status='active',
        )
        .annotate(day=TruncDate('time'))
        .values('day')
        .annotate(c=Count('id'))
    )
    return {row['day']: row['c'] for row in rows}


def _dashboard_series(days):
    """Daily revenue (completed payments) and booking counts, oldest first."""
    start_date = timezone.localdate() - timedelta(days=days - 1)
    start = timezone.make_aware(datetime.combine(start_date, datetime.min.time()), showtime_zone())
    rev_map = {
        row['day']: row['total']
        for row in (
            Payment.objects.filter(status='completed', paid_at__gte=start)
            .annotate(day=TruncDate('paid_at'))
            .values('day')
            .annotate(total=Sum('amount'))
        )
    }
    book_map = {
        row['day']: row['count']
        for row in (
            Booking.objects.filter(booked_at__gte=start)
            .annotate(day=TruncDate('booked_at'))
            .values('day')
            .annotate(count=Count('id'))
        )
    }
    labels, revenue, bookings = [], [], []
    for k in range(days):
        day = start_date + timedelta(days=k)
        labels.append(day.strftime('%d %b'))
        revenue.append(float(rev_map.get(day, 0)))
        bookings.append(book_map.get(day, 0))
    return labels, revenue, bookings


def _dashboard_monthly(months=12):
    """Monthly revenue + booking counts for the last ``months`` months."""
    today = timezone.localdate()
    starts = []
    for k in range(months - 1, -1, -1):
        offset = today.month - k
        starts.append(date(today.year + (offset - 1) // 12, (offset - 1) % 12 + 1, 1))
    start = timezone.make_aware(datetime.combine(starts[0], datetime.min.time()), showtime_zone())
    rev_map = {
        row['month'].date(): row['total']
        for row in (
            Payment.objects.filter(status='completed', paid_at__gte=start)
            .annotate(month=TruncMonth('paid_at'))
            .values('month')
            .annotate(total=Sum('amount'))
        )
    }
    book_map = {
        row['month'].date(): row['count']
        for row in (
            Booking.objects.filter(booked_at__gte=start)
            .annotate(month=TruncMonth('booked_at'))
            .values('month')
            .annotate(count=Count('id'))
        )
    }
    labels, revenue, bookings = [], [], []
    for s in starts:
        labels.append(s.strftime('%b %y'))
        revenue.append(float(rev_map.get(s, 0)))
        bookings.append(book_map.get(s, 0))
    return labels, revenue, bookings


def _dashboard_sparkline(values, width=120, height=32):
    """Return (line_points, area_path) strings for an inline SVG sparkline."""
    if not values:
        return '', ''
    mn, mx = min(values), max(values)
    rng = (mx - mn) or 1
    n = len(values)
    points = []
    for i, v in enumerate(values):
        x = round(2 + i * (width - 4) / (n - 1), 1) if n > 1 else width / 2
        y = round(height - 3 - (v - mn) / rng * (height - 8), 1)
        points.append((x, y))
    line = ' '.join(f'{x},{y}' for x, y in points)
    area = (
        f'M{points[0][0]},{height - 2} '
        + ' '.join(f'L{x},{y}' for x, y in points)
        + f' L{points[-1][0]},{height - 2} Z'
    )
    return line, area


class DashboardView(AdminSessionMixin, TemplateView):
    template_name = 'admin/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            return self._build_dashboard_context(context)
        except Exception:
            logger.exception('Dashboard query error')
            context['kpis'] = []
            context['chart_ranges'] = '{}'
            context['operations'] = []
            context['recent_bookings'] = []
            context['top_content'] = []
            context['theatre_perf'] = []
            messages.error(self.request, 'A dashboard widget failed to load. Some data may be missing.')
            return context

    def _build_dashboard_context(self, context):
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        week_start = today - timedelta(days=6)
        month_start = today.replace(day=1)
        context['today'] = today

        # Index-friendly aware bounds for the theatre-local calendar windows.
        # Half-open [start, end) ranges on the raw datetime columns let the
        # database use the btree indexes; __date casts cannot.
        today_start, tomorrow_start = day_range_utc(today)
        yesterday_start = day_range_utc(yesterday)[0]
        week_start_dt = day_range_utc(week_start)[0]
        month_start_dt = day_range_utc(month_start)[0]

        # --- Revenue + booking KPIs: one conditional-aggregation query each
        # instead of four separate scans per metric family. ---
        rev = Payment.objects.filter(
            status='completed', paid_at__gte=month_start_dt,
        ).aggregate(
            today=Coalesce(Sum('amount', filter=Q(
                paid_at__gte=today_start, paid_at__lt=tomorrow_start,
            )), Decimal('0.00')),
            yesterday=Coalesce(Sum('amount', filter=Q(
                paid_at__gte=yesterday_start, paid_at__lt=today_start,
            )), Decimal('0.00')),
            week=Coalesce(Sum('amount', filter=Q(
                paid_at__gte=week_start_dt,
            )), Decimal('0.00')),
            month=Coalesce(Sum('amount'), Decimal('0.00')),
        )
        today_revenue = rev['today']
        yesterday_revenue = rev['yesterday']
        week_revenue = rev['week']
        month_revenue = rev['month']

        bk = Booking.objects.filter(booked_at__gte=month_start_dt).aggregate(
            today=Count('id', filter=Q(
                booked_at__gte=today_start, booked_at__lt=tomorrow_start,
            )),
            yesterday=Count('id', filter=Q(
                booked_at__gte=yesterday_start, booked_at__lt=today_start,
            )),
            week=Count('id', filter=Q(booked_at__gte=week_start_dt)),
            month=Count('id'),
            cancelled_today=Count('id', filter=Q(
                booked_at__gte=today_start, booked_at__lt=tomorrow_start,
                status='cancelled',
            )),
        )
        today_bookings = bk['today']
        yesterday_bookings = bk['yesterday']
        week_bookings = bk['week']
        month_bookings = bk['month']
        cancelled_today = bk['cancelled_today']

        # --- Occupancy: one grouped range query feeds the KPIs AND the
        # sparkline (was two full join scans here plus six for the sparkline).
        occupancy_map = _dashboard_occupancy_series(days=8)
        occupancy_today, today_booked_seats, today_total_seats = (
            occupancy_map.get(today, (0, 0, 0)))
        occupancy_yesterday, _, _ = occupancy_map.get(yesterday, (0, 0, 0))

        # --- Active shows: one grouped query for the whole window.
        show_counts = _dashboard_active_show_counts(days=8)
        active_shows_today = show_counts.get(today, 0)
        active_shows_yesterday = show_counts.get(yesterday, 0)
        upcoming_shows = Theater.objects.filter(
            time__gte=today_start,
        ).exclude(status='cancelled').count()
        total_shows = Theater.objects.exclude(status='cancelled').count()

        pending_refunds = PaymentTransaction.objects.filter(status='refund_requested').count()
        active_reservations = Reservation.objects.filter(status='active').count()
        held_seats = ReservedSeat.objects.filter(reservation__status='active').count()
        unread_notifications = Notification.objects.filter(is_read=False).count()

        # --- Business performance chart data for 7 / 30 / 90 days + 12 months.
        # The 7-day series is computed once and reused for the KPI sparkline.
        d7_labels, spark_revenue, spark_bookings = _dashboard_series(7)
        spark_occupancy = [
            occupancy_map.get(today - timedelta(days=i), (0, 0, 0))[0]
            for i in range(6, -1, -1)
        ]
        spark_shows = [
            show_counts.get(today - timedelta(days=i), 0)
            for i in range(6, -1, -1)
        ]
        rev_line, rev_area = _dashboard_sparkline(spark_revenue)
        book_line, book_area = _dashboard_sparkline(spark_bookings)
        occ_line, occ_area = _dashboard_sparkline(spark_occupancy)
        show_line, show_area = _dashboard_sparkline(spark_shows)

        context['kpis'] = [
            {
                'key': 'revenue',
                'label': "Today's Revenue",
                'value': '\u20b9{:,.0f}'.format(today_revenue),
                'icon': 'bi-currency-rupee',
                'color': 'var(--accent-primary)',
                'change': _dashboard_pct(today_revenue, yesterday_revenue),
                'link': reverse('admin_payment_list'),
                'spark_line': rev_line,
                'spark_area': rev_area,
            },
            {
                'key': 'bookings',
                'label': "Today's Bookings",
                'value': str(today_bookings),
                'icon': 'bi-ticket-perforated',
                'color': 'var(--accent-secondary)',
                'change': _dashboard_pct(today_bookings, yesterday_bookings),
                'link': reverse('admin_booking_list'),
                'spark_line': book_line,
                'spark_area': book_area,
            },
            {
                'key': 'shows',
                'label': 'Active Shows Today',
                'value': str(active_shows_today),
                'icon': 'bi-calendar-check',
                'color': 'var(--accent-gold)',
                'change': _dashboard_pct(active_shows_today, active_shows_yesterday),
                'link': reverse('admin_show_list'),
                'spark_line': show_line,
                'spark_area': show_area,
            },
            {
                'key': 'occupancy',
                'label': "Today's Occupancy",
                'value': f'{occupancy_today}%',
                'icon': 'bi-grid-3x3-gap-fill',
                'color': 'var(--accent-success)',
                'change': _dashboard_pct(occupancy_today, occupancy_yesterday),
                'link': reverse('admin_analytics_occupancy'),
                'spark_line': occ_line,
                'spark_area': occ_area,
            },
        ]

        # --- Business performance chart data for 7 / 30 / 90 days + 12 months.
        # The 7-day series was already computed above for the sparkline —
        # reuse it instead of running the same grouped queries again.
        d30_labels, d30_rev, d30_book = _dashboard_series(30)
        d90_labels, d90_rev, d90_book = _dashboard_series(90)
        m_labels, m_rev, m_book = _dashboard_monthly(12)
        context['chart_ranges'] = json.dumps({
            '7d': {'labels': d7_labels, 'revenue': spark_revenue, 'bookings': spark_bookings},
            '30d': {'labels': d30_labels, 'revenue': d30_rev, 'bookings': d30_book},
            '90d': {'labels': d90_labels, 'revenue': d90_rev, 'bookings': d90_book},
            '12m': {'labels': m_labels, 'revenue': m_rev, 'bookings': m_book},
        })

        # --- Today's operations status tiles ---
        context['operations'] = [
            {
                'label': 'Shows Running Today', 'value': active_shows_today,
                'icon': 'bi-play-circle', 'color': 'var(--accent-gold)',
                'link': reverse('admin_show_list'),
            },
            {
                'label': 'Active Reservations', 'value': active_reservations,
                'icon': 'bi-hourglass-split', 'color': 'var(--accent-secondary)',
                'link': reverse('admin_reservation_list'),
            },
            {
                'label': 'Held Seats', 'value': held_seats,
                'icon': 'bi-grid-1x2', 'color': 'var(--accent-secondary)',
                'link': reverse('admin_reservation_list'),
            },
            {
                'label': 'Pending Refunds', 'value': pending_refunds,
                'icon': 'bi-arrow-counterclockwise', 'color': 'var(--accent-gold)',
                'link': reverse('admin_payment_list') + '?status=refund_requested',
            },
            {
                'label': 'Cancelled Today', 'value': cancelled_today,
                'icon': 'bi-x-octagon', 'color': 'var(--accent-primary)',
                'link': reverse('admin_booking_list') + '?status=cancelled',
            },
            {
                'label': 'Unread Notifications', 'value': unread_notifications,
                'icon': 'bi-bell', 'color': 'var(--accent-secondary)',
                'link': reverse('admin_notification_list'),
            },
        ]

        # --- Recent bookings with payment/refund status (grouped per transaction) ---
        recent_window = list(
            Booking.objects
            .select_related('user', 'movie', 'theater', 'seat', 'payment', 'reservation')
            .order_by('-booked_at')[:50]
        )
        context['recent_bookings'] = group_bookings_into_transactions(recent_window)[:8]

        # --- Top performing content across all categories (this month) ---
        context['top_content'] = (
            Movie.objects.filter(is_deleted=False)
            .annotate(
                recent_bookings=Count(
                    'booking',
                    filter=Q(booking__booked_at__gte=month_start_dt, booking__status='confirmed'),
                ),
                recent_revenue=Coalesce(
                    Sum(
                        'booking__total',
                        filter=Q(booking__booked_at__gte=month_start_dt, booking__status='confirmed'),
                    ),
                    Decimal('0.00'),
                ),
            )
            .order_by('-recent_bookings')[:5]
        )

        # --- Theatre performance by venue name (occupancy + shows) ---
        # Two-step aggregation: pick the top-5 venues by show count first
        # (cheap, theatre-side only), then aggregate seat stats just for those
        # venues. The previous single query joined EVERY seat row of every
        # theatre before grouping, which scanned the whole (multi-hundred-
        # thousand-row) seat table on each dashboard load.
        top_names = [
            row['name']
            for row in (
                Theater.objects.values('name').annotate(
                    shows=Count('id'),
                ).order_by('-shows')[:5]
            )
        ]
        perf_shows = {
            row['name']: row['shows']
            for row in (
                Theater.objects.filter(name__in=top_names)
                .values('name')
                .annotate(shows=Count('id', distinct=True))
            )
        } if top_names else {}
        perf_seats = {
            row['theater__name']: (row['total'], row['booked'])
            for row in (
                Seat.objects.filter(theater__name__in=top_names)
                .values('theater__name')
                .annotate(
                    total=Count('id'),
                    booked=Count('id', filter=Q(is_booked=True)),
                )
            )
        } if top_names else {}
        theatre_perf = []
        for name in sorted(perf_shows, key=lambda n: -perf_shows[n]):
            total, booked = perf_seats.get(name, (0, 0))
            theatre_perf.append({
                'name': name,
                'shows': perf_shows[name],
                'total_seats': total,
                'booked_seats': booked,
                'available_seats': total - booked,
                'occupancy': round(booked / total * 100) if total else 0,
            })
        context['theatre_perf'] = theatre_perf

        # --- Platform inventory summary ---
        # Status-split counts collapse into one aggregate per table instead
        # of one query per row of the summary grid.
        movie_stats = Movie.objects.aggregate(
            total=Count('id', filter=Q(is_deleted=False)),
            active=Count('id', filter=Q(status='now_showing', is_deleted=False)),
            upcoming=Count('id', filter=Q(status='coming_soon', is_deleted=False)),
        )
        seat_stats = Seat.objects.aggregate(
            available=Count('id', filter=Q(is_booked=False)),
            booked=Count('id', filter=Q(is_booked=True)),
        )
        context['total_movies'] = movie_stats['total']
        context['total_bookings'] = Booking.objects.count()
        context['total_users'] = User.objects.count()
        context['total_staff'] = AdminProfile.objects.count()
        context['total_theatres'] = Theater.objects.values('name').distinct().count()
        context['total_screens'] = Screen.objects.count()
        context['total_shows'] = total_shows
        context['active_movies'] = movie_stats['active']
        context['upcoming_movies'] = movie_stats['upcoming']
        context['pending_refunds'] = pending_refunds
        context['total_payments'] = Payment.objects.count()
        context['total_transactions'] = PaymentTransaction.objects.count()
        context['total_revenue'] = Payment.objects.filter(
            status='completed',
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        context['total_available_seats'] = seat_stats['available']
        context['total_booked_seats'] = seat_stats['booked']
        context['total_active_reservations'] = active_reservations
        context['held_seats'] = held_seats
        context['cancelled_today'] = cancelled_today
        context['today_bookings'] = today_bookings
        context['today_revenue'] = today_revenue
        context['week_bookings'] = week_bookings
        context['week_revenue'] = week_revenue
        context['month_bookings'] = month_bookings
        context['month_revenue'] = month_revenue
        context['todays_shows'] = active_shows_today
        context['upcoming_shows'] = upcoming_shows
        context['occupancy_rate'] = occupancy_today
        context['today_booked_seats'] = today_booked_seats
        context['today_total_seats'] = today_total_seats
        context['recent_movies'] = Movie.objects.filter(is_deleted=False).order_by('-id')[:5]
        context['upcoming_movies_list'] = Movie.objects.filter(status='coming_soon', is_deleted=False).order_by('release_date')[:5]

        return context


class MovieListView(AdminSessionMixin, ListView):
    model = Movie
    template_name = 'admin/movies/movie_list.html'
    context_object_name = 'movies'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_queryset(self):
        qs = Movie.objects.all()
        search = self.request.GET.get('search')
        status = self.request.GET.get('status')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(director__icontains=search))
        if status:
            qs = qs.filter(status=status)
        sort = self.request.GET.get('sort', 'id')
        order = self.request.GET.get('order', 'desc')
        valid_sort = ['id', 'name', 'rating', 'duration', 'status']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('-id')
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = Movie.STATUS_CHOICES
        return context


class MovieCreateView(AdminSessionMixin, CreateView):
    model = Movie
    form_class = MovieForm
    template_name = 'admin/movies/movie_form.html'
    success_url = reverse_lazy('admin_movie_list')

    def form_valid(self, form):
        try:
            response = super().form_valid(form)
        except Exception as exc:
            logger.exception('Movie creation failed')
            messages.error(
                self.request,
                f'Failed to create movie: {exc}. Please try again.',
            )
            return self.form_invalid(form)
        messages.success(self.request, 'Movie added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Movie Added',
            module='Movie',
            object_id=self.object.id,
            details=f'Added movie: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class MovieUpdateView(AdminSessionMixin, UpdateView):
    model = Movie
    form_class = MovieForm
    template_name = 'admin/movies/movie_form.html'
    success_url = reverse_lazy('admin_movie_list')

    def form_valid(self, form):
        try:
            response = super().form_valid(form)
        except Exception as exc:
            logger.exception('Movie update failed for pk=%s', form.instance.pk)
            messages.error(
                self.request,
                f'Failed to save movie: {exc}. Please try again.',
            )
            return self.form_invalid(form)
        messages.success(self.request, 'Movie updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Movie Updated',
            module='Movie',
            object_id=self.object.id,
            details=f'Updated movie: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class AdminDeleteViewMixin:
    def post(self, request, *args, **kwargs):
        return self.delete(request, *args, **kwargs)


@method_decorator(permission_required('movie', 'can_delete'), name='dispatch')
class MovieDeleteView(AdminSessionMixin, DeleteView):
    model = Movie
    template_name = 'admin/movies/movie_confirm_delete.html'
    success_url = reverse_lazy('admin_movie_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        movie = self.get_object()
        now = timezone.now()
        today = now.date()
        context['active_bookings'] = Booking.objects.filter(movie=movie).count()
        context['running_bookings'] = Booking.objects.filter(
            movie=movie, theater__time__gte=now
        ).count()
        context['active_reservations'] = Reservation.objects.filter(
            show__movie=movie, status__in=['active', 'booked']
        ).count()
        context['future_shows'] = Show.objects.filter(movie=movie, date__gte=today, status='active').count()
        context['past_shows'] = Show.objects.filter(movie=movie, date__lt=today).count()
        context['future_theaters'] = Theater.objects.filter(movie=movie, time__gte=now).count()
        context['theater_shows'] = Theater.objects.filter(movie=movie).count()
        context['related_trailers'] = Trailer.objects.filter(movie=movie).count()
        context['related_gallery'] = MovieImage.objects.filter(movie=movie).count()
        context['related_cast'] = CastMember.objects.filter(movie=movie).count()
        context['running_with_bookings'] = bool(context['running_bookings'])
        context['can_hard_delete'] = not context['running_with_bookings']
        return context

    def post(self, request, *args, **kwargs):
        return self.delete(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        movie = self.get_object()
        action = request.POST.get('action', 'archive')
        now = timezone.now()
        running_bookings = Booking.objects.filter(
            movie=movie, theater__time__gte=now
        ).exists()
        next_url = request.POST.get('next') or self.success_url
        if not next_url.startswith('/'):
            next_url = self.success_url

        if action == 'hard_delete':
            if running_bookings:
                messages.error(
                    request,
                    f'"{movie.name}" still has booked tickets on shows that are running or upcoming. '
                    'It will become available for permanent deletion after all its shows are over.'
                )
                return redirect(next_url)
            name = movie.name
            movie.delete()
            AuditLog.objects.create(
                user=request.user,
                action='Movie Permanently Deleted',
                module='Movie',
                object_id=movie.id,
                details=f'Permanently deleted movie: {name}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(
                request,
                f'Movie "{name}" and all its shows, seats, trailers and images have been permanently deleted.'
            )
            return redirect(next_url)
        else:
            movie.is_deleted = True
            movie.show_on_homepage = False
            movie.status = 'archived'
            # Cancel all future shows for this movie
            Show.objects.filter(movie=movie, date__gte=now.date(), status='active').update(status='cancelled')
            movie.save()
            AuditLog.objects.create(
                user=request.user,
                action='Movie Deleted',
                module='Movie',
                object_id=movie.id,
                details=f'Soft-deleted movie: {movie.name}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'Movie "{movie.name}" has been archived and removed from all public listings.')
            return redirect(next_url)


@admin_session_required
@permission_required('movie', 'can_view')
def movie_removal_list(request):
    now = timezone.now()
    week_ago = now - timedelta(days=7)

    movies_qs = (
        Movie.objects.annotate(
            bookings=Count('booking'),
            revenue=Coalesce(Sum('booking__total'), Decimal('0.00')),
            last7=Count('booking', filter=Q(booking__booked_at__gte=week_ago)),
            theater_count=Count('theaters', distinct=True),
            shows_count=Count('shows', distinct=True),
            seat_capacity=Count('theaters__seats', distinct=True),
            future_theaters=Count(
                'theaters', filter=Q(theaters__time__gte=now), distinct=True
            ),
            future_shows=Count(
                'shows',
                filter=Q(shows__date__gte=now.date(), shows__status='active'),
                distinct=True,
            ),
            has_running_bookings=Exists(
                Booking.objects.filter(movie=OuterRef('pk'), theater__time__gte=now)
            ),
        )
        .order_by('-id')
    )

    per_page = 20
    paginator = Paginator(movies_qs, per_page)
    page_num = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_num)
    except Exception:
        page_obj = paginator.page(1)

    rows = []
    for m in page_obj:
        occupancy = round(m.bookings / m.seat_capacity * 100, 1) if m.seat_capacity else 0
        runs_days = (now.date() - m.release_date).days if m.release_date else None
        running_with_bookings = m.has_running_bookings
        rows.append({
            'movie': m,
            'bookings': m.bookings,
            'revenue': m.revenue,
            'last7': m.last7,
            'theaters': m.theater_count,
            'shows': m.shows_count,
            'capacity': m.seat_capacity,
            'occupancy': occupancy,
            'runs_days': runs_days,
            'future_theaters': m.future_theaters,
            'future_shows': m.future_shows,
            'running_with_bookings': running_with_bookings,
            'can_delete': not running_with_bookings,
        })

    totals = Movie.objects.aggregate(
        total_revenue=Coalesce(Sum('booking__total', filter=Q(
            booking__status='confirmed',
        )), Decimal('0.00')),
        total_bookings=Count('booking'),
    )
    chart_json = {
        'labels': [r['movie'].name for r in rows],
        'bookings': [r['bookings'] for r in rows],
        'last7': [r['last7'] for r in rows],
    }
    return render(request, 'admin/movies/movie_removal.html', {
        'rows': rows,
        'page_obj': page_obj,
        'total_movies': paginator.count,
        'total_bookings': totals['total_bookings'],
        'total_revenue': totals['total_revenue'],
        'chart_json': chart_json,
    })


class MovieDetailView(AdminSessionMixin, TemplateView):
    template_name = 'admin/movies/movie_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        movie = get_object_or_404(Movie, id=self.kwargs['pk'])
        context['movie'] = movie
        context['cast_members'] = CastMember.objects.filter(movie=movie)
        context['trailers'] = Trailer.objects.filter(movie=movie)
        context['gallery'] = MovieImage.objects.filter(movie=movie)
        context['shows'] = Show.objects.filter(movie=movie).select_related('theatre', 'screen')
        context['bookings'] = Booking.objects.filter(movie=movie).select_related('user', 'theater').order_by('-booked_at')[:20]
        return context


@require_POST
@admin_session_required
@permission_required('movie', 'can_edit')
def movie_toggle_status(request, pk):
    movie = get_object_or_404(Movie, id=pk)
    valid_statuses = [s[0] for s in Movie.STATUS_CHOICES]
    default_status = 'draft'
    status_cycle = {'draft': 'coming_soon', 'coming_soon': 'now_showing', 'now_showing': 'archived', 'archived': 'hidden', 'hidden': 'draft'}
    old_status = movie.status
    if old_status not in status_cycle:
        movie.status = default_status
    else:
        movie.status = status_cycle[old_status]
    movie.save()
    AuditLog.objects.create(
        user=request.user,
        action='Movie Status Toggled',
        module='Movie',
        object_id=movie.id,
        details=f'Changed {movie.name} status from {old_status} to {movie.status}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, f'Movie "{movie.name}" status changed to {movie.get_status_display()}.')
    return redirect('admin_movie_list')


@require_POST
@admin_session_required
@permission_required('movie', 'can_edit')
def movie_toggle_homepage(request, pk):
    movie = get_object_or_404(Movie, id=pk)
    movie.show_on_homepage = not movie.show_on_homepage
    movie.save()
    status = 'shown' if movie.show_on_homepage else 'hidden'
    AuditLog.objects.create(
        user=request.user,
        action='Movie Homepage Toggled',
        module='Movie',
        object_id=movie.id,
        details=f'{movie.name} homepage visibility: {status}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, f'Movie "{movie.name}" homepage visibility updated.')
    return redirect('admin_movie_list')


@require_POST
@admin_session_required
@permission_required('movie', 'can_edit')
def movie_restore(request, pk):
    movie = get_object_or_404(Movie, id=pk)
    movie.is_deleted = False
    movie.show_on_homepage = True
    if movie.status in ['archived', 'hidden']:
        movie.status = 'draft'
    movie.save()
    AuditLog.objects.create(
        user=request.user,
        action='Movie Restored',
        module='Movie',
        object_id=movie.id,
        details=f'Restored movie: {movie.name}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, f'Movie "{movie.name}" restored successfully and is visible again in public listings.')
    return redirect('admin_movie_list')


def search_suggestions(request):
    q = request.GET.get('q', '').strip()
    if len(q) < 1:
        from movies.discovery import trending_movies
        movies = trending_movies(6)
        results = []
        for m in movies:
            results.append({
                'id': m.id,
                'name': m.name,
                'image': m.image.url if m.image and hasattr(m.image, 'url') else '',
                'url': f'/movies/{m.id}/',
                'type': 'trending',
            })
        return JsonResponse(results, safe=False)
    movies = Movie.objects.filter(
        name__icontains=q,
        is_deleted=False
    ).exclude(
        status__in=['archived', 'hidden']
    )[:8]
    results = []
    for m in movies:
        results.append({
            'id': m.id,
            'name': m.name,
            'image': m.image.url if m.image and hasattr(m.image, 'url') else '',
            'url': f'/movies/{m.id}/',
            'type': 'result',
        })
    return JsonResponse(results, safe=False)


@admin_session_required
def admin_global_search(request):
    """Unified global search across Movies, Users, Bookings, Theatres, Shows."""
    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'results': {}})
    limit = 5
    results = {'movies': [], 'users': [], 'bookings': [], 'theatres': [], 'shows': []}

    for m in Movie.objects.filter(is_deleted=False).filter(
        Q(name__icontains=q) | Q(director__icontains=q) | Q(category__icontains=q)
    )[:limit]:
        results['movies'].append({
            'id': m.id,
            'title': m.name,
            'subtitle': m.get_category_display() + (f' · {m.director}' if m.director else ''),
            'url': reverse('admin_movie_detail', args=[m.id]),
        })

    for u in User.objects.filter(
        Q(username__icontains=q) | Q(email__icontains=q)
        | Q(first_name__icontains=q) | Q(last_name__icontains=q)
    )[:limit]:
        results['users'].append({
            'id': u.id,
            'title': u.username,
            'subtitle': u.email or u.get_full_name() or 'User',
            'url': reverse('admin_user_bookings', args=[u.id]),
        })

    for b in Booking.objects.select_related('movie', 'user').filter(
        Q(booking_ref__icontains=q) | Q(id__icontains=q)
    )[:limit]:
        results['bookings'].append({
            'id': b.id,
            'title': b.booking_ref or f'#{b.id}',
            'subtitle': (b.movie.name + ' · ' + b.user.username) if b.user_id else b.movie.name,
            'url': reverse('admin_booking_detail', args=[b.id]),
        })

    theatre_names = (
        Theater.objects.filter(name__icontains=q)
        .values_list('name', flat=True)
        .distinct()[:limit]
    )
    for name in theatre_names:
        results['theatres'].append({
            'id': name,
            'title': name,
            'subtitle': 'Theatre',
            'url': reverse('admin_show_list') + '?theatre=' + quote_plus(name),
        })

    for s in Theater.objects.select_related('movie').filter(
        Q(name__icontains=q) | Q(movie__name__icontains=q)
    )[:limit]:
        results['shows'].append({
            'id': s.id,
            'title': f'{s.name} · {s.movie.name}',
            'subtitle': s.time.strftime('%d %b %Y, %I:%M %p'),
            'url': reverse('admin_pricing_show_edit', args=[s.id]),
        })

    return JsonResponse({'results': results})


@admin_session_required
def genre_ajax_add(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'error': 'Name is required'}, status=400)
        genre, created = Genre.objects.get_or_create(name=name)
        return JsonResponse({
            'id': genre.id,
            'name': genre.name,
            'slug': genre.slug,
            'created': created,
        })
    return JsonResponse({'error': 'POST required'}, status=405)


@admin_session_required
def language_ajax_add(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'error': 'Name is required'}, status=400)
        lang, created = Language.objects.get_or_create(name=name)
        return JsonResponse({
            'id': lang.id,
            'name': lang.name,
            'code': lang.code,
            'created': created,
        })
    return JsonResponse({'error': 'POST required'}, status=405)


class GenreListView(AdminSessionMixin, ListView):
    model = Genre
    template_name = 'admin/genres/genre_list.html'
    context_object_name = 'genres'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_queryset(self):
        qs = Genre.objects.annotate(movie_count=Count('movies'))
        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(name__icontains=search)
        sort = self.request.GET.get('sort', 'name')
        order = self.request.GET.get('order', 'asc')
        valid_sort = ['name', 'movie_count']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('name')
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_genres'] = Genre.objects.count()
        return context


class GenreCreateView(AdminSessionMixin, CreateView):
    model = Genre
    form_class = GenreForm
    template_name = 'admin/genres/genre_form.html'
    success_url = reverse_lazy('admin_genre_list')

    def form_valid(self, form):
        form.instance.slug = slugify(form.instance.name)
        try:
            with transaction.atomic():
                response = super().form_valid(form)
        except IntegrityError:
            messages.error(self.request, 'A genre with a similar name already exists.')
            return redirect('admin_genre_list')
        messages.success(self.request, 'Genre added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Genre Added',
            module='Genre',
            object_id=self.object.id,
            details=f'Added genre: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class GenreUpdateView(AdminSessionMixin, UpdateView):
    model = Genre
    form_class = GenreForm
    template_name = 'admin/genres/genre_form.html'
    success_url = reverse_lazy('admin_genre_list')

    def form_valid(self, form):
        form.instance.slug = slugify(form.instance.name)
        try:
            with transaction.atomic():
                response = super().form_valid(form)
        except IntegrityError:
            messages.error(self.request, 'A genre with a similar name already exists.')
            return redirect('admin_genre_list')
        messages.success(self.request, 'Genre updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Genre Updated',
            module='Genre',
            object_id=self.object.id,
            details=f'Updated genre: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class GenreDeleteView(AdminDeleteViewMixin, AdminSessionMixin, DeleteView):
    model = Genre
    template_name = 'admin/genres/genre_confirm_delete.html'
    success_url = reverse_lazy('admin_genre_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        genre = self.get_object()
        context['movie_count'] = genre.movies.count()
        return context

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        movie_count = obj.movies.count()
        AuditLog.objects.create(
            user=request.user,
            action='Genre Deleted',
            module='Genre',
            object_id=obj.id,
            details=f'Deleted genre: {obj.name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        if movie_count > 0:
            messages.warning(request, f'Genre "{obj.name}" is used by {movie_count} movie(s).')
        messages.success(request, 'Genre deleted successfully.')
        return super().delete(request, *args, **kwargs)


class LanguageListView(AdminSessionMixin, ListView):
    model = Language
    template_name = 'admin/languages/language_list.html'
    context_object_name = 'languages'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_queryset(self):
        qs = Language.objects.annotate(movie_count=Count('movies'))
        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(name__icontains=search)
        sort = self.request.GET.get('sort', 'name')
        order = self.request.GET.get('order', 'asc')
        valid_sort = ['name', 'movie_count']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('name')
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_languages'] = Language.objects.count()
        return context


class LanguageCreateView(AdminSessionMixin, CreateView):
    model = Language
    form_class = LanguageForm
    template_name = 'admin/languages/language_form.html'
    success_url = reverse_lazy('admin_language_list')

    def form_valid(self, form):
        form.instance.code = slugify(form.instance.name).replace('-', '_').upper()[:10]
        try:
            with transaction.atomic():
                response = super().form_valid(form)
        except IntegrityError:
            messages.error(self.request, 'A language with a similar name already exists.')
            return redirect('admin_language_list')
        messages.success(self.request, 'Language added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Language Added',
            module='Language',
            object_id=self.object.id,
            details=f'Added language: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class LanguageUpdateView(AdminSessionMixin, UpdateView):
    model = Language
    form_class = LanguageForm
    template_name = 'admin/languages/language_form.html'
    success_url = reverse_lazy('admin_language_list')

    def form_valid(self, form):
        form.instance.code = slugify(form.instance.name).replace('-', '_').upper()[:10]
        try:
            with transaction.atomic():
                response = super().form_valid(form)
        except IntegrityError:
            messages.error(self.request, 'A language with a similar name already exists.')
            return redirect('admin_language_list')
        messages.success(self.request, 'Language updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Language Updated',
            module='Language',
            object_id=self.object.id,
            details=f'Updated language: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class LanguageDeleteView(AdminDeleteViewMixin, AdminSessionMixin, DeleteView):
    model = Language
    template_name = 'admin/languages/language_confirm_delete.html'
    success_url = reverse_lazy('admin_language_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lang = self.get_object()
        context['movie_count'] = lang.movies.count()
        return context

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        movie_count = obj.movies.count()
        AuditLog.objects.create(
            user=request.user,
            action='Language Deleted',
            module='Language',
            object_id=obj.id,
            details=f'Deleted language: {obj.name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        if movie_count > 0:
            messages.warning(request, f'Language "{obj.name}" is used by {movie_count} movie(s).')
        messages.success(request, 'Language deleted successfully.')
        return super().delete(request, *args, **kwargs)




class CastListView(AdminSessionMixin, ListView):
    model = CastMember
    template_name = 'admin/cast/cast_list.html'
    context_object_name = 'cast_members'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_queryset(self):
        qs = CastMember.objects.select_related('movie').all()
        search = self.request.GET.get('search')
        movie_id = self.request.GET.get('movie')
        if search:
            qs = qs.filter(name__icontains=search)
        if movie_id:
            qs = qs.filter(movie_id=movie_id)
        sort = self.request.GET.get('sort', 'id')
        order = self.request.GET.get('order', 'desc')
        valid_sort = ['id', 'name', 'movie__name', 'character_name', 'role']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('-id')
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['movies'] = Movie.objects.all()
        return context


class CastCreateView(AdminSessionMixin, CreateView):
    model = CastMember
    form_class = CastMemberForm
    template_name = 'admin/cast/cast_form.html'
    success_url = reverse_lazy('admin_cast_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Cast member added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Cast Added',
            module='Cast',
            object_id=self.object.id,
            details=f'Added cast: {self.object.name} for {self.object.movie.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class CastUpdateView(AdminSessionMixin, UpdateView):
    model = CastMember
    form_class = CastMemberForm
    template_name = 'admin/cast/cast_form.html'
    success_url = reverse_lazy('admin_cast_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Cast member updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Cast Updated',
            module='Cast',
            object_id=self.object.id,
            details=f'Updated cast: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class CastDeleteView(AdminDeleteViewMixin, AdminSessionMixin, DeleteView):
    model = CastMember
    template_name = 'admin/cast/cast_confirm_delete.html'
    success_url = reverse_lazy('admin_cast_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Cast Deleted',
            module='Cast',
            object_id=obj.id,
            details=f'Deleted cast: {obj.name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Cast member deleted successfully.')
        return super().delete(request, *args, **kwargs)


class TheatreListView(AdminSessionMixin, ListView):
    model = Theater
    template_name = 'admin/theatres/theatre_list.html'
    context_object_name = 'theatres'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_queryset(self):
        qs = Theater.objects.values('name').annotate(
            show_count=Count('id'),
            movie_count=Count('movie', distinct=True),
            last_show=Max('time')
        )
        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(name__icontains=search)
        sort = self.request.GET.get('sort', 'last_show')
        order = self.request.GET.get('order', 'desc')
        valid_sort = ['name', 'show_count', 'movie_count', 'last_show']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('-last_show')
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_theatres'] = Theater.objects.values('name').distinct().count()
        context['theatre_profiles'] = Theatre.objects.all().order_by('name')
        return context


class TheatreCreateView(AdminSessionMixin, CreateView):
    model = Theatre
    form_class = TheatreForm
    template_name = 'admin/theatres/theatre_form.html'
    success_url = reverse_lazy('admin_theatre_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Theatre added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Theatre Added',
            module='Theatre',
            object_id=self.object.id,
            details=f'Added theatre: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class TheatreUpdateView(AdminSessionMixin, UpdateView):
    model = Theatre
    form_class = TheatreForm
    template_name = 'admin/theatres/theatre_form.html'
    success_url = reverse_lazy('admin_theatre_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Theatre updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Theatre Updated',
            module='Theatre',
            object_id=self.object.id,
            details=f'Updated theatre: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class TheatreDeleteView(AdminDeleteViewMixin, AdminSessionMixin, DeleteView):
    model = Theatre
    template_name = 'admin/theatres/theatre_confirm_delete.html'
    success_url = reverse_lazy('admin_theatre_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Theatre Deleted',
            module='Theatre',
            object_id=obj.id,
            details=f'Deleted theatre: {obj.name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Theatre deleted successfully.')
        return super().delete(request, *args, **kwargs)


@admin_session_required
def theatre_movie_management(request, pk):
    theatre = get_object_or_404(Theatre, id=pk)
    assigned_movies = Movie.objects.filter(theaters__name=theatre.name, is_deleted=False).distinct()
    running_shows = Show.objects.filter(theatre=theatre, status='active', date__gte=timezone.now().date()).select_related('movie', 'screen').order_by('date', 'time')
    all_movies = Movie.objects.filter(is_deleted=False).exclude(status__in=['archived', 'hidden'])
    return render(request, 'admin/theatres/theatre_movies.html', {
        'theatre': theatre,
        'assigned_movies': assigned_movies,
        'running_shows': running_shows,
        'all_movies': all_movies,
    })


@admin_session_required
def theatre_remove_movie(request):
    if request.method == 'POST':
        theatre_id = request.POST.get('theatre_id')
        movie_id = request.POST.get('movie_id')
        theatre = get_object_or_404(Theatre, id=theatre_id)
        movie = get_object_or_404(Movie, id=movie_id)
        future_shows = Show.objects.filter(
            theatre=theatre, movie=movie,
            date__gte=timezone.now().date(),
            status='active'
        )
        count = future_shows.count()
        future_shows.update(status='cancelled')
        old_theaters = Theater.objects.filter(name=theatre.name, movie=movie, time__gte=timezone.now())
        old_count = old_theaters.count()
        old_theaters.delete()
        AuditLog.objects.create(
            user=request.user,
            action='Movie Removed from Theatre',
            module='Theatre',
            details=f'Removed {movie.name} from {theatre.name}: cancelled {count} show(s), removed {old_count} old listing(s)',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, f'"{movie.name}" removed from "{theatre.name}". {count} future show(s) cancelled.')
    return redirect('admin_theatre_list')


class ScreenListView(AdminSessionMixin, ListView):
    model = Theater
    template_name = 'admin/screens/screen_list.html'
    context_object_name = 'screens'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_queryset(self):
        qs = Theater.objects.select_related('movie').annotate(
            total_seats=Count('seats'),
            available_seats=Count('seats', filter=Q(seats__is_booked=False)),
            booked_seats=Count('seats', filter=Q(seats__is_booked=True))
        )
        theatre_name = self.request.GET.get('theatre')
        if theatre_name:
            qs = qs.filter(name__icontains=theatre_name)
        sort = self.request.GET.get('sort', 'time')
        order = self.request.GET.get('order', 'desc')
        valid_sort = ['id', 'name', 'movie__name', 'time', 'total_seats', 'available_seats', 'booked_seats']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('-time')
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['theatre_names'] = Theater.objects.values_list('name', flat=True).distinct().order_by('name')
        self._annotate_bulk_seat_counts(context.get('page_obj') or context.get('screens') or [])
        return context

    @staticmethod
    def _annotate_bulk_seat_counts(page_theaters):
        if not page_theaters:
            return
        ids = [t.id for t in page_theaters]
        counts = (
            Theater.objects.filter(pk__in=ids)
            .annotate(_total=Count('seats'), _avail=Count('seats', filter=Q(seats__is_booked=False)))
            .values_list('pk', '_total', '_avail')
        )
        map_ = {pk: (total, avail) for pk, total, avail in counts}
        for t in page_theaters:
            total, avail = map_.get(t.id, (0, 0))
            t.total_seats = total
            t.available_seats = avail
            t.booked_seats = total - avail


class ScreenCreateView(AdminSessionMixin, CreateView):
    model = Screen
    form_class = ScreenForm
    template_name = 'admin/screens/screen_form.html'
    success_url = reverse_lazy('admin_screen_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from admin_panel.layouts import build_layout_spec, preview_rows
        size = self.request.GET.get('size', 'small')
        spec = build_layout_spec(size)
        context['preview_rows'] = preview_rows(spec)
        context['preview_capacity'] = len(spec['seats'])
        context['preview_size'] = size
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Screen added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Screen Added',
            module='Screen',
            object_id=self.object.id,
            details=f'Added screen: {self.object.name} at {self.object.theatre.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class ScreenUpdateView(AdminSessionMixin, UpdateView):
    model = Screen
    form_class = ScreenForm
    template_name = 'admin/screens/screen_form.html'
    success_url = reverse_lazy('admin_screen_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from admin_panel.layouts import build_layout_spec, preview_rows
        size = self.object.size if self.object.size in ('small', 'medium', 'large', 'imax', 'premium') else 'small'
        spec = build_layout_spec(size)
        context['preview_rows'] = preview_rows(spec)
        context['preview_capacity'] = len(spec['seats'])
        context['preview_size'] = size
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Screen updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Screen Updated',
            module='Screen',
            object_id=self.object.id,
            details=f'Updated screen: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class ScreenDeleteView(AdminDeleteViewMixin, AdminSessionMixin, DeleteView):
    model = Screen
    template_name = 'admin/screens/screen_confirm_delete.html'
    success_url = reverse_lazy('admin_screen_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Screen Deleted',
            module='Screen',
            object_id=obj.id,
            details=f'Deleted screen: {obj.name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Screen deleted successfully.')
        return super().delete(request, *args, **kwargs)


@admin_session_required
def screen_layout_preview(request):
    """JSON seat-layout preview for a screen size (admin layout picker)."""
    from admin_panel.layouts import build_layout_spec, preview_rows
    size = request.GET.get('size', 'small')
    if size not in ('small', 'medium', 'large', 'imax', 'premium'):
        size = 'small'
    spec = build_layout_spec(size)
    return JsonResponse({
        'size': size,
        'rows': spec['rows'],
        'cols_per_section': spec['cols_per_section'],
        'total_cols': spec['total_cols'],
        'capacity': len(spec['seats']),
        'sections': spec['sections'],
        'preview_rows': preview_rows(spec),
    })


class ShowListView(AdminSessionMixin, ListView):
    model = Theater
    template_name = 'admin/shows/show_list.html'
    context_object_name = 'shows'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_queryset(self):
        today = timezone.now().date()
        qs = Theater.objects.select_related('movie').annotate(
            total_seats=Count('seats'),
            available_seats=Count('seats', filter=Q(seats__is_booked=False)),
            booked_seats=Count('seats', filter=Q(seats__is_booked=True))
        )
        movie_id = self.request.GET.get('movie')
        theatre_name = self.request.GET.get('theatre')
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        if movie_id:
            qs = qs.filter(movie_id=movie_id)
        if theatre_name:
            qs = qs.filter(name__icontains=theatre_name)
        if date_from:
            qs = qs.filter(time__date__gte=date_from)
        if date_to:
            qs = qs.filter(time__date__lte=date_to)
        sort = self.request.GET.get('sort', 'time')
        order = self.request.GET.get('order', 'desc')
        valid_sort = ['id', 'movie__name', 'name', 'time', 'total_seats', 'available_seats', 'booked_seats']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('-time')
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['movies'] = Movie.objects.filter(is_deleted=False)
        context['theatre_names'] = Theater.objects.values_list('name', flat=True).distinct().order_by('name')
        shows = context.get('page_obj') or context.get(self.context_object_name) or []
        for s in shows:
            s.status_info = show_status_info(s)
        self._annotate_bulk_seat_counts(shows)
        return context

    @staticmethod
    def _annotate_bulk_seat_counts(page_theaters):
        if not page_theaters:
            return
        ids = [t.id for t in page_theaters]
        counts = (
            Theater.objects.filter(pk__in=ids)
            .annotate(_total=Count('seats'), _avail=Count('seats', filter=Q(seats__is_booked=False)))
            .values_list('pk', '_total', '_avail')
        )
        map_ = {pk: (total, avail) for pk, total, avail in counts}
        for t in page_theaters:
            total, avail = map_.get(t.id, (0, 0))
            t.total_seats = total
            t.available_seats = avail
            t.booked_seats = total - avail


class ShowCreateView(AdminSessionMixin, CreateView):
    model = Show
    form_class = ShowForm
    template_name = 'admin/shows/show_form.html'
    success_url = reverse_lazy('admin_show_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        sync_theater_from_show(self.object)
        messages.success(self.request, 'Show added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Show Added',
            module='Show',
            object_id=self.object.id,
            details=f'Added show: {self.object.movie.name} at {self.object.theatre.name} on {self.object.date}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class ShowUpdateView(AdminSessionMixin, UpdateView):
    model = Show
    form_class = ShowForm
    template_name = 'admin/shows/show_form.html'
    success_url = reverse_lazy('admin_show_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        sync_theater_from_show(self.object)
        messages.success(self.request, 'Show updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Show Updated',
            module='Show',
            object_id=self.object.id,
            details=f'Updated show: {self.object.movie.name} at {self.object.theatre.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class ShowDeleteView(AdminDeleteViewMixin, AdminSessionMixin, DeleteView):
    model = Show
    template_name = 'admin/shows/show_confirm_delete.html'
    success_url = reverse_lazy('admin_show_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        theater = None
        if obj.theater_id is not None:
            try:
                theater = Theater.objects.get(pk=obj.theater_id)
            except Theater.DoesNotExist:
                theater = None
            if theater is not None:
                has_bookings = (
                    Booking.objects.filter(theater=theater).exists()
                    or Reservation.objects.filter(show=theater).exists()
                )
                if has_bookings:
                    Theater.objects.filter(pk=theater.pk).update(status='cancelled')
                    messages.warning(
                        request,
                        'Show has existing bookings, so the linked show was kept and marked cancelled '
                        'to preserve booking history.'
                    )
                else:
                    theater.delete()
        AuditLog.objects.create(
            user=request.user,
            action='Show Deleted',
            module='Show',
            object_id=obj.id,
            details=f'Deleted show: {obj.movie.name} at {obj.theatre.name} on {obj.date}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Show deleted successfully.')
        return super().delete(request, *args, **kwargs)


@require_POST
@admin_session_required
@permission_required('show', 'can_edit')
def show_toggle_status(request, pk):
    show = get_object_or_404(Show, id=pk)
    status_cycle = {'active': 'sold_out', 'sold_out': 'paused', 'paused': 'cancelled', 'cancelled': 'active'}
    old_status = show.status
    show.status = status_cycle.get(show.status, 'active')
    show.save()
    hide_theater_for_show(show, status=show.status)
    AuditLog.objects.create(
        user=request.user,
        action='Show Status Toggled',
        module='Show',
        object_id=show.id,
        details=f'Changed show status from {old_status} to {show.status}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, f'Show status changed to {show.status}.')
    return redirect('admin_show_list')


@admin_session_required
@permission_required('show', 'can_edit')
def show_bulk_action(request):
    if request.method == 'POST':
        movie_id = request.POST.get('movie')
        theatre_id = request.POST.get('theatre')
        date_val = request.POST.get('date')
        shows = Show.objects.all()
        if movie_id:
            shows = shows.filter(movie_id=movie_id)
        if theatre_id:
            shows = shows.filter(theatre_id=theatre_id)
        if date_val:
            shows = shows.filter(date=date_val)
        theater_ids = list(shows.exclude(theater=None).values_list('theater_id', flat=True))
        count = shows.update(status='cancelled')
        if theater_ids:
            Theater.objects.filter(id__in=theater_ids).update(status='cancelled')
        AuditLog.objects.create(
            user=request.user,
            action='Bulk Cancel Shows',
            module='Show',
            details=f'Cancelled {count} shows. Movie:{movie_id}, Theatre:{theatre_id}, Date:{date_val}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, f'{count} show(s) cancelled successfully.')
    return redirect('admin_show_list')


class TrailerListView(AdminSessionMixin, ListView):
    model = Trailer
    template_name = 'admin/trailers/trailer_list.html'
    context_object_name = 'trailers'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_queryset(self):
        qs = Trailer.objects.select_related('movie').all()
        movie_id = self.request.GET.get('movie')
        if movie_id:
            qs = qs.filter(movie_id=movie_id)
        search = self.request.GET.get('search', '')
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(movie__name__icontains=search))
        sort = self.request.GET.get('sort', 'id')
        order = self.request.GET.get('order', 'desc')
        valid_sort = ['id', 'movie__name', 'title', 'is_featured']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('-id')
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['movies'] = Movie.objects.all()
        return context


class TrailerCreateView(AdminSessionMixin, CreateView):
    model = Trailer
    form_class = TrailerForm
    template_name = 'admin/trailers/trailer_form.html'
    success_url = reverse_lazy('admin_trailer_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Trailer added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Trailer Added',
            module='Trailer',
            object_id=self.object.id,
            details=f'Added trailer for {self.object.movie.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class TrailerUpdateView(AdminSessionMixin, UpdateView):
    model = Trailer
    form_class = TrailerForm
    template_name = 'admin/trailers/trailer_form.html'
    success_url = reverse_lazy('admin_trailer_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Trailer updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Trailer Updated',
            module='Trailer',
            object_id=self.object.id,
            details=f'Updated trailer for {self.object.movie.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class TrailerDeleteView(AdminDeleteViewMixin, AdminSessionMixin, DeleteView):
    model = Trailer
    template_name = 'admin/trailers/trailer_confirm_delete.html'
    success_url = reverse_lazy('admin_trailer_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Trailer Deleted',
            module='Trailer',
            object_id=obj.id,
            details=f'Deleted trailer for {obj.movie.name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Trailer deleted successfully.')
        return super().delete(request, *args, **kwargs)


class MovieImageListView(AdminSessionMixin, ListView):
    model = MovieImage
    template_name = 'admin/images/image_list.html'
    context_object_name = 'images'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_queryset(self):
        qs = MovieImage.objects.select_related('movie').all()
        movie_id = self.request.GET.get('movie')
        if movie_id:
            qs = qs.filter(movie_id=movie_id)
        search = self.request.GET.get('search', '')
        if search:
            qs = qs.filter(Q(caption__icontains=search) | Q(movie__name__icontains=search))
        sort = self.request.GET.get('sort', 'uploaded_at')
        order = self.request.GET.get('order', 'desc')
        valid_sort = ['id', 'movie__name', 'uploaded_at']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('-uploaded_at')
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['movies'] = Movie.objects.all()
        return context


class MovieImageUpdateView(AdminSessionMixin, UpdateView):
    model = MovieImage
    form_class = MovieImageForm
    template_name = 'admin/images/image_form.html'
    success_url = reverse_lazy('admin_image_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['object'] = self.object
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Image updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Image Updated',
            module='MovieImage',
            object_id=self.object.id,
            details=f'Updated image for {self.object.movie.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class MovieImageCreateView(AdminSessionMixin, CreateView):
    model = MovieImage
    form_class = MovieImageForm
    template_name = 'admin/images/image_form.html'
    success_url = reverse_lazy('admin_image_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Image uploaded successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Image Added',
            module='MovieImage',
            object_id=self.object.id,
            details=f'Added image for {self.object.movie.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class MovieImageDeleteView(AdminDeleteViewMixin, AdminSessionMixin, DeleteView):
    model = MovieImage
    template_name = 'admin/images/image_confirm_delete.html'
    success_url = reverse_lazy('admin_image_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Image Deleted',
            module='MovieImage',
            object_id=obj.id,
            details=f'Deleted image for {obj.movie.name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Image deleted successfully.')
        return super().delete(request, *args, **kwargs)


@admin_session_required
def seat_management(request):
    theatre_names = Theater.objects.values_list('name', flat=True).distinct().order_by('name')
    selected_theater = None
    seats = []
    theater_id = request.GET.get('theater')

    if theater_id:
        selected_theater = get_object_or_404(Theater, id=theater_id)
        seats = Seat.objects.filter(theater=selected_theater).order_by('row_idx', 'col_idx', 'seat_number')
    else:
        seats = Seat.objects.none()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'generate_seats':
            theater_id = request.POST.get('theater_id')
            theater = get_object_or_404(Theater, id=theater_id)
            from admin_panel.services import create_seats_for_theater
            from admin_panel.layouts import capacity_of
            admin_show = getattr(theater, 'admin_show', None)
            layout_spec = theater.layout_spec or getattr(
                admin_show.screen if admin_show else None, 'layout_spec', None)
            created_count = create_seats_for_theater(
                theater, capacity_of(layout_spec) if layout_spec else 0,
                layout_spec=layout_spec,
            )
            AuditLog.objects.create(
                user=request.user,
                action='Seats Generated',
                module='Seat',
                details=f'Generated {created_count} seats for {theater.name} from layout',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'{created_count} seats generated from the screen layout.')
            return redirect(f'{reverse("admin_seat_management")}?theater={theater.id}')

        elif action == 'block_seat':
            seat_id = request.POST.get('seat_id')
            seat = get_object_or_404(Seat, id=seat_id)
            seat.is_booked = True
            seat.save()
            seat.theater.bump_seat_revision()
            AuditLog.objects.create(
                user=request.user,
                action='Seat Blocked',
                module='Seat',
                object_id=seat.id,
                details=f'Blocked seat {seat.seat_number}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'Seat {seat.seat_number} blocked.')

        elif action == 'unblock_seat':
            seat_id = request.POST.get('seat_id')
            seat = get_object_or_404(Seat, id=seat_id)
            seat.is_booked = False
            seat.save()
            seat.theater.bump_seat_revision()
            AuditLog.objects.create(
                user=request.user,
                action='Seat Unblocked',
                module='Seat',
                object_id=seat.id,
                details=f'Unblocked seat {seat.seat_number}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'Seat {seat.seat_number} unblocked.')

        elif action == 'maintenance_seat':
            seat_id = request.POST.get('seat_id')
            seat = get_object_or_404(Seat, id=seat_id)
            seat.is_booked = not seat.is_booked
            seat.save()
            seat.theater.bump_seat_revision()
            AuditLog.objects.create(
                user=request.user,
                action='Seat Maintenance Toggle',
                module='Seat',
                object_id=seat.id,
                details=f'Toggled maintenance for seat {seat.seat_number}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'Seat {seat.seat_number} toggled.')

        return redirect(f'{reverse("admin_seat_management")}?theater={theater_id or ""}')

    context = {
        'theatre_names': theatre_names,
        'theatres_list': Theater.objects.select_related('movie').order_by('-time')[:50],
        'selected_theater': selected_theater,
        'seats': seats,
    }
    return render(request, 'admin/seat_management.html', context)


class BookingListView(AdminSessionMixin, ListView):
    template_name = 'admin/bookings/booking_list.html'
    context_object_name = 'bookings'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def _base_queryset(self):
        return Booking.objects.select_related(
            'user', 'movie', 'theater', 'seat', 'reservation'
        )

    def _apply_filters(self, qs):
        form = BookingSearchForm(self.request.GET)
        if form.is_valid():
            movie = form.cleaned_data.get('movie')
            username = form.cleaned_data.get('user')
            date_from = form.cleaned_data.get('date_from')
            date_to = form.cleaned_data.get('date_to')
            theatre = form.cleaned_data.get('theatre')
            if movie:
                qs = qs.filter(movie=movie)
            if username:
                qs = qs.filter(user__username__icontains=username)
            if date_from:
                qs = qs.filter(booked_at__date__gte=date_from)
            if date_to:
                qs = qs.filter(booked_at__date__lte=date_to)
            if theatre:
                if str(theatre).isdigit():
                    qs = qs.filter(theater_id=int(theatre))
                else:
                    qs = qs.filter(theater__name__icontains=theatre)
        return qs

    def _apply_sort(self, qs):
        sort = self.request.GET.get('sort', 'booked_at')
        order = self.request.GET.get('order', 'desc')
        valid_sort = ['id', 'user__username', 'movie__name', 'theater__name', 'booked_at']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('-booked_at')
        return qs

    def get(self, request, *args, **kwargs):
        per_page = self.get_paginate_by(None)
        page_num = request.GET.get('page', 1)
        try:
            page_num = int(page_num)
        except (ValueError, TypeError):
            page_num = 1
        if page_num < 1:
            page_num = 1

        base_qs = self._apply_sort(self._apply_filters(self._base_queryset()))

        # Count standalone bookings (no reservation) and grouped reservations
        # at the database level to get total transaction count.
        standalone_qs = base_qs.filter(reservation__isnull=True)
        grouped_qs = base_qs.filter(reservation__isnull=False)

        standalone_count = standalone_qs.count()
        grouped_res_count = (
            grouped_qs
            .values('reservation_id')
            .distinct()
            .count()
        )
        total_transactions = standalone_count + grouped_res_count

        # Determine which transactions fall on the requested page.
        page_start = (page_num - 1) * per_page
        page_end = page_start + per_page

        page_transactions = []

        if page_start < standalone_count:
            # Current page includes some standalone bookings.
            standalone_page_end = min(page_end, standalone_count)
            standalone_bookings = list(
                standalone_qs[page_start:standalone_page_end]
            )
            page_transactions.extend(
                BookingTransaction([b]) for b in standalone_bookings
            )

        if page_end > standalone_count and grouped_res_count:
            # Current page includes some grouped transactions.
            grp_start = max(0, page_start - standalone_count)
            grp_end = max(0, page_end - standalone_count)

            # Fetch the distinct reservation IDs for this page of groups.
            res_ids = list(
                grouped_qs.values_list('reservation_id', flat=True).order_by('reservation_id').distinct()[grp_start:grp_end]
            )

            if res_ids:
                page_bookings = list(
                    grouped_qs.filter(reservation_id__in=res_ids)
                )
                tx_map = {}
                for b in page_bookings:
                    tx_map.setdefault(b.reservation_id, []).append(b)
                for rid in res_ids:
                    bookings = tx_map.get(rid, [])
                    if bookings:
                        page_transactions.append(BookingTransaction(bookings))

        # Build a fake paginator for template compatibility.
        from django.core.paginator import EmptyPage, PageNotAnInteger
        paginator = Paginator(range(total_transactions), per_page)
        try:
            page_obj = paginator.page(page_num)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)
        page_obj.object_list = page_transactions

        self.object_list = page_transactions
        context = self.get_context_data(
            object_list=page_transactions,
            paginator=paginator,
            page_obj=page_obj,
            is_paginated=page_obj.has_other_pages(),
        )
        return render(request, self.template_name, context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_form'] = BookingSearchForm(self.request.GET)
        context['movies'] = Movie.objects.all()
        context['theatres'] = Theater.objects.values('name', 'id').distinct().order_by('name')
        return context


@admin_session_required
def booking_detail(request, pk):
    booking = get_object_or_404(
        Booking.objects.select_related(
            'user', 'movie', 'theater', 'seat', 'payment', 'reservation'
        ),
        id=pk,
    )
    if booking.reservation_id:
        return booking_transaction_detail(request, booking.reservation_id)
    try:
        ps = booking.payment.status
    except Exception:
        ps = ''
    return render(request, 'admin/bookings/booking_detail.html', {
        'booking': booking,
        'bookings': [booking],
        'reservation': None,
        'booking_ref': booking.booking_ref or str(booking.id),
        'total_amount': booking.total,
        'payment_status': ps,
        'status': booking.status,
    })


@admin_session_required
def booking_transaction_detail(request, pk):
    reservation = get_object_or_404(
        Reservation.objects.select_related('user', 'show', 'show__movie'),
        id=pk,
    )
    bookings = list(
        reservation.bookings.select_related('seat', 'payment', 'movie', 'theater', 'user')
        .order_by('seat__seat_number')
    )
    return render(request, 'admin/bookings/booking_detail.html', {
        'booking': bookings[0] if bookings else None,
        'bookings': bookings,
        'reservation': reservation,
        'booking_ref': reservation.booking_ref or str(reservation.id),
        'total_amount': reservation.total_amount,
        'payment_status': reservation.payment_status,
        'status': reservation.status,
    })


@require_POST
@admin_session_required
@permission_required('booking', 'can_edit')
def booking_transaction_cancel(request, pk):
    with transaction.atomic():
        reservation = get_object_or_404(
            Reservation.objects.select_for_update().select_related('show'),
            id=pk,
        )
        bookings = list(
            reservation.bookings.select_for_update().select_related('seat', 'payment')
        )
        if not bookings:
            messages.warning(request, 'This booking has no tickets to cancel.')
            return redirect('admin_booking_transaction_detail', pk=pk)
        if all(b.status == 'cancelled' for b in bookings):
            messages.warning(request, 'This booking has already been cancelled.')
            return redirect('admin_booking_list')
        if reservation.show.time <= timezone.now():
            messages.error(
                request,
                'Cancellation is not allowed once the show has started.',
            )
            return redirect('admin_booking_transaction_detail', pk=pk)
        try:
            from movies.payments import refund_reservation_transactions
            refund_reservation_transactions(reservation)
        except Exception:
            logger.warning(
                'Gateway refund for reservation %s failed.',
                reservation.booking_ref or reservation.id,
                exc_info=True,
            )
        Payment.objects.filter(
            booking__in=bookings, status='completed'
        ).update(status='refunded')
        Seat.objects.filter(pk__in=[b.seat_id for b in bookings]).update(is_booked=False)
        Booking.objects.filter(pk__in=[b.pk for b in bookings]).update(
            status='cancelled', cancelled_at=timezone.now()
        )
        reservation.status = 'cancelled'
        reservation.payment_status = 'refunded'
        reservation.save(update_fields=['status', 'payment_status', 'updated_at'])
        reservation.show.bump_seat_revision()
    AuditLog.objects.create(
        user=request.user,
        action='Booking Cancelled',
        module='Booking',
        object_id=reservation.id,
        details='Cancelled booking {} ({} ticket(s)) for {}'.format(
            reservation.booking_ref or reservation.id,
            len(bookings),
            reservation.show.movie.name,
        ),
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, 'Booking cancelled and payment refunded.')
    return redirect('admin_booking_list')


@require_POST
@admin_session_required
@permission_required('booking', 'can_edit')
def booking_cancel(request, pk):
    with transaction.atomic():
        booking = get_object_or_404(
            Booking.objects.select_for_update().select_related('seat', 'theater', 'reservation'),
            id=pk,
        )
        if booking.status == 'cancelled':
            messages.warning(request, 'This booking has already been cancelled.')
            return redirect('admin_booking_list')
        if booking.theater.time <= timezone.now():
            messages.error(
                request,
                'Cancellation is not allowed once the show has started.',
            )
            return redirect('admin_booking_detail', pk=pk)
        try:
            from movies.payments import refund_reservation_transactions
            refund_reservation_transactions(booking.reservation)
        except Exception:
            logger.warning(
                'Gateway refund for booking %s failed.',
                booking.booking_ref or booking.id,
                exc_info=True,
            )
        Payment.objects.filter(booking=booking, status='completed').update(
            status='refunded'
        )
        Seat.objects.filter(pk=booking.seat_id).update(is_booked=False)
        booking.theater.bump_seat_revision()
        booking.status = 'cancelled'
        booking.cancelled_at = timezone.now()
        booking.save(update_fields=['status', 'cancelled_at'])
    AuditLog.objects.create(
        user=request.user,
        action='Booking Cancelled',
        module='Booking',
        object_id=booking.id,
        details=f'Cancelled booking {booking.booking_ref or booking.id} for {booking.movie.name}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, 'Booking cancelled and payment refunded.')
    return redirect('admin_booking_list')


@admin_session_required
@permission_required('booking', 'can_create')
def booking_reserve(request):
    form = ReserveBookingForm()
    if request.method == 'POST':
        form = ReserveBookingForm(request.POST)
        if form.is_valid():
            user = form.cleaned_data['user']
            movie = form.cleaned_data['movie']
            show = form.cleaned_data['show']
            seat_count = form.cleaned_data['seat_count']
            try:
                bookings, reservation = create_walkin_bookings(user, movie, show, seat_count)
            except ReservationError as exc:
                messages.error(request, str(exc))
                return redirect('admin_booking_list')
            AuditLog.objects.create(
                user=request.user,
                action='Booking Reserved',
                module='Booking',
                details=f'Reserved {len(bookings)} seat(s) for {user.username} - {movie.name}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            try:
                from movies.notifications import send_manual_booking_confirmation
                send_manual_booking_confirmation(user, bookings, reservation=reservation)
            except Exception:
                pass
            messages.success(request, f'{len(bookings)} seat(s) reserved successfully for {user.username}.')
            return redirect('admin_booking_list')
    return render(request, 'admin/bookings/booking_reserve.html', {
        'form': form,
        'users': User.objects.filter(is_active=True).order_by('username')[:200],
        'movies': Movie.objects.filter(is_deleted=False).order_by('name'),
        'shows': Theater.objects.filter(
            time__gte=timezone.now(),
            time__lt=timezone.now() + timedelta(days=7),
        ).select_related('movie').order_by('time'),
    })


@admin_session_required
@permission_required('booking', 'can_edit')
def booking_modify(request, pk):
    if request.method != 'POST':
        return redirect('admin_booking_detail', pk=pk)
    new_seat_id = request.POST.get('new_seat')
    with transaction.atomic():
        booking = get_object_or_404(
            Booking.objects.select_for_update().select_related('seat', 'theater'),
            id=pk,
        )
        if booking.status == 'cancelled':
            messages.error(request, 'A cancelled booking cannot be moved to another seat.')
            return redirect('admin_booking_detail', pk=pk)
        if booking.theater.time <= timezone.now():
            messages.error(request, 'Seats cannot be changed once the show has started.')
            return redirect('admin_booking_detail', pk=pk)
        show = Theater.objects.select_for_update().get(pk=booking.theater_id)
        try:
            new_seat = Seat.objects.select_for_update().get(
                pk=new_seat_id, theater=show
            )
        except Seat.DoesNotExist:
            messages.error(request, 'The selected seat is not part of this show.')
            return redirect('admin_booking_detail', pk=pk)
        if new_seat.pk == booking.seat_id:
            messages.info(request, 'The selected seat is already assigned to this booking.')
            return redirect('admin_booking_detail', pk=pk)
        if new_seat.is_booked:
            messages.error(request, 'Selected seat is already booked.')
            return redirect('admin_booking_detail', pk=pk)
        old_seat = booking.seat
        pricing = pricing_for_seats(show, [new_seat])
        entry = pricing['seats'][0]
        booking.seat = new_seat
        booking.seat_category = entry['category']
        booking.ticket_price = entry['price']
        booking.gst_rate = pricing['gst_rate']
        booking.gst_amount = pricing['gst']
        booking.platform_fee = pricing['platform_fee']
        booking.misc_fee = pricing['misc_fee']
        booking.discount = Decimal('0.00')
        booking.total = pricing['total']
        booking.save()
        Payment.objects.filter(booking=booking, status='completed').update(
            amount=pricing['total']
        )
        if old_seat:
            Seat.objects.filter(pk=old_seat.pk).update(is_booked=False)
        Seat.objects.filter(pk=new_seat.pk).update(is_booked=True)
        show.bump_seat_revision()
    AuditLog.objects.create(
        user=request.user,
        action='Booking Modified',
        module='Booking',
        object_id=booking.id,
        details=f'Changed seat from {old_seat.seat_number if old_seat else "None"} to {new_seat.seat_number}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, 'Booking moved and re-priced for the new seat.')
    return redirect('admin_booking_detail', pk=pk)


@admin_session_required
@permission_required('booking', 'can_edit')
def booking_resend_confirmation(request, pk):
    booking = get_object_or_404(Booking, id=pk)
    if booking.status == 'cancelled':
        messages.error(request, 'A cancelled booking has no confirmation to resend.')
        return redirect('admin_booking_detail', pk=pk)
    try:
        from movies.notifications import send_manual_booking_confirmation
        send_manual_booking_confirmation(booking.user, [booking])
        messages.success(request, f'Confirmation email sent for booking #{booking.booking_ref or booking.id}.')
    except Exception:
        messages.error(request, 'Could not send the confirmation email.')
    return redirect('admin_booking_detail', pk=pk)


class ReservationListView(AdminSessionMixin, ListView):
    model = Reservation
    template_name = 'admin/reservations/reservation_list.html'
    context_object_name = 'reservations'
    paginate_by = 20

    def get_queryset(self):
        qs = Reservation.objects.select_related('user', 'show', 'show__movie').prefetch_related('reserved_seats__seat')
        movie = self.request.GET.get('movie')
        theatre = self.request.GET.get('theatre')
        date_val = self.request.GET.get('date')
        username = self.request.GET.get('user')
        status = self.request.GET.get('status')
        payment = self.request.GET.get('payment')
        if movie:
            qs = qs.filter(show__movie_id=movie)
        if theatre:
            qs = qs.filter(show__name__icontains=theatre)
        if date_val:
            qs = qs.filter(show__time__date=date_val)
        if username:
            qs = qs.filter(user__username__icontains=username)
        if status:
            qs = qs.filter(status=status)
        if payment:
            qs = qs.filter(payment_status=payment)
        return qs.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['movies'] = Movie.objects.all()
        context['theatre_names'] = Theater.objects.values_list('name', flat=True).distinct().order_by('name')
        context['status_choices'] = Reservation.STATUS_CHOICES
        context['payment_choices'] = Reservation.PAYMENT_STATUS_CHOICES
        context['total_active'] = Reservation.objects.filter(status='active').count()
        context['now'] = timezone.now()
        return context


class PaymentTransactionListView(AdminSessionMixin, ListView):
    model = PaymentTransaction
    template_name = 'admin/payments/payment_list.html'
    context_object_name = 'transactions'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_queryset(self):
        qs = PaymentTransaction.objects.select_related(
            'user', 'reservation', 'reservation__show', 'reservation__show__movie'
        )
        form = PaymentSearchForm(self.request.GET)
        if form.is_valid():
            user = form.cleaned_data.get('user')
            status = form.cleaned_data.get('status')
            gateway_order_id = form.cleaned_data.get('gateway_order_id')
            date_from = form.cleaned_data.get('date_from')
            date_to = form.cleaned_data.get('date_to')
            if user:
                qs = qs.filter(user__username__icontains=user)
            if status:
                qs = qs.filter(status=status)
            if gateway_order_id:
                qs = qs.filter(
                    Q(gateway_order_id__icontains=gateway_order_id)
                    | Q(gateway_payment_id__icontains=gateway_order_id)
                )
            if date_from:
                qs = qs.filter(created_at__date__gte=date_from)
            if date_to:
                qs = qs.filter(created_at__date__lte=date_to)
        sort = self.request.GET.get('sort', 'created_at')
        order = self.request.GET.get('order', 'desc')
        valid_sort = [
            'created_at', 'id', 'user__username', 'amount', 'status',
            'gateway_order_id', 'reservation__show__movie__name',
        ]
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('-created_at')
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_form'] = PaymentSearchForm(self.request.GET)
        context['status_choices'] = PaymentTransaction.STATUS_CHOICES
        context['total_captured'] = PaymentTransaction.objects.filter(status='captured').count()
        context['total_failed'] = PaymentTransaction.objects.filter(status='failed').count()
        context['total_refunds'] = PaymentTransaction.objects.filter(
            status__in=['refund_requested', 'refunded']
        ).count()
        context['total_revenue'] = Payment.objects.filter(status='completed').aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')
        return context


@admin_session_required
def payment_transaction_detail(request, pk):
    tx = get_object_or_404(
        PaymentTransaction.objects.select_related(
            'user', 'reservation', 'reservation__show', 'reservation__show__movie'
        ),
        id=pk,
    )
    bookings = (
        tx.reservation.bookings.select_related('seat', 'payment')
        if tx.reservation_id else PaymentTransaction.objects.none()
    )
    return render(request, 'admin/payments/payment_detail.html', {
        'tx': tx,
        'bookings': bookings,
    })


@admin_session_required
@permission_required('payment', 'can_edit')
def payment_transaction_refund(request, pk):
    if request.method != 'POST':
        return redirect('admin_payment_detail', pk=pk)
    with transaction.atomic():
        tx = get_object_or_404(
            PaymentTransaction.objects.select_for_update(), id=pk
        )
        if tx.status != 'captured':
            messages.error(
                request,
                'Only captured payments can be refunded (current status: {}).'.format(
                    tx.get_status_display()
                ),
            )
            return redirect('admin_payment_detail', pk=pk)
        try:
            from movies.payments import refund_transaction

            refunded = refund_transaction(tx, reason='Refunded by admin')
        except Exception as exc:
            messages.error(request, 'Refund failed: {}'.format(exc))
            return redirect('admin_payment_detail', pk=pk)

        if refunded.status not in ('refunded', 'refund_requested'):
            messages.warning(request, 'The refund request could not be completed.')
            return redirect('admin_payment_detail', pk=pk)

        Payment.objects.filter(
            booking__reservation=tx.reservation,
            status='completed',
            transaction_id=tx.gateway_payment_id,
        ).update(status='refunded')
        AuditLog.objects.create(
            user=request.user,
            action='Payment Refunded',
            module='Payment',
            object_id=tx.id,
            details='Refunded {} for order {}'.format(tx.amount, tx.gateway_order_id),
            ip_address=request.META.get('REMOTE_ADDR')
        )
    messages.success(request, 'Refund initiated successfully.')
    return redirect('admin_payment_detail', pk=pk)


class UserListView(AdminSessionMixin, ListView):
    model = User
    template_name = 'admin/users/user_list.html'
    context_object_name = 'users'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_queryset(self):
        qs = User.objects.annotate(booking_count=Count('booking'))
        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(
                Q(username__icontains=search) |
                Q(email__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search)
            )
        sort = self.request.GET.get('sort', 'date_joined')
        order = self.request.GET.get('order', 'desc')
        valid_sort = ['id', 'username', 'email', 'date_joined', 'is_active', 'is_staff']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('-date_joined')
        return qs


@require_POST
@admin_session_required
@permission_required('user', 'can_edit')
def user_toggle_active(request, pk):
    user_obj = get_object_or_404(User, id=pk)
    if user_obj.id == request.user.id:
        messages.error(request, 'You cannot deactivate your own account.')
        return redirect('admin_user_list')
    actor_profile = AdminProfile.objects.filter(user=request.user).first()
    is_super_actor = request.user.is_superuser or (
        actor_profile and actor_profile.role == 'super_admin'
    )
    is_super_admin_user = user_obj.is_superuser or AdminProfile.objects.filter(
        user=user_obj, role='super_admin'
    ).exists()
    if user_obj.is_active and is_super_admin_user and not is_super_actor:
        messages.error(request, 'Only a super admin can deactivate a super admin account.')
        return redirect('admin_user_list')
    user_obj.is_active = not user_obj.is_active
    user_obj.save()
    status = 'activated' if user_obj.is_active else 'deactivated'
    AuditLog.objects.create(
        user=request.user,
        action=f'User {status}',
        module='User',
        object_id=user_obj.id,
        details=f'User {user_obj.username} {status}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, f'User {user_obj.username} {status} successfully.')
    return redirect('admin_user_list')


@admin_session_required
@permission_required('user', 'can_view')
def user_booking_history(request, pk):
    user_obj = get_object_or_404(User, id=pk)
    bookings_qs = Booking.objects.filter(user=user_obj).select_related('movie', 'theater', 'seat').order_by('-booked_at')
    paginator = Paginator(bookings_qs, 20)
    page_num = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_num)
    except Exception:
        page_obj = paginator.page(1)

    booking_ids = [b.id for b in page_obj]
    payment_amounts = {
        p.booking_id: p.amount
        for p in Payment.objects.filter(booking_id__in=booking_ids).only('booking_id', 'amount')
    }
    return render(request, 'admin/users/user_bookings.html', {
        'user_obj': user_obj,
        'bookings': page_obj,
        'page_obj': page_obj,
        'payment_amounts': payment_amounts,
    })


@admin_session_required
@permission_required('user', 'can_edit')
def user_reset_password(request, pk):
    if request.method != 'POST':
        return redirect('admin_user_list')
    user_obj = get_object_or_404(User, id=pk)
    current_password = request.POST.get('current_password', '')
    new_password = request.POST.get('new_password', '')
    confirm_password = request.POST.get('confirm_password', '')

    if current_password:
        if user_obj.id != request.user.id:
            messages.error(request, 'You may only change your own password this way.')
            return redirect('admin_user_list')
        if not user_obj.check_password(current_password):
            messages.error(request, 'Your current password is incorrect.')
            return redirect('admin_profile')
        if not new_password:
            messages.error(request, 'Please enter a new password.')
            return redirect('admin_profile')
        if new_password != confirm_password:
            messages.error(request, 'New password and confirmation do not match.')
            return redirect('admin_profile')
        try:
            password_validation.validate_password(new_password, user_obj)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
            return redirect('admin_profile')
        user_obj.set_password(new_password)
        user_obj.save()
        update_session_auth_hash(request, user_obj)
        AuditLog.objects.create(
            user=request.user,
            action='Password Changed',
            module='User',
            object_id=user_obj.id,
            details=f'Password changed for {user_obj.username}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Your password has been changed successfully.')
        return redirect('admin_profile')

    actor_profile = AdminProfile.objects.filter(user=request.user, is_active=True).first()
    is_super_actor = request.user.is_superuser or (
        actor_profile and actor_profile.role in ('super_admin', 'admin')
    )
    if not is_super_actor:
        messages.error(request, 'You do not have permission to reset passwords.')
        return redirect('admin_user_list')
    target_is_super = user_obj.is_superuser or AdminProfile.objects.filter(
        user=user_obj, role='super_admin'
    ).exists()
    can_manage_super = request.user.is_superuser or (
        actor_profile and actor_profile.role == 'super_admin'
    )
    if target_is_super and not can_manage_super:
        messages.error(request, 'Only a super admin can reset a super admin password.')
        return redirect('admin_user_list')
    if not user_obj.email:
        messages.error(
            request,
            'This user has no email address, so the password cannot be reset.',
        )
        return redirect('admin_user_list')

    temp_password = secrets.token_urlsafe(9)
    try:
        from django.core.mail import send_mail
        from django.conf import settings
        send_mail(
            'Your temporary BookMySeat password',
            'Hi {},\n\nA temporary password has been set for your account: {}\n\n'
            'Please log in and change it immediately.\n\n— BookMySeat'.format(
                user_obj.username, temp_password
            ),
            getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@bookmyseat.com'),
            [user_obj.email],
            fail_silently=False,
        )
    except Exception:
        messages.error(
            request,
            'The temporary password could not be emailed, so no changes were made.',
        )
        return redirect('admin_user_list')

    user_obj.set_password(temp_password)
    user_obj.save()
    AuditLog.objects.create(
        user=request.user,
        action='Password Reset',
        module='User',
        object_id=user_obj.id,
        details=f'Password reset for {user_obj.username}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(
        request,
        'A temporary password has been set for {}. It has been emailed to the user.'.format(
            user_obj.username
        ),
    )
    return redirect('admin_user_list')


class StaffListView(AdminSessionMixin, ListView):
    model = AdminProfile
    template_name = 'admin/staff/staff_list.html'
    context_object_name = 'staff_members'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_queryset(self):
        qs = AdminProfile.objects.select_related('user').all()
        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(Q(user__username__icontains=search) | Q(user__email__icontains=search))
        sort = self.request.GET.get('sort', 'id')
        order = self.request.GET.get('order', 'asc')
        valid_sort = ['id', 'user__username', 'user__email', 'role', 'department']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('id')
        return qs


@admin_session_required
@permission_required('staff', 'can_create')
def staff_create(request):
    if request.method == 'POST':
        form = StaffCreateForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            password = form.cleaned_data.get('password')
            user.set_password(password)
            user.is_staff = True
            user.save()
            AdminProfile.objects.create(
                user=user,
                role='staff',
                department=request.POST.get('department', ''),
                phone=request.POST.get('phone', ''),
            )
            AuditLog.objects.create(
                user=request.user,
                action='Staff Created',
                module='Staff',
                object_id=user.id,
                details=f'Created staff: {user.username}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'Staff member {user.username} created successfully.')
            return redirect('admin_staff_list')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = StaffCreateForm()
    profile_form = AdminProfileForm()
    return render(request, 'admin/staff/staff_form.html', {'form': form, 'profile_form': profile_form, 'is_create': True})


@admin_session_required
@permission_required('staff', 'can_edit')
def staff_edit(request, pk):
    profile = get_object_or_404(AdminProfile, id=pk)
    user = profile.user
    if request.method == 'POST':
        user_form = StaffUpdateForm(request.POST, instance=user)
        profile_form = AdminProfileForm(request.POST, instance=profile)
        if user_form.is_valid() and profile_form.is_valid():
            actor_profile = AdminProfile.objects.filter(user=request.user).first()
            is_super_actor = request.user.is_superuser or (
                actor_profile and actor_profile.role == 'super_admin'
            )
            new_role = profile_form.cleaned_data.get('role')
            if profile.user_id == request.user.id and new_role != profile.role:
                messages.error(request, 'You cannot change your own role.')
                return redirect('admin_staff_list')
            if new_role == 'super_admin' and not is_super_actor:
                messages.error(request, 'Only a super admin can grant the super admin role.')
                return redirect('admin_staff_list')
            if new_role == 'admin' and not is_super_actor:
                messages.error(request, 'Only a super admin can grant the admin role.')
                return redirect('admin_staff_list')
            if profile.role in ('super_admin', 'admin') and new_role not in ('super_admin', 'admin'):
                remaining = AdminProfile.objects.filter(
                    role=profile.role
                ).exclude(pk=profile.pk).count()
                if remaining == 0:
                    messages.error(
                        request,
                        f'Cannot demote the last {profile.role} account.',
                    )
                    return redirect('admin_staff_list')
            user_form.save()
            profile_form.save()
            AuditLog.objects.create(
                user=request.user,
                action='Staff Updated',
                module='Staff',
                object_id=user.id,
                details=f'Updated staff: {user.username}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'Staff {user.username} updated successfully.')
            return redirect('admin_staff_list')
    else:
        user_form = StaffUpdateForm(instance=user)
        profile_form = AdminProfileForm(instance=profile)
    return render(request, 'admin/staff/staff_form.html', {
        'form': user_form,
        'profile_form': profile_form,
        'object': user,
    })


@require_POST
@admin_session_required
@permission_required('staff', 'can_delete')
def staff_delete(request, pk):
    profile = get_object_or_404(AdminProfile, id=pk)
    user = profile.user
    if request.method == 'POST':
        actor_profile = AdminProfile.objects.filter(user=request.user).first()
        is_super_actor = request.user.is_superuser or (
            actor_profile and actor_profile.role == 'super_admin'
        )
        if profile.user_id == request.user.id:
            messages.error(request, 'You cannot delete your own account.')
            return redirect('admin_staff_list')
        if profile.role == 'super_admin' and not is_super_actor:
            messages.error(request, 'Only a super admin can delete a super admin.')
            return redirect('admin_staff_list')
        if profile.role in ('super_admin', 'admin'):
            remaining = AdminProfile.objects.filter(
                role=profile.role
            ).exclude(pk=profile.pk).count()
            if remaining == 0:
                messages.error(
                    request,
                    f'Cannot delete the last {profile.role} account.',
                )
                return redirect('admin_staff_list')
        AuditLog.objects.create(
            user=request.user,
            action='Staff Deleted',
            module='Staff',
            object_id=user.id,
            details=f'Deleted staff: {user.username}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        profile.delete()
        user.is_staff = False
        user.save()
        messages.success(request, f'Staff {user.username} removed successfully.')
        return redirect('admin_staff_list')
    return render(request, 'admin/staff/staff_confirm_delete.html', {'object': user})


@admin_session_required
@permission_required('staff', 'can_edit')
def staff_permissions(request, pk):
    profile = get_object_or_404(AdminProfile, id=pk)
    modules = ['Movie', 'Theatre', 'Screen', 'Show', 'Booking', 'Payment', 'User', 'Staff', 'Coupon', 'Notification', 'Review', 'Genre', 'Language', 'Cast', 'Settings', 'Analytics', 'Ticket']

    if request.method == 'POST':
        actor_profile = AdminProfile.objects.filter(user=request.user).first()
        is_super_actor = request.user.is_superuser or (
            actor_profile and actor_profile.role == 'super_admin'
        )
        if profile.user_id == request.user.id and not is_super_actor:
            messages.error(request, 'You cannot modify your own permissions.')
            return redirect('admin_staff_list')
        AdminPermission.objects.filter(admin_profile=profile).delete()
        for module in modules:
            can_view = request.POST.get(f'{module}_can_view') == 'on'
            can_create = request.POST.get(f'{module}_can_create') == 'on'
            can_edit = request.POST.get(f'{module}_can_edit') == 'on'
            can_delete = request.POST.get(f'{module}_can_delete') == 'on'
            AdminPermission.objects.create(
                admin_profile=profile,
                module=module.lower(),
                can_view=can_view,
                can_create=can_create,
                can_edit=can_edit,
                can_delete=can_delete,
            )
        AuditLog.objects.create(
            user=request.user,
            action='Permissions Updated',
            module='Staff',
            object_id=profile.user.id,
            details=f'Updated permissions for {profile.user.username}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, f'Permissions updated for {profile.user.username}.')
        return redirect('admin_staff_list')

    existing_perms = {p.module: p for p in AdminPermission.objects.filter(admin_profile=profile)}
    return render(request, 'admin/staff/staff_permissions.html', {
        'profile': profile,
        'modules': modules,
        'existing_perms': existing_perms,
    })


class CouponListView(AdminSessionMixin, ListView):
    model = Coupon
    template_name = 'admin/coupons/coupon_list.html'
    context_object_name = 'coupons'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20


class CouponCreateView(AdminSessionMixin, CreateView):
    model = Coupon
    form_class = CouponForm
    template_name = 'admin/coupons/coupon_form.html'
    success_url = reverse_lazy('admin_coupon_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Coupon created successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Coupon Created',
            module='Coupon',
            object_id=self.object.id,
            details=f'Created coupon: {self.object.code}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class CouponUpdateView(AdminSessionMixin, UpdateView):
    model = Coupon
    form_class = CouponForm
    template_name = 'admin/coupons/coupon_form.html'
    success_url = reverse_lazy('admin_coupon_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Coupon updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Coupon Updated',
            module='Coupon',
            object_id=self.object.id,
            details=f'Updated coupon: {self.object.code}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class CouponDeleteView(AdminDeleteViewMixin, AdminSessionMixin, DeleteView):
    model = Coupon
    template_name = 'admin/coupons/coupon_confirm_delete.html'
    success_url = reverse_lazy('admin_coupon_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Coupon Deleted',
            module='Coupon',
            object_id=obj.id,
            details=f'Deleted coupon: {obj.code}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Coupon deleted successfully.')
        return super().delete(request, *args, **kwargs)


class NotificationListView(AdminSessionMixin, ListView):
    model = Notification
    template_name = 'admin/notifications/notification_list.html'
    context_object_name = 'notifications'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_queryset(self):
        qs = Notification.objects.select_related('user').all()
        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(message__icontains=search))
        sort = self.request.GET.get('sort', 'created_at')
        order = self.request.GET.get('order', 'desc')
        valid_sort = ['title', 'notification_type', 'created_at']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('-created_at')
        return qs


@admin_session_required
@permission_required('notification', 'can_create')
def notification_create(request):
    if request.method == 'POST':
        form = NotificationForm(request.POST)
        if form.is_valid():
            notification = form.save(commit=False)
            send_to_all = request.POST.get('send_to_all')
            if send_to_all:
                now = timezone.now()
                Notification.objects.bulk_create([
                    Notification(
                        user=user,
                        title=notification.title,
                        message=notification.message,
                        notification_type=notification.notification_type,
                        link=notification.link,
                        created_at=now,
                    )
                    for user in User.objects.filter(is_active=True).iterator()
                ])
                AuditLog.objects.create(
                    user=request.user,
                    action='Notification Sent (All)',
                    module='Notification',
                    details=f'Sent notification to all users: {notification.title}',
                    ip_address=request.META.get('REMOTE_ADDR')
                )
                messages.success(request, 'Notification sent to all users.')
            else:
                user_id = request.POST.get('user_id')
                if user_id:
                    notification.user = get_object_or_404(User, id=int(user_id))
                else:
                    notification.user = None
                notification.save()
                AuditLog.objects.create(
                    user=request.user,
                    action='Notification Created',
                    module='Notification',
                    object_id=notification.id,
                    details=f'Created notification: {notification.title}',
                    ip_address=request.META.get('REMOTE_ADDR')
                )
                messages.success(request, 'Notification created successfully.')
            return redirect('admin_notification_list')
    else:
        form = NotificationForm()
    return render(request, 'admin/notifications/notification_form.html', {
        'form': form,
        'users': User.objects.filter(is_active=True),
    })


@require_POST
@admin_session_required
@permission_required('notification', 'can_view')
def notification_mark_read(request, pk):
    notification = get_object_or_404(Notification, id=pk)
    notification.is_read = True
    notification.save()
    return redirect('admin_notification_list')


@require_POST
@admin_session_required
@permission_required('notification', 'can_delete')
def notification_delete(request, pk):
    notification = get_object_or_404(Notification, id=pk)
    AuditLog.objects.create(
        user=request.user,
        action='Notification Deleted',
        module='Notification',
        object_id=notification.id,
        details=f'Deleted notification: {notification.title}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    notification.delete()
    messages.success(request, 'Notification deleted.')
    return redirect('admin_notification_list')


class ReviewListView(AdminSessionMixin, ListView):
    model = Review
    template_name = 'admin/reviews/review_list.html'
    context_object_name = 'reviews'

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_queryset(self):
        qs = Review.objects.select_related('movie', 'user').all()
        status = self.request.GET.get('status')
        movie_id = self.request.GET.get('movie')
        reported = self.request.GET.get('reported')
        hidden = self.request.GET.get('hidden')
        if status == 'approved':
            qs = qs.filter(is_approved=True)
        elif status == 'pending':
            qs = qs.filter(is_approved=False)
        if movie_id:
            qs = qs.filter(movie_id=movie_id)
        if reported == '1':
            qs = qs.filter(is_reported=True)
        if hidden == '1':
            qs = qs.filter(is_hidden=True)
        sort = self.request.GET.get('sort', 'created_at')
        order = self.request.GET.get('order', 'desc')
        valid_sort = ['id', 'movie__name', 'user__username', 'rating', 'created_at']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('-created_at')
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from movies.models import Movie
        context['movies'] = Movie.objects.all()
        return context


@require_POST
@admin_session_required
@permission_required('review', 'can_edit')
def review_approve(request, pk):
    review = get_object_or_404(Review, id=pk)
    review.is_approved = not review.is_approved
    review.save()
    status = 'approved' if review.is_approved else 'unapproved'
    AuditLog.objects.create(
        user=request.user,
        action=f'Review {status}',
        module='Review',
        object_id=review.id,
        details=f'Review {status} for {review.movie.name} by {review.user.username}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, f'Review {status} successfully.')
    return redirect('admin_review_list')


@require_POST
@admin_session_required
@permission_required('review', 'can_edit')
def review_hide(request, pk):
    review = get_object_or_404(Review, id=pk)
    review.is_hidden = True
    review.save()
    AuditLog.objects.create(
        user=request.user,
        action='Review Hidden',
        module='Review',
        object_id=review.id,
        details=f'Hidden review for {review.movie.name} by {review.user.username}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, 'Review hidden from public view.')
    return redirect('admin_review_list')


@require_POST
@admin_session_required
@permission_required('review', 'can_edit')
def review_restore(request, pk):
    review = get_object_or_404(Review, id=pk)
    review.is_hidden = False
    review.save()
    AuditLog.objects.create(
        user=request.user,
        action='Review Restored',
        module='Review',
        object_id=review.id,
        details=f'Restored review for {review.movie.name} by {review.user.username}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, 'Review restored to public view.')
    return redirect('admin_review_list')


@require_POST
@admin_session_required
@permission_required('review', 'can_delete')
def review_delete(request, pk):
    review = get_object_or_404(Review, id=pk)
    movie_name = review.movie.name
    username = review.user.username
    AuditLog.objects.create(
        user=request.user,
        action='Review Deleted',
        module='Review',
        object_id=review.id,
        details=f'Deleted review for {movie_name} by {username}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    review.delete()
    messages.success(request, 'Review deleted permanently.')
    return redirect('admin_review_list')




@permission_required('ticket', 'can_view')
def ticket_scanner(request):
    """Admin camera/QR scanner page (mobile-first, admin session only)."""
    return render(request, 'admin/tickets/scanner.html')


@permission_required('ticket', 'can_view')
@require_POST
def ticket_scan_api(request):
    """Admin-only scan endpoint.

    Accepts the same HMAC-signed QR payload as the public gate API but is
    locked to the admin session and records the scanning staff member in the
    scan-history audit trail.
    """
    raw = (request.body or b'').decode('utf-8', 'ignore')
    try:
        payload = json.loads(raw)
    except Exception:
        payload = None
    return scan_ticket(
        payload,
        scanned_by=request.user,
        ip_address=request.META.get('REMOTE_ADDR'),
    )


@method_decorator(permission_required('ticket', 'can_view'), name='dispatch')
class TicketScanHistoryView(AdminSessionMixin, ListView):
    model = TicketScan
    template_name = 'admin/tickets/scan_history.html'
    context_object_name = 'scans'
    ordering = ['-scanned_at']

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '20')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 20
        except (ValueError, TypeError):
            return 20

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['staff_users'] = User.objects.filter(is_staff=True).order_by('username')
        context['result_choices'] = TicketScan.RESULT_CHOICES
        return context

    def get_queryset(self):
        qs = TicketScan.objects.select_related('scanned_by').all()
        search = self.request.GET.get('search')
        result = self.request.GET.get('result')
        staff = self.request.GET.get('staff')
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        if search:
            qs = qs.filter(
                Q(booking_ref__icontains=search) |
                Q(movie__icontains=search) |
                Q(scanned_by__username__icontains=search)
            )
        if result:
            qs = qs.filter(result=result)
        if staff:
            qs = qs.filter(scanned_by_id=staff)
        try:
            if date_from:
                qs = qs.filter(scanned_at__date__gte=date_from)
            if date_to:
                qs = qs.filter(scanned_at__date__lte=date_to)
        except (ValueError, TypeError):
            pass
        sort = self.request.GET.get('sort', 'scanned_at')
        order = self.request.GET.get('order', 'desc')
        valid_sort = ['id', 'booking_ref', 'result', 'scanned_by__username', 'scanned_at']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('-scanned_at')
        return qs


class AuditLogListView(AdminSessionMixin, ListView):
    model = AuditLog
    template_name = 'admin/audit_logs.html'
    context_object_name = 'logs'
    ordering = ['-created_at']

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get('per_page', '30')
        try:
            return int(per_page) if int(per_page) in [10, 20, 50, 100] else 30
        except (ValueError, TypeError):
            return 30

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['users'] = User.objects.filter(is_staff=True).order_by('username')
        context['modules'] = AuditLog.objects.values_list('module', flat=True).distinct().order_by('module')
        return context

    def get_queryset(self):
        qs = AuditLog.objects.select_related('user').all()
        search = self.request.GET.get('search')
        action = self.request.GET.get('action')
        module = self.request.GET.get('module')
        if search:
            qs = qs.filter(
                Q(action__icontains=search) |
                Q(user__username__icontains=search) |
                Q(module__icontains=search) |
                Q(details__icontains=search)
            )
        if action:
            qs = qs.filter(action__icontains=action)
        if module:
            qs = qs.filter(module__icontains=module)
        sort = self.request.GET.get('sort', 'created_at')
        order = self.request.GET.get('order', 'desc')
        valid_sort = ['id', 'user__username', 'action', 'module', 'created_at']
        if sort.lstrip('-') in valid_sort:
            if order == 'desc' and not sort.startswith('-'):
                sort = f'-{sort}'
            elif order == 'asc' and sort.startswith('-'):
                sort = sort[1:]
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by('-created_at')
        return qs


@admin_session_required
def get_notifications(request):
    count = Notification.objects.filter(is_read=False).count()
    return JsonResponse({'count': count})


@method_decorator(permission_required('settings', 'can_view'), name='dispatch')
class SettingsView(AdminSessionMixin, TemplateView):
    template_name = 'admin/settings.html'

    def get_context_data(self, **kwargs):
        from django.conf import settings
        context = super().get_context_data(**kwargs)
        context['total_movies'] = Movie.objects.count()
        context['total_theatres'] = Theater.objects.values('name').distinct().count()
        context['total_screens'] = Screen.objects.count()
        context['total_staff'] = AdminProfile.objects.count()
        context['django_version'] = django.get_version()
        context['python_version'] = sys.version
        context['database_type'] = settings.DATABASES['default']['ENGINE'].split('.')[-1]
        context['server_time'] = timezone.now()
        context['session_timeout'] = settings.ADMIN_SESSION_TIMEOUT // 60 if hasattr(settings, 'ADMIN_SESSION_TIMEOUT') else 60
        context['admin_count'] = AdminProfile.objects.filter(role='super_admin').count() + AdminProfile.objects.filter(role='admin').count()
        context['staff_count'] = AdminProfile.objects.filter(role='staff').count()
        context['debug'] = settings.DEBUG
        return context


@admin_session_required
def profile_view(request):
    profile, _ = AdminProfile.objects.get_or_create(
        user=request.user,
        defaults={'role': 'staff'}
    )
    if request.method == 'POST':
        user_form = AdminUserSelfEditForm(request.POST, instance=request.user)
        profile_form = AdminProfileSelfEditForm(request.POST, instance=profile)
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            messages.success(request, 'Profile updated successfully.')
            return redirect('admin_profile')
        form = profile_form
    else:
        user_form = AdminUserSelfEditForm(instance=request.user)
        form = AdminProfileSelfEditForm(instance=profile)
    return render(request, 'admin/profile.html', {
        'form': form,
        'user_form': user_form,
        'profile': profile,
    })


@admin_session_required
@permission_required('settings', 'can_view')
def pricing_dashboard(request):
    categories = list(SeatCategory.objects.all())
    shows = Theater.objects.select_related('movie').order_by('-time')
    movie_id = request.GET.get('movie')
    search = request.GET.get('search')
    if movie_id:
        shows = shows.filter(movie_id=movie_id)
    if search:
        shows = shows.filter(Q(name__icontains=search) | Q(movie__name__icontains=search))

    per_page = 20
    paginator = Paginator(shows, per_page)
    page_num = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_num)
    except Exception:
        page_obj = paginator.page(1)

    page_ids = [s.id for s in page_obj]
    price_map = {}
    for sp in ShowPrice.objects.filter(theater_id__in=page_ids):
        price_map.setdefault(sp.theater_id, {})[sp.category_id] = sp.price

    counts = (
        Theater.objects.filter(pk__in=page_ids)
        .annotate(_total=Count('seats'), _avail=Count('seats', filter=Q(seats__is_booked=False)))
        .values_list('pk', '_total', '_avail')
    )
    count_map = {pk: (total, avail) for pk, total, avail in counts}
    for s in page_obj:
        total, avail = count_map.get(s.id, (0, 0))
        s.total_seats = total
        s.available_seats = avail
        s.booked_seats = total - avail

    config, _ = PricingConfig.objects.get_or_create(pk=1)
    return render(request, 'admin/pricing/pricing_dashboard.html', {
        'shows': page_obj,
        'page_obj': page_obj,
        'categories': categories,
        'price_map': price_map,
        'config': config,
        'slabs': GSTSlab.objects.all().order_by('display_order'),
        'movies': Movie.objects.filter(is_deleted=False),
    })


@admin_session_required
@permission_required('settings', 'can_edit')
def pricing_show_edit(request, pk):
    show = get_object_or_404(Theater.objects.select_related('movie'), id=pk)
    categories = SeatCategory.objects.all()

    if request.method == 'POST':
        updates = {}
        valid = True
        for category in categories:
            raw = request.POST.get('price_{}'.format(category.id), '').strip()
            try:
                value = Decimal(raw)
            except (TypeError, ValueError, InvalidOperation):
                messages.error(request, 'Enter a valid price for {}.'.format(category.name))
                valid = False
                continue
            if value < 0:
                messages.error(request, 'Price for {} cannot be negative.'.format(category.name))
                valid = False
                continue
            updates[category.id] = value

        if valid:
            if not updates:
                messages.error(request, 'Enter at least one valid price.')
                return redirect('admin_pricing_show_edit', pk=show.pk)
            for category in categories:
                if category.id in updates:
                    ShowPrice.objects.update_or_create(
                        theater=show,
                        category=category,
                        defaults={'price': updates[category.id]},
                    )
            Theater.objects.filter(pk=show.pk).update(ticket_price=min(updates.values()))
            AuditLog.objects.create(
                user=request.user,
                action='Show Pricing Updated',
                module='Show',
                object_id=show.id,
                details='Updated seat-category pricing for {} at {} ({})'.format(
                    show.movie.name, show.name, show.time.strftime('%d %b, %I:%M %p')
                ),
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(
                request,
                'Pricing saved. Changes apply to new bookings only; existing tickets are unchanged.'
            )
            return redirect('admin_pricing_show_edit', pk=show.pk)

    prices = {sp.category_id: sp.price for sp in ShowPrice.objects.filter(theater=show)}
    return render(request, 'admin/pricing/show_pricing.html', {
        'show': show,
        'categories': categories,
        'prices': prices,
    })


@admin_session_required
@permission_required('settings', 'can_edit')
def pricing_config(request):
    config, _ = PricingConfig.objects.get_or_create(pk=1)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'save_fees':
            try:
                platform = Decimal(request.POST.get('platform_fee_per_ticket', ''))
                misc = Decimal(request.POST.get('misc_fee_per_booking', ''))
            except (TypeError, ValueError, InvalidOperation):
                messages.error(request, 'Fees must be valid amounts.')
            else:
                if platform < 0 or misc < 0:
                    messages.error(request, 'Fees cannot be negative.')
                else:
                    config.platform_fee_per_ticket = platform
                    config.misc_fee_per_booking = misc
                    config.save()
                    AuditLog.objects.create(
                        user=request.user,
                        action='Fees Updated',
                        module='Pricing',
                        details='Platform fee \u20b9{}, misc fee \u20b9{}'.format(platform, misc),
                        ip_address=request.META.get('REMOTE_ADDR')
                    )
                    messages.success(request, 'Fee configuration saved.')

        elif action == 'save_slabs':
            ids = request.POST.getlist('slab_id')
            mins = request.POST.getlist('slab_min')
            maxs = request.POST.getlist('slab_max')
            rates = request.POST.getlist('slab_rate')
            orders = request.POST.getlist('slab_order')
            parsed = []
            ok = True
            for i in range(min(len(ids), len(mins), len(maxs), len(rates), len(orders))):
                try:
                    min_v = Decimal(mins[i])
                    rate = Decimal(rates[i])
                    max_raw = (maxs[i] or '').strip()
                    max_v = Decimal(max_raw) if max_raw else None
                except (TypeError, ValueError, InvalidOperation):
                    messages.error(request, 'Each slab needs a valid minimum, maximum, and rate.')
                    ok = False
                    break
                if min_v < 0 or rate < 0 or (max_v is not None and max_v < min_v):
                    messages.error(request, 'Invalid slab range or rate.')
                    ok = False
                    break
                order = int(orders[i]) if (orders[i] or '').isdigit() else 0
                parsed.append((ids[i], min_v, max_v, rate, order))
            if ok:
                kept_ids = [int(p[0]) for p in parsed if p[0].isdigit()]
                GSTSlab.objects.exclude(id__in=kept_ids).delete()
                for raw_id, min_v, max_v, rate, order in parsed:
                    if raw_id.isdigit():
                        GSTSlab.objects.filter(id=int(raw_id)).update(
                            min_amount=min_v, max_amount=max_v, rate=rate, display_order=order
                        )
                    else:
                        GSTSlab.objects.create(
                            min_amount=min_v, max_amount=max_v, rate=rate, display_order=order
                        )
                AuditLog.objects.create(
                    user=request.user,
                    action='GST Slabs Updated',
                    module='Pricing',
                    details='Saved {} GST slab(s)'.format(len(parsed)),
                    ip_address=request.META.get('REMOTE_ADDR')
                )
                messages.success(request, 'GST slabs saved.')

        elif action == 'add_category':
            name = request.POST.get('name', '').strip().upper()
            row_start = request.POST.get('row_start', '').strip().upper()
            row_end = request.POST.get('row_end', '').strip().upper()
            if not name or not row_start or not row_end:
                messages.error(request, 'Category name and row range are required.')
            else:
                try:
                    SeatCategory.objects.create(
                        name=name,
                        row_start=row_start,
                        row_end=row_end,
                        display_order=SeatCategory.objects.count(),
                    )
                    AuditLog.objects.create(
                        user=request.user,
                        action='Seat Category Added',
                        module='Pricing',
                        details='Added category {} (rows {}-{})'.format(name, row_start, row_end),
                        ip_address=request.META.get('REMOTE_ADDR')
                    )
                    messages.success(request, 'Seat category "{}" added.'.format(name))
                except IntegrityError:
                    messages.error(request, 'A category with that name already exists.')

        return redirect('admin_pricing_config')

    return render(request, 'admin/pricing/config.html', {
        'config': config,
        'slabs': GSTSlab.objects.all().order_by('display_order'),
        'categories': SeatCategory.objects.all(),
    })
