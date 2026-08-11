"""Services for keeping the admin Show model and the booking-flow Theater model in sync.

The public booking pipeline (seat map, reservations, bookings) is driven by
``movies.Theater`` rows, while the admin panel manages ``admin_panel.Show`` rows.
These helpers bridge the two so that shows created in the admin panel become
bookable on the customer side and status changes are reflected both ways.
"""
from datetime import datetime, timedelta
from math import ceil

from django.utils import timezone

from movies.models import Movie, Seat, SeatCategory, ShowPrice, Theater

from .layouts import build_layout_spec
from .models import Show


def _row_label(index):
    """0 -> A, 1 -> B, ..., 25 -> Z (26 rows max)."""
    if index >= 26:
        return f'R{index + 1}'
    return chr(ord('A') + index)


def _ensure_categories(band_names):
    """Create any SeatCategory rows referenced by a layout (idempotent)."""
    created = []
    for idx, name in enumerate(band_names):
        obj, was_created = SeatCategory.objects.get_or_create(
            name=name,
            defaults={'display_order': idx},
        )
        if was_created:
            created.append(obj)
    return created


def create_seats_for_theater(theater, capacity, layout_spec=None):
    """Bulk-create seats for a booking-flow theater.

    When a layout spec is available the seat grid is generated per the
    geometry (sections, aisles, couple and wheelchair seats, best-view rows).
    Otherwise a simple grid is used as a fallback.
    """
    layout_spec = layout_spec or theater.layout_spec
    if layout_spec:
        _ensure_categories({s['category'] for s in layout_spec['seats']})
        category_lookup = {
            cat.name: cat for cat in SeatCategory.objects.filter(name__in={
                s['category'] for s in layout_spec['seats']
            })
        }
        couple_group = 0
        pair_lookup = {}
        for pair in layout_spec.get('couple_pairs', []):
            couple_group += 1
            for num in pair:
                pair_lookup[num] = couple_group
        seats = []
        for s in layout_spec['seats']:
            seats.append(Seat(
                theater=theater,
                seat_number=s['num'],
                is_booked=False,
                seat_type=s['type'],
                category=category_lookup.get(s['category']),
                row_label=s['row'],
                row_idx=s['r'],
                col_idx=s['c'],
                side=s['side'],
                gap_before=s['gap_before'],
                is_best_view=s['best_view'],
                couple_group=pair_lookup.get(s['num'], 0),
            ))
        Seat.objects.bulk_create(seats, ignore_conflicts=True)
        theater.layout_spec = layout_spec
        theater.save(update_fields=['layout_spec'])
        return len(seats)

    if not capacity or capacity <= 0:
        return 0
    seats_per_row = 30 if capacity >= 450 else 20
    rows = max(1, min(26, ceil(capacity / seats_per_row)))
    seats = [
        Seat(theater=theater, seat_number=f'{_row_label(r)}{s}', is_booked=False)
        for r in range(rows)
        for s in range(1, seats_per_row + 1)
    ]
    Seat.objects.bulk_create(seats, ignore_conflicts=True)
    return len(seats)


def _show_datetime(show):
    return timezone.make_aware(datetime.combine(show.date, show.time))


def sync_theater_from_show(show):
    """Create or update the ``movies.Theater`` row backing an admin ``Show``.

    Returns the linked Theater instance. On first creation, seats are generated
    from the screen layout so the show is immediately bookable.
    """
    show_datetime = _show_datetime(show)
    layout_spec = show.screen.get_layout_spec() if show.screen else {}
    if show.theater_id is None:
        theater = Theater.objects.create(
            name=show.theatre.name,
            movie=show.movie,
            time=show_datetime,
            screen_name=show.screen.name,
            ticket_price=show.ticket_price,
            status=show.status,
            layout_spec=layout_spec,
        )
        show.theater = theater
        show.save(update_fields=['theater'])
        create_seats_for_theater(theater, show.screen.capacity, layout_spec=layout_spec)
        return theater

    Theater.objects.filter(pk=show.theater_id).update(
        name=show.theatre.name,
        movie=show.movie,
        time=show_datetime,
        screen_name=show.screen.name,
        ticket_price=show.ticket_price,
        status=show.status,
        layout_spec=layout_spec,
    )
    return Theater.objects.get(pk=show.theater_id)


def hide_theater_for_show(show, status='cancelled'):
    """Reflect a Show-level status change on the linked booking-flow Theater."""
    if show.theater_id is not None:
        Theater.objects.filter(pk=show.theater_id).update(status=status)


SCHEDULE_HORIZON_DAYS = 4


def _schedule_template(movie, window_last):
    """Daily (theatre, screen, time, price) slate of the most recent scheduled
    day at or before the rolling window — the pattern re-applied to new days."""
    last = (
        Show.objects.filter(movie=movie, status='active', date__lte=window_last)
        .order_by('-date', '-time')
        .first()
    )
    if last is None:
        return []
    return list(
        Show.objects.filter(movie=movie, status='active', date=last.date)
        .order_by('theatre_id', 'screen_id', 'time')
        .values_list('theatre_id', 'screen_id', 'time', 'ticket_price')
    )


def _copy_pricing(source, target):
    """Copy a booking-flow theater's per-category price catalog to another."""
    rows = list(ShowPrice.objects.filter(theater=source).select_related('category'))
    if not rows:
        return
    ShowPrice.objects.bulk_create(
        [ShowPrice(theater=target, category=r.category, price=r.price) for r in rows],
        ignore_conflicts=True,
    )


def ensure_movie_schedule(movie, horizon=SCHEDULE_HORIZON_DAYS):
    """Roll a movie's shows forward so every one of the next ``horizon`` days
    carries its usual daily slate. Idempotent — existing shows are kept.

    Days are never deleted; as calendar days pass the date tabs roll forward
    and this fills the freshly appearing days with the same theatres/screens/
    times, each backed by a bookable ``movies.Theater`` row with seats.
    """
    if horizon < 1:
        return 0
    today = timezone.localdate()
    window_last = today + timedelta(days=horizon - 1)
    template = _schedule_template(movie, window_last)
    if not template:
        return 0
    created = 0
    for day in (today + timedelta(days=i) for i in range(horizon)):
        existing = set(
            Show.objects.filter(movie=movie, date=day, status='active')
            .values_list('theatre_id', 'screen_id', 'time')
        )
        for theatre_id, screen_id, show_time, ticket_price in template:
            if day == today:
                start = timezone.make_aware(datetime.combine(day, show_time))
                if start <= timezone.now():
                    continue
            if (theatre_id, screen_id, show_time) in existing:
                continue
            show, was_created = Show.objects.get_or_create(
                movie=movie,
                theatre_id=theatre_id,
                screen_id=screen_id,
                date=day,
                time=show_time,
                defaults={'ticket_price': ticket_price, 'status': 'active'},
            )
            if not was_created and show.status != 'active':
                Show.objects.filter(pk=show.pk).update(status='active')
            theater = sync_theater_from_show(show)
            source = (
                Theater.objects.filter(
                    admin_show__movie=movie,
                    admin_show__theatre_id=theatre_id,
                    admin_show__screen_id=screen_id,
                    prices__isnull=False,
                )
                .exclude(pk=theater.pk)
                .order_by('id')
                .first()
            )
            if source is not None:
                _copy_pricing(source, theater)
            created += 1
    return created


def ensure_rolling_schedule(horizon=SCHEDULE_HORIZON_DAYS):
    """Roll the schedule forward for every movie that currently has a show."""
    movie_ids = (
        Show.objects.filter(status='active').values_list('movie_id', flat=True).distinct()
    )
    return sum(
        ensure_movie_schedule(movie, horizon)
        for movie in Movie.objects.filter(pk__in=movie_ids)
    )
