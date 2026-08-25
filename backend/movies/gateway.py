"""Thin, testable wrapper around the Razorpay SDK.

Everything that talks to the payment gateway lives here so the rest of the
codebase (and the test suite) can swap/mock it in one place. All functions
return None or raise on gateway problems; business rules stay in payments.py.
"""
import hashlib
import hmac
import logging
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)

try:
    from razorpay import Client, Utility
    _RAZORPAY_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    Client = None
    Utility = None
    _RAZORPAY_AVAILABLE = False


class GatewayError(Exception):
    """Raised when the payment gateway is unreachable or misbehaves."""


def is_configured():
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def demo_mode():
    """Demo checkout is active only when the real keys are not configured."""
    return bool(settings.RAZORPAY_DEMO_MODE) and not is_configured()


def get_client():
    """Return a configured Razorpay client, or None when unavailable."""
    if not _RAZORPAY_AVAILABLE or not is_configured():
        return None
    return Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def paise_from_decimal(value):
    return int((Decimal(value) * 100).quantize(Decimal('1')))


def decimal_from_paise(value):
    return Decimal(value) / 100


def create_order(amount, receipt, notes=None, currency='INR'):
    """Create a Razorpay order. Returns the order dict; None in demo mode."""
    client = get_client()
    if client is None:
        return None
    amount_paise = paise_from_decimal(amount)
    logger.info(
        'Razorpay create_order request: key=%s*** amount=%s paise currency=%s receipt=%s notes=%s',
        settings.RAZORPAY_KEY_ID[:8] if settings.RAZORPAY_KEY_ID else '',
        amount_paise, currency, receipt, notes,
    )
    try:
        order = client.order.create(data={
            'amount': amount_paise,
            'currency': currency,
            'receipt': receipt,
            'notes': notes or {},
        })
        logger.info('Razorpay create_order response: %s', order)
        return order
    except Exception as exc:
        logger.error('Razorpay create_order failed: %r', exc)
        raise GatewayError('Could not create the payment order.') from exc


def fetch_order(order_id):
    client = get_client()
    if client is None:
        return None
    try:
        order = client.order.fetch(order_id)
        logger.info('Razorpay fetch_order: %s', order)
        return order
    except Exception as exc:
        logger.error('Razorpay fetch_order failed for %s: %r', order_id, exc)
        raise GatewayError('Could not fetch the payment order.') from exc


def fetch_payment(payment_id):
    client = get_client()
    if client is None:
        return None
    try:
        payment = client.payment.fetch(payment_id)
        logger.info('Razorpay fetch_payment: %s', payment)
        return payment
    except Exception as exc:
        logger.error('Razorpay fetch_payment failed for %s: %r', payment_id, exc)
        raise GatewayError('Could not fetch the payment details.') from exc


def capture_payment(payment_id, amount):
    client = get_client()
    if client is None:
        return None
    amount_paise = paise_from_decimal(amount)
    try:
        payment = client.payment.capture(payment_id, amount_paise)
        logger.info('Razorpay capture_payment: %s', payment)
        return payment
    except Exception as exc:
        logger.error('Razorpay capture_payment failed for %s: %r', payment_id, exc)
        raise GatewayError('Could not capture the payment.') from exc


def create_refund(payment_id, amount, notes=None):
    client = get_client()
    if client is None:
        return None
    amount_paise = paise_from_decimal(amount)
    try:
        refund = client.payment.refund(payment_id, data={
            'amount': amount_paise,
            'notes': notes or {},
        })
        logger.info('Razorpay create_refund: %s', refund)
        return refund
    except Exception as exc:
        logger.error('Razorpay create_refund failed for %s: %r', payment_id, exc)
        raise GatewayError('Could not create the refund.') from exc


def verify_payment_signature(order_id, payment_id, signature):
    """Return True when the checkout signature matches our key secret."""
    if not is_configured() or Utility is None:
        logger.warning(
            'verify_payment_signature skipped: configured=%s sdk=%s',
            is_configured(), Utility is not None,
        )
        return False
    client = get_client()
    if client is None:
        return False
    logger.debug(
        'verify_payment_signature: order=%s payment=%s signature=%s',
        order_id, payment_id, signature,
    )
    try:
        Utility(client).verify_payment_signature({
            'razorpay_order_id': order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature,
        })
        logger.info('Payment signature verified for order %s', order_id)
        return True
    except Exception as exc:
        logger.warning(
            'Payment signature verification FAILED for order=%s payment=%s: %r',
            order_id, payment_id, exc,
        )
        return False


def demo_signature(order_id, payment_id):
    """Signature used only by the demo checkout.

    Mirrors Razorpay's HMAC-SHA256 over 'order_id|payment_id' with the key
    secret, so the demo flow exercises the exact same verification path. The
    demo path is only reachable when the gateway is not configured.
    """
    secret = settings.RAZORPAY_KEY_SECRET or 'bookmyseat-demo-secret'
    return hmac.HMAC(
        secret.encode('utf-8'),
        '{}|{}'.format(order_id, payment_id).encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def verify_webhook_signature(body, signature):
    """Return True when the webhook HMAC matches the configured secret."""
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not secret:
        logger.warning(
            'Webhook signature verification skipped: RAZORPAY_WEBHOOK_SECRET is not configured.'
        )
        return False
    if isinstance(body, str):
        body = body.encode('utf-8')
    expected = hmac.HMAC(
        secret.encode('utf-8'), body, hashlib.sha256
    ).hexdigest()
    ok = hmac.compare_digest(expected, signature or '')
    logger.debug('Webhook signature verification: %s', 'passed' if ok else 'FAILED')
    return ok
