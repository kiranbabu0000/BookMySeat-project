from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

handler404 = 'movies.views.custom_404'
handler403 = 'movies.views.custom_403'
handler500 = 'movies.views.custom_500'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('users.urls')),
    path('movies/', include('movies.urls')),
    path('', include('admin_panel.urls')),
]

# Serve uploaded media (banners/posters/thumbnails) from MEDIA_ROOT in both
# development and production. On Render the movie image files are committed to
# the repo (see .gitignore) because serverless/Render free disks are not
# persistent. `insecure=True` keeps the pattern active when DEBUG=False.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT, insecure=True)
