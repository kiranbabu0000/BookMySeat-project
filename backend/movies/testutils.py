"""Shared helpers for the test-suite.

The developer's .env sets real Razorpay keys. Tests must never touch the
real (or sandbox) Razorpay API, so payment-flow test classes are decorated
with DEMO_RAZORPAY to force the gateway back into offline demo mode
regardless of the ambient environment.
"""
from django.test import override_settings
from django.utils import timezone

DEMO_RAZORPAY = override_settings(
    RAZORPAY_DEMO_MODE=True,
    RAZORPAY_KEY_ID='',
    RAZORPAY_KEY_SECRET='',
)

ADMIN_BROWSER_MARKER = 'bms_admin_bsession'


def set_admin_session(client, user):
    """Set up a valid admin session on the test client.

    Mirrors the session keys the real admin_login_view writes and also
    places the browser-session marker cookie so that
    AdminIdentityMiddleware does not clear the session.
    """
    client.force_login(user)
    session = client.session
    session['admin_user_id'] = user.id
    session['is_admin_authenticated'] = True
    session['admin_session_id'] = session.session_key
    session['admin_login_time'] = str(timezone.now())
    session.save()
    client.cookies[ADMIN_BROWSER_MARKER] = '1'
