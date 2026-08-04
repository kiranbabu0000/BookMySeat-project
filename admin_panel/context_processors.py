from .models import Notification


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
    return {'notifications': Notification.objects.filter(is_read=False).count()}
