"""One-time password (OTP) helpers for the login flow.

Codes are stored in the cache (not the session) with a short TTL and a
separate attempt counter, so a leaked session can't be used to replay a code
and brute-forcing is capped.
"""
import hmac
import logging
import secrets

from django.conf import settings
from django.core.cache import cache
from django.template.loader import render_to_string

from movies.models import EmailOutbox

logger = logging.getLogger(__name__)

OTP_CODE_LENGTH = 6
OTP_TTL_SECONDS = 600          # 10 minutes
OTP_MAX_ATTEMPTS = 5
OTP_MAX_RESENDS = 5            # absolute cap on code re-sends per OTP lifetime
RESEND_COOLDOWN_SECONDS = 30


def _otp_key(user_id):
    return 'bms_otp_{}'.format(user_id)


def _attempts_key(user_id):
    return 'bms_otp_attempts_{}'.format(user_id)


def _cooldown_key(user_id):
    return 'bms_otp_cooldown_{}'.format(user_id)


def _resends_key(user_id):
    return 'bms_otp_resends_{}'.format(user_id)


def mask_email(email):
    """Mask an email for display, e.g. j***@example.com."""
    if not email or '@' not in email:
        return email or ''
    local, _, domain = email.partition('@')
    first = local[0] if local else '*'
    return '{}***@{}'.format(first, domain)


def generate_and_store(user):
    """Generate a fresh code for the user and store it in the cache.

    Attempts reset, but the resend counter is left untouched so a re-sent
    code still counts toward the absolute resend cap.
    """
    otp = '{:0{d}}'.format(
        secrets.randbelow(10 ** OTP_CODE_LENGTH), d=OTP_CODE_LENGTH
    )
    cache.set(_otp_key(user.id), otp, OTP_TTL_SECONDS)
    cache.set(_attempts_key(user.id), 0, OTP_TTL_SECONDS)
    cache.delete(_cooldown_key(user.id))
    return otp


def reset_resend_count(user_id):
    """Clear the resend counter (and any cooldown) for a brand-new flow."""
    cache.set(_resends_key(user_id), 0, OTP_TTL_SECONDS)
    cache.delete(_cooldown_key(user_id))


def send_otp_email(user, otp):
    """Enqueue the code into the async email outbox.

    Returns True when the message was queued. Delivery happens asynchronously
    via the ``process_email_outbox`` worker, so a slow or unreachable SMTP
    server never blocks (or hangs) the register request.
    """
    recipient = (user.email or '').strip()
    if not recipient:
        logger.warning('OTP EMAIL SKIPPED: user=%s has no email address', user.id)
        return False
    subject = 'BookMySeat — Verify your email'
    lines = [
        'Hi {},'.format(user.username),
        '',
        'Your one-time verification code is:',
        '',
        '   {}'.format(otp),
        '',
        'Enter this code to activate your account. It expires in 10 minutes.',
        'If you did not create this account, you can safely ignore this email.',
        '',
        '\u2014 BookMySeat',
    ]
    try:
        from movies.notifications import logo_data_uri
        html_body = render_to_string('emails/otp_email.html', {
            'user': user,
            'otp': otp,
            'otp_expiry_minutes': OTP_TTL_SECONDS // 60,
            'site_url': getattr(settings, 'SITE_URL', '').rstrip('/'),
            'logo_data_uri': logo_data_uri(),
        })
        EmailOutbox.objects.create(
            recipient=recipient,
            subject=subject,
            plain_body='\n'.join(lines),
            html_body=html_body,
            max_attempts=getattr(settings, 'EMAIL_OUTBOX_MAX_ATTEMPTS', 6),
        )
        return True
    except Exception as exc:  # noqa: BLE001 - registration must never fail on email
        logger.warning('OTP EMAIL ENQUEUE FAILED for user=%s: %s', user.id, exc)
        return False


def resend_count(user_id):
    """Number of code re-sends issued during the current OTP lifetime."""
    return cache.get(_resends_key(user_id), 0)


def can_resend(user_id):
    """True if the user may request another code (cooldown elapsed, cap not hit)."""
    if cache.get(_cooldown_key(user_id)) is not None:
        return False
    return resend_count(user_id) < OTP_MAX_RESENDS


def mark_resend(user_id):
    cache.set(_cooldown_key(user_id), 1, RESEND_COOLDOWN_SECONDS)
    cache.set(_resends_key(user_id), resend_count(user_id) + 1, OTP_TTL_SECONDS)


def remaining_attempts(user_id):
    return max(0, OTP_MAX_ATTEMPTS - cache.get(_attempts_key(user_id), 0))


def verify(user_id, code):
    """Validate a submitted code against the stored one.

    Returns ``(ok, message)``. Each incorrect code consumes one attempt;
    after ``OTP_MAX_ATTEMPTS`` the stored code is invalidated.
    """
    expected = cache.get(_otp_key(user_id))
    if expected is None:
        return False, 'Your code has expired. Please sign in again.'
    attempts = cache.get(_attempts_key(user_id), 0)
    if attempts >= OTP_MAX_ATTEMPTS:
        cache.delete(_otp_key(user_id))
        return False, 'Too many incorrect attempts. Please sign in again.'
    submitted = str(code or '').strip()
    if not submitted.isdigit() or not hmac.compare_digest(expected, submitted):
        attempts += 1
        cache.set(_attempts_key(user_id), attempts, OTP_TTL_SECONDS)
        remaining = OTP_MAX_ATTEMPTS - attempts
        return False, (
            'Incorrect code. {} attempt{} remaining.'.format(
                remaining, '' if remaining == 1 else 's'
            )
        )
    cache.delete(_otp_key(user_id))
    cache.delete(_attempts_key(user_id))
    return True, 'ok'
