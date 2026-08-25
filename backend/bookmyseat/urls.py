from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.http import JsonResponse
from django.views.static import serve as media_serve

handler404 = 'movies.views.custom_404'
handler403 = 'movies.views.custom_403'
handler500 = 'movies.views.custom_500'


def health_check(request):
    """Lightweight health endpoint for Render / load-balancer probes.

    Returns HTTP 200 immediately with zero database or external-service
    calls so the probe never times out even during cold start.
    """
    return JsonResponse({'status': 'ok'})


urlpatterns = [
    path('health/', health_check, name='health_check'),
    path('', include('users.urls')),
    path('movies/', include('movies.urls')),
    path('', include('admin_panel.urls')),
]

# Serve uploaded media in development. In production with CLOUDINARY_URL set
# (see settings.STORAGES) uploads live on Cloudinary and are served straight
# from res.cloudinary.com; this route only handles legacy local files.
# In production without Cloudinary, media on the ephemeral disk is lost on
# every restart — serving it through Django is pointless and adds latency.
if settings.DEBUG:
    urlpatterns += [
        re_path(
            r'^media/(?P<path>.*)$',
            media_serve,
            {'document_root': settings.MEDIA_ROOT},
        ),
    ]
