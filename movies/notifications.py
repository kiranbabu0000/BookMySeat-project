"""Booking confirmations: email + in-app notification."""
from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from admin_panel.models import Notification


def _fmt_currency(value):
    return '\u20b9{:.2f}'.format(value)


def _send_confirmation(user, bookings, show, total, payment_tx=None):
    """Send a confirmation email and create an in-app notification."""
    lines = [
        'Hi {},'.format(user.username),
        '',
        'Your booking for {} is confirmed!'.format(show.movie.name),
        '',
        'Movie       : {}'.format(show.movie.name),
        'Cinema      : {}'.format(show.name),
        'Showtime    : {}'.format(show.time.strftime('%I:%M %p, %A, %d %b %Y')),
        'Seats       : {}'.format(', '.join(b.seat.seat_number for b in bookings)),
        'Seat count  : {}'.format(len(bookings)),
        'Booking refs: {}'.format(', '.join(b.booking_ref for b in bookings)),
        'Total paid  : {}'.format(_fmt_currency(total)),
    ]
    payment_id = ''
    if payment_tx is not None:
        payment_id = payment_tx.gateway_payment_id or ''
        if payment_tx.gateway_order_id:
            lines.append('Order ID    : {}'.format(payment_tx.gateway_order_id))
    if not payment_id and bookings:
        try:
            payment_id = bookings[0].payment.transaction_id or ''
        except Exception:
            payment_id = ''
    if payment_id:
        lines.append('Payment ID  : {}'.format(payment_id))
    lines.extend([
        '',
        'You can view your tickets anytime from your profile.',
        '',
        'Enjoy the show!',
        '— BookMySeat',
    ])
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


def send_booking_confirmation(user, reservation, bookings):
    """Send a confirmation email and create an in-app notification.

    Email failures never break the booking — they are logged and skipped.
    """
    payment_tx = (
        reservation.transactions.filter(status='captured')
        .order_by('-captured_at')
        .first()
    )
    _send_confirmation(
        user, bookings, reservation.show, reservation.total_amount,
        payment_tx=payment_tx,
    )


def send_manual_booking_confirmation(user, bookings):
    """Send a confirmation for admin/walk-in bookings that have no reservation."""
    if not bookings:
        return
    show = bookings[0].theater
    total = sum((b.total for b in bookings), 0)
    _send_confirmation(user, bookings, show, total)
