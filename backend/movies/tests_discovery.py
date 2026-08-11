from datetime import time, timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from admin_panel.models import Genre, Language, Review, Screen, Show, Theatre
from movies.discovery import (
    DiscoveryParams,
    chip_data,
    discover_movies,
    facet_data,
    querystring,
    recently_released,
    recommended_for_user,
    similar_movies,
    trending_movies,
)
from movies.models import Booking, Movie, Seat, Theater
from movies.services import generate_booking_ref

def _make_genre(name, slug):
    return Genre.objects.create(name=name, slug=slug)


def _make_language(name, code):
    return Language.objects.create(name=name, code=code)


def _make_theatre(name, city, active=True):
    return Theatre.objects.create(name=name, city=city, is_active=active)


def _make_screen(theatre, name='Screen 1'):
    return Screen.objects.create(theatre=theatre, name=name, capacity=100)


def _make_show(movie, theatre, screen, show_time=time(12, 0), price=200, status='active'):
    return Show.objects.create(
        movie=movie, theatre=theatre, screen=screen,
        date=timezone.localdate(), time=show_time,
        ticket_price=price, status=status,
    )


def _make_movie(name, genres=None, languages=None, **kwargs):
    defaults = {'rating': 7.0, 'cast': 'Actor', 'status': 'now_showing'}
    defaults.update(kwargs)
    movie = Movie.objects.create(name=name, **defaults)
    if genres:
        movie.genres.set(genres)
    if languages:
        movie.languages.set(languages)
    return movie


def _make_booking(user, movie, theater, status='confirmed'):
    seat = Seat.objects.create(theater=theater, seat_number=f'S{seat_counter.next()}')
    return Booking.objects.create(
        user=user, seat=seat, movie=movie, theater=theater,
        status=status, booking_ref=generate_booking_ref(),
    )


class _SeatCounter:
    def __init__(self):
        self._n = 0

    def next(self):
        self._n += 1
        return self._n


seat_counter = _SeatCounter()


def _theater_for(movie, name='PVR', at_hour=None):
    when = timezone.now() + timedelta(hours=5)
    if at_hour is not None:
        when = when.replace(hour=at_hour, minute=0, second=0, microsecond=0)
    return Theater.objects.create(name=name, movie=movie, time=when)


class DiscoveryBase(TestCase):
    def setUp(self):
        self.action = _make_genre('Action', 'action')
        self.comedy = _make_genre('Comedy', 'comedy')
        self.drama = _make_genre('Drama', 'drama')
        self.hindi = _make_language('Hindi', 'hi')
        self.tamil = _make_language('Tamil', 'ta')
        self.user = User.objects.create_user('alice', 'a@example.com', 'password123')

        self.mumbai = _make_theatre('PVR Mumbai', 'Mumbai')
        self.delhi = _make_theatre('PVR Delhi', 'Delhi')
        self.mumbai_screen = _make_screen(self.mumbai)
        self.delhi_screen = _make_screen(self.delhi)

        self.today = timezone.localdate()

        self.m1 = _make_movie('Action Blast', [self.action], [self.hindi], rating=8.5, release_date=self.today)
        self.m2 = _make_movie('Comedy Nights', [self.comedy], [self.hindi], rating=7.0, release_date=self.today - timedelta(days=2))
        self.m3 = _make_movie('Drama Queen', [self.drama], [self.tamil], rating=6.0, release_date=self.today - timedelta(days=90))
        self.m4 = _make_movie('Hidden Film', [self.action], [self.hindi], rating=9.0, status='hidden', release_date=self.today)
        self.m5 = _make_movie('Archived Film', [self.drama], [self.tamil], rating=5.0, status='archived', release_date=self.today)
        self.m6 = _make_movie('Deleted Film', [self.comedy], [self.hindi], rating=6.5, is_deleted=True, release_date=self.today)

        self.t_m1 = _make_theater_for_show(self.m1)
        self.t_m2 = _make_theater_for_show(self.m2)
        self.t_m3 = _make_theater_for_show(self.m3)

        _make_show(self.m1, self.mumbai, self.mumbai_screen, show_time=time(9, 0), price=150)
        _make_show(self.m2, self.mumbai, self.mumbai_screen, show_time=time(22, 0), price=350)
        _make_show(self.m3, self.delhi, self.delhi_screen, show_time=time(13, 0), price=250)


def _make_theater_for_show(movie):
    return Theater.objects.create(
        name=f'{movie.name} Theater', movie=movie,
        time=timezone.now() + timedelta(hours=5),
    )


class DiscoveryParamsTests(DiscoveryBase):
    def test_defaults_are_sanitized(self):
        params = DiscoveryParams.from_request(self.client.get('/').wsgi_request)
        self.assertEqual(params.sort, 'popularity')
        self.assertEqual(params.page, 1)
        self.assertEqual(params.per_page, 12)

    def test_invalid_values_fall_back_to_defaults(self):
        request = self.client.get(
            reverse('movie_list') + '?sort=banana&page=-5&rating=99&release=tomorrow&per_page=9999'
        ).wsgi_request
        params = DiscoveryParams.from_request(request)
        self.assertEqual(params.sort, 'popularity')
        self.assertEqual(params.page, 1)
        self.assertIsNone(params.rating)
        self.assertEqual(params.release, '')
        self.assertEqual(params.per_page, 48)  # clamped to MAX_PER_PAGE

    def test_search_supports_q_alias(self):
        request = self.client.get(reverse('movie_list') + '?q=action').wsgi_request
        params = DiscoveryParams.from_request(request)
        self.assertEqual(params.search, 'action')

    def test_list_values_are_deduplicated_and_capped(self):
        request = self.client.get(
            reverse('movie_list') + '?' + '&'.join(['genre=action'] * 20)
        ).wsgi_request
        params = DiscoveryParams.from_request(request)
        self.assertEqual(len(params.genres), 1)

    def test_to_query_roundtrip(self):
        params = DiscoveryParams(search='x', genres=['action'], sort='rating', page=2)
        self.assertEqual(
            querystring(params),
            'search=x&genre=action&sort=rating',
        )


class DiscoverMoviesTests(DiscoveryBase):
    def test_search_matches_name_case_insensitive(self):
        qs = discover_movies(DiscoveryParams(search='comedy'))
        self.assertEqual(list(qs.values_list('name', flat=True)), ['Comedy Nights'])

    def test_no_match_returns_empty(self):
        qs = discover_movies(DiscoveryParams(search='zzzzzz'))
        self.assertFalse(qs.exists())

    def test_hidden_archived_deleted_never_appear(self):
        qs = discover_movies(DiscoveryParams())
        names = list(qs.values_list('name', flat=True))
        self.assertEqual(len(names), 3)
        self.assertNotIn('Hidden Film', names)
        self.assertNotIn('Archived Film', names)
        self.assertNotIn('Deleted Film', names)

    def test_genre_filter_single_and_multiple(self):
        self.assertEqual(discover_movies(DiscoveryParams(genres=['action'])).count(), 1)
        qs = discover_movies(DiscoveryParams(genres=['action', 'comedy']))
        self.assertEqual(qs.count(), 2)
        self.assertFalse(qs.filter(name='Hidden Film').exists())

    def test_language_filter(self):
        qs = discover_movies(DiscoveryParams(languages=['ta']))
        self.assertEqual(list(qs.values_list('name', flat=True)), ['Drama Queen'])

    def test_city_filter(self):
        qs = discover_movies(DiscoveryParams(city='Mumbai'))
        self.assertEqual(set(qs.values_list('name', flat=True)), {'Action Blast', 'Comedy Nights'})

    def test_city_filter_is_case_insensitive(self):
        qs = discover_movies(DiscoveryParams(city='mumbai'))
        self.assertEqual(qs.count(), 2)

    def test_theatre_filter(self):
        qs = discover_movies(DiscoveryParams(theatre_id=self.mumbai.id))
        self.assertEqual(set(qs.values_list('name', flat=True)), {'Action Blast', 'Comedy Nights'})

    def test_rating_filter(self):
        qs = discover_movies(DiscoveryParams(rating=8))
        self.assertEqual(list(qs.values_list('name', flat=True)), ['Action Blast'])

    def test_release_this_week(self):
        qs = discover_movies(DiscoveryParams(release='this_week'))
        self.assertIn('Action Blast', list(qs.values_list('name', flat=True)))

    def test_release_last_3_months(self):
        qs = discover_movies(DiscoveryParams(release='last_3_months'))
        self.assertEqual(qs.count(), 3)

    def test_timing_filter(self):
        morning = discover_movies(DiscoveryParams(timings=['morning']))
        self.assertEqual(list(morning.values_list('name', flat=True)), ['Action Blast'])
        night = discover_movies(DiscoveryParams(timings=['night']))
        self.assertEqual(list(night.values_list('name', flat=True)), ['Comedy Nights'])

    def test_multiple_timings(self):
        qs = discover_movies(DiscoveryParams(timings=['morning', 'night']))
        self.assertEqual(set(qs.values_list('name', flat=True)), {'Action Blast', 'Comedy Nights'})

    def test_combined_filters(self):
        qs = discover_movies(DiscoveryParams(genres=['comedy'], city='Mumbai', timings=['night']))
        self.assertEqual(list(qs.values_list('name', flat=True)), ['Comedy Nights'])

    def test_movies_without_active_shows_excluded_from_price_sort(self):
        self.m3.status = 'archived'
        self.m3.save()
        no_show = _make_movie('No Show Film', [self.action], [self.hindi], rating=7.5)
        qs = discover_movies(DiscoveryParams(sort='price_asc'))
        self.assertNotIn(no_show.pk, list(qs.values_list('pk', flat=True)))

    def test_min_price_annotation(self):
        qs = discover_movies(DiscoveryParams())
        m1 = qs.get(pk=self.m1.pk)
        self.assertEqual(m1.min_price, 150)


class DiscoverSortTests(DiscoveryBase):
    def test_popularity_orders_by_confirmed_bookings(self):
        _make_booking(self.user, self.m2, self.t_m2)
        _make_booking(self.user, self.m2, self.t_m2)
        _make_booking(self.user, self.m1, self.t_m1)
        qs = discover_movies(DiscoveryParams(sort='popularity'))
        self.assertEqual(list(qs.values_list('name', flat=True))[0], 'Comedy Nights')

    def test_cancelled_bookings_do_not_count_for_popularity(self):
        _make_booking(self.user, self.m2, self.t_m2, status='cancelled')
        qs = discover_movies(DiscoveryParams(sort='popularity'))
        self.assertEqual(list(qs.values_list('name', flat=True))[0], 'Action Blast')

    def test_newest_sort(self):
        qs = discover_movies(DiscoveryParams(sort='newest'))
        names = list(qs.values_list('name', flat=True))
        self.assertEqual(names, ['Action Blast', 'Comedy Nights', 'Drama Queen'])

    def test_rating_sort(self):
        qs = discover_movies(DiscoveryParams(sort='rating'))
        self.assertEqual(list(qs.values_list('name', flat=True))[0], 'Action Blast')

    def test_price_asc_sort(self):
        qs = discover_movies(DiscoveryParams(sort='price_asc'))
        self.assertEqual(list(qs.values_list('name', flat=True))[0], 'Action Blast')

    def test_price_desc_sort(self):
        qs = discover_movies(DiscoveryParams(sort='price_desc'))
        self.assertEqual(list(qs.values_list('name', flat=True))[0], 'Comedy Nights')

    def test_alpha_sort(self):
        qs = discover_movies(DiscoveryParams(sort='alpha_asc'))
        names = list(qs.values_list('name', flat=True))
        self.assertEqual(names, ['Action Blast', 'Comedy Nights', 'Drama Queen'])


class DiscoveryFeedTests(DiscoveryBase):
    def test_trending_uses_booking_weight(self):
        _make_booking(self.user, self.m2, self.t_m2)
        _make_booking(self.user, self.m2, self.t_m2)
        _make_booking(self.user, self.m1, self.t_m1)
        trending = trending_movies(5)
        self.assertEqual(trending[0].pk, self.m2.pk)

    def test_recently_released_only_now_showing(self):
        released = recently_released(5)
        names = [m.name for m in released]
        self.assertIn('Action Blast', names)
        self.assertNotIn('Hidden Film', names)

    def test_similar_movies_prefers_shared_genres(self):
        m_shared = _make_movie('Action Drama', [self.action, self.drama], [self.hindi], rating=8.0)
        m_partial = _make_movie('Action Saga', [self.action], [self.tamil], rating=8.0)
        m_unrelated = _make_movie('Funny Flicks', [self.comedy], [self.tamil], rating=9.0)
        similar = similar_movies(self.m1, limit=3)
        self.assertEqual(similar[0].pk, m_shared.pk)  # genre + language match scores highest
        self.assertIn(m_partial.pk, [s.pk for s in similar])
        self.assertNotIn(m_unrelated.pk, [s.pk for s in similar])

    def test_recommended_uses_user_booking_history(self):
        _make_booking(self.user, self.m1, self.t_m1)
        self.client.force_login(self.user)
        request = self.client.get(reverse('home')).wsgi_request
        recs = recommended_for_user(request, 5)
        rec_names = [m.name for m in recs]
        self.assertIn('Comedy Nights', rec_names)  # shares Hindi language
        self.assertNotIn('Action Blast', rec_names)  # already booked

    def test_recommended_falls_back_to_trending_for_new_users(self):
        request = self.client.get(reverse('home')).wsgi_request
        recs = recommended_for_user(request, 5)
        self.assertEqual(len(recs), 3)
        self.assertIn(self.m1.pk, [m.pk for m in recs])


class DiscoveryFacetTests(DiscoveryBase):
    def test_facets_only_list_visible_movies(self):
        scifi = _make_genre('Sci-Fi', 'scifi')
        self.m4.genres.add(scifi)
        facets = facet_data()
        genre_slugs = [g.slug for g in facets['genres']]
        self.assertIn('action', genre_slugs)
        self.assertNotIn('scifi', genre_slugs)  # linked only to a hidden movie

    def test_facets_list_cities_and_active_theatres(self):
        facets = facet_data()
        self.assertIn('Mumbai', facets['cities'])
        self.assertIn('Delhi', facets['cities'])
        theatre_names = [t.name for t in facets['theatres']]
        self.assertIn('PVR Mumbai', theatre_names)

    def test_facets_never_duplicate_shared_values(self):
        # m1 + m2 share Hindi and PVR Mumbai; two movies share each genre.
        self.m2.genres.add(self.action)
        facets = facet_data()
        genre_slugs = [g.slug for g in facets['genres']]
        language_codes = [l.code for l in facets['languages']]
        theatre_names = [t.name for t in facets['theatres']]
        for values in (genre_slugs, language_codes, theatre_names):
            self.assertEqual(len(values), len(set(values)),
                             f'facet values must be unique, got {values}')

    def test_chips_reflect_active_filters(self):
        params = DiscoveryParams(search='boom', genres=['action'], city='Mumbai', rating=7)
        chips = chip_data(params, facet_data())
        chip_params = [c['param'] for c in chips]
        self.assertIn('search', chip_params)
        self.assertIn('genre', chip_params)
        self.assertIn('city', chip_params)
        self.assertIn('rating', chip_params)


class DiscoveryViewTests(DiscoveryBase):
    def setUp(self):
        super().setUp()
        for i in range(15):
            extra = _make_movie(f'Extra Movie {i}', [self.action], [self.hindi], rating=6.0)
            _make_show(extra, self.mumbai, self.mumbai_screen, show_time=time(14, 0), price=200)
        self.url = reverse('movie_list')

    def test_full_page_render(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Action Blast')
        self.assertContains(response, 'discoveryForm')
        self.assertContains(response, 'Movies found')

    def test_ajax_returns_json(self):
        response = self.client.get(self.url, headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertIn('html', data)
        self.assertIn('Action Blast', data['html'])
        self.assertEqual(data['count'], 18)
        self.assertGreater(data['pages'], 1)

    def test_ajax_combines_filters_and_returns_count(self):
        response = self.client.get(
            self.url + '?genre=action&city=Mumbai',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        data = response.json()
        self.assertEqual(data['count'], 16)  # Action Blast + 15 Extra Movies (Mumbai show only)

    def test_pagination_keeps_query_string(self):
        response = self.client.get(
            self.url + '?genre=action&page=2',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        data = response.json()
        self.assertEqual(data['page'], 2)
        self.assertIn('genre=action', data['html'])

    def test_invalid_inputs_do_not_break_the_view(self):
        response = self.client.get(
            self.url + '?sort=banana&page=-3&rating=999&release=huh&per_page=1',
        )
        self.assertEqual(response.status_code, 200)

    def test_clear_all_button_present_when_filters_active(self):
        response = self.client.get(self.url + '?search=Action')
        self.assertContains(response, 'data-clear-filters')

    def test_recommended_strip_only_for_authenticated(self):
        response = self.client.get(self.url)
        self.assertNotContains(response, 'Recommended for You')
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, 'Recommended for You')

    def test_price_sort_respects_selected_category(self):
        therapy = _make_movie(
            'Calm Therapy', [self.comedy], [self.hindi], rating=6.0,
            category='laughing_therapy', release_date=self.today,
        )
        _make_show(therapy, self.mumbai, self.mumbai_screen, show_time=time(16, 0), price=100)
        for sort in ('price_asc', 'price_desc', 'popularity', 'newest'):
            response = self.client.get(
                self.url + '?category=laughing_therapy&sort=' + sort,
                headers={'X-Requested-With': 'XMLHttpRequest'},
            )
            data = response.json()
            self.assertIn('Calm Therapy', data['html'])
            self.assertNotIn('Action Blast', data['html'])
            self.assertNotIn('Comedy Nights', data['html'])

    def test_category_hidden_input_preserves_section_on_sort(self):
        response = self.client.get(self.url + '?category=laughing_therapy&sort=price_desc')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<input type="hidden" name="category" value="laughing_therapy">',
            html=True,
        )

    def test_category_tab_links_preserve_active_sort(self):
        response = self.client.get(self.url + '?category=laughing_therapy&sort=price_desc')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'category=laughing_therapy')
        # The Movies tab link keeps the active sort while leaving the section.
        self.assertContains(response, 'href="?sort=price_desc"')


class CategoryAwareFacetTests(DiscoveryBase):
    """Event categories must not leak pseudo-genres into the Movies tab (or vice versa)."""

    def setUp(self):
        super().setUp()
        self.therapy_genre = _make_genre('Laughing Therapy', 'laughing-therapy')
        self.concert_genre = _make_genre('Live Concert', 'live-concert')
        self.therapy = _make_movie(
            'Comedy Night', [self.therapy_genre], [self.hindi], rating=7.5,
            category='laughing_therapy', release_date=self.today,
        )
        self.concert = _make_movie(
            'Rock Night', [self.concert_genre], [self.tamil], rating=8.0,
            category='live_concert', release_date=self.today,
        )
        _make_show(self.therapy, self.mumbai, self.mumbai_screen, show_time=time(20, 0), price=500)
        _make_show(self.concert, self.delhi, self.delhi_screen, show_time=time(21, 0), price=900)

    def test_movies_facets_exclude_event_pseudo_genres(self):
        facets = facet_data('movie')
        genre_slugs = [g.slug for g in facets['genres']]
        self.assertIn('action', genre_slugs)
        self.assertNotIn('laughing-therapy', genre_slugs)
        self.assertNotIn('live-concert', genre_slugs)

    def test_event_facets_only_include_their_own_genre(self):
        facets = facet_data('live_concert')
        genre_slugs = [g.slug for g in facets['genres']]
        self.assertEqual(genre_slugs, ['live-concert'])
        self.assertNotIn('action', genre_slugs)

    def test_event_category_with_pseudo_genre_returns_results(self):
        qs = discover_movies(DiscoveryParams(category='live_concert', genres=['live-concert']))
        self.assertEqual(list(qs.values_list('name', flat=True)), ['Rock Night'])

    def test_movies_tab_with_pseudo_genre_has_no_results(self):
        # The sidebar no longer offers pseudo-genres here, but a hand-crafted
        # URL must never crash or leak event rows into the Movies tab.
        qs = discover_movies(DiscoveryParams(genres=['live-concert']))
        self.assertEqual(qs.count(), 0)

    def test_date_filter_restricts_to_show_date(self):
        qs = discover_movies(DiscoveryParams(category='live_concert', date=self.today.isoformat()))
        self.assertEqual(list(qs.values_list('name', flat=True)), ['Rock Night'])
        tomorrow = (self.today + timedelta(days=1)).isoformat()
        qs = discover_movies(DiscoveryParams(category='live_concert', date=tomorrow))
        self.assertEqual(qs.count(), 0)

    def test_price_range_filter(self):
        qs = discover_movies(DiscoveryParams(price_min=250, price_max=350))
        self.assertEqual(set(qs.values_list('name', flat=True)), {'Comedy Nights', 'Drama Queen'})
        qs = discover_movies(DiscoveryParams(price_max=200))
        self.assertEqual(list(qs.values_list('name', flat=True)), ['Action Blast'])

    def test_price_range_filter_scoped_to_category(self):
        qs = discover_movies(DiscoveryParams(category='live_concert', price_min=900))
        self.assertEqual(list(qs.values_list('name', flat=True)), ['Rock Night'])
        qs = discover_movies(DiscoveryParams(category='live_concert', price_max=800))
        self.assertEqual(qs.count(), 0)

    def test_invalid_date_and_price_are_ignored(self):
        params = DiscoveryParams.from_request(
            self.client.get(reverse('movie_list') + '?date=not-a-date&price_min=-5&price_max=abc').wsgi_request
        )
        self.assertEqual(params.date, '')
        self.assertIsNone(params.price_min)
        self.assertIsNone(params.price_max)

    def test_event_tab_renders_date_and_price_filters_only(self):
        response = self.client.get(reverse('movie_list') + '?category=live_concert')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Event Date')
        self.assertContains(response, 'Price Range')
        self.assertNotContains(response, 'Show Timing')
        self.assertNotContains(response, 'Genres')

    def test_movies_tab_does_not_offer_event_pseudo_genres(self):
        response = self.client.get(reverse('movie_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="genre" value="action"')
        self.assertNotContains(response, 'name="genre" value="live-concert"')
        self.assertNotContains(response, 'name="genre" value="laughing-therapy"')


class HomeGenreChipTests(DiscoveryBase):
    """Home page "Browse by Category" chips must never land on an empty Movies tab."""

    def setUp(self):
        super().setUp()
        self.therapy_genre = _make_genre('Laughing Therapy', 'laughing-therapy')
        self.concert_genre = _make_genre('Live Concert', 'live-concert')
        # Attach the event pseudo-genres to real (visible) rows so the chips render.
        self.m1.genres.add(self.therapy_genre)
        self.m2.genres.add(self.concert_genre)

    def test_event_pseudo_genre_chips_link_to_category_tabs(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '?category=laughing_therapy')
        self.assertContains(response, '?category=live_concert')

    def test_movie_genre_chips_still_link_to_genre_filter(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, '?genre=action')
        self.assertContains(response, '?genre=comedy')
        # Pseudo-genres must never be offered as a Movies-tab genre filter.
        self.assertNotContains(response, '?genre=laughing-therapy')
        self.assertNotContains(response, '?genre=live-concert')
