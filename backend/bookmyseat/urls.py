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

# Serve uploaded media (banners/posters/thumbnails) in both development and
# production. Django 6's static() helper only registers routes when
# DEBUG=True, so we register the /media/ route explicitly instead.
# On Render/Vercel the movie image files are committed to the repo (see
# .gitignore) because those platforms' disks are not persistent.
urlpatterns += [
    re_path(
        r'^media/(?P<path>.*)$',
        media_serve,
        {'document_root': settings.MEDIA_ROOT},
    ),
]
