from django.core.cache import cache
from admin_panel.models import Notification

_CACHE_TTL = 30  # seconds — badge count can lag slightly


def unread_notifications(request):
    if not request.user.is_authenticated:
        return {'unread_notification_count': 0}
    cache_key = 'bms:unread_notif:{}'.format(request.user.pk)
    count = cache.get(cache_key)
    if count is None:
        count = Notification.objects.filter(
            user=request.user, is_read=False
        ).count()
        cache.set(cache_key, count, _CACHE_TTL)
    return {'unread_notification_count': count}
