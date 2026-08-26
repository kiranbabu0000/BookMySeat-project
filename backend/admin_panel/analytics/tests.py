import json
import re
import zipfile
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from admin_panel.analytics import services as analytics
from admin_panel.models import AdminPermission, AdminProfile, Payment, PaymentTransaction
from movies.models import Booking, Movie, Reservation, Seat, Theater
from movies.services import generate_booking_ref


def _at(days_ago, hour=14, minute=0):
    """A timezone-aware datetime ``days_ago`` days back at a fixed time."""
    base = timezone.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return base - timedelta(days=days_ago)


def _backdate(model, pk, **fields):
    model.objects.filter(pk=pk).update(**fields)


class AnalyticsDataTestCase(TestCase):
    """Shared seeded dataset for the analytics service functions."""

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.now().date()
        cls.alice = User.objects.create_user('alice', 'alice@example.com', 'password123')
        cls.bob = User.objects.create_user('bob', 'bob@example.com', 'password123')
        cls.carol = User.objects.create_user('carol', 'carol@example.com', 'password123')
        for u in (cls.alice, cls.bob):
            _backdate(User, u.pk, date_joined=_at(10))
        _backdate(User, cls.carol.pk, date_joined=_at(0, hour=9))

        cls.movie1 = Movie.objects.create(name='Analytics Movie One', rating=7.5, status='now_showing')
        cls.movie2 = Movie.objects.create(name='Analytics Movie Two', rating=8.0, status='coming_soon')

        cls.theater1 = Theater.objects.create(
            name='Analytics PVR', movie=cls.movie1,
            time=_at(0, hour=5),
        )
        cls.theater2 = Theater.objects.create(
            name='Analytics IMAX', movie=cls.movie2,
            time=_at(0, hour=6),
        )

        seats = [
            Seat.objects.create(theater=cls.theater1, seat_number=f'A{i}')
            for i in range(1, 4)
        ]
        cls.seat4 = Seat.objects.create(theater=cls.theater2, seat_number='B1')

        cls.res1 = Reservation.objects.create(
            token='an-tok-1', user=cls.alice, show=cls.theater1,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        cls.res2 = Reservation.objects.create(
            token='an-tok-2', user=cls.bob, show=cls.theater1,
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        def make_booking(user, seat, movie, theater, total, status, days_ago, hour=14):
            b = Booking.objects.create(
                user=user, seat=seat, movie=movie, theater=theater, status=status,
                booking_ref=generate_booking_ref(), total=Decimal(total),
                ticket_price=Decimal(total), gst_rate=Decimal('18.00'),
            )
            _backdate(Booking, b.pk, booked_at=_at(days_ago, hour=hour))
            return b

        cls.b_prev = make_booking(cls.alice, seats[0], cls.movie1, cls.theater1,
                                  '500.00', 'confirmed', 10)
        cls.b1 = make_booking(cls.alice, seats[1], cls.movie1, cls.theater1,
                              '250.00', 'confirmed', 1)
        cls.b2 = make_booking(cls.alice, seats[2], cls.movie1, cls.theater1,
                              '320.00', 'confirmed', 2)
        cls.b3 = make_booking(cls.bob, cls.seat4, cls.movie2, cls.theater2,
                              '180.00', 'cancelled', 3)

        def make_payment(booking, amount, status, method, days_ago):
            p = Payment.objects.create(
                booking=booking, amount=Decimal(amount), status=status, payment_method=method,
            )
            _backdate(Payment, p.pk, paid_at=_at(days_ago))
            return p

        cls.p_prev = make_payment(cls.b_prev, '500.00', 'completed', 'online', 10)
        cls.p1 = make_payment(cls.b1, '250.00', 'completed', 'online', 1)
        cls.p2 = make_payment(cls.b2, '320.00', 'completed', 'upi', 2)
        cls.p3 = make_payment(cls.b3, '180.00', 'refunded', 'online', 3)

        def make_tx(user, reservation, amount, status, days_ago, **extra):
            t = PaymentTransaction.objects.create(
                reservation=reservation, user=user, amount=Decimal(amount), status=status, **extra
            )
            _backdate(PaymentTransaction, t.pk, created_at=_at(days_ago, hour=16))
            return t

        cls.tx1 = make_tx(cls.alice, cls.res1, '250.00', 'captured', 1, method='card')
        cls.tx2 = make_tx(cls.alice, cls.res1, '250.00', 'failed', 1, method='card')
        cls.tx3 = make_tx(cls.bob, cls.res2, '180.00', 'refunded', 2,
                          method='upi', refund_id='ref-1')

        cls.rng = analytics.resolve_range('last_7_days')


class ResolveRangeTests(TestCase):
    def test_defaults_to_last_30_days(self):
        rng = analytics.resolve_range('bogus_key')
        self.assertEqual(rng.span_days, 30)
        self.assertEqual(rng.label, 'Last 30 Days')

    def test_today_window_is_single_day(self):
        rng = analytics.resolve_range('today')
        self.assertEqual(rng.span_days, 1)
        self.assertEqual(rng.label, 'Today')

    def test_custom_range_swaps_and_labels(self):
        rng = analytics.resolve_range('custom', date(2025, 1, 10), date(2025, 1, 1))
        self.assertEqual(rng.span_days, 10)
        self.assertEqual(rng.start.date(), date(2025, 1, 1))
        self.assertEqual(rng.end.date(), date(2025, 1, 11))
        self.assertEqual(rng.prev_end, rng.start)
        self.assertEqual(rng.prev_start.date(), date(2024, 12, 22))
        self.assertEqual(rng.label, 'Custom (01 Jan 2025 - 10 Jan 2025)')

    def test_custom_without_dates_falls_back(self):
        rng = analytics.resolve_range('custom', None, None)
        self.assertEqual(rng.label, 'Last 30 Days')

    def test_choose_granularity(self):
        self.assertEqual(analytics.choose_granularity(analytics.resolve_range('last_7_days')), 'day')
        self.assertEqual(analytics.choose_granularity(analytics.resolve_range('previous_year')), 'month')
        long_range = analytics.DateRange(
            label='x', start=date(2021, 1, 1), end=date(2024, 1, 1),
            prev_start=date(2018, 1, 1), prev_end=date(2021, 1, 1),
        )
        self.assertEqual(analytics.choose_granularity(long_range), 'year')


class SummaryMetricsTests(AnalyticsDataTestCase):
    def test_summary_metrics_with_seeded_data(self):
        s = analytics.summary_metrics(self.rng)
        self.assertEqual(s['revenue'], Decimal('570.00'))
        self.assertEqual(s['revenue_fmt'], '\u20b9570.00')
        self.assertEqual(s['bookings'], 3)
        self.assertEqual(s['confirmed'], 2)
        self.assertEqual(s['tickets'], 2)
        self.assertEqual(s['cancelled'], 1)
        self.assertEqual(s['cancelled_value'], Decimal('180.00'))
        self.assertEqual(s['aov'], Decimal('285.00'))
        self.assertEqual(s['refunds'], 1)
        self.assertEqual(s['refund_amount'], Decimal('180.00'))
        self.assertEqual(s['new_users'], 1)

    def test_period_over_period_changes(self):
        s = analytics.summary_metrics(self.rng)
        self.assertEqual(s['revenue_change'], 14.0)
        self.assertEqual(s['bookings_change'], 200.0)
        self.assertEqual(s['cancelled_change'], None)
        self.assertEqual(s['new_users_change'], -50.0)


class AreaDataTests(AnalyticsDataTestCase):
    def test_overview_recent_bookings(self):
        data = analytics.overview_data(self.rng)
        self.assertEqual(len(data['revenue_series']['labels']), 7)
        self.assertEqual(len(data['revenue_series']['values']), 7)
        self.assertEqual(data['revenue_series']['values'][4], 320.0)
        self.assertEqual(data['revenue_series']['values'][5], 250.0)
        self.assertEqual(sum(data['revenue_series']['values']), 570.0)
        recent = data['recent_bookings']
        self.assertEqual(len(recent), 3)
        self.assertEqual(recent[0]['booking_ref'], self.b1.booking_ref)

    def test_revenue_data_components_and_methods(self):
        data = analytics.revenue_data(self.rng)
        methods = {m['key']: m for m in data['by_method']}
        self.assertEqual(methods['online']['amount'], 250.0)
        self.assertEqual(methods['upi']['amount'], 320.0)
        self.assertEqual(data['components']['total'], 570.0)
        self.assertEqual(data['components']['gst'], 0.0)

    def test_bookings_data_distribution(self):
        data = analytics.bookings_data(self.rng)
        statuses = {s['key']: s['count'] for s in data['statuses']}
        self.assertEqual(statuses['confirmed'], 2)
        self.assertEqual(statuses['cancelled'], 1)
        self.assertEqual(data['total'], 3)
        self.assertEqual(data['cancelled_rate'], 33.3)
        self.assertEqual(len(data['weekday']['values']), 7)
        self.assertEqual(len(data['hour']['values']), 24)
        self.assertEqual(sum(data['weekday']['values']), 3)

    def test_occupancy_data_rates(self):
        data = analytics.occupancy_data(self.rng)
        self.assertEqual(data['shows'], 2)
        self.assertEqual(data['total_seats'], 4)
        self.assertEqual(data['booked_seats'], 2)
        self.assertEqual(data['occupancy_rate'], 50.0)
        by_name = {t['name']: t for t in data['per_theater']}
        self.assertEqual(by_name['Analytics PVR']['booked_seats'], 2)
        self.assertEqual(by_name['Analytics PVR']['rate'], 66.7)
        self.assertEqual(by_name['Analytics IMAX']['rate'], 0.0)

    def test_movies_data_ranking(self):
        data = analytics.movies_data(self.rng)
        self.assertEqual(data['top_by_revenue'][0]['name'], 'Analytics Movie One')
        self.assertEqual(data['top_by_revenue'][0]['revenue'], 570.0)
        self.assertEqual(data['top_by_revenue'][0]['bookings'], 2)
        self.assertEqual(data['total_revenue'], 570.0)
        self.assertEqual(data['movies_with_bookings'], 1)
        self.assertEqual(data['active_movies'], 1)
        self.assertEqual(data['upcoming_movies'], 1)

    def test_theaters_data(self):
        data = analytics.theaters_data(self.rng)
        self.assertEqual(data['total_shows'], 2)
        self.assertEqual(data['total_theaters'], 2)
        self.assertEqual(data['avg_revenue_show'], 285.0)
        top = {t['name']: t for t in data['theaters']}
        self.assertEqual(top['Analytics PVR']['revenue'], 570.0)
        self.assertEqual(top['Analytics IMAX']['revenue'], 0.0)

    def test_peak_data_heatmap_and_distributions(self):
        data = analytics.peak_data(self.rng)
        self.assertEqual(len(data['matrix']['weekdays']), 7)
        self.assertEqual(len(data['matrix']['matrix']), 7)
        self.assertEqual(len(data['matrix']['matrix'][0]), 24)
        self.assertEqual(sum(data['hour']['values']), 2)
        self.assertEqual(sum(data['weekday']['values']), 2)
        self.assertGreaterEqual(data['peak_hour_count'], 1)

    def test_payments_data_success_rate(self):
        data = analytics.payments_data(self.rng)
        self.assertEqual(data['total_transactions'], 3)
        self.assertEqual(data['captured'], 1)
        self.assertEqual(data['failed'], 1)
        self.assertEqual(data['success_rate'], 33.3)
        self.assertEqual(data['failure_rate'], 33.3)
        methods = {m['key']: m['count'] for m in data['methods']}
        self.assertEqual(methods['upi'], 1)

    def test_refunds_data(self):
        data = analytics.refunds_data(self.rng)
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['amount'], 180.0)
        self.assertEqual(data['rate'], 72.0)
        self.assertEqual(data['avg_fmt'], '\u20b9180.00')
        self.assertEqual(data['details'][0]['id'], self.tx3.id)

    def test_users_data_ranking(self):
        data = analytics.users_data(self.rng)
        self.assertEqual(data['new_in_range'], 1)
        self.assertEqual(data['total_users'], 3)
        top = data['top']
        self.assertEqual(top[0]['username'], 'alice')
        self.assertEqual(top[0]['bookings'], 2)
        self.assertEqual(top[0]['spend'], 570.0)

    def test_all_areas_safe_on_empty_range(self):
        rng = analytics.resolve_range('today', date(2000, 1, 1), date(2000, 1, 1))
        funcs = [
            analytics.overview_data, analytics.revenue_data, analytics.bookings_data,
            analytics.occupancy_data, analytics.movies_data, analytics.theaters_data,
            analytics.peak_data, analytics.payments_data, analytics.refunds_data,
            analytics.users_data,
        ]
        for fn in funcs:
            data = fn(rng)
            self.assertIsInstance(data, dict)
        self.assertEqual(analytics.summary_metrics(rng)['revenue'], Decimal('0.00'))
        self.assertEqual(analytics.summary_metrics(rng)['bookings'], 0)


class ExportTests(AnalyticsDataTestCase):
    def test_csv_bytes_structure(self):
        sections = analytics.export_sections('overview', self.rng)
        self.assertEqual(len(sections), 3)
        raw = analytics.csv_bytes(sections)
        self.assertTrue(raw.startswith('\ufeff'.encode('utf-8')))
        text = raw.decode('utf-8-sig')
        self.assertIn('Summary', text)
        self.assertIn('Metric', text)
        self.assertIn('\u20b9570.00', text)

    def test_export_sections_all_areas(self):
        for area in ('overview', 'revenue', 'bookings', 'occupancy', 'movies',
                     'theaters', 'peak', 'payments', 'refunds', 'users'):
            sections = analytics.export_sections(area, self.rng)
            self.assertTrue(sections, f'no sections for {area}')
            for title, headers, rows in sections:
                self.assertTrue(title)
                self.assertIsInstance(headers, list)
                self.assertIsInstance(rows, list)

    def test_xlsx_bytes_valid_zip(self):
        raw = analytics.xlsx_bytes(analytics.export_sections('revenue', self.rng))
        with zipfile.ZipFile(__import__('io').BytesIO(raw)) as zf:
            names = zf.namelist()
        self.assertIn('[Content_Types].xml', names)
        self.assertIn('xl/workbook.xml', names)
        self.assertTrue(any(n.startswith('xl/worksheets/') for n in names))
        workbook = [n for n in names if n.startswith('xl/worksheets/')]
        self.assertEqual(len(workbook), 3)

    def test_xlsx_empty_sections(self):
        raw = analytics.xlsx_bytes([])
        self.assertTrue(raw.startswith(b'PK'))


class AnalyticsViewTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            'rootadmin', 'root@example.com', 'password123'
        )
        self.customer = User.objects.create_user('cust', 'cust@example.com', 'password123')

    def _admin_session(self, client, user):
        from movies.testutils import set_admin_session
        set_admin_session(client, user)

    def test_anonymous_redirected_to_admin_login(self):
        for url in ('/analytics/', '/analytics/revenue/', '/analytics/data/overview/'):
            response = self.client.get(url)
            self.assertRedirects(response, '/admin-login/', fetch_redirect_response=False)

    def test_customer_redirected_to_admin_login(self):
        self.client.force_login(self.customer)
        response = self.client.get('/analytics/')
        self.assertRedirects(response, '/admin-login/', fetch_redirect_response=False)

    def test_staff_without_profile_redirected(self):
        staff = User.objects.create_user('noprofile', 'noprofile@example.com', 'password123')
        staff.is_staff = True
        staff.save()
        client = self.client.__class__()
        self._admin_session(client, staff)
        response = client.get('/analytics/')
        self.assertRedirects(response, '/admin-login/', fetch_redirect_response=False)

    def test_staff_without_permission_redirected_to_dashboard(self):
        staff = User.objects.create_user('limited', 'limited@example.com', 'password123')
        profile = AdminProfile.objects.create(user=staff, role='staff', is_active=True)
        AdminPermission.objects.create(admin_profile=profile, module='payments', can_view=True)
        client = self.client.__class__()
        self._admin_session(client, staff)
        response = client.get('/analytics/')
        self.assertRedirects(response, reverse('admin_dashboard'), fetch_redirect_response=False)
        response = client.get('/analytics/export/revenue/?format=csv')
        self.assertRedirects(response, reverse('admin_dashboard'), fetch_redirect_response=False)

    def test_staff_admin_role_can_view(self):
        staff = User.objects.create_user('manager', 'manager@example.com', 'password123')
        AdminProfile.objects.create(user=staff, role='admin', is_active=True)
        client = self.client.__class__()
        self._admin_session(client, staff)
        response = client.get('/analytics/')
        self.assertEqual(response.status_code, 200)

    def test_superuser_all_pages_render(self):
        client = self.client.__class__()
        self._admin_session(client, self.superuser)
        urls = {
            'overview': '/analytics/',
            'revenue': '/analytics/revenue/',
            'bookings': '/analytics/bookings/',
            'occupancy': '/analytics/occupancy/',
            'movies': '/analytics/movies/',
            'theaters': '/analytics/theaters/',
            'peak': '/analytics/peak/',
            'payments': '/analytics/payments/',
            'refunds': '/analytics/refunds/',
            'users': '/analytics/users/',
        }
        for area, url in urls.items():
            response = client.get(url)
            self.assertEqual(response.status_code, 200, f'{area} page failed')

    def test_invalid_range_falls_back(self):
        client = self.client.__class__()
        self._admin_session(client, self.superuser)
        response = client.get('/analytics/?range=not_a_preset')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Last 30 Days')

    def test_data_json_endpoint(self):
        client = self.client.__class__()
        self._admin_session(client, self.superuser)
        response = client.get('/analytics/data/overview/')
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertIn('charts', payload)
        self.assertIn('tables', payload)
        self.assertIn('revenue_trend', payload['charts'])

    def test_data_json_unknown_area_returns_400(self):
        client = self.client.__class__()
        self._admin_session(client, self.superuser)
        response = client.get('/analytics/data/nope/')
        self.assertEqual(response.status_code, 400)

    def test_csv_export(self):
        client = self.client.__class__()
        self._admin_session(client, self.superuser)
        response = client.get('/analytics/export/revenue/?format=csv')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment; filename="analytics_revenue_', response['Content-Disposition'])
        self.assertIn(b'Revenue by period', response.content)
        self.assertIn(b'Component', response.content)

    def test_xlsx_export(self):
        client = self.client.__class__()
        self._admin_session(client, self.superuser)
        response = client.get('/analytics/export/revenue/?format=xlsx')
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])
        with zipfile.ZipFile(__import__('io').BytesIO(response.content)) as zf:
            self.assertIn('xl/workbook.xml', zf.namelist())

    def test_pdf_export_renders_report(self):
        client = self.client.__class__()
        self._admin_session(client, self.superuser)
        response = client.get('/analytics/export/revenue/?format=pdf')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Revenue Analytics')

    def test_export_unknown_area_redirects(self):
        client = self.client.__class__()
        self._admin_session(client, self.superuser)
        response = client.get('/analytics/export/nope/?format=csv')
        self.assertRedirects(response, reverse('admin_analytics_overview'),
                             fetch_redirect_response=False)

    def test_all_data_stat_paths_resolve_in_ajax_payload(self):
        """Every data-stat / data-change path rendered by a page must resolve in
        the AJAX payload for that area (prevents silent JS no-ops)."""
        client = self.client.__class__()
        self._admin_session(client, self.superuser)
        areas = ['overview', 'revenue', 'bookings', 'occupancy', 'movies',
                 'theaters', 'peak', 'payments', 'refunds', 'users']
        for area in areas:
            html = client.get(f'/analytics/{area}/').content.decode('utf-8')
            stats = re.findall(r'data-stat="([^"]+)"', html)
            changes = re.findall(r'data-change="([^"]+)"', html)
            payload = json.loads(client.get(f'/analytics/data/{area}/').content)

            def resolve(obj, dotted):
                for key in dotted.split('.'):
                    if not isinstance(obj, dict) or key not in obj:
                        return None
                    obj = obj[key]
                return True

            for dotted in stats:
                self.assertTrue(resolve(payload, dotted),
                                f'{area}: data-stat "{dotted}" missing from payload')
            for dotted in changes:
                self.assertTrue(resolve(payload, dotted),
                                f'{area}: data-change "{dotted}" missing from payload')


class AnalyticsPageRenderTests(AnalyticsDataTestCase):
    """Seeded end-to-end render checks: the templates must show real values
    (not empty placeholders) for a known dataset."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.superuser = User.objects.create_superuser(
            'renderadmin', 'render@example.com', 'password123'
        )

    def setUp(self):
        from movies.testutils import set_admin_session
        set_admin_session(self.client, self.superuser)

    def test_overview_shows_seeded_totals(self):
        r = self.client.get('/analytics/?range=last_7_days')
        self.assertContains(r, '\u20b9570.00')
        self.assertContains(r, 'data-stat="summary.revenue_fmt"')

    def test_revenue_page_shows_components(self):
        r = self.client.get('/analytics/revenue/?range=last_7_days')
        self.assertContains(r, 'data-stat="components.ticket_fmt"')
        self.assertContains(r, '\u20b9570.00')

    def test_peak_weekday_row_headers_render(self):
        """Regression for the get_item-on-list bug (blank weekday labels)."""
        r = self.client.get('/analytics/peak/?range=last_7_days')
        headers = re.findall(
            r'<th class="text-nowrap">([^<]*)</th>', r.content.decode('utf-8')
        )
        self.assertEqual(headers, ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'])

    def test_peak_heatmap_cells_have_values(self):
        r = self.client.get('/analytics/peak/?range=last_7_days')
        html = r.content.decode('utf-8')
        self.assertContains(r, 'data-heatmap')
        cells = re.findall(r'<td class="heat-cell"[^>]*>', html)
        self.assertTrue(cells)
        self.assertTrue(re.search(r'data-v="[1-9]', html),
                        'heatmap should contain at least one non-zero cell')

    def test_occupancy_and_movies_pages_show_seeded_rows(self):
        r = self.client.get('/analytics/occupancy/?range=last_7_days')
        self.assertContains(r, '66.7%')
        r = self.client.get('/analytics/movies/?range=last_7_days')
        self.assertContains(r, 'Analytics Movie One')

    def test_export_links_carry_current_range(self):
        r = self.client.get('/analytics/?range=last_7_days')
        self.assertContains(r, 'format=csv&range=last_7_days')

    def test_all_ten_areas_render_200(self):
        for area in ('overview', 'revenue', 'bookings', 'occupancy', 'movies',
                     'theaters', 'peak', 'payments', 'refunds', 'users'):
            url = '/analytics/' if area == 'overview' else f'/analytics/{area}/'
            self.assertEqual(self.client.get(f'{url}?range=last_7_days').status_code, 200)


class MonthGranularitySeriesTests(TestCase):
    """Regression: month-bucketed series must not be all zeros.

    TruncMonth returns an aware datetime on the 1st of the month. The series
    builder previously normalised window starts to ``date`` objects, so every
    lookup missed and any range wider than ~92 days (this_year, previous_year,
    long custom ranges) rendered an all-zero chart.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('series_user', 'series@example.com', 'password123')
        cls.movie = Movie.objects.create(name='Series Movie', rating=7.5, status='now_showing')
        cls.theater = Theater.objects.create(name='Series PVR', movie=cls.movie, time=_at(0, hour=5))
        cls.seats = [
            Seat.objects.create(theater=cls.theater, seat_number=f'C{i}')
            for i in range(1, 4)
        ]
        cls.reservation = Reservation.objects.create(
            token='series-tok-1', user=cls.user, show=cls.theater,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        cls._seat_cursor = 0

    def _payment(self, when, amount):
        seat = self.seats[self._seat_cursor]
        self._seat_cursor += 1
        booking = Booking.objects.create(
            user=self.user, seat=seat, movie=self.movie, theater=self.theater,
            status='confirmed', booking_ref=generate_booking_ref(),
            total=Decimal(amount), ticket_price=Decimal(amount),
            gst_rate=Decimal('18.00'),
        )
        payment = Payment.objects.create(
            booking=booking, amount=Decimal(amount), status='completed',
            payment_method='upi',
        )
        _backdate(Payment, payment.pk, paid_at=when)
        return payment

    def test_month_series_buckets_are_populated(self):
        jan = timezone.make_aware(
            timezone.datetime(2024, 1, 5, 14, 0, 0))
        jun = timezone.make_aware(
            timezone.datetime(2024, 6, 15, 14, 0, 0))
        dec = timezone.make_aware(
            timezone.datetime(2024, 12, 25, 14, 0, 0))
        self._payment(jan, '100.00')
        self._payment(jun, '200.00')
        self._payment(dec, '300.00')

        rng = analytics.resolve_range('custom', date(2024, 1, 1), date(2024, 12, 31))
        labels, values, gran = analytics._series(
            Payment.objects.filter(status='completed'), 'paid_at', rng, 'sum', 'amount')
        self.assertEqual(gran, 'month')
        self.assertEqual(len(labels), 12)
        self.assertEqual(labels[0].startswith('Jan'), True)
        self.assertEqual(values[0], Decimal('100.00'))
        self.assertEqual(values[5], Decimal('200.00'))
        self.assertEqual(values[11], Decimal('300.00'))
        self.assertEqual(sum(values), Decimal('600.00'))
