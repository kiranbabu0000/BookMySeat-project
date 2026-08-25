from django.core.cache import cache
from .models import Notification

_CACHE_TTL = 30


def admin_notifications(request):
    """Unread notification count for the admin topbar badge.

    Only populated for requests that carry an authenticated admin session so
    customer-facing pages never run this query.
    """
    if not (
        request.session.get('is_admin_authenticated')
        and request.session.get('admin_user_id')
    ):
        return {}
    cache_key = 'bms:admin_notif_count'
    count = cache.get(cache_key)
    if count is None:
        count = Notification.objects.filter(is_read=False).count()
        cache.set(cache_key, count, _CACHE_TTL)
    return {'notifications': count}
