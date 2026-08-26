from django.contrib.auth.models import User
from django.core.cache import cache
from .models import AdminProfile
from .decorators import clear_admin_session, ADMIN_BROWSER_MARKER

ADMIN_URL_PREFIXES = (
    '/admin-login/', '/admin-logout/', '/dashboard/',
    '/admin-movies/', '/genres/', '/languages/',
    '/cast/', '/theatres/', '/screens/', '/shows/', '/pricing/',
    '/trailers/', '/images/', '/seats/', '/bookings/', '/reservations/',
    '/payments/', '/users/', '/staff/', '/coupons/', '/notifications/', '/admin-notifications/',
    '/reviews/', '/logs/', '/settings/', '/search-suggestions/', '/admin-search/',
    '/analytics/', '/account/', '/scanner/', '/scans/',
)

_CACHE_TTL = 30  # seconds — short enough to never serve stale after permission change


def is_admin_request(path):
    return any(path.startswith(prefix) for prefix in ADMIN_URL_PREFIXES)


class AdminIdentityMiddleware:
    """Resolve request.user to the admin identity stored in the admin session.

    The admin portal keeps its own session key (admin_user_id) instead of
    Django's shared _auth_user_id, so an admin login never becomes a customer
    login and vice versa. This middleware maps the admin identity onto
    request.user only for admin portal URLs; customer pages keep their own
    customer identity (or remain anonymous).

    The User + AdminProfile lookup is cached per session for a short TTL so
    repeated admin-page loads do not hit the database on every request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if is_admin_request(request.path):
            # A volatile browser-session cookie (max-age=None) acts as a
            # "browser session marker".  When the browser is closed and
            # reopened the cookie disappears, signalling a new browser
            # session.  Any stale admin session data left in the server-
            # side session store must then be cleared so the admin is
            # forced to log in again.  This works regardless of Django's
            # SESSION_EXPIRE_AT_BROWSER_CLOSE setting and is immune to
            # browser session-restore features.
            if not request.COOKIES.get(ADMIN_BROWSER_MARKER):
                clear_admin_session(request)

            admin_user_id = request.session.get('admin_user_id')
            if admin_user_id is not None:
                cache_key = 'bms:admin_id:{}'.format(admin_user_id)
                admin = cache.get(cache_key)
                if admin is None:
                    try:
                        admin = User.objects.get(pk=admin_user_id, is_active=True)
                    except (User.DoesNotExist, ValueError, TypeError):
                        admin = None
                    if admin is not None and not (
                        admin.is_superuser
                        or AdminProfile.objects.filter(user=admin, is_active=True).exists()
                    ):
                        admin = None
                    # Cache None too so we don't re-query a deleted/blocked admin
                    # on every subsequent request within the TTL window.
                    cache.set(cache_key, admin, _CACHE_TTL)
                if admin is not None:
                    request.user = admin
                else:
                    cache.delete(cache_key)
                    clear_admin_session(request)
        response = self.get_response(request)
        return response
