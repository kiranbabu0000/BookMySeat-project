from admin_panel.models import Notification


def unread_notifications(request):
    if not request.user.is_authenticated:
        return {'unread_notification_count': 0}
    return {'unread_notification_count': Notification.objects.filter(
        user=request.user, is_read=False
    ).count()}
