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

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
