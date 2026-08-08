"""Shared helpers for the test-suite.

The developer's .env sets real Razorpay keys. Tests must never touch the
real (or sandbox) Razorpay API, so payment-flow test classes are decorated
with DEMO_RAZORPAY to force the gateway back into offline demo mode
regardless of the ambient environment.
"""
from django.test import override_settings

DEMO_RAZORPAY = override_settings(
    RAZORPAY_DEMO_MODE=True,
    RAZORPAY_KEY_ID='',
    RAZORPAY_KEY_SECRET='',
)
