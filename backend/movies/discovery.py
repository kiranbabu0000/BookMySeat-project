"""
Movie Discovery engine for BookMySeat.

Pure query logic for searching, filtering, sorting and paginating the movie
catalog, plus the trending / recently-released / similar-movies / personalized
recommendation feeds. All queries are built with the ORM (Q objects, annotated
Count aggregates, correlated Subqueries) so they stay index-friendly at scale.

Canonical data sources (kept in sync automatically with the admin panel):
    - movies.Movie          -> catalog, genres (M2M), languages (M2M), rating, release_date
    - admin_panel.Show      -> available showtimes, theatre (city), ticket price
    - movies.Booking        -> confirmed bookings (trending + recommendations)
    - admin_panel.Review    -> approved review counts (engagement signal)
"""
from datetime import date as date_type, time, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, F, FloatField, OuterRef, Q, Subquery
from django.db.models.functions import Abs
from django.utils import timezone
from django.utils.http import urlencode

from admin_panel.models import Genre, Language, Show, Theatre
from .models import Booking, Movie

# Visible catalog: soft-deleted, archived and hidden movies are excluded everywhere.
HIDDEN_STATUSES = ['archived', 'hidden']

# Catalog categories. 'movie' is the default browse scope; events are standalone
# rail categories surfaced via the home page and the discovery toolbar.
CATEGORY_CHOICES = {'movie', 'laughing_therapy', 'live_concert'}

CATEGORY_LABELS = {
    'movie': 'Movies',
    'laughing_therapy': 'Laughing Therapy',
    'live_concert': 'Live Concerts',
}

CATEGORY_SINGULAR = {
    'movie': 'Movie',
    'laughing_therapy': 'Laughing Therapy',
    'live_concert': 'Live Concert',
}

# Short-lived cache for the expensive catalog feeds so the home and
# movie-detail pages stop recomputing them on every request.
FEED_CACHE_TTL = 300


def _cached_feed(key, fn):
    if getattr(settings, 'TESTING', False):
        return fn()
    value = cache.get(key)
    if value is None:
        value = fn()
        cache.set(key, value, FEED_CACHE_TTL)
    return value

SORT_CHOICES = {
    'popularity',
    'newest',
    'rating',
    'price_asc',
    'price_desc',
    'alpha_asc',
    'alpha_desc',
}

TIMING_OPTIONS = {
    'morning': (time(6, 0), time(12, 0)),
    'afternoon': (time(12, 0), time(17, 0)),
    'evening': (time(17, 0), time(21, 0)),
    'night': (time(21, 0), time(23, 59, 59)),
}

RELEASE_RANGES = {
    'this_week': lambda today: (today - timedelta(days=today.weekday()), today),
    'this_month': lambda today: (today.replace(day=1), today),
    'last_3_months': lambda today: (today - timedelta(days=90), today),
    'last_year': lambda today: (today - timedelta(days=365), today),
}

RELEASE_LABELS = {
    'this_week': 'This Week',
    'this_month': 'This Month',
    'last_3_months': 'Last 3 Months',
    'last_year': 'Last Year',
}

TIMING_LABELS = {
    'morning': 'Morning (6 AM - 12 PM)',
    'afternoon': 'Afternoon (12 PM - 5 PM)',
    'evening': 'Evening (5 PM - 9 PM)',
    'night': 'Night (9 PM+)',
}

MAX_PER_PAGE = 48
MIN_PER_PAGE = 12
DEFAULT_PER_PAGE = getattr(settings, 'MOVIE_DISCOVERY_PER_PAGE', 12)
MAX_LIST_VALUES = 10
MAX_SEARCH_LENGTH = 100


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class DiscoveryParams:
    """Validated, normalized discovery query parameters (no raw user input leaks in)."""

    def __init__(self, search='', genres=None, languages=None, city='', theatre_id=None,
                 release='', rating=None, timings=None, sort='popularity', page=1, per_page=None,
                 category='movie', date='', price_min=None, price_max=None):
        self.search = search
        self.genres = genres or []
        self.languages = languages or []
        self.city = city
        self.theatre_id = theatre_id
        self.release = release
        self.rating = rating
        self.timings = timings or []
        self.sort = sort if sort in SORT_CHOICES else 'popularity'
        self.page = max(1, page)
        self.per_page = per_page or DEFAULT_PER_PAGE
        self.category = category if category in CATEGORY_CHOICES else 'movie'
        self.date = date
        self.price_min = price_min
        self.price_max = price_max

    @classmethod
    def from_request(cls, request):
        GET = request.GET

        def clean_list(values, limit=MAX_LIST_VALUES):
            cleaned = []
            for value in values:
                value = (value or '').strip()
                if value and value not in cleaned and len(cleaned) < limit:
                    cleaned.append(value)
            return cleaned

        search = (GET.get('search') or GET.get('q') or '').strip()[:MAX_SEARCH_LENGTH]
        genres = clean_list(GET.getlist('genre'))
        languages = clean_list(GET.getlist('language'))
        city = (GET.get('city') or '').strip()[:100]

        raw_theatre = (GET.get('theatre') or '').strip()
        theatre_id = _safe_int(raw_theatre, None)
        if theatre_id is not None and theatre_id <= 0:
            theatre_id = None

        release = GET.get('release') if GET.get('release') in RELEASE_RANGES else ''
        raw_rating = (GET.get('rating') or '').strip()
        rating = _safe_int(raw_rating, None)
        if rating is not None and not (1 <= rating <= 10):
            rating = None

        timings = [t for t in GET.getlist('timing') if t in TIMING_OPTIONS][:4]
        sort = GET.get('sort') if GET.get('sort') in SORT_CHOICES else 'popularity'
        category = GET.get('category') if GET.get('category') in CATEGORY_CHOICES else 'movie'
        page = max(1, _safe_int(GET.get('page'), 1))
        per_page = min(MAX_PER_PAGE, max(MIN_PER_PAGE, _safe_int(GET.get('per_page'), DEFAULT_PER_PAGE)))

        raw_date = (GET.get('date') or '').strip()
        date_value = ''
        if raw_date:
            try:
                parsed_date = date_type.fromisoformat(raw_date)
            except ValueError:
                parsed_date = None
            if parsed_date is not None:
                date_value = parsed_date.isoformat()

        price_min = _safe_int(GET.get('price_min'), None)
        price_max = _safe_int(GET.get('price_max'), None)
        if price_min is not None and price_min < 0:
            price_min = None
        if price_max is not None and price_max < 0:
            price_max = None
        if price_min is not None and price_max is not None and price_min > price_max:
            price_min = price_max = None

        return cls(search=search, genres=genres, languages=languages, city=city,
                   theatre_id=theatre_id, release=release, rating=rating, timings=timings,
                   sort=sort, page=page, per_page=per_page, category=category,
                   date=date_value, price_min=price_min, price_max=price_max)

    def to_query(self):
        """Non-empty params as a tuple list for urlencode (preserves multi-values)."""
        parts = []
        if self.search:
            parts.append(('search', self.search))
        if self.category != 'movie':
            parts.append(('category', self.category))
        for value in self.genres:
            parts.append(('genre', value))
        for value in self.languages:
            parts.append(('language', value))
        if self.city:
            parts.append(('city', self.city))
        if self.theatre_id:
            parts.append(('theatre', str(self.theatre_id)))
        if self.release:
            parts.append(('release', self.release))
        if self.rating:
            parts.append(('rating', str(self.rating)))
        if self.date:
            parts.append(('date', self.date))
        if self.price_min is not None:
            parts.append(('price_min', str(self.price_min)))
        if self.price_max is not None:
            parts.append(('price_max', str(self.price_max)))
        for value in self.timings:
            parts.append(('timing', value))
        if self.sort != 'popularity':
            parts.append(('sort', self.sort))
        return parts


def querystring(params):
    """Query string for pagination links that preserve every active filter."""
    return urlencode(params.to_query())


def category_links(params):
    """Category-tab links that preserve every active filter (search/sort/etc.).

    The 'movie' tab drops the category param entirely since it is the default
    browse scope, keeping URLs clean while staying on the current section.
    """
    links = []
    for value in CATEGORY_CHOICES:
        parts = [(k, v) for k, v in params.to_query() if k != 'category']
        if value != 'movie':
            parts.append(('category', value))
        links.append({
            'value': value,
            'label': CATEGORY_LABELS.get(value, value),
            'href': '?' + urlencode(parts),
        })
    return links


def visible_movies():
    return Movie.objects.filter(is_deleted=False).exclude(status__in=HIDDEN_STATUSES)


def _min_price_subquery():
    """Lowest active-show ticket price for a movie (correlated scalar subquery)."""
    return Subquery(
        Show.objects.filter(movie=OuterRef('pk'), status='active')
        .order_by('ticket_price')
        .values('ticket_price')[:1]
    )


def discover_movies(params):
    """Apply search + all filters + sorting; returns an efficient queryset."""
    qs = visible_movies().filter(category=params.category)

    if params.search:
        qs = qs.filter(name__icontains=params.search)

    joined = bool(params.genres or params.languages or params.city or params.theatre_id
                  or params.timings or params.date)

    if params.genres:
        qs = qs.filter(genres__slug__in=params.genres)
    if params.languages:
        qs = qs.filter(languages__code__in=params.languages)
    if params.city:
        qs = qs.filter(shows__theatre__city__iexact=params.city, shows__status='active')
    if params.theatre_id:
        qs = qs.filter(shows__theatre_id=params.theatre_id, shows__status='active')
    if params.rating:
        qs = qs.filter(rating__gte=params.rating)

    if params.release:
        start, end = RELEASE_RANGES[params.release](timezone.localdate())
        qs = qs.filter(release_date__gte=start, release_date__lte=end)

    if params.timings:
        timing_q = Q()
        for key in params.timings:
            start, end = TIMING_OPTIONS[key]
            timing_q |= Q(shows__time__gte=start, shows__time__lt=end)
        qs = qs.filter(timing_q, shows__status='active')

    if params.date:
        qs = qs.filter(shows__date=params.date, shows__status='active')

    if params.price_min is not None or params.price_max is not None:
        qs = qs.annotate(_filter_price=_min_price_subquery())
        if params.price_min is not None:
            qs = qs.filter(_filter_price__gte=params.price_min)
        if params.price_max is not None:
            qs = qs.filter(_filter_price__lte=params.price_max)

    aggregating = params.sort == 'popularity'
    if params.sort in ('price_asc', 'price_desc'):
        joined = True  # price derives from active shows, so we join them
        qs = qs.filter(shows__status='active')

    if params.sort == 'popularity':
        qs = qs.annotate(
            booking_count=Count('booking', filter=Q(booking__status='confirmed'), distinct=True)
        ).order_by('-booking_count', 'name')
    elif params.sort == 'newest':
        qs = qs.order_by('-release_date', 'name')
    elif params.sort == 'rating':
        qs = qs.order_by('-rating', '-release_date', 'name')
    elif params.sort == 'price_asc':
        qs = qs.annotate(min_price=_min_price_subquery()).order_by('min_price', 'name')
    elif params.sort == 'price_desc':
        qs = qs.annotate(min_price=_min_price_subquery()).order_by('-min_price', 'name')
    elif params.sort == 'alpha_asc':
        qs = qs.order_by('name')
    elif params.sort == 'alpha_desc':
        qs = qs.order_by('-name')

    # Always expose min_price so the card can show a "from ₹X" price.
    if params.sort not in ('price_asc', 'price_desc'):
        qs = qs.annotate(min_price=_min_price_subquery())

    # M2M / related filters produce duplicate rows unless de-duplicated.
    # Aggregate queries already group by movie, so distinct() is only needed
    # when we are not aggregating.
    if joined and not aggregating:
        qs = qs.distinct()

    return qs


def available_cities(category='movie'):
    """Distinct cities with active shows for visible catalog items (navbar + facets).

    Scoped to a catalog category (movie / laughing_therapy / live_concert) so the
    city list never mixes event venues with cinema cities on a single tab.
    """
    if category not in CATEGORY_CHOICES:
        category = 'movie'
    return list(
        Theatre.objects.filter(
            is_active=True, shows__status='active',
            shows__movie_id__in=visible_movies().filter(category=category),
        )
        .exclude(city__isnull=True).exclude(city='')
        .values_list('city', flat=True).distinct().order_by('city')
    )


def facet_data(category='movie'):
    """Filter options shown on the discovery page, scoped to the active category.

    Kept as a subquery (not materialized) so a large catalog never builds
    a giant IN (...) list in Python. The ``__in`` lookups JOIN the M2M / FK
    through tables, so every matching row repeats once per related
    movie/show; DISTINCT collapses them to one entry per genre/language/theatre.
    Only genres/languages/theatres actually attached to that category are listed,
    so event pseudo-genres never appear on the Movies tab (and vice versa).
    """
    if category not in CATEGORY_CHOICES:
        category = 'movie'
    visible_ids = visible_movies().filter(category=category).values_list('pk', flat=True)
    genres = list(Genre.objects.filter(movies__in=visible_ids).distinct().order_by('name'))
    languages = list(Language.objects.filter(movies__in=visible_ids).distinct().order_by('name'))
    cities = available_cities(category)
    theatres = list(
        Theatre.objects.filter(is_active=True, shows__status='active', shows__movie_id__in=visible_ids)
        .distinct()
        .order_by('name')
    )
    return {
        'genres': genres,
        'languages': languages,
        'cities': cities,
        'theatres': theatres,
    }


def chip_data(params, facets):
    """Active filters rendered as removable chips. Each chip: label, param, value."""
    genre_map = {g.slug: g.name for g in facets['genres']}
    language_map = {l.code: l.name for l in facets['languages']}
    theatre_map = {t.id: t.name for t in facets['theatres']}

    chips = []
    if params.category != 'movie':
        chips.append({'label': CATEGORY_LABELS.get(params.category, params.category),
                      'param': 'category', 'value': params.category})
    if params.search:
        chips.append({'label': f'"{params.search}"', 'param': 'search', 'value': params.search})
    for slug in params.genres:
        chips.append({'label': genre_map.get(slug, slug), 'param': 'genre', 'value': slug})
    for code in params.languages:
        chips.append({'label': language_map.get(code, code), 'param': 'language', 'value': code})
    if params.city:
        chips.append({'label': params.city, 'param': 'city', 'value': params.city})
    if params.theatre_id:
        chips.append({'label': theatre_map.get(params.theatre_id, f'Theatre #{params.theatre_id}'),
                      'param': 'theatre', 'value': str(params.theatre_id)})
    if params.release:
        chips.append({'label': RELEASE_LABELS.get(params.release, params.release),
                      'param': 'release', 'value': params.release})
    if params.rating:
        chips.append({'label': f'{params.rating}+ stars', 'param': 'rating', 'value': str(params.rating)})
    if params.date:
        chips.append({'label': params.date, 'param': 'date', 'value': params.date})
    if params.price_min is not None:
        chips.append({'label': 'From ₹{}'.format(params.price_min),
                      'param': 'price_min', 'value': str(params.price_min)})
    if params.price_max is not None:
        chips.append({'label': 'Up to ₹{}'.format(params.price_max),
                      'param': 'price_max', 'value': str(params.price_max)})
    for key in params.timings:
        chips.append({'label': TIMING_LABELS.get(key, key), 'param': 'timing', 'value': key})
    return chips


def trending_movies(limit=10, category='movie'):
    """Bookings (total + recent) + wishlists + approved reviews + rating, weighted."""
    return _cached_feed(
        'feed:trending:{}:{}'.format(limit, category),
        lambda: _trending_movies(limit, category),
    )


def _trending_movies(limit, category):
    recent_cutoff = timezone.now() - timedelta(days=7)
    base = visible_movies().filter(category=category)
    bookings = Count('booking', filter=Q(booking__status='confirmed'), distinct=True)
    recent_bookings = Count(
        'booking',
        filter=Q(booking__status='confirmed', booking__booked_at__gte=recent_cutoff),
        distinct=True,
    )
    wishlists = Count('wishlisted_by', distinct=True)
    reviews = Count(
        'reviews', filter=Q(reviews__is_approved=True, reviews__is_hidden=False), distinct=True
    )
    qs = base.annotate(
        booking_count=bookings,
        recent_bookings=recent_bookings,
        wish_count=wishlists,
        review_count=reviews,
        trend_score=bookings * 10 + recent_bookings * 20 + wishlists * 2 + reviews * 3 + F('rating'),
    ).order_by('-trend_score', '-release_date', 'name')
    return list(qs[:limit])


def recently_released(limit=8, category='movie'):
    """Newly released movies, newest first. Expired/unavailable statuses excluded."""
    return _cached_feed(
        'feed:recent:{}:{}'.format(limit, category),
        lambda: _recently_released(limit, category),
    )


def _recently_released(limit, category):
    today = timezone.localdate()
    return list(
        visible_movies()
        .filter(status='now_showing', release_date__lte=today, category=category)
        .order_by('-release_date', 'name')[:limit]
    )


def similar_movies(movie, limit=6):
    """Movies sharing genres, languages, rating proximity and popularity.
    Results stay within the same category (movies vs events) so an event
    detail page always recommends related events."""
    return _cached_feed(
        'feed:similar:{}:{}:{}'.format(movie.pk, movie.category, limit),
        lambda: _similar_movies(movie, limit),
    )


def _similar_movies(movie, limit):
    genre_ids = list(movie.genres.values_list('pk', flat=True))
    language_ids = list(movie.languages.values_list('pk', flat=True))
    qs = visible_movies().exclude(pk=movie.pk).filter(category=movie.category)

    if not genre_ids and not language_ids:
        return list(qs.order_by('-rating', '-release_date')[:limit])

    qs = qs.annotate(
        genre_matches=Count('genres', filter=Q(genres__in=genre_ids), distinct=True),
        language_matches=Count('languages', filter=Q(languages__in=language_ids), distinct=True),
        booking_count=Count('booking', filter=Q(booking__status='confirmed'), distinct=True),
        rating_distance=Abs(F('rating') - movie.rating, output_field=FloatField()),
        sim_score=(
            Count('genres', filter=Q(genres__in=genre_ids), distinct=True) * 4
            + Count('languages', filter=Q(languages__in=language_ids), distinct=True) * 3
            + Count('booking', filter=Q(booking__status='confirmed'), distinct=True)
            + F('rating')
        ),
    )
    if genre_ids:
        qs = qs.filter(genre_matches__gt=0)
    else:
        qs = qs.filter(language_matches__gt=0)
    return list(qs.order_by('-sim_score', 'rating_distance', '-release_date')[:limit])


def _favourite_ids(booked_movie_ids, model):
    """Most frequent model instances across the user's confirmed bookings."""
    rows = (
        model.objects.filter(movies__id__in=booked_movie_ids)
        .annotate(weight=Count('movies', distinct=True))
        .order_by('-weight')[:5]
    )
    return list(rows.values_list('pk', flat=True))


def recommended_for_user(request, limit=10, category='movie'):
    """Personalized feed from booking history + recently viewed, trending fallback."""
    if request.user.is_authenticated:
        booked_movie_ids = list(
            Booking.objects.filter(user=request.user, status='confirmed').values_list('movie_id', flat=True)
        )
        genre_ids = _favourite_ids(booked_movie_ids, Genre) if booked_movie_ids else []
        language_ids = _favourite_ids(booked_movie_ids, Language) if booked_movie_ids else []
        favourite_theatre_names = set(
            Booking.objects.filter(user=request.user, status='confirmed')
            .values_list('theater__name', flat=True)
        )
        favourite_theatre_ids = list(
            Theatre.objects.filter(name__in=favourite_theatre_names).values_list('pk', flat=True)
        )
    else:
        booked_movie_ids = []
        genre_ids = []
        language_ids = []
        favourite_theatre_ids = []

    # Recently viewed movies contribute their genres as an extra signal.
    recent_ids = [mid for mid in request.session.get('recently_viewed', []) if isinstance(mid, int)]
    if recent_ids:
        recent_genre_ids = list(
            Genre.objects.filter(movies__id__in=recent_ids).values_list('pk', flat=True)
        )
        genre_ids = list(dict.fromkeys(genre_ids + recent_genre_ids))

    if not genre_ids and not language_ids:
        # New user / no signals -> trending feed (never empty).
        return trending_movies(limit, category=category)

    base = visible_movies().filter(category=category).exclude(pk__in=booked_movie_ids)
    genre_hits = Count('genres', filter=Q(genres__in=genre_ids), distinct=True)
    language_hits = Count('languages', filter=Q(languages__in=language_ids), distinct=True)
    theatre_hits = Count(
        'shows',
        filter=Q(shows__status='active', shows__theatre_id__in=favourite_theatre_ids),
        distinct=True,
    )
    qs = base.annotate(
        genre_matches=genre_hits,
        language_matches=language_hits,
        theatre_matches=theatre_hits,
        rec_score=genre_hits * 3 + language_hits * 2 + theatre_hits + F('rating'),
    ).filter(
        Q(genre_matches__gt=0) | Q(language_matches__gt=0) | Q(theatre_matches__gt=0)
    ).order_by('-rec_score', '-release_date')

    recommendations = list(qs[:limit])

    # Backfill from trending so the feed is never thin.
    if len(recommendations) < limit:
        seen = {m.pk for m in recommendations} | set(booked_movie_ids)
        for movie in trending_movies(limit * 2, category=category):
            if len(recommendations) >= limit:
                break
            if movie.pk not in seen:
                recommendations.append(movie)
                seen.add(movie.pk)

    return recommendations[:limit]
