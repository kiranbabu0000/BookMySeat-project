"""Booking confirmations: email + in-app notification."""
from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from admin_panel.models import Notification


def _fmt_currency(value):
    return '\u20b9{:.2f}'.format(value)


def send_booking_confirmation(user, reservation, bookings):
    """Send a confirmation email and create an in-app notification.

    Email failures never break the booking — they are logged and skipped.
    """
    show = reservation.show
    lines = [
        'Hi {},'.format(user.username),
        '',
        'Your booking for {} is confirmed!'.format(show.movie.name),
        '',
        'Cinema    : {}'.format(show.name),
        'Showtime  : {}'.format(show.time.strftime('%I:%M %p, %A, %d %b %Y')),
        'Seats     : {}'.format(', '.join(b.seat.seat_number for b in bookings)),
        'Booking refs: {}'.format(', '.join(b.booking_ref for b in bookings)),
        'Total paid: {}'.format(_fmt_currency(reservation.total_amount)),
        '',
        'You can view your tickets anytime from your profile.',
        '',
        'Enjoy the show!',
        '— BookMySeat',
    ]
    subject = 'Booking confirmed — {}'.format(show.movie.name)
    try:
        send_mail(
            subject,
            '\n'.join(lines),
            getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@bookmyseat.com'),
            [user.email],
            fail_silently=False,
        )
    except Exception:
        pass

    try:
        Notification.objects.create(
            user=user,
            title='Booking confirmed',
            message='{} seats booked for {} at {} ({})'.format(
                len(bookings),
                show.movie.name,
                show.name,
                show.time.strftime('%d %b, %I:%M %p'),
            ),
            notification_type='success',
            link=reverse('profile'),
        )
    except Exception:
        pass
