"""Payment orchestration: checkout, verified callbacks, webhooks, refunds.

Security model
--------------
* The amount charged is always recomputed server-side from the reservation
  (seat catalog, fees, GST, coupon) — the client never supplies an amount.
* A booking is confirmed only after the Razorpay signature is verified (or, on
  the webhook path, after the webhook HMAC + order/payment entity checks).
* Every mutation is idempotent: duplicate callbacks and duplicate webhook
  events are safe and never create a second set of bookings or payments.
* Coupon codes are bound to the transaction at checkout start and reused at
  confirmation, so a client cannot swap coupons after an order is created.
"""
import logging
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from admin_panel.models import PaymentTransaction
from . import gateway
from .models import Reservation
from .notifications import send_booking_confirmation, send_payment_failed_email
from .services import ReservationError, confirm_booking, reservation_pricing

logger = logging.getLogger(__name__)

# A real Razorpay checkout (filling card details, 3-D Secure, etc.) takes longer
# than the seat-selection hold, so once a real order is created the reservation
# is given a fresh grace window. Demo checkout completes instantly and keeps the
# original hold.
PAYMENT_HOLD_SECONDS = 600


class PaymentError(Exception):
    """Raised for any payment-flow violation (unverified, unknown, mismatched)."""


def _owned_active_reservation(user, token, for_update=False):
    qs = Reservation.objects.select_related('show').all()
    if for_update:
        qs = qs.select_for_update()
    try:
        reservation = qs.get(token=token)
    except Reservation.DoesNotExist:
        raise ReservationError('Reservation not found.') from None
    if reservation.user_id != user.id:
        raise ReservationError('This reservation does not belong to you.')
    if reservation.status == 'booked':
        return reservation, True
    if reservation.status != 'active' or reservation.expires_at <= timezone.now():
        raise ReservationError(
            'Your reservation has expired. Please select your seats again.'
        )
    if reservation.show.time <= timezone.now():
        raise ReservationError(
            'This show has already started and cannot be booked.'
        )
    return reservation, False


def _transaction_for_order(user, gateway_order_id):
    if not gateway_order_id:
        raise PaymentError('Missing payment order reference.')
    tx = (
        PaymentTransaction.objects.select_related('reservation')
        .filter(user=user, gateway_order_id=gateway_order_id)
        .first()
    )
    if tx is None:
        raise PaymentError('Unknown payment order.')
    return tx


def _find_transaction_for_order(user, gateway_order_id):
    if not gateway_order_id:
        return None
    return (
        PaymentTransaction.objects.select_related('reservation')
        .filter(user=user, gateway_order_id=gateway_order_id)
        .first()
    )


def _recompute_total(reservation, coupon_code):
    pricing = reservation_pricing(reservation, coupon_code=coupon_code or None)
    total = pricing['total']
    applied = pricing['coupon'].code if pricing['coupon'] else ''
    return total, applied


def start_checkout(user, token, coupon_code=''):
    """Create (or reuse) a payment transaction and a gateway order.

    Returns (transaction, checkout_payload). Raises ReservationError when the
    reservation is no longer bookable and PaymentError when the gateway cannot
    create the order.
    """
    reservation, already_booked = _owned_active_reservation(user, token)
    if already_booked:
        raise ReservationError('This reservation has already been completed.')
    total, applied_coupon = _recompute_total(reservation, coupon_code)

    with transaction.atomic():
        reservation = (
            Reservation.objects.select_for_update().select_related('show').get(pk=reservation.pk)
        )
        if reservation.status != 'active' or reservation.expires_at <= timezone.now():
            raise ReservationError(
                'Your reservation has expired. Please select your seats again.'
            )
        if reservation.show.time <= timezone.now():
            raise ReservationError('This show has already started and cannot be booked.')

        existing = PaymentTransaction.objects.filter(
            reservation=reservation, status='created'
        ).first()
        if existing:
            if existing.amount == total:
                tx = existing
            else:
                existing.status = 'cancelled'
                existing.save(update_fields=['status', 'updated_at'])
                tx = None
        else:
            tx = None

        if tx is None:
            tx = PaymentTransaction.objects.create(
                reservation=reservation,
                user=user,
                amount=total,
                status='created',
                coupon_code=applied_coupon,
                is_demo=gateway.demo_mode(),
            )

        if not tx.gateway_order_id:
            if gateway.demo_mode():
                tx.gateway_order_id = 'order_DEMO{}'.format(
                    secrets.token_hex(8).upper()
                )
            else:
                order = gateway.create_order(
                    total,
                    receipt='BMS-{}'.format(reservation.token[:10].upper()),
                    notes={
                        'token': reservation.token,
                        'user': user.username,
                        'reservation_id': reservation.id,
                    },
                )
                if not order or not order.get('id'):
                    raise PaymentError('The payment gateway did not return an order.')
                tx.gateway_order_id = order['id']
                tx.payload = {'order': order}
            tx.save(update_fields=['gateway_order_id', 'payload', 'is_demo', 'updated_at'])

        if not gateway.demo_mode():
            reservation.expires_at = timezone.now() + timedelta(
                seconds=PAYMENT_HOLD_SECONDS
            )
            reservation.save(update_fields=['expires_at', 'updated_at'])

    checkout = {
        'key': settings.RAZORPAY_KEY_ID if not gateway.demo_mode() else '',
        'order_id': tx.gateway_order_id,
        'amount': gateway.paise_from_decimal(total),
        'currency': 'INR',
        'demo': gateway.demo_mode(),
        'transaction_id': tx.id,
        'hold_seconds': int((reservation.expires_at - timezone.now()).total_seconds()),
    }
    if gateway.demo_mode():
        checkout['demo_signature'] = gateway.demo_signature(
            tx.gateway_order_id, 'pay_DEMO_pending'
        )
    logger.info(
        'start_checkout: reservation=%s tx=%s order=%s key=%s amount=%s paise currency=%s demo=%s hold_seconds=%s',
        reservation.token, tx.id, tx.gateway_order_id, checkout['key'],
        checkout['amount'], checkout['currency'], checkout['demo'], checkout['hold_seconds'],
    )
    return tx, checkout


def _verify_capture(tx, gateway_payment_id, gateway_signature, demo):
    """Verify the payment really belongs to this order and matches the amount.

    Returns the verified payment entity (None in demo mode) so callers can
    record the exact payment method reported by the gateway instead of a
    client-supplied label.
    """
    if demo:
        expected = gateway.demo_signature(tx.gateway_order_id, gateway_payment_id)
        if not secrets.compare_digest(expected, gateway_signature or ''):
            raise PaymentError('Payment signature verification failed.')
        return None

    if not gateway.verify_payment_signature(
        tx.gateway_order_id, gateway_payment_id, gateway_signature
    ):
        raise PaymentError('Payment signature verification failed.')

    payment = gateway.fetch_payment(gateway_payment_id)
    if not payment:
        raise PaymentError('Could not verify the payment with the gateway.')
    if payment.get('order_id') != tx.gateway_order_id:
        raise PaymentError('Payment does not belong to this order.')
    if int(payment.get('amount') or 0) != gateway.paise_from_decimal(tx.amount):
        raise PaymentError('Payment amount does not match the order.')
    if payment.get('status') not in ('captured', 'authorized'):
        raise PaymentError('Payment has not been captured.')
    return payment


def _claim_email_sent(tx_id, kind):
    """Atomically claim the right to send one email of a given kind.

    Only one caller (of the racing callback + webhook paths) wins the update
    and returns True, so a transaction can never produce duplicate
    confirmation or failure emails. Returns the number of rows updated.
    """
    field = 'confirmation_email_sent' if kind == 'confirmation' else 'failure_email_sent'
    return PaymentTransaction.objects.filter(pk=tx_id, **{field: False}).update(
        **{field: True, 'updated_at': timezone.now()}
    )


def _send_confirmation_once(tx, user, reservation, bookings):
    """Send the confirmation email exactly once per captured transaction."""
    if not _claim_email_sent(tx.pk, 'confirmation'):
        return
    try:
        send_booking_confirmation(user, reservation, bookings)
    except Exception as exc:  # noqa: BLE001 - email must never fail a verified booking
        logger.warning(
            'Confirmation email enqueue failed for reservation=%s: %s',
            reservation.token, exc,
        )


def _send_failure_email_once(tx):
    """Send the failed-payment email exactly once per failed transaction."""
    if not _claim_email_sent(tx.pk, 'failure'):
        return
    try:
        send_payment_failed_email(tx)
    except Exception as exc:  # noqa: BLE001 - email must never fail a failed payment
        logger.warning(
            'Failure email enqueue failed for order=%s: %s',
            tx.gateway_order_id, exc,
        )


def verify_and_confirm(user, token, *, gateway_order_id, gateway_payment_id,
                       gateway_signature, method='upi', demo=False):
    """Verify a checkout callback and confirm the booking. Idempotent.

    Returns (reservation, bookings). Raises PaymentError for unverified or
    mismatched payments and ReservationError for expired/unbookable ones.
    """
    reservation, _ = _owned_active_reservation(user, token)
    tx = _transaction_for_order(user, gateway_order_id)
    logger.info(
        'verify_and_confirm callback: reservation=%s order=%s payment=%s signature=%s... demo=%s tx=%s tx.status=%s',
        token, gateway_order_id, gateway_payment_id, (gateway_signature or '')[:16],
        demo, tx.pk, tx.status,
    )

    if not demo and not gateway.demo_mode():
        if tx.reservation_id != reservation.id:
            raise PaymentError('Order does not belong to this reservation.')

    if tx.status == 'captured':
        reservation.refresh_from_db()
        if reservation.status == 'booked':
            return reservation, list(reservation.bookings.order_by('pk').select_related('seat'))
        raise ReservationError(
            'Payment was captured but the booking could not be confirmed. '
            'Please contact support.'
        )

    verified_payment = _verify_capture(
        tx, gateway_payment_id, gateway_signature, demo
    )
    payment_method = (verified_payment or {}).get('method') or method
    logger.info(
        'Payment verified for order=%s payment=%s method=%s demo=%s',
        gateway_order_id, gateway_payment_id, payment_method, demo,
    )

    with transaction.atomic():
        locked = (
            PaymentTransaction.objects.select_for_update().get(pk=tx.pk)
        )
        if locked.status == 'captured':
            reservation.refresh_from_db()
            if reservation.status == 'booked':
                return reservation, list(reservation.bookings.order_by('pk').select_related('seat'))
            raise PaymentError('Payment was already captured for this order.')

        total, applied_coupon = _recompute_total(
            locked.reservation, locked.coupon_code
        )
        if total != locked.amount:
            raise PaymentError('Order amount no longer matches the current price.')

        try:
            reservation, bookings = confirm_booking(
                user,
                token,
                transaction_id=gateway_payment_id,
                payment_method=payment_method,
                coupon_code=applied_coupon or None,
            )
        except ReservationError as exc:
            reservation, already_booked = _owned_active_reservation(user, token)
            if already_booked and reservation.status == 'booked':
                PaymentTransaction.objects.filter(pk=locked.pk).update(
                    status='captured',
                    gateway_payment_id=gateway_payment_id,
                    gateway_signature=gateway_signature,
                    method=payment_method,
                    captured_at=timezone.now(),
                    payload=dict(locked.payload or {}, **{'payment_id': gateway_payment_id}),
                    updated_at=timezone.now(),
                )
                return reservation, list(reservation.bookings.order_by('pk').select_related('seat'))
            raise

        PaymentTransaction.objects.filter(pk=locked.pk).update(
            status='captured',
            gateway_payment_id=gateway_payment_id,
            gateway_signature=gateway_signature,
            method=payment_method,
            captured_at=timezone.now(),
            payload=dict(locked.payload or {}, **{'payment_id': gateway_payment_id}),
            updated_at=timezone.now(),
        )

    try:
        _send_confirmation_once(locked, user, reservation, bookings)
    except Exception as exc:  # noqa: BLE001 - email must never fail a verified booking
        logger.warning(
            'Confirmation email enqueue failed for reservation=%s: %s',
            reservation.token, exc,
        )
    return reservation, bookings


def record_failure(user, token, *, gateway_order_id, gateway_payment_id,
                   failure_reason='', method=''):
    """Record a failed payment attempt. Seats stay held for a retry."""
    reservation, _ = _owned_active_reservation(user, token)
    tx = _find_transaction_for_order(user, gateway_order_id)
    if tx is None:
        tx = (
            PaymentTransaction.objects.filter(
                user=user, reservation=reservation, status='created'
            )
            .order_by('-id')
            .first()
        )
        if tx is None:
            raise PaymentError('No active payment order found for this reservation.')
    if tx.status == 'captured':
        return tx
    logger.info(
        'Payment attempt recorded as failed: reservation=%s order=%s payment=%s reason=%s',
        token, gateway_order_id, gateway_payment_id, failure_reason,
    )
    tx.status = 'failed'
    tx.gateway_payment_id = gateway_payment_id or tx.gateway_payment_id
    tx.failure_reason = (failure_reason or '')[:255]
    tx.method = method or tx.method
    tx.payload = {
        'failure_reason': tx.failure_reason,
        'payment_id': gateway_payment_id,
    }
    tx.save(update_fields=[
        'status', 'gateway_payment_id', 'failure_reason', 'method',
        'payload', 'updated_at',
    ])
    Reservation.objects.filter(pk=reservation.pk).update(
        payment_status='failed', updated_at=timezone.now()
    )
    _send_failure_email_once(tx)
    return tx


def refund_transaction(tx, reason=''):
    """Request a gateway refund for a captured transaction. Best-effort."""
    if tx.status not in ('captured',):
        return tx
    notes = {'reason': reason or 'Booking cancelled'}
    if tx.is_demo or gateway.demo_mode():
        tx.status = 'refunded'
        tx.refund_id = 'ref_DEMO{}'.format(secrets.token_hex(8).upper())
        tx.refunded_at = timezone.now()
        tx.save(update_fields=['status', 'refund_id', 'refunded_at', 'updated_at'])
        return tx
    refund = gateway.create_refund(tx.gateway_payment_id, tx.amount, notes=notes)
    if not refund or not refund.get('id'):
        raise PaymentError('The gateway did not return a refund.')
    tx.status = 'refund_requested' if refund.get('status') != 'processed' else 'refunded'
    tx.refund_id = refund['id']
    tx.refunded_at = timezone.now()
    tx.payload = dict(tx.payload or {})
    tx.payload['refund'] = refund
    tx.save(update_fields=['status', 'refund_id', 'refunded_at', 'payload', 'updated_at'])
    return tx


def refund_reservation_transactions(reservation):
    """Best-effort gateway refunds for every captured transaction of a reservation."""
    count = 0
    for tx in PaymentTransaction.objects.filter(
        reservation=reservation, status='captured'
    ):
        try:
            refund_transaction(tx, reason='Booking cancelled')
            count += 1
        except Exception:
            continue
    return count


def cancel_stale_orders():
    """Cancel 'created' transactions whose reservation is no longer active."""
    now = timezone.now()
    stale = PaymentTransaction.objects.filter(
        status='created',
        reservation__status__in=['expired', 'cancelled'],
    )
    ids = list(stale.values_list('pk', flat=True))
    if ids:
        PaymentTransaction.objects.filter(pk__in=ids).update(
            status='cancelled', updated_at=now
        )
    return len(ids)


def handle_webhook(body, signature):
    """Validate and process a Razorpay webhook event. Raises PaymentError."""
    if isinstance(body, bytes):
        body = body.decode('utf-8', errors='replace')
    if not gateway.verify_webhook_signature(body, signature):
        raise PaymentError('Webhook signature verification failed.')
    import json
    try:
        payload = json.loads(body)
    except ValueError:
        raise PaymentError('Invalid webhook payload.') from None

    event = payload.get('event', '')
    logger.info('Razorpay webhook event received: %s', event)
    if event in ('payment.authorized', 'payment.captured', 'order.paid'):
        _webhook_payment_captured(payload)
    elif event == 'payment.failed':
        _webhook_payment_failed(payload)
    elif event in ('refund.created', 'refund.processed', 'refund.updated'):
        _webhook_refund(payload)
    return event


def _webhook_entity(payload):
    data = payload.get('payload') or {}
    for container in ('payment', 'order'):
        entity = (data.get(container) or {}).get('entity') or {}
        if entity.get('id'):
            return entity
    return {}


def _webhook_payment_captured(payload):
    entity = _webhook_entity(payload)
    order_id = entity.get('order_id') or entity.get('id') or ''
    payment_id = entity.get('id') or ''
    amount_paise = int(entity.get('amount') or 0)
    method = entity.get('method') or ''

    tx = PaymentTransaction.objects.filter(gateway_order_id=order_id).first()
    if tx is None:
        return
    if tx.status == 'captured':
        return
    if amount_paise and amount_paise != gateway.paise_from_decimal(tx.amount):
        raise PaymentError('Webhook payment amount does not match the order.')

    with transaction.atomic():
        locked = PaymentTransaction.objects.select_for_update().get(pk=tx.pk)
        if locked.status == 'captured':
            return
        reservation = Reservation.objects.select_for_update().get(pk=locked.reservation_id)
        if reservation.status == 'booked':
            PaymentTransaction.objects.filter(pk=locked.pk).update(
                status='captured',
                gateway_payment_id=payment_id,
                method=method or locked.method,
                captured_at=timezone.now(),
                payload=dict(locked.payload or {}, **{'webhook': entity}),
                updated_at=timezone.now(),
            )
            return

        try:
            reservation, bookings = confirm_booking(
                locked.user,
                reservation.token,
                transaction_id=payment_id,
                payment_method=method or locked.method or 'upi',
                coupon_code=locked.coupon_code or None,
            )
        except ReservationError:
            PaymentTransaction.objects.filter(pk=locked.pk).update(
                status='captured',
                gateway_payment_id=payment_id,
                method=method or locked.method,
                captured_at=timezone.now(),
                payload=dict(locked.payload or {}, **{
                    'webhook': entity,
                    'reconciliation_error': 'Booking could not be confirmed; '
                    'payment was captured.',
                }),
                updated_at=timezone.now(),
            )
            return

        PaymentTransaction.objects.filter(pk=locked.pk).update(
            status='captured',
            gateway_payment_id=payment_id,
            method=method or locked.method,
            captured_at=timezone.now(),
            payload=dict(locked.payload or {}, **{'webhook': entity}),
            updated_at=timezone.now(),
        )

    try:
        _send_confirmation_once(locked, locked.user, reservation, bookings)
    except Exception as exc:  # noqa: BLE001 - email must never fail a captured payment
        logger.warning(
            'Confirmation email enqueue failed for order=%s: %s',
            order_id, exc,
        )


def _webhook_payment_failed(payload):
    entity = _webhook_entity(payload)
    order_id = entity.get('order_id') or entity.get('id') or ''
    payment_id = entity.get('id') or ''
    failure_reason = (entity.get('error_description') or entity.get('error') or '')[:255]
    tx = PaymentTransaction.objects.filter(gateway_order_id=order_id).first()
    if tx is None or tx.status == 'captured':
        return
    tx.status = 'failed'
    tx.gateway_payment_id = payment_id or tx.gateway_payment_id
    tx.failure_reason = failure_reason or tx.failure_reason
    tx.payload = dict(tx.payload or {})
    tx.payload['webhook'] = entity
    tx.save(update_fields=['status', 'gateway_payment_id', 'failure_reason', 'payload', 'updated_at'])
    Reservation.objects.filter(pk=tx.reservation_id).update(
        payment_status='failed', updated_at=timezone.now()
    )
    _send_failure_email_once(tx)


def _webhook_refund(payload):
    data = payload.get('payload') or {}
    entity = (data.get('refund') or {}).get('entity') or {}
    refund_id = entity.get('id') or ''
    payment_id = entity.get('payment_id') or ''
    status = entity.get('status') or 'processed'
    tx = PaymentTransaction.objects.filter(
        refund_id=refund_id
    ).first()
    if tx is None and payment_id:
        tx = PaymentTransaction.objects.filter(
            gateway_payment_id=payment_id
        ).first()
    if tx is None:
        return
    tx.status = 'refunded' if status == 'processed' else 'refund_requested'
    tx.refund_id = refund_id or tx.refund_id
    tx.refunded_at = timezone.now()
    tx.payload = dict(tx.payload or {})
    tx.payload['webhook'] = entity
    tx.save(update_fields=['status', 'refund_id', 'refunded_at', 'payload', 'updated_at'])
