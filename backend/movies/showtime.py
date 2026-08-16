"""
Real-Time Showtime Validation & Late-Entry Warning system.

Single source of truth for a show's live booking status:

    UPCOMING   -> ``now < show.time``                    (normal booking)
    LATE ENTRY -> ``show.time <= now < deadline``        (bookable, requires warning)
    EXPIRED    -> ``now >= deadline``                    (never bookable)

The *deadline* is the end of the configurable late-entry window
(``settings.LATE_ENTRY_WINDOW_MINUTES``, default 30) capped by the movie's end
time (``show.time + movie.duration``) when the movie has a duration, so a show
can never be joined after it has actually ended.

All wall-clock computation and human display happens in the theatre timezone
(``settings.SHOWTIME_TIME_ZONE`` / ``settings.TIME_ZONE``, default
Asia/Kolkata). Server-side booking entry points (``movies.services``,
``movies.payments``) call :func:`assert_show_bookable` so the backend is the
final authority — the frontend only mirrors the status for display and never
decides what is bookable.
"""
from datetime import datetime as _datetime
from datetime import date as _date_type
from datetime import time as _time_type
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

#: Human label for each dynamic status. Shown on the public site and the
#: admin show list so users/staff always know what "now" means for a show.
STATUS_LABELS = {
    'upcoming': 'UPCOMING',
    'late_entry': 'LIVE · LATE ENTRY',
    'expired': 'EXPIRED',
}

#: CSS / badge tone used by the frontend and admin list.
STATUS_LEVELS = {
    'upcoming': 'success',
    'late_entry': 'warning',
    'expired': 'danger',
}

#: Default expiry message surfaced to users trying to book a dead show.
EXPIRED_MESSAGE = (
    'This show is no longer available for booking — its late-entry window has closed.'
)

#: Message shown on the seat page / theater list for a late-entry show.
LATE_ENTRY_HINT = (
    'This show has already started. You can still book during the late-entry '
    'window, but you may miss the beginning.'
)


def showtime_zone():
    """Return the theatre timezone (Asia/Kolkata by default)."""
    return ZoneInfo(settings.SHOWTIME_TIME_ZONE or settings.TIME_ZONE)


def late_entry_window():
    """Minutes after start during which a show is bookable (with warning)."""
    try:
        return int(getattr(settings, 'LATE_ENTRY_WINDOW_MINUTES', 30) or 30)
    except (TypeError, ValueError):
        return 30


def local_now():
    """Current time expressed in the theatre timezone."""
    return timezone.now().astimezone(showtime_zone())


def to_local(value):
    """Convert an aware datetime into the theatre timezone (for display)."""
    if value is None:
        return None
    if timezone.is_naive(value):
        return value
    return value.astimezone(showtime_zone())


def aware_showtime(day, show_time):
    """Build the aware datetime for an admin ``Show`` (date + time) in IST."""
    if isinstance(day, _datetime):
        day = day.date()
    return timezone.make_aware(
        _datetime.combine(day, show_time), showtime_zone()
    )


def day_range_utc(day):
    """UTC bounds covering one entire theatre-timezone calendar day.

    Handles the midnight boundary correctly: a 12:30 AM IST show is stored as
    the previous UTC day, so date filtering must use explicit aware ranges
    instead of ``time__date``.
    """
    if isinstance(day, _datetime):
        day = day.date()
    local_start = timezone.make_aware(
        _datetime.combine(day, _time_type.min), showtime_zone()
    )
    local_end = local_start + timedelta(days=1)
    return local_start, local_end


def show_end_time(show):
    """Movie end time (``show.time + movie.duration``) or ``None``."""
    duration = getattr(show.movie, 'duration', None)
    if not duration:
        return None
    return show.time + timedelta(minutes=int(duration))


def late_entry_deadline(show):
    """Instant after which the show is no longer bookable.

    The late-entry window capped by the movie end time, whichever comes first.
    """
    deadline = show.time + timedelta(minutes=late_entry_window())
    end = show_end_time(show)
    if end is not None and end < deadline:
        return end
    return deadline


def show_status(show, now=None):
    """Return ``'upcoming'``, ``'late_entry'`` or ``'expired'``."""
    now = now or timezone.now()
    if now < show.time:
        return 'upcoming'
    if now < late_entry_deadline(show):
        return 'late_entry'
    return 'expired'


def show_bookable(show, now=None):
    """True only while the show is upcoming or inside the late-entry window."""
    return show_status(show, now) != 'expired'


def minutes_since(value, now=None):
    """Whole minutes elapsed since ``value`` (never negative)."""
    now = now or timezone.now()
    delta = now - value
    if delta <= timedelta(0):
        return 0
    return max(0, int(delta.total_seconds() // 60))


def show_status_info(show, now=None):
    """Render-ready status payload for templates / JSON / admin list."""
    now = now or timezone.now()
    status = show_status(show, now)
    deadline = late_entry_deadline(show)
    end = show_end_time(show)
    info = {
        'status': status,
        'label': STATUS_LABELS[status],
        'level': STATUS_LEVELS[status],
        'bookable': status != 'expired',
        'started_minutes_ago': minutes_since(show.time, now) if status != 'upcoming' else 0,
        'deadline': deadline,
        'deadline_local': to_local(deadline),
        'end_time_local': to_local(end),
        'start_time_local': to_local(show.time),
        'late_entry_window': late_entry_window(),
        'message': EXPIRED_MESSAGE if status == 'expired' else LATE_ENTRY_HINT,
    }
    return info


def assert_show_bookable(show, now=None, error=None):
    """Raise ``ReservationError`` when a show must no longer accept bookings.

    Used by every server-side booking / payment entry point so the backend is
    the final authority on showtime validity.
    """
    if show_bookable(show, now):
        return
    from .services import ReservationError  # deferred: avoids circular import

    raise ReservationError(error or EXPIRED_MESSAGE)
