"""Services for keeping the admin Show model and the booking-flow Theater model in sync.

The public booking pipeline (seat map, reservations, bookings) is driven by
``movies.Theater`` rows, while the admin panel manages ``admin_panel.Show`` rows.
These helpers bridge the two so that shows created in the admin panel become
bookable on the customer side and status changes are reflected both ways.
"""
from datetime import datetime
from math import ceil

from django.utils import timezone

from movies.models import Theater, Seat


def _row_label(index):
    """0 -> A, 1 -> B, ..., 25 -> Z (26 rows max)."""
    if index >= 26:
        return f'R{index + 1}'
    return chr(ord('A') + index)


def create_seats_for_theater(theater, capacity):
    """Bulk-create seat rows for a newly created booking-flow theater."""
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
    from the screen capacity so the show is immediately bookable.
    """
    show_datetime = _show_datetime(show)
    if show.theater_id is None:
        theater = Theater.objects.create(
            name=show.theatre.name,
            movie=show.movie,
            time=show_datetime,
            screen_name=show.screen.name,
            ticket_price=show.ticket_price,
            status=show.status,
        )
        show.theater = theater
        show.save(update_fields=['theater'])
        create_seats_for_theater(theater, show.screen.capacity)
        return theater

    Theater.objects.filter(pk=show.theater_id).update(
        name=show.theatre.name,
        movie=show.movie,
        time=show_datetime,
        screen_name=show.screen.name,
        ticket_price=show.ticket_price,
        status=show.status,
    )
    return Theater.objects.get(pk=show.theater_id)


def hide_theater_for_show(show, status='cancelled'):
    """Reflect a Show-level status change on the linked booking-flow Theater."""
    if show.theater_id is not None:
        Theater.objects.filter(pk=show.theater_id).update(status=status)
