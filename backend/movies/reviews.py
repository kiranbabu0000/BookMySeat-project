"""Server-side review verification built on REAL booking/show/payment data.

A review earns the "Verified Booking" badge only while there exists a booking
by the same user for the same movie where ALL of the following hold:

1. ``Booking.status == 'confirmed'``           (never cancelled)
2. Payment completed                           (Reservation.payment_status ==
                                               'completed' or Payment.status ==
                                               'completed'; refunds/cancellations
                                               flip these off)
3. The booked show belongs to the same movie   (Booking.movie)
4. The scheduled show has ENDED                (Theater.time + movie.duration
                                               <= now, mirroring
                                               ``movies.showtime.show_end_time``;
                                               falls back to 180 minutes when the
                                               movie has no duration)
5. The review was SUBMITTED after that show    (created_at or, for edits,
   ended                                       edited_at >= show end)

Nothing here trusts client input: verification is *derived* from PostgreSQL on
every read via one correlated EXISTS subquery, so a booking cancelled or
refunded AFTER posting stops counting immediately and no stored flag can drift.
Timezone note: Theater.time is an aware datetime column (USE_TZ=True), so every
comparison below happens between absolute instants against timezone.now() —
exactly equivalent to comparing wall-clock times projected into
settings.SHOWTIME_TIME_ZONE, without naive/aware mixing.
"""
from datetime import timedelta

from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from .models import Booking

#: Fallback runtime when a movie has no duration set (matches the long-standing
#: behaviour of the previous inline eligibility checks).
DEFAULT_SHOW_DURATION_MINUTES = 180


def _duration_delta(movie):
    minutes = movie.duration or DEFAULT_SHOW_DURATION_MINUTES
    return timedelta(minutes=int(minutes))


def show_end_cutoff(movie, now=None):
    """Aware instant after which every show of ``movie`` counts as finished."""
    return (now or timezone.now()) - _duration_delta(movie)


def _payment_completed_q():
    """Completed-payment condition across both payment record styles."""
    return (
        Q(reservation__payment_status='completed')
        | Q(payment__status='completed')
    )


def _show_finished_q(movie, now=None):
    """The booked show's scheduled end time lies in the past."""
    return Q(theater__time__lte=show_end_cutoff(movie, now))


def _review_after_show_end_q(movie):
    """The reviewed row was written (or last edited) after the show ended.

    Runs INSIDE the Booking EXISTS subquery, so the review's own timestamps
    arrive as ``OuterRef`` and each booking is judged against its own show
    end time (``theater.time + duration``). A NULL ``edited_at`` simply never
    matches its branch.
    """
    duration = _duration_delta(movie)
    return (
        Q(theater__time__lte=OuterRef('created_at') - duration)
        | Q(theater__time__lte=OuterRef('edited_at') - duration)
    )


def completed_viewing_criteria(movie, user_ref, now=None):
    """Q object matching finished, paid bookings (ignores review timing).

    ``user_ref`` may be a User pk, instance or ``OuterRef('user')``.
    """
    return (
        Q(user=user_ref, movie=movie, status='confirmed')
        & _payment_completed_q()
        & _show_finished_q(movie, now)
    )


def verified_booking_criteria(movie, user_ref, now=None):
    """Full badge criteria: completed viewing AND reviewed after the end."""
    return completed_viewing_criteria(movie, user_ref, now) & _review_after_show_end_q(movie)


def find_verified_booking(user, movie, now=None):
    """Return the user's newest qualifying booking for ``movie``, or None.

    Used at submission time to attach evidence and choose the success message.
    """
    if not getattr(user, 'is_authenticated', False):
        return None
    # The review is being written NOW and every qualifying show already ended
    # (see _show_finished_q), so the review-after-end clause is implied here.
    return (
        Booking.objects.filter(completed_viewing_criteria(movie, user, now))
        .select_related('theater', 'reservation')
        .order_by('-booked_at', '-id')
        .first()
    )


def annotate_review_verification(queryset, movie, now=None):
    """Annotate each Review row with a boolean ``is_verified``.

    Single correlated EXISTS subquery per row executed inside PostgreSQL (no
    N+1 Python queries); it hits the indexed booking columns.
    """
    exists = Booking.objects.filter(
        verified_booking_criteria(movie, OuterRef('user'), now)
    ).values('pk')
    return queryset.annotate(is_verified=Exists(exists))


def has_completed_viewing(user, movie, now=None):
    """True when the user holds a finished, paid booking for this movie.

    Deliberately ignores *when the review was written* — this drives the
    "your booking earns a verified badge" hint under the review form, not the
    badge itself.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    return Booking.objects.filter(
        completed_viewing_criteria(movie, user, now)
    ).exists()
