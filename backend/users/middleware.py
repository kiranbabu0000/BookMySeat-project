import logging
import time

from datetime import datetime, timedelta

from django.shortcuts import redirect
from django.utils import timezone

logger = logging.getLogger('bookmyseat')


CACHEABLE_STATIC_PREFIXES = ('/static/', '/media/')
CACHEABLE_FILE_EXTENSIONS = (
    '.css', '.js', '.mjs', '.json', '.png', '.jpg', '.jpeg', '.gif', '.svg',
    '.webp', '.avif', '.ico', '.woff', '.woff2', '.ttf', '.otf', '.eot',
    '.mp4', '.webm', '.pdf',
)

JUST_LOGGED_OUT_FLAG = 'just_logged_out'
JUST_LOGGED_OUT_WINDOW = timedelta(minutes=30)


class NoStoreMiddleware:
    """Stop the browser from caching session-dependent pages.

    Without this, the browser's back/forward cache (bfcache) can restore a
    previously rendered logged-in page after the user logs out (or a
    logged-out page after login) without ever hitting the server, so the
    back button appears to "re-log" the user. Pages served with
    ``Cache-Control: no-store`` are ineligible for the bfcache, so the back
    button always re-requests the page and gets the current session state.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path.lower()
        if path.startswith(CACHEABLE_STATIC_PREFIXES) or path.endswith(CACHEABLE_FILE_EXTENSIONS):
            return response
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response


class LoggedOutGuardMiddleware:
    """Send freshly logged-out users home instead of the login page.

    Combined with :class:`NoStoreMiddleware` (which keeps the back button from
    restoring a cached logged-in page), pressing Back right after logging out
    re-requests the previous page. If that page is login-protected,
    ``@login_required`` answers with a redirect to the login form — which is
    confusing right after logout. For a short window after logout we rewrite
    those login redirects to the home page instead. The marker is cleared on
    the first bounce, on a successful login, or once the window passes.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.user.is_authenticated:
            if JUST_LOGGED_OUT_FLAG in request.session:
                del request.session[JUST_LOGGED_OUT_FLAG]
            return response
        marker = request.session.get(JUST_LOGGED_OUT_FLAG)
        if not marker:
            return response
        try:
            logged_out_at = datetime.fromisoformat(marker)
        except (TypeError, ValueError):
            del request.session[JUST_LOGGED_OUT_FLAG]
            return response
        if timezone.now() - logged_out_at > JUST_LOGGED_OUT_WINDOW:
            del request.session[JUST_LOGGED_OUT_FLAG]
            return response
        location = response.get('Location', '')
        if location.startswith('/login/?'):
            del request.session[JUST_LOGGED_OUT_FLAG]
            return redirect('home')
        return response


_SLOW_THRESHOLD = 2.0  # seconds


class SlowRequestMiddleware:
    """Log requests that exceed the slow-request threshold.

    Runs last in the middleware chain so it measures the full round-trip
    (DB + template + static).  Logs at WARNING so Render picks it up
    without needing DEBUG-level verbosity.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)
        elapsed = time.monotonic() - start
        if elapsed > _SLOW_THRESHOLD:
            logger.warning(
                'SLOW REQUEST %.2fs %s %s',
                elapsed,
                request.method,
                request.path,
            )
        return response
