"""Views for the Admin Analytics dashboard.

Every view enforces the admin session and the ``analytics`` module permission
(reusing the existing ``AdminSessionMixin`` / ``permission_required`` pattern),
returns HTTP 403 for unauthorized access and validates every request parameter.
"""
from datetime import date, datetime

from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from admin_panel.analytics import services as analytics
from admin_panel.decorators import AdminSessionMixin, admin_session_required, permission_required

AREA_TITLES = {
    'overview': 'Analytics Overview',
    'revenue': 'Revenue Analytics',
    'bookings': 'Booking Analytics',
    'occupancy': 'Occupancy Analytics',
    'movies': 'Movie Analytics',
    'theaters': 'Theater Analytics',
    'peak': 'Peak Booking Analytics',
    'payments': 'Payment Analytics',
    'refunds': 'Refund Analytics',
    'users': 'User Analytics',
}

DATA_FUNCS = {
    'overview': analytics.overview_data,
    'revenue': analytics.revenue_data,
    'bookings': analytics.bookings_data,
    'occupancy': analytics.occupancy_data,
    'movies': analytics.movies_data,
    'theaters': analytics.theaters_data,
    'peak': analytics.peak_data,
    'payments': analytics.payments_data,
    'refunds': analytics.refunds_data,
    'users': analytics.users_data,
}

PALETTE = ['#dc2626', '#2563eb', '#d97706', '#16a34a', '#9333ea', '#0891b2', '#db2777', '#65a30d']

ANALYTICS_CACHE_TTL = 300


def _analytics_cache_key(area, rng):
    return f'analytics:{area}:{rng.start_date.isoformat()}:{rng.end_date.isoformat()}'


def _load_data(area, rng):
    """Return the analytics payload for an area/range, cached for 5 minutes."""
    key = _analytics_cache_key(area, rng)
    data = cache.get(key)
    if data is None:
        data = DATA_FUNCS[area](rng)
        cache.set(key, data, ANALYTICS_CACHE_TTL)
    return data


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def resolve_range(request):
    range_key = request.GET.get('range', 'last_30_days')
    valid = {key for key, _ in analytics.RANGE_PRESETS}
    if range_key not in valid:
        range_key = 'last_30_days'
    return analytics.resolve_range(
        range_key,
        _parse_date(request.GET.get('start_date')),
        _parse_date(request.GET.get('end_date')),
    )


def _build_charts(area, data):
    """Turn an analytics data dict into render-ready Chart.js specs."""
    charts = {}
    if area == 'overview':
        charts['revenue_trend'] = _line('Revenue', data['revenue_series'], '#16a34a')
        charts['bookings_trend'] = _bar('Bookings', data['bookings_series'], '#dc2626')
    elif area == 'revenue':
        charts['revenue_trend'] = _line('Revenue', data['series'], '#16a34a')
        charts['revenue_by_method'] = _doughnut(
            'Revenue by method', [m['key'] for m in data['by_method']],
            [m['amount'] for m in data['by_method']])
    elif area == 'bookings':
        charts['bookings_trend'] = _bar('Bookings', data['series'], '#2563eb')
        charts['bookings_by_status'] = _doughnut(
            'Bookings by status', [s['key'] for s in data['statuses']],
            [s['count'] for s in data['statuses']])
        charts['bookings_by_weekday'] = _bar(
            'Bookings by weekday', {'labels': data['weekday']['labels'], 'values': data['weekday']['values']},
            '#9333ea')
        charts['bookings_by_hour'] = _bar(
            'Bookings by hour', {'labels': data['hour']['labels'], 'values': data['hour']['values']},
            '#0891b2')
    elif area == 'occupancy':
        charts['occupancy_by_theater'] = _hbar(
            'Occupancy by theater',
            [t['name'] for t in data['per_theater']],
            [t['rate'] for t in data['per_theater']],
            '#d97706')
    elif area == 'movies':
        charts['movies_by_revenue'] = _hbar(
            'Top movies by revenue',
            [m['name'] for m in data['top_by_revenue']],
            [m['revenue'] for m in data['top_by_revenue']],
            '#dc2626')
        charts['movies_by_bookings'] = _hbar(
            'Top movies by bookings',
            [m['name'] for m in data['top_by_bookings']],
            [m['bookings'] for m in data['top_by_bookings']],
            '#2563eb')
    elif area == 'theaters':
        charts['theaters_by_revenue'] = _hbar(
            'Top theaters by revenue',
            [t['name'] for t in data['theaters']],
            [t['revenue'] for t in data['theaters']],
            '#16a34a')
    elif area == 'peak':
        charts['peak_hour'] = _line(
            'Bookings by hour', {'labels': data['hour']['labels'], 'values': data['hour']['values']},
            '#db2777')
        charts['peak_weekday'] = _bar(
            'Bookings by weekday', {'labels': data['weekday']['labels'], 'values': data['weekday']['values']},
            '#d97706')
        charts['peak_heatmap'] = {
            'type': 'heatmap',
            'weekdays': data['matrix']['weekdays'],
            'matrix': data['matrix']['matrix'],
        }
    elif area == 'payments':
        charts['payments_by_status'] = _doughnut(
            'Payment status', [s['key'] for s in data['payment_statuses']],
            [s['count'] for s in data['payment_statuses']])
        charts['tx_by_status'] = _doughnut(
            'Transaction status', [s['key'] for s in data['tx_statuses']],
            [s['count'] for s in data['tx_statuses']])
        charts['payments_by_method'] = _bar(
            'Payments by method', {'labels': [m['key'] for m in data['methods']],
                                   'values': [m['count'] for m in data['methods']]},
            '#0891b2')
        charts['captured_trend'] = _line('Captured payments', data['series'], '#16a34a')
    elif area == 'refunds':
        charts['refund_trend'] = _line('Refund amount', data['series'], '#dc2626')
        charts['refund_by_status'] = _doughnut(
            'Refunds by status', [s['key'] for s in data['statuses']],
            [s['count'] for s in data['statuses']])
    elif area == 'users':
        charts['users_trend'] = _bar('New users', data['series'], '#2563eb')
    return charts


def _line(label, series, color):
    return {
        'type': 'line', 'labels': series['labels'],
        'datasets': [{'label': label, 'data': series['values'], 'borderColor': color,
                      'backgroundColor': color + '22', 'fill': True, 'tension': 0.3}],
    }


def _bar(label, series, color):
    return {
        'type': 'bar', 'labels': series['labels'],
        'datasets': [{'label': label, 'data': series['values'], 'backgroundColor': color}],
    }


def _hbar(label, labels, values, color):
    return {
        'type': 'bar', 'labels': labels, 'indexAxis': 'y',
        'datasets': [{'label': label, 'data': values, 'backgroundColor': color}],
    }


def _doughnut(label, labels, values):
    colors = [PALETTE[i % len(PALETTE)] for i in range(len(labels))]
    return {
        'type': 'doughnut', 'labels': labels,
        'datasets': [{'label': label, 'data': values, 'backgroundColor': colors}],
    }


def _build_tables(area, data):
    """Return ``{table_key: {columns: [...], rows: [[...], ...]}}`` for AJAX refresh.

    Rows are column-aligned arrays so the server-rendered table and the
    JavaScript rebuild always stay in sync.
    """
    tables = {}
    if area == 'overview':
        tables['recent_bookings'] = {
            'columns': ['ID', 'Reference', 'User', 'Movie', 'Theater', 'Status', 'Total', 'Booked at'],
            'rows': [[b['id'], b['booking_ref'], b['user'], b['movie'], b['theater'],
                      b['status'], b['total'], b['booked_at']] for b in data['recent_bookings']],
        }
    elif area == 'bookings':
        tables['bookings_status'] = {
            'columns': ['Status', 'Count'],
            'rows': [[s['key'], s['count']] for s in data['statuses']],
        }
    elif area == 'occupancy':
        tables['per_theater'] = {
            'columns': ['Theater', 'Movie', 'Total seats', 'Booked seats', 'Occupancy'],
            'rows': [[t['name'], t['movie'], t['total_seats'], t['booked_seats'],
                      f"{t['rate']}%"] for t in data['per_theater']],
        }
    elif area == 'movies':
        tables['top_by_revenue'] = {
            'columns': ['Movie', 'Bookings', 'Revenue', 'Share'],
            'rows': [[m['name'], m['bookings'], f"{m['revenue']:,.2f}", f"{m['share']}%"]
                     for m in data['top_by_revenue']],
        }
        tables['top_by_bookings'] = {
            'columns': ['Movie', 'Bookings', 'Revenue', 'Share'],
            'rows': [[m['name'], m['bookings'], f"{m['revenue']:,.2f}", f"{m['share']}%"]
                     for m in data['top_by_bookings']],
        }
    elif area == 'theaters':
        tables['theaters'] = {
            'columns': ['Theater', 'Movie', 'Bookings', 'Revenue'],
            'rows': [[t['name'], t['movie'], t['bookings'], f"{t['revenue']:,.2f}"]
                     for t in data['theaters']],
        }
    elif area == 'refunds':
        tables['refund_details'] = {
            'columns': ['ID', 'Order ID', 'User', 'Reservation', 'Method', 'Status', 'Amount', 'Date'],
            'rows': [[t['id'], t['gateway_order_id'], t['user'], t['reservation'], t['method'],
                      t['status'], t['amount'], t['created_at']] for t in data['details']],
        }
    elif area == 'users':
        tables['top_users'] = {
            'columns': ['User', 'Email', 'Joined', 'Bookings', 'Spend', 'Last booking'],
            'rows': [[u['username'], u['email'], u['date_joined'], u['bookings'],
                      f"{u['spend']:,.2f}", u['last_booking']] for u in data['top']],
        }
    return tables


@method_decorator(
    [admin_session_required, permission_required('analytics', 'can_view')],
    name='dispatch',
)
class AnalyticsBaseView(AdminSessionMixin, TemplateView):
    active_section = 'overview'
    area_title = ''

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rng = resolve_range(self.request)
        data = _load_data(self.active_section, rng)
        context['analytics_section'] = self.active_section
        context['area_title'] = self.area_title or AREA_TITLES[self.active_section]
        context['range'] = rng
        context['range_key'] = self.request.GET.get('range', 'last_30_days')
        context['range_presets'] = analytics.RANGE_PRESETS
        context['start_date'] = rng.start_date.isoformat()
        context['end_date'] = rng.end_date.isoformat()
        context['data'] = data
        context['charts'] = _build_charts(self.active_section, data)
        context['tables'] = _build_tables(self.active_section, data)
        context['analytics_json'] = {
            'section': self.active_section,
            'charts': context['charts'],
            'tables': context['tables'],
        }
        return context


class OverviewView(AnalyticsBaseView):
    template_name = 'admin/analytics/overview.html'
    active_section = 'overview'


class RevenueView(AnalyticsBaseView):
    template_name = 'admin/analytics/revenue.html'
    active_section = 'revenue'


class BookingsView(AnalyticsBaseView):
    template_name = 'admin/analytics/bookings.html'
    active_section = 'bookings'


class OccupancyView(AnalyticsBaseView):
    template_name = 'admin/analytics/occupancy.html'
    active_section = 'occupancy'


class MoviesView(AnalyticsBaseView):
    template_name = 'admin/analytics/movies.html'
    active_section = 'movies'


class TheatersView(AnalyticsBaseView):
    template_name = 'admin/analytics/theaters.html'
    active_section = 'theaters'


class PeakView(AnalyticsBaseView):
    template_name = 'admin/analytics/peak.html'
    active_section = 'peak'


class PaymentsView(AnalyticsBaseView):
    template_name = 'admin/analytics/payments.html'
    active_section = 'payments'


class RefundsView(AnalyticsBaseView):
    template_name = 'admin/analytics/refunds.html'
    active_section = 'refunds'


class UsersView(AnalyticsBaseView):
    template_name = 'admin/analytics/users.html'
    active_section = 'users'


@admin_session_required
@permission_required('analytics', 'can_view')
def analytics_data_json(request, area):
    """JSON payload for a range used by the AJAX refresh on every analytics page."""
    if area not in DATA_FUNCS:
        return JsonResponse({'error': 'Unknown analytics area.'}, status=400)
    rng = resolve_range(request)
    data = dict(_load_data(area, rng))
    data['range'] = {
        'label': rng.label,
        'start': rng.start_date.isoformat(),
        'end': rng.end_date.isoformat(),
    }
    data['charts'] = _build_charts(area, data)
    data['tables'] = _build_tables(area, data)
    return JsonResponse(data)


@admin_session_required
@permission_required('analytics', 'can_view')
def analytics_export(request, area):
    """Export the given analytics area as CSV, XLSX or a print-friendly PDF."""
    if area not in AREA_TITLES:
        messages.error(request, 'Unknown export area.')
        return redirect('admin_analytics_overview')

    rng = resolve_range(request)
    fmt = request.GET.get('format', 'csv')
    if fmt not in ('csv', 'xlsx', 'pdf'):
        fmt = 'csv'
    sections = analytics.export_sections(area, rng)

    if fmt == 'pdf':
        return render(request, 'admin/analytics/report_pdf.html', {
            'sections': sections,
            'area_title': AREA_TITLES[area],
            'range': rng,
            'generated_at': timezone.now(),
        })

    if fmt == 'xlsx':
        content = analytics.xlsx_bytes(sections)
        content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        extension = 'xlsx'
    else:
        content = analytics.csv_bytes(sections)
        content_type = 'text/csv; charset=utf-8'
        extension = 'csv'

    filename = f'analytics_{area}_{rng.start_date.isoformat()}_to_{rng.end_date.isoformat()}.{extension}'
    response = HttpResponse(content, content_type=content_type)
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
