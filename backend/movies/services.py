"""Core seat reservation business logic.

All seat state transitions are enforced here inside explicit transactions.
Concurrency is protected with select_for_update() row locking; the unique
OneToOne relationship on ReservedSeat.seat provides a database-level backstop
so the same seat can never be part of two active reservations.
"""
import logging
import secrets
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction, IntegrityError
from django.db.models import Exists, F, OuterRef
from django.utils import timezone

from admin_panel.models import Coupon, GSTSlab, Payment, PaymentTransaction, PricingConfig
from .models import (
    RESERVATION_HOLD_SECONDS,
    Booking,
    Reservation,
    ReservedSeat,
    Seat,
    SeatCategory,
    ShowPrice,
    Theater,
)
from .showtime import assert_show_bookable

logger = logging.getLogger(__name__)


def _round2(value):
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


ECONOMY_MIN_PRICE = Decimal('60.00')


def is_economy_category(category):
    """True when a seat category is the discounted Economy band."""
    return category is not None and category.name.strip().lower() == 'economy'


def economy_ticket_price(base):
    """Economy rows are priced at 50% of the normal price (min ₹60; taxes extra)."""
    half = _round2(Decimal(base) * Decimal('0.5'))
    return half if half >= ECONOMY_MIN_PRICE else ECONOMY_MIN_PRICE


def row_of(seat_number):
    return (seat_number or '').rstrip('0123456789').upper() or 'Z'


def _row_index(row):
    """Numeric row band for ordering; multi-letter rows sit beyond 'Z'."""
    row = (row or '').upper()
    if len(row) == 1 and 'A' <= row <= 'Z':
        return ord(row)
    return 1000


def _load_categories():
    return list(SeatCategory.objects.all())


def _price_map(show):
    return {
        row.category_id: Decimal(row.price)
        for row in ShowPrice.objects.filter(theater=show)
    }


def category_for_seat(seat_number, categories=None):
    """Return the SeatCategory covering a seat, or the last one as fallback."""
    idx = _row_index(row_of(seat_number))
    cats = categories if categories is not None else _load_categories()
    for category in cats:
        if _row_index(category.row_start) <= idx <= _row_index(category.row_end):
            return category
    if cats:
        return cats[-1]
    return None


def seat_price(show, seat_number, categories=None, price_map=None):
    """Ticket price for a seat from the show's per-category catalog.

    Falls back to the show's reference ticket_price when the catalog has no
    entry for the seat's category.
    """
    if price_map is None or categories is None:
        categories = categories or _load_categories()
        price_map = price_map or _price_map(show)
    category = category_for_seat(seat_number, categories)
    if category is not None and category.id in price_map:
        base = _round2(price_map[category.id])
    else:
        base = _round2(Decimal(show.ticket_price))
    if is_economy_category(category):
        return economy_ticket_price(base)
    return base


def _category_of(seat, categories=None, by_id=None):
    """Resolve a seat's category, preferring the explicit FK over row ranges."""
    if getattr(seat, 'category_id', None):
        if by_id is not None:
            return by_id.get(seat.category_id)
        try:
            return seat.category
        except Exception:
            pass
    return category_for_seat(seat.seat_number, categories)


def _price_for_category(show, category, price_map):
    if category is not None and category.id in price_map:
        base = _round2(price_map[category.id])
    else:
        base = _round2(Decimal(show.ticket_price))
    if is_economy_category(category):
        return economy_ticket_price(base)
    return base


def get_pricing_config():
    """Current platform (per ticket) and miscellaneous (per booking) fees."""
    config, _ = PricingConfig.objects.get_or_create(pk=1)
    return {
        'platform_fee_per_ticket': Decimal(config.platform_fee_per_ticket),
        'misc_fee_per_booking': Decimal(config.misc_fee_per_booking),
    }


def gst_rate_for(taxable):
    """Return the GST percentage for a taxable amount using the configured slabs."""
    slabs = list(GSTSlab.objects.all().order_by('display_order'))
    if not slabs:
        return Decimal('0.00')
    taxable = Decimal(taxable)
    for slab in slabs:
        if taxable < slab.min_amount:
            continue
        if slab.max_amount is None or taxable <= slab.max_amount:
            return Decimal(slab.rate)
    return Decimal(slabs[-1].rate)


def gst_slabs():
    """Serializable GST slab list for the client-side price breakdown."""
    return [
        {
            'min_amount': str(s.min_amount),
            'max_amount': str(s.max_amount) if s.max_amount is not None else None,
            'rate': str(s.rate),
        }
        for s in GSTSlab.objects.all().order_by('display_order')
    ]


def validate_coupon(code, subtotal):
    """Return the Coupon for a code or None; raise ReservationError if unusable."""
    if not code or not str(code).strip():
        return None
    coupon = Coupon.objects.filter(code__iexact=str(code).strip()).first()
    if not coupon or not coupon.is_active:
        raise ReservationError('This coupon code is invalid or inactive.')
    now = timezone.now()
    if coupon.valid_from and coupon.valid_from > now:
        raise ReservationError('This coupon is not active yet.')
    if coupon.valid_to and coupon.valid_to < now:
        raise ReservationError('This coupon has expired.')
    if subtotal < coupon.min_order_amount:
        raise ReservationError(
            'This coupon requires a minimum order of \u20b9{:.2f}.'.format(coupon.min_order_amount)
        )
    if coupon.max_uses and coupon.used_count >= coupon.max_uses:
        raise ReservationError('This coupon has reached its usage limit.')
    return coupon


def discount_for(subtotal, coupon):
    if not coupon:
        return Decimal('0.00')
    if coupon.discount_amount:
        return min(Decimal(coupon.discount_amount), subtotal)
    return _round2(subtotal * Decimal(coupon.discount_percent) / 100)


def _pricing_for_seats(show, seat_objects):
    """Price breakdown for a set of Seat rows (fees, GST, no coupon)."""
    categories = _load_categories()
    by_id = {c.id: c for c in categories}
    price_map = _price_map(show)
    seats = []
    for seat in sorted(seat_objects, key=lambda s: (s.row_idx, s.col_idx, s.seat_number)):
        category = _category_of(seat, categories, by_id)
        category_name = category.name if category else ''
        seats.append({
            'seat_id': seat.id,
            'seat_number': seat.seat_number,
            'tier': category_name,
            'category': category_name,
            'price': _price_for_category(show, category, price_map),
        })
    subtotal = sum((s['price'] for s in seats), Decimal('0.00'))
    config = get_pricing_config()
    platform_fee = config['platform_fee_per_ticket'] * len(seats)
    misc_fee = config['misc_fee_per_booking'] if seats else Decimal('0.00')
    convenience_fee = _round2(platform_fee + misc_fee)
    taxable = subtotal + platform_fee + misc_fee
    gst_rate = gst_rate_for(taxable)
    gst = _round2(taxable * gst_rate / 100)
    return {
        'seats': seats,
        'subtotal': _round2(subtotal),
        'platform_fee': _round2(platform_fee),
        'misc_fee': _round2(misc_fee),
        'convenience_fee': convenience_fee,
        'gst_rate': gst_rate,
        'gst': gst,
        'discount': Decimal('0.00'),
        'total': _round2(subtotal + platform_fee + misc_fee + gst),
        'coupon': None,
    }


def pricing_for_seats(show, seat_objects):
    """Public price breakdown for a set of seats (used by walk-in bookings)."""
    return _pricing_for_seats(show, seat_objects)


def reservation_pricing(reservation, coupon_code=None):
    """Full price breakdown for a reservation (seats, fees, GST, discount, total)."""
    entries = list(
        reservation.reserved_seats.select_related('seat').order_by('seat__seat_number')
    )
    pricing = _pricing_for_seats(reservation.show, [e.seat for e in entries])
    coupon = validate_coupon(coupon_code, pricing['subtotal'])
    discount = discount_for(pricing['subtotal'], coupon) if coupon else Decimal('0.00')
    pricing['discount'] = discount
    pricing['coupon'] = coupon
    pricing['total'] = _round2(pricing['total'] - discount)
    return pricing


class ReservationError(Exception):
    """Raised for any business-rule violation during seat reservation."""


def _expire_stale_reservations(show, now=None):
    """Lazily release expired reservations for a show (bulk, one UPDATE)."""
    now = now or timezone.now()
    expired_ids = list(
        Reservation.objects.filter(show=show, status='active', expires_at__lte=now)
        .values_list('id', flat=True)
    )
    if not expired_ids:
        return 0
    Reservation.objects.filter(id__in=expired_ids).update(status='expired')
    ReservedSeat.objects.filter(reservation_id__in=expired_ids).delete()
    Theater.objects.filter(pk=show.pk).update(seat_revision=F('seat_revision') + 1)
    return len(expired_ids)


def expire_stale_for_show(show):
    """Release a show's expired reservations inside its own transaction."""
    with transaction.atomic():
        try:
            locked = Theater.objects.select_for_update().get(pk=show.pk)
        except Theater.DoesNotExist:
            raise ReservationError('Show not found.') from None
        return _expire_stale_reservations(locked)


def release_expired_reservations():
    """Release every expired reservation across all shows. Returns count.

    Intended to be run periodically (cron / management command). User-facing
    requests also perform lazy expiry, so this is a safety net rather than the
    only mechanism, avoiding continuous database polling.
    """
    now = timezone.now()
    expired = list(
        Reservation.objects.filter(status='active', expires_at__lte=now)
        .values_list('id', 'show_id')
    )
    if not expired:
        return 0
    ids = [row[0] for row in expired]
    show_ids = {row[1] for row in expired}
    with transaction.atomic():
        Reservation.objects.filter(id__in=ids).update(status='expired')
        ReservedSeat.objects.filter(reservation_id__in=ids).delete()
        if show_ids:
            Theater.objects.filter(id__in=show_ids).update(
                seat_revision=F('seat_revision') + 1
            )
    return len(ids)


def seat_data_for_show(show, now=None):
    """Return ordered seat info: {id, number, row, tier, price, state, ...geometry}."""
    now = now or timezone.now()
    reserved = Reservation.objects.filter(
        show=show,
        status='active',
        expires_at__gt=now,
        reserved_seats__seat=OuterRef('pk'),
    )
    seats = (
        Seat.objects.filter(theater=show)
        .annotate(is_reserved=Exists(reserved))
        .order_by('row_idx', 'col_idx', 'seat_number')
        .values(
            'pk', 'seat_number', 'is_booked', 'is_reserved',
            'seat_type', 'category_id', 'row_label', 'row_idx',
            'col_idx', 'side', 'gap_before', 'is_best_view', 'couple_group',
        )
    )
    categories = _load_categories()
    by_id = {c.id: c for c in categories}
    price_map = _price_map(show)
    rows = []
    for item in seats:
        number = item['seat_number']
        category = by_id.get(item['category_id']) or category_for_seat(number, categories)
        rows.append({
            'id': item['pk'],
            'number': number,
            'row': item['row_label'] or (number.rstrip('0123456789') or 'Z'),
            'row_idx': item['row_idx'],
            'col_idx': item['col_idx'],
            'side': item['side'],
            'gap_before': item['gap_before'],
            'seat_type': item['seat_type'],
            'best_view': item['is_best_view'],
            'couple_group': item['couple_group'],
            'tier': category.name if category else '',
            'category': category.name if category else '',
            'price': _price_for_category(show, category, price_map),
            'state': 'booked' if item['is_booked'] else ('reserved' if item['is_reserved'] else 'available'),
        })
    return rows


def seat_states_for_show(show, now=None):
    """Return {seat_id: state} where state is available|reserved|booked."""
    return {
        str(item['id']): item['state'] for item in seat_data_for_show(show, now)
    }


def seat_layout_for_show(show):
    """Return serializable layout metadata for rendering the seat map."""
    spec = show.layout_spec or {}
    return {
        'variant': spec.get('variant', 'straight'),
        'rows': spec.get('rows'),
        'cols_per_section': spec.get('cols_per_section'),
        'total_cols': spec.get('total_cols'),
        'screen_cols': spec.get('screen_cols'),
        'tier_gap_row': spec.get('tier_gap_row'),
        'sections': spec.get('sections', []),
        'couple_rows': spec.get('couple_rows', []),
        'couple_pairs': spec.get('couple_pairs', []),
        'wheelchair_seats': spec.get('wheelchair_seats', []),
        'exits': spec.get('exits', []),
        'size': spec.get('size', 'small'),
    }


def _validate_couple_pairs(seats):
    """Both seats of a couple pair must be selected together."""
    grouped = {}
    for seat in seats:
        if seat.couple_group:
            grouped.setdefault(seat.couple_group, []).append(seat)
    for group, members in grouped.items():
        if len(members) != 2:
            raise ReservationError(
                'Couple seats must be selected together as a pair.'
            )


MAX_TICKET_COUNT = 10


def _parse_seat_ids(raw_ids, limit=MAX_TICKET_COUNT):
    """Validate and normalise seat id input from the client."""
    if not raw_ids:
        return set()
    ids = set()
    for value in raw_ids:
        try:
            seat_id = int(value)
        except (TypeError, ValueError):
            raise ReservationError('Invalid seat identifier received.')
        if seat_id <= 0:
            raise ReservationError('Invalid seat identifier received.')
        ids.add(seat_id)
    if len(ids) > limit:
        raise ReservationError(f'You can select a maximum of {limit} seats at a time.')
    return ids


def _locked_seats(seat_ids, show):
    seats = list(
        Seat.objects.select_for_update().filter(pk__in=seat_ids, theater=show)
    )
    if len(seats) != len(seat_ids):
        raise ReservationError('One or more seats are not part of this show.')
    return seats


def _assert_available(seats, now):
    """Raise if any seat is booked or held by another active reservation."""
    for seat in seats:
        if seat.is_booked:
            raise ReservationError(f'Seat {seat.seat_number} has already been booked.')
        held = ReservedSeat.objects.filter(
            seat=seat,
            reservation__status='active',
            reservation__expires_at__gt=now,
        ).exists()
        if held:
            raise ReservationError(
                f'Seat {seat.seat_number} has just been reserved by another user.'
            )


def _bulk_hold_seats(reservation, seats):
    """Insert held seats, converting unique-constraint races to a clean error."""
    try:
        ReservedSeat.objects.bulk_create(
            [ReservedSeat(reservation=reservation, seat=seat) for seat in seats]
        )
    except IntegrityError:
        raise ReservationError(
            'This seat has just been reserved by another user.'
        ) from None


def _coerce_ticket_count(ticket_count):
    """Validate a user-chosen ticket count (1-10). Returns int or None."""
    if ticket_count is None:
        return None
    try:
        count = int(ticket_count)
    except (TypeError, ValueError):
        raise ReservationError('Invalid ticket count.') from None
    if count < 1 or count > MAX_TICKET_COUNT:
        raise ReservationError(
            'You can book between 1 and {} tickets.'.format(MAX_TICKET_COUNT)
        )
    return count


def create_reservation(user, show_id, seat_ids, ticket_count=None):
    """Atomically reserve a set of seats for the user."""
    now = timezone.now()
    requested = _parse_seat_ids(seat_ids)
    if not requested:
        raise ReservationError('Please select at least one seat.')
    count = _coerce_ticket_count(ticket_count)
    if count is not None and len(requested) != count:
        raise ReservationError(
            f'Your order is for {count} ticket{"s" if count != 1 else ""} — '
            f'please select exactly {count} seat{"s" if count != 1 else ""}.'
        )

    with transaction.atomic():
        try:
            show = Theater.objects.select_for_update().get(pk=show_id)
        except Theater.DoesNotExist:
            raise ReservationError('Show not found.') from None
        assert_show_bookable(show, now)
        _expire_stale_reservations(show, now)

        existing = Reservation.objects.filter(
            user=user, show=show, status='active'
        ).select_for_update().first()
        if existing and existing.expires_at > now:
            held_ids = set(existing.reserved_seats.values_list('seat_id', flat=True))
            if held_ids == requested:
                return existing
            raise ReservationError(
                'You already have an active hold for this show. '
                'Release it before selecting different seats.'
            )

        seats = _locked_seats(requested, show)
        _assert_available(seats, now)
        _validate_couple_pairs(seats)

        reservation = Reservation.objects.create(
            token=secrets.token_urlsafe(24),
            user=user,
            show=show,
            status='active',
            payment_status='pending',
            ticket_count=count or len(seats),
            expires_at=now + timedelta(seconds=RESERVATION_HOLD_SECONDS),
        )
        _bulk_hold_seats(reservation, seats)
        show.bump_seat_revision()
        return reservation


def _get_owned_active_reservation(user, token, now=None):
    now = now or timezone.now()
    try:
        reservation = (
            Reservation.objects.select_for_update()
            .select_related('show')
            .get(token=token)
        )
    except Reservation.DoesNotExist:
        raise ReservationError('Reservation not found.') from None
    if reservation.user_id != user.id:
        raise ReservationError('This reservation does not belong to you.')
    if reservation.status == 'booked':
        raise ReservationError('This reservation has already been completed.')
    if reservation.status != 'active' or reservation.expires_at <= now:
        raise ReservationError(
            'Your reservation has expired. Please select your seats again.'
        )
    return reservation


def modify_reservation(user, token, add_seat_ids, remove_seat_ids, ticket_count=None):
    """Add/remove seats on an active reservation and refresh its expiry.

    When ``ticket_count`` is supplied the reservation is locked to exactly that
    many held seats. Otherwise the held seat count becomes the new ticket count,
    so the two never drift apart after the user starts modifying seats.
    """
    now = timezone.now()
    to_add = _parse_seat_ids(add_seat_ids)
    to_remove = _parse_seat_ids(remove_seat_ids)
    to_remove = {s for s in to_remove if s not in to_add}
    if not to_add and not to_remove and ticket_count is None:
        raise ReservationError('No seat changes requested.')

    with transaction.atomic():
        reservation = _get_owned_active_reservation(user, token, now)
        show = reservation.show
        assert_show_bookable(show, now)
        _expire_stale_reservations(show, now)
        reservation.refresh_from_db()
        if reservation.status != 'active' or reservation.expires_at <= now:
            raise ReservationError(
                'Your reservation has expired. Please select your seats again.'
            )

        already_held = set(
            ReservedSeat.objects.filter(reservation=reservation)
            .values_list('seat_id', flat=True)
        )
        to_remove &= already_held
        to_add -= already_held

        if to_remove:
            ReservedSeat.objects.filter(
                reservation=reservation, seat_id__in=to_remove
            ).delete()

        if to_add:
            seats = _locked_seats(to_add, show)
            _assert_available(seats, now)
            _bulk_hold_seats(reservation, seats)

        held_count = ReservedSeat.objects.filter(reservation=reservation).count()
        if ticket_count is not None:
            target = _coerce_ticket_count(ticket_count)
            if held_count != target:
                raise ReservationError(
                    f'Your order is for {target} ticket{"s" if target != 1 else ""} — '
                    f'please select exactly {target} seat{"s" if target != 1 else ""}.'
                )
            reservation.ticket_count = target
        else:
            reservation.ticket_count = held_count
            if held_count > MAX_TICKET_COUNT:
                raise ReservationError(
                    'You can hold a maximum of {} seats.'.format(MAX_TICKET_COUNT)
                )
        if not held_count:
            raise ReservationError('Reservation must keep at least one seat.')

        _validate_couple_pairs([
            rs.seat
            for rs in ReservedSeat.objects.filter(reservation=reservation).select_related('seat')
        ])

        reservation.expires_at = now + timedelta(seconds=RESERVATION_HOLD_SECONDS)
        reservation.save(update_fields=['ticket_count', 'expires_at', 'updated_at'])
        show.bump_seat_revision()
        return reservation


def _generate_booking_ref():
    while True:
        ref = 'BMS' + secrets.token_hex(4).upper()
        if not Booking.objects.filter(booking_ref=ref).exists():
            return ref


def generate_booking_ref():
    """Public helper returning a unique booking reference."""
    return _generate_booking_ref()


def _generate_reservation_booking_ref():
    """Unique transaction-level booking reference (e.g. BMS39DBA878)."""
    while True:
        ref = 'BMS' + secrets.token_hex(4).upper()
        if not Reservation.objects.filter(booking_ref=ref).exists():
            return ref


def confirm_booking(user, token, transaction_id=None, payment_method='upi', coupon_code=None):
    """Mark an active reservation as paid and convert seats into bookings."""
    now = timezone.now()
    with transaction.atomic():
        reservation = _get_owned_active_reservation(user, token, now)
        show = reservation.show
        assert_show_bookable(show, now)
        _expire_stale_reservations(show, now)
        reservation.refresh_from_db()
        if reservation.status != 'active' or reservation.expires_at <= now:
            raise ReservationError(
                'Your reservation has expired. Please select your seats again.'
            )

        reserved_seats = list(
            ReservedSeat.objects.select_for_update()
            .filter(reservation=reservation)
            .select_related('seat')
        )
        if not reserved_seats:
            raise ReservationError('Reservation has no seats left.')
        if reservation.ticket_count and len(reserved_seats) != reservation.ticket_count:
            raise ReservationError(
                'Ticket count no longer matches the selected seats. '
                'Please release and reselect your seats.'
            )

        pricing = reservation_pricing(reservation, coupon_code=coupon_code)
        coupon = pricing['coupon']
        total = pricing['total']

        seat_prices = {s['seat_id']: s['price'] for s in pricing['seats']}
        seat_categories = {s['seat_id']: s['category'] for s in pricing['seats']}
        count = len(reserved_seats)
        platform_share = pricing['platform_fee'] / count
        misc_share = pricing['misc_fee'] / count
        gst_share = pricing['gst'] / count
        subtotal = pricing['subtotal']

        rows = []
        charged = []
        for entry in reserved_seats:
            try:
                seat = Seat.objects.select_for_update().get(pk=entry.seat_id)
            except Seat.DoesNotExist:
                raise ReservationError(
                    f'Seat {entry.seat.seat_number} is no longer available. '
                    'Please reselect your seats.'
                ) from None
            if seat.is_booked:
                raise ReservationError(
                    f'Seat {seat.seat_number} was booked by another user. '
                    'Please reselect your seats.'
                )
            price = seat_prices[entry.seat_id]
            disc_share = pricing['discount'] * price / subtotal if subtotal else Decimal('0.00')
            amount = _round2(price + platform_share + misc_share + gst_share - disc_share)
            rows.append({
                'seat': seat,
                'seat_id': entry.seat_id,
                'price': price,
                'disc_share': disc_share,
                'amount': amount,
            })
            charged.append(amount)

        if len(charged) > 1:
            charged[-1] = _round2(total - sum(charged[:-1], Decimal('0.00')))

        bookings = []
        for idx, row in enumerate(rows):
            seat = row['seat']
            booking, created = Booking.objects.get_or_create(
                seat=seat,
                defaults={
                    'user': user,
                    'movie': show.movie,
                    'theater': show,
                    'reservation': reservation,
                    'booking_ref': _generate_booking_ref(),
                    'seat_category': seat_categories.get(row['seat_id'], ''),
                    'ticket_price': row['price'],
                    'gst_rate': pricing['gst_rate'],
                    'gst_amount': _round2(gst_share),
                    'platform_fee': _round2(platform_share),
                    'misc_fee': _round2(misc_share),
                    'discount': _round2(row['disc_share']),
                    'total': charged[idx],
                    'status': 'confirmed',
                },
            )
            if not created:
                booking.user = user
                booking.movie = show.movie
                booking.theater = show
                booking.reservation = reservation
                booking.booking_ref = _generate_booking_ref()
                booking.seat_category = seat_categories.get(row['seat_id'], '')
                booking.ticket_price = row['price']
                booking.gst_rate = pricing['gst_rate']
                booking.gst_amount = _round2(gst_share)
                booking.platform_fee = _round2(platform_share)
                booking.misc_fee = _round2(misc_share)
                booking.discount = _round2(row['disc_share'])
                booking.total = charged[idx]
                booking.status = 'confirmed'
                booking.booked_at = timezone.now()
                booking.save(update_fields=[
                    'user', 'movie', 'theater', 'reservation', 'booking_ref',
                    'seat_category', 'ticket_price', 'gst_rate', 'gst_amount',
                    'platform_fee', 'misc_fee', 'discount', 'total', 'status',
                    'booked_at',
                ])
            Seat.objects.filter(pk=seat.pk).update(is_booked=True)
            payment, _ = Payment.objects.get_or_create(
                booking=booking,
                defaults={
                    'amount': charged[idx],
                    'payment_method': payment_method,
                    'transaction_id': transaction_id or '',
                    'status': 'completed',
                },
            )
            if not _:
                payment.amount = charged[idx]
                payment.payment_method = payment_method
                payment.transaction_id = transaction_id or ''
                payment.status = 'completed'
                payment.save(update_fields=[
                    'amount', 'payment_method', 'transaction_id', 'status',
                ])
            bookings.append(booking)

        reservation.coupon = coupon
        reservation.coupon_code = coupon.code if coupon else ''
        reservation.subtotal_amount = pricing['subtotal']
        reservation.convenience_fee = pricing['convenience_fee']
        reservation.platform_fee = pricing['platform_fee']
        reservation.misc_fee = pricing['misc_fee']
        reservation.gst_rate = pricing['gst_rate']
        reservation.gst_amount = pricing['gst']
        reservation.discount_amount = pricing['discount']
        reservation.total_amount = total
        reservation.status = 'booked'
        reservation.payment_status = 'completed'
        reservation.ticket_count = count
        if not reservation.booking_ref:
            reservation.booking_ref = _generate_reservation_booking_ref()
        reservation.save(update_fields=[
            'coupon', 'coupon_code', 'subtotal_amount', 'convenience_fee',
            'platform_fee', 'misc_fee', 'gst_rate', 'gst_amount',
            'discount_amount', 'total_amount', 'status',
            'payment_status', 'ticket_count', 'booking_ref', 'updated_at',
        ])
        if coupon:
            Coupon.objects.filter(pk=coupon.pk).update(used_count=F('used_count') + 1)
        ReservedSeat.objects.filter(reservation=reservation).delete()
        show.bump_seat_revision()
        return reservation, bookings


def create_walkin_bookings(user, movie, show, seat_count, payment_method='manual'):
    """Book seats directly (walk-in) without an online reservation.

    Follows the same pricing rules and seat-safety rules as the online flow:
    stale holds are released, seats currently held by an active customer
    reservation are never taken, and fee/GST shares are recorded per booking.
    """
    now = timezone.now()
    with transaction.atomic():
        show = Theater.objects.select_for_update().get(pk=show.pk)
        if show.movie_id != movie.id:
            raise ReservationError('The selected show is not screening this movie.')
        assert_show_bookable(show, now)
        _expire_stale_reservations(show, now)
        held = ReservedSeat.objects.filter(
            reservation__show=show,
            reservation__status='active',
            reservation__expires_at__gt=now,
        ).values('seat_id')
        seats = list(
            Seat.objects.select_for_update()
            .filter(theater=show, is_booked=False)
            .exclude(pk__in=held)
            .order_by('seat_number')[:seat_count]
        )
        if len(seats) < seat_count:
            raise ReservationError(
                f'Only {len(seats)} seats are available, need {seat_count}.'
            )

        pricing = _pricing_for_seats(show, seats)
        count = len(seats)
        platform_share = _round2(pricing['platform_fee'] / count)
        misc_share = _round2(pricing['misc_fee'] / count)
        gst_share = _round2(pricing['gst'] / count)
        charged = [
            _round2(entry['price'] + platform_share + misc_share + gst_share)
            for entry in pricing['seats']
        ]
        if len(charged) > 1:
            charged[-1] = _round2(
                pricing['total'] - sum(charged[:-1], Decimal('0.00'))
            )

        reservation = Reservation.objects.create(
            token=secrets.token_urlsafe(24),
            booking_ref=_generate_reservation_booking_ref(),
            ticket_count=count,
            user=user,
            show=show,
            status='booked',
            payment_status='completed',
            subtotal_amount=pricing['subtotal'],
            convenience_fee=pricing['convenience_fee'],
            platform_fee=pricing['platform_fee'],
            misc_fee=pricing['misc_fee'],
            gst_rate=pricing['gst_rate'],
            gst_amount=pricing['gst'],
            discount_amount=pricing['discount'],
            total_amount=pricing['total'],
            expires_at=now + timedelta(seconds=RESERVATION_HOLD_SECONDS),
        )

        bookings = []
        for idx, entry in enumerate(pricing['seats']):
            seat = Seat.objects.get(pk=entry['seat_id'])
            booking = Booking.objects.create(
                user=user,
                seat=seat,
                movie=movie,
                theater=show,
                reservation=reservation,
                booking_ref=_generate_booking_ref(),
                seat_category=entry['category'],
                ticket_price=entry['price'],
                gst_rate=pricing['gst_rate'],
                gst_amount=gst_share,
                platform_fee=platform_share,
                misc_fee=misc_share,
                discount=Decimal('0.00'),
                total=charged[idx],
            )
            Seat.objects.filter(pk=seat.pk).update(is_booked=True)
            Payment.objects.create(
                booking=booking,
                amount=charged[idx],
                payment_method=payment_method,
                transaction_id='',
                status='completed',
            )
            bookings.append(booking)
        show.bump_seat_revision()
        return bookings, reservation


def cancel_booking(user, booking_id):
    """Cancel a booking before showtime and issue a refund."""
    now = timezone.now()
    with transaction.atomic():
        try:
            booking = (
                Booking.objects.select_for_update()
                .select_related('seat', 'theater')
                .get(pk=booking_id)
            )
        except Booking.DoesNotExist:
            raise ReservationError('Booking not found.') from None
        if booking.user_id != user.id:
            raise ReservationError('This booking does not belong to you.')
        if booking.theater.time <= now:
            raise ReservationError(
                'Cancellation is not allowed once the show has started.'
            )
        Payment.objects.filter(booking=booking, status='completed').update(
            status='refunded'
        )
        try:
            from .payments import refund_reservation_transactions
            refund_reservation_transactions(booking.reservation)
        except Exception:
            logger.warning(
                'Gateway refund for booking %s failed.',
                booking.booking_ref or booking.id,
                exc_info=True,
            )
        if PaymentTransaction.objects.filter(
            reservation=booking.reservation, status='captured'
        ).exists():
            logger.warning(
                'Booking %s cancelled but captured payment transaction(s) remain '
                'unrefunded for reservation %s.',
                booking.booking_ref or booking.id,
                booking.reservation_id,
            )
        Seat.objects.filter(pk=booking.seat_id).update(is_booked=False)
        booking.theater.bump_seat_revision()
        booking.status = 'cancelled'
        booking.save(update_fields=['status'])
    return True


def cancel_reservation_booking(user, booking_ref):
    """Cancel an entire booking (all seats) by its transaction booking reference."""
    now = timezone.now()
    with transaction.atomic():
        try:
            reservation = (
                Reservation.objects.select_for_update()
                .select_related('show')
                .get(booking_ref=booking_ref)
            )
        except Reservation.DoesNotExist:
            raise ReservationError('Booking not found.') from None
        if reservation.user_id != user.id:
            raise ReservationError('This booking does not belong to you.')
        if reservation.show.time <= now:
            raise ReservationError(
                'Cancellation is not allowed once the show has started.'
            )
        bookings = list(
            reservation.bookings.select_for_update().select_related('seat')
        )
        if not bookings:
            raise ReservationError('This booking has no seats to cancel.')
        Payment.objects.filter(
            booking__in=bookings, status='completed'
        ).update(status='refunded')
        try:
            from .payments import refund_reservation_transactions
            refund_reservation_transactions(reservation)
        except Exception:
            logger.warning(
                'Gateway refund for reservation %s failed.',
                reservation.booking_ref or reservation.id,
                exc_info=True,
            )
        if PaymentTransaction.objects.filter(
            reservation=reservation, status='captured'
        ).exists():
            logger.warning(
                'Reservation %s cancelled but captured payment transaction(s) '
                'remain unrefunded.',
                reservation.booking_ref or reservation.id,
            )
        seat_ids = [b.seat_id for b in bookings]
        Seat.objects.filter(pk__in=seat_ids).update(is_booked=False)
        Booking.objects.filter(pk__in=[b.pk for b in bookings]).update(
            status='cancelled'
        )
        reservation.status = 'cancelled'
        reservation.payment_status = 'refunded'
        reservation.save(update_fields=['status', 'payment_status', 'updated_at'])
        reservation.show.bump_seat_revision()
    return True


def release_reservation(user, token):
    """Cancel a reservation and immediately free its seats."""
    with transaction.atomic():
        try:
            reservation = (
                Reservation.objects.select_for_update().select_related('show').get(token=token)
            )
        except Reservation.DoesNotExist:
            raise ReservationError('Reservation not found.') from None
        if reservation.user_id != user.id:
            raise ReservationError('This reservation does not belong to you.')
        if reservation.status == 'booked':
            raise ReservationError('A completed reservation cannot be released.')
        if reservation.status != 'active':
            return reservation
        reservation.status = 'cancelled'
        reservation.save(update_fields=['status', 'updated_at'])
        ReservedSeat.objects.filter(reservation=reservation).delete()
        reservation.show.bump_seat_revision()
        return reservation
