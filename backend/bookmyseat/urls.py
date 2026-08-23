from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve as media_serve

handler404 = 'movies.views.custom_404'
handler403 = 'movies.views.custom_403'
handler500 = 'movies.views.custom_500'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('users.urls')),
    path('movies/', include('movies.urls')),
    path('', include('admin_panel.urls')),
]

# Serve uploaded media in development. In production with CLOUDINARY_URL set
# (see settings.STORAGES) uploads live on Cloudinary and are served straight
# from res.cloudinary.com; this route only handles legacy local files.
# Django's static() helper only registers routes when DEBUG=True, so we
# register the /media/ route explicitly instead.
urlpatterns += [
    re_path(
        r'^media/(?P<path>.*)$',
        media_serve,
        {'document_root': settings.MEDIA_ROOT},
    ),
]
