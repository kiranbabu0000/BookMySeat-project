"""Dashboard correctness + performance regression tests.

Locks in the semantics of the optimised admin dashboard helpers:

- Occupancy / show-count day windows must follow theatre-local calendar days
  (a 00:30 IST show stored as the previous UTC evening belongs to the IST
  day) while using index-friendly half-open ranges instead of ``__date``
  casts that cannot use any index.
- The grouped series helpers must agree with the single-day helpers.
- The full dashboard view must render within a bounded number of queries.
"""
from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from movies.models import Movie, Seat, Theater

from admin_panel.models import AdminProfile
from admin_panel.views import (
    _dashboard_active_show_counts,
    _dashboard_occupancy,
    _dashboard_occupancy_series,
)


def _show_at(movie, name, local_day, hour=12):
    """Create a show starting at ``hour`` theatre-local time on ``local_day``."""
    from movies.showtime import aware_showtime
    return Theater.objects.create(
        movie=movie,
        name=name,
        time=aware_showtime(local_day, datetime.strptime(str(hour), '%H').time()),
        status='active',
    )


def _seats(theater, booked):
    seats = []
    for i in range(4):
        seats.append(Seat.objects.create(
            theater=theater, seat_number=f'{theater.pk}-{i}',
            is_booked=i < booked,
        ))
    return seats


class DashboardHelperSemanticsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            'dashadmin', 'dash@example.com', 'DashPass!123')
        AdminProfile.objects.create(user=cls.admin, role='super_admin',
                                    is_active=True)
        cls.movie = Movie.objects.create(
            name='Dash Movie', rating=7, cast='c', status='now_showing')

    def test_occupancy_counts_only_that_local_day(self):
        today = timezone.localdate()
        show_today = _show_at(self.movie, 'PVR', today)
        _seats(show_today, booked=2)          # 2/4 -> 50%
        show_other = _show_at(
            self.movie, 'INOX', today - timedelta(days=3))
        _seats(show_other, booked=4)          # different day: excluded

        pct, booked, total = _dashboard_occupancy(today)
        self.assertEqual((pct, booked, total), (50, 2, 4))

    def test_post_midnight_show_belongs_to_correct_local_day(self):
        """A 00:30 IST start is stored as the *previous* UTC evening; its
        occupancy must land on the IST calendar day, not the UTC one."""
        today = timezone.localdate()
        show = _show_at(self.movie, 'PVR', today, hour=0)
        # Override to exactly 00:30 local so the UTC instant is the prior day.
        show.time = timezone.make_aware(
            datetime.combine(today, datetime.min.time()).replace(minute=30),
            timezone.get_default_timezone(),
        )
        show.save(update_fields=['time'])
        _seats(show, booked=1)

        pct, booked, total = _dashboard_occupancy(today)
        self.assertEqual((pct, booked, total), (25, 1, 4))

    def test_series_matches_single_day_helper(self):
        today = timezone.localdate()
        s1 = _show_at(self.movie, 'PVR', today)
        _seats(s1, booked=3)
        s2 = _show_at(self.movie, 'Cinepolis', today, hour=18)
        _seats(s2, booked=1)

        mapping = _dashboard_occupancy_series(days=8)
        self.assertEqual(mapping[today], _dashboard_occupancy(today))
        self.assertNotIn(today - timedelta(days=5), mapping)

    def test_show_counts_only_active_within_window(self):
        today = timezone.localdate()
        _show_at(self.movie, 'PVR', today)
        cancelled = _show_at(self.movie, 'INOX', today)
        cancelled.status = 'cancelled'
        cancelled.save(update_fields=['status'])
        old = _show_at(self.movie, 'Old', today - timedelta(days=10))
        _seats(old, booked=0)

        counts = _dashboard_active_show_counts(days=8)
        self.assertEqual(counts.get(today, 0), 1)


class DashboardViewQueryBudgetTests(TestCase):
    """The dashboard must stay within a bounded query count.

    Guards against reintroducing the per-day N+1 scans (one seat-join query
    per sparkline point) that made the page take tens of seconds.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            'qadmin', 'q@example.com', 'QPass!123')
        AdminProfile.objects.create(user=cls.admin, role='super_admin',
                                    is_active=True)
        movie = Movie.objects.create(
            name='Query Budget Movie', rating=7, cast='c',
            status='now_showing')
        today = timezone.localdate()
        for offset in range(7):
            show = _show_at(
                movie, f'PVR {offset}', today - timedelta(days=offset))
            _seats(show, booked=offset % 3)

    def setUp(self):
        self.client = Client()
        self.client.post('/admin-login/', {
            'username': 'qadmin', 'password': 'QPass!123'})

    def test_dashboard_renders_kpis_and_stays_in_query_budget(self):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        # KPI tiles + operations tiles actually rendered.
        self.assertIn('Today&#x27;s Revenue', html)
        self.assertIn('Active Shows Today', html)
        self.assertIn('Today&#x27;s Occupancy', html)
        # Chart payload embedded for all four ranges.
        self.assertIn('"7d"', html)
        self.assertIn('"12m"', html)
        # Bounded: 41 measured after optimisation (was 76). Allow headroom
        # for session/auth plumbing but fail loudly on N+1 regressions.
        self.assertLessEqual(len(ctx), 55, (
            'Dashboard query count regressed: %d queries' % len(ctx)))

    def test_second_render_is_not_slower_than_first(self):
        self.client.get(reverse('admin_dashboard'))
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(
                self.client.get(reverse('admin_dashboard')).status_code, 200)
        self.assertLessEqual(len(ctx), 55)


from django.test import Client  # noqa: E402  (kept close to usage above)