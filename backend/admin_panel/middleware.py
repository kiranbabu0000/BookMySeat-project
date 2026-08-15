from django.contrib.auth.models import User
from .models import AdminProfile
from .decorators import clear_admin_session

ADMIN_URL_PREFIXES = (
    '/admin-login/', '/admin-logout/', '/dashboard/',
    '/admin-movies/', '/remove-movie/', '/genres/', '/languages/',
    '/cast/', '/theatres/', '/screens/', '/shows/', '/pricing/',
    '/trailers/', '/images/', '/seats/', '/bookings/', '/reservations/',
    '/payments/', '/users/', '/staff/', '/coupons/', '/notifications/', '/admin-notifications/',
    '/reviews/', '/logs/', '/settings/', '/search-suggestions/', '/admin-search/',
    '/analytics/', '/account/',
)


def is_admin_request(path):
    return any(path.startswith(prefix) for prefix in ADMIN_URL_PREFIXES)


class AdminIdentityMiddleware:
    """Resolve request.user to the admin identity stored in the admin session.

    The admin portal keeps its own session key (admin_user_id) instead of
    Django's shared _auth_user_id, so an admin login never becomes a customer
    login and vice versa. This middleware maps the admin identity onto
    request.user only for admin portal URLs; customer pages keep their own
    customer identity (or remain anonymous).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        admin_user_id = request.session.get('admin_user_id')
        if admin_user_id is not None and is_admin_request(request.path):
            admin = None
            try:
                admin = User.objects.get(pk=admin_user_id, is_active=True)
            except (User.DoesNotExist, ValueError, TypeError):
                admin = None
            if admin is not None and (
                admin.is_superuser
                or AdminProfile.objects.filter(user=admin, is_active=True).exists()
            ):
                request.user = admin
            else:
                clear_admin_session(request)
        response = self.get_response(request)
        return response
