"""Booking confirmations: HTML + plain-text email, optional PDF, in-app notification.

Email delivery is asynchronous via the database-backed outbox
(``movies.EmailOutbox``). ``send_booking_confirmation`` only performs a fast
INSERT — the booking request never blocks on SMTP. Pending messages are sent by
the ``process_email_outbox`` management command (cron job or ``--loop`` worker;
``runserver`` auto-starts it locally), which retries failed deliveries
automatically with exponential backoff up to ``EMAIL_OUTBOX_MAX_ATTEMPTS``.
"""
import base64
import json
import logging
import re
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from admin_panel.models import Notification
from movies.qr import build_qr_payload, ticket_qr_png_bytes
from .models import EmailOutbox

logger = logging.getLogger(__name__)


def _fmt_currency(value):
    return '\u20b9{:.2f}'.format(value)


def _data_uri(png_bytes):
    """Base64 data URI for embedding a PNG directly inside an email body."""
    return 'data:image/png;base64,{}'.format(
        base64.b64encode(bytes(png_bytes)).decode('ascii')
    )


def logo_data_uri():
    """Base64 data URI of the (downscaled) BookMySeat logo, or '' if missing."""
    for name in ('logo_email.png', 'logo.png'):
        try:
            path = Path(settings.PROJECT_ROOT) / 'frontend' / 'static' / 'img' / name
            with open(path, 'rb') as handle:
                return _data_uri(handle.read())
        except Exception:
            continue
    return ''


def _absolute_url(path):
    site_url = getattr(settings, 'SITE_URL', '').rstrip('/')
    if site_url:
        return site_url + path
    return path


def _plain_lines(user, show, bookings, total, payment_tx=None, reservation=None):
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
        'Booking ref : {}'.format(reservation.booking_ref if reservation else ', '.join(b.booking_ref for b in bookings)),
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
    return lines, payment_id


def _build_pdf_bytes(context):
    """Best-effort PDF ticket generation; returns b'' when reportlab is missing."""
    try:
        from movies.pdf import build_ticket_pdf
        return build_ticket_pdf(context) or b''
    except Exception:
        return b''


def _email_content(user, show, bookings, total, payment_tx=None, reservation=None):
    """Render subject, plain/html bodies and the PDF attachment (no SMTP I/O)."""
    seats = [b.seat.seat_number for b in bookings]
    booking_ref = reservation.booking_ref if reservation else ', '.join(b.booking_ref for b in bookings)
    plain_lines, payment_id = _plain_lines(user, show, bookings, total, payment_tx, reservation)

    payment_method = payment_tx.method if payment_tx else 'Online'
    transaction_id = payment_tx.gateway_payment_id if payment_tx else ''
    if not transaction_id and bookings:
        try:
            transaction_id = bookings[0].payment.transaction_id or ''
            payment_method = bookings[0].payment.payment_method or payment_method
        except Exception:
            pass

    qr_payload = build_qr_payload(booking_ref, show.movie.name, show.name, seats)
    qr_bytes = ticket_qr_png_bytes(qr_payload) or b''
    context = {
        'user': user,
        'movie_name': show.movie.name,
        'theatre_name': show.name,
        'screen_name': show.screen_name or 'Main',
        'show_time': show.time,
        'seats': seats,
        'seat_label': ', '.join(seats),
        'ticket_count': len(bookings),
        'booking_ref': booking_ref,
        'total': total,
        'total_label': _fmt_currency(total),
        'payment_id': payment_id,
        'payment_method': payment_method or 'Online',
        'transaction_id': transaction_id,
        'ticket_url': _absolute_url(reverse('download_ticket', args=[booking_ref])),
        'pdf_url': _absolute_url(reverse('ticket_pdf', args=[booking_ref])),
        'profile_url': _absolute_url(reverse('profile')),
        'qr_payload': qr_payload,
        'has_qr': bool(qr_bytes),
        'logo_data_uri': logo_data_uri(),
        'site_url': _absolute_url('/'),
    }

    try:
        html_body = render_to_string('emails/booking_confirmation.html', context)
    except Exception:
        html_body = ''
    # Embed the QR directly in the HTML so it renders inline (never as a
    # separate attachment) in every mail client.
    if qr_bytes and html_body:
        html_body = html_body.replace(
            'cid:qr_ticket', _data_uri(qr_bytes)
        )
    return {
        'subject': 'Booking confirmed — {}'.format(show.movie.name),
        'plain_body': '\n'.join(plain_lines),
        'html_body': html_body,
        'pdf_filename': 'ticket_{}.pdf'.format(booking_ref),
        'pdf_bytes': _build_pdf_bytes(context),
        'qr_bytes': qr_bytes,
    }


def _enqueue(user, show, bookings, total, payment_tx=None, reservation=None):
    """Persist one confirmation email to the outbox. Never blocks on SMTP."""
    recipient = (user.email or '').strip()
    if not recipient:
        logger.warning(
            'EMAIL RECIPIENT MISSING: user=%s has no email address; '
            'confirmation email skipped for movie=%s. The booking is unaffected.',
            getattr(user, 'username', '?'),
            show.movie.name if show and show.movie else '?',
        )
        return None
    try:
        content = _email_content(user, show, bookings, total, payment_tx, reservation)
        outbox = EmailOutbox.objects.create(
            recipient=recipient,
            subject=content['subject'],
            plain_body=content['plain_body'],
            html_body=content['html_body'],
            pdf_filename=content['pdf_filename'],
            pdf_attachment=content['pdf_bytes'] or b'',
            qr_image=content['qr_bytes'] or b'',
            max_attempts=getattr(settings, 'EMAIL_OUTBOX_MAX_ATTEMPTS', 6),
        )
    except Exception as exc:  # noqa: BLE001 - the booking must never fail on email
        logger.warning(
            'EMAIL ENQUEUE FAILED for %s (movie=%s): %s',
            recipient, show.movie.name if show and show.movie else '?', exc,
        )
        return None
    logger.info(
        'EMAIL ENQUEUED outbox=%s recipient=%s movie=%s',
        outbox.pk, recipient, show.movie.name if show and show.movie else '?',
    )
    return outbox


def _create_in_app_notification(user, show, bookings):
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
    """Enqueue a confirmation email + create an in-app notification.

    Asynchronous: the message is written to the EmailOutbox and delivered by the
    ``process_email_outbox`` worker. Email failures never break the booking.
    """
    payment_tx = (
        reservation.transactions.filter(status='captured')
        .order_by('-captured_at')
        .first()
    )
    _enqueue(
        user, reservation.show, bookings, reservation.total_amount,
        payment_tx=payment_tx, reservation=reservation,
    )
    _create_in_app_notification(user, reservation.show, bookings)


def send_manual_booking_confirmation(user, bookings):
    """Enqueue a confirmation for admin/walk-in bookings that have no reservation."""
    if not bookings:
        return
    show = bookings[0].theater
    total = sum((b.total for b in bookings), 0)
    _enqueue(user, show, bookings, total)
    _create_in_app_notification(user, show, bookings)


def _from_address():
    """Split DEFAULT_FROM_EMAIL into (name, email) for the Brevo API."""
    value = getattr(settings, 'DEFAULT_FROM_EMAIL', '') or ''
    match = re.search(r'<([^>]+)>', value)
    email = match.group(1) if match else value
    name = re.sub(r'\s*<[^>]+>', '', value).strip() or 'BookMySeat'
    return name, email


def _send_via_brevo(outbox):
    """Deliver a single outbox message through Brevo's HTTPS API (port 443).

    Render's free tier blocks SMTP ports (25/465/587); HTTPS is unaffected, so
    production sends email as a JSON POST instead of a raw SMTP connection.
    Raises on failure so the worker records the error and schedules a retry.
    """
    name, email = _from_address()
    payload = {
        'sender': {'name': name, 'email': email},
        'to': [{'email': outbox.recipient}],
        'subject': outbox.subject,
        'textContent': outbox.plain_body or '',
        'htmlContent': outbox.html_body or outbox.plain_body or '',
        'attachment': [],
    }
    if outbox.pdf_attachment:
        payload['attachment'].append({
            'content': base64.b64encode(bytes(outbox.pdf_attachment)).decode('ascii'),
            'name': outbox.pdf_filename or 'ticket.pdf',
        })
    if not payload['attachment']:
        payload.pop('attachment')
    request = Request(
        'https://api.brevo.com/v3/smtp/email',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'api-key': settings.BREVO_API_KEY,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        method='POST',
    )
    try:
        with urlopen(request, timeout=15) as response:
            status = getattr(response, 'status', 200)
    except (URLError, OSError) as exc:
        raise RuntimeError('Brevo API request failed: {}'.format(exc))
    if status not in (200, 201):
        raise RuntimeError('Brevo API returned HTTP {}'.format(status))


def send_outbox_message(outbox):
    """Send a single outbox message (Brevo API when configured, SMTP otherwise).

    Returns True on success. Any exception is recorded on the row so the worker
    can schedule the next backoff attempt. No retries happen here — the worker
    (``process_email_outbox``) owns the retry/backoff lifecycle.
    """
    try:
        if getattr(settings, 'BREVO_API_KEY', ''):
            _send_via_brevo(outbox)
        else:
            message = EmailMultiAlternatives(
                outbox.subject,
                outbox.plain_body,
                getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@bookmyseat.com'),
                [outbox.recipient],
            )
            if outbox.html_body:
                message.attach_alternative(outbox.html_body, 'text/html')
            if outbox.pdf_attachment:
                message.attach(
                    outbox.pdf_filename or 'ticket.pdf',
                    bytes(outbox.pdf_attachment),
                    'application/pdf',
                )
            message.send(fail_silently=False)
        logger.info(
            'EMAIL SENT outbox=%s recipient=%s', outbox.pk, outbox.recipient,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - failures are recorded for retry
        outbox.last_error = str(exc)[:500]
        outbox.save(update_fields=['last_error', 'updated_at'])
        logger.warning(
            'EMAIL SEND FAILED outbox=%s recipient=%s error=%s',
            outbox.pk, outbox.recipient, exc,
        )
        return False


def backoff_delay(attempts, base_seconds=None):
    """Exponential backoff (seconds) for the given number of past failures.

    Returns base * 2 ** (attempts - 1), capped at 24 hours. ``attempts`` is the
    number of attempts already made (so the first failure schedules base).
    """
    base = base_seconds or getattr(settings, 'EMAIL_OUTBOX_BACKOFF_BASE', 60)
    if attempts <= 0:
        return base
    delay = base * (2 ** (attempts - 1))
    return min(delay, 86400)
