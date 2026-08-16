"""Shared ticket QR scanning logic.

Both the public gate endpoint (``verify_ticket_qr``) and the admin scanner
use this single code path so signature validation, one-time claiming and the
scan-history audit trail stay consistent. Nothing here trusts the client: the
QR payload is HMAC-signed and verified server-side, and the one-time claim is
an atomic guarded update so simultaneous scans cannot double-admit a ticket.
"""
from django.db.models import F
from django.http import JsonResponse
from django.utils import timezone

from .models import Booking, Reservation, TicketScan
from .qr import verify_qr_payload


REASON_LABELS = {
    'invalid_signature': 'This QR code is not a valid BookMySeat ticket.',
    'not_found': 'No matching booking was found for this ticket.',
    'already_scanned': 'This ticket has already been used for entry.',
    'unpaid': 'This booking was never paid for.',
    'cancelled': 'This booking was cancelled / refunded.',
}


def resolve_ticket_target(booking_ref):
    """Resolve a booking reference to its (Reservation, Booking) pair.

    Supports the transaction-level booking_ref (BMS39DBA878) as well as
    legacy per-seat references (Booking.booking_ref or a numeric Booking id)
    so existing tickets keep working after the model mapping.
    """
    reservation = Reservation.objects.filter(booking_ref=booking_ref).select_related(
        'user', 'show', 'show__movie'
    ).prefetch_related('bookings__seat', 'bookings__payment', 'show__movie__languages').first()
    if reservation:
        return reservation, None
    booking = Booking.objects.filter(booking_ref=booking_ref).select_related(
        'user', 'movie', 'theater', 'seat', 'reservation', 'payment'
    ).prefetch_related('movie__languages').first()
    if booking:
        return None, booking
    if booking_ref.isdigit():
        booking = Booking.objects.filter(id=int(booking_ref)).select_related(
            'user', 'movie', 'theater', 'seat', 'reservation', 'payment'
        ).prefetch_related('movie__languages').first()
        if booking:
            return None, booking
    return None, None


def _context_names(payload, target=None):
    """Best-effort movie/theatre/show-time/seats labels for display and the audit trail."""
    movie = payload.get('movie') or ''
    theatre = payload.get('theatre') or ''
    show_time = None
    seats = payload.get('seats') or []
    if target is not None:
        if isinstance(target, Reservation):
            if not movie and target.show_id:
                movie = target.show.movie.name
            if not theatre and target.show_id:
                theatre = target.show.name
            if not show_time and target.show_id:
                show_time = target.show.time
            if not seats:
                seats = list(
                    target.bookings.select_related('seat')
                    .values_list('seat__seat_number', flat=True)
                )
        else:
            if not movie and target.movie_id:
                movie = target.movie.name
            if not theatre and target.theater_id:
                theatre = target.theater.name
            if not show_time and target.theater_id:
                show_time = target.theater.time
            if not seats and target.seat_id:
                seats = [target.seat.seat_number]
    return (
        str(movie or ''),
        str(theatre or ''),
        show_time,
        [str(s) for s in (seats or [])],
    )


def record_scan(payload, result, scanned_by=None, ip_address=None, target=None):
    """Persist one scan-attempt audit row (best-effort, never blocks the scan)."""
    if not isinstance(payload, dict):
        payload = {}
    if result not in dict(TicketScan.RESULT_CHOICES):
        result = 'not_found'
    booking_ref = str(payload.get('booking_id') or '')
    movie, theatre, show_time, seats = _context_names(payload, target)
    scanned_by = scanned_by if getattr(scanned_by, 'is_authenticated', False) else None
    try:
        TicketScan.objects.create(
            booking_ref=booking_ref,
            movie=movie[:255],
            theatre=theatre[:255],
            show_time=show_time,
            seats=', '.join(seats)[:500],
            result=result,
            scanned_by=scanned_by,
            ip_address=ip_address,
        )
    except Exception:
        pass


def _refuse(reason, payload, scanned_by, ip_address, target=None):
    result = {
        'invalid_signature': 'invalid',
        'already_scanned': 'already_scanned',
        'unpaid': 'unpaid',
        'cancelled': 'cancelled',
        'not_found': 'not_found',
    }.get(reason, 'not_found')
    record_scan(payload, result, scanned_by, ip_address, target=target)
    _, _, show_time, _ = _context_names(payload, target)
    status = 400 if reason == 'invalid_signature' else 200
    return JsonResponse({
        'valid': False,
        'reason': reason,
        'message': REASON_LABELS.get(reason, ''),
        'booking_ref': str(payload.get('booking_id') or ''),
        'movie': payload.get('movie'),
        'theatre': payload.get('theatre'),
        'show_time': show_time.isoformat() if show_time else None,
        'seats': payload.get('seats'),
    }, status=status)


def _claim_scan(target, payload, scanned_by, ip_address):
    """Atomically claim a confirmed ticket and return the gate response.

    The first caller that sees ``scanned_at`` unset wins the scan (guarded by
    an update filter), so simultaneous scans cannot double-claim a ticket.
    """
    now = timezone.now()
    _, _, show_time, _ = _context_names(payload, target)
    if target.scanned_at:
        record_scan(payload, 'already_scanned', scanned_by, ip_address, target=target)
        return JsonResponse({
            'valid': False,
            'used': True,
            'reason': 'already_scanned',
            'message': REASON_LABELS['already_scanned'],
            'scanned_at': target.scanned_at.isoformat(),
            'scan_count': target.scan_count,
            'booking_ref': target.booking_ref,
            'movie': payload.get('movie'),
            'theatre': payload.get('theatre'),
            'show_time': show_time.isoformat() if show_time else None,
            'seats': payload.get('seats'),
        })
    claimed = type(target).objects.filter(
        pk=target.pk, scanned_at__isnull=True
    ).update(scanned_at=now, scan_count=F('scan_count') + 1)
    if claimed == 0:
        target.refresh_from_db(fields=['scanned_at', 'scan_count'])
        record_scan(payload, 'already_scanned', scanned_by, ip_address, target=target)
        return JsonResponse({
            'valid': False,
            'used': True,
            'reason': 'already_scanned',
            'message': REASON_LABELS['already_scanned'],
            'scanned_at': target.scanned_at.isoformat() if target.scanned_at else None,
            'scan_count': target.scan_count,
            'booking_ref': target.booking_ref,
            'movie': payload.get('movie'),
            'theatre': payload.get('theatre'),
            'show_time': show_time.isoformat() if show_time else None,
            'seats': payload.get('seats'),
        })
    record_scan(payload, 'admitted', scanned_by, ip_address, target=target)
    return JsonResponse({
        'valid': True,
        'scanned': True,
        'message': 'Entry allowed.',
        'booking_ref': target.booking_ref,
        'movie': payload.get('movie'),
        'theatre': payload.get('theatre'),
        'show_time': show_time.isoformat() if show_time else None,
        'seats': payload.get('seats'),
        'scanned_at': now.isoformat(),
        'scan_count': target.scan_count + 1,
    })


def scan_ticket(payload, *, scanned_by=None, ip_address=None):
    """Validate a signed ticket QR payload and atomically claim it.

    Returns a JsonResponse suitable for both the public gate API and the admin
    scanner. Every attempt with a verifiable signature is recorded in
    TicketScan for the audit trail.
    """
    if not isinstance(payload, dict):
        return _refuse('invalid_signature', payload, scanned_by, ip_address)
    payload = dict(payload)
    if not verify_qr_payload(payload):
        return _refuse('invalid_signature', payload, scanned_by, ip_address)
    booking_ref = str(payload.get('booking_id') or '')
    reservation, booking = resolve_ticket_target(booking_ref)
    if reservation:
        if reservation.status != 'booked':
            reason = 'cancelled' if reservation.status == 'cancelled' else 'unpaid'
            return _refuse(reason, payload, scanned_by, ip_address, target=reservation)
        return _claim_scan(reservation, payload, scanned_by, ip_address)
    if booking:
        if booking.status != 'confirmed':
            return _refuse('cancelled', payload, scanned_by, ip_address, target=booking)
        return _claim_scan(booking, payload, scanned_by, ip_address)
    return _refuse('not_found', payload, scanned_by, ip_address)
