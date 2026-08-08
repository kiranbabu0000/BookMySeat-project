"""Shared ticket QR helpers: signed payloads, PNG bytes and data URIs.

The QR payload is HMAC-signed so a venue gate scanner can cryptographically
verify that a ticket was issued by BookMySeat without needing a live DB row.
"""
import base64
import hashlib
import hmac
import json
from io import BytesIO

from django.conf import settings


def _signing_key():
    return settings.SECRET_KEY.encode('utf-8')


def _payload_message(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')


def sign_qr_payload(payload):
    """Return a hex HMAC-SHA256 signature for a payload dict."""
    return hmac.new(_signing_key(), _payload_message(payload), hashlib.sha256).hexdigest()


def build_qr_payload(booking_ref, movie_name, theatre_name, seats):
    """Build the ticket QR payload with an HMAC signature attached."""
    payload = {
        'booking_id': booking_ref,
        'movie': movie_name,
        'theatre': theatre_name,
        'seats': list(seats),
    }
    payload['sig'] = sign_qr_payload(payload)
    return payload


def verify_qr_payload(payload):
    """Return True when payload carries a valid HMAC signature."""
    if not isinstance(payload, dict):
        return False
    payload = dict(payload)
    sig = payload.pop('sig', None)
    if not isinstance(sig, str) or not sig:
        return False
    return hmac.compare_digest(sign_qr_payload(payload), sig)


def ticket_qr_png_bytes(payload):
    """Render a payload dict as QR PNG bytes (None if the library is missing)."""
    try:
        import qrcode
    except ImportError:
        return None
    try:
        image = qrcode.make(json.dumps(payload), box_size=8, border=2)
    except Exception:
        return None
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def ticket_qr_data_uri(payload):
    """Render a payload dict as a QR PNG data URI ('' if unavailable)."""
    png = ticket_qr_png_bytes(payload)
    if not png:
        return ''
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')
