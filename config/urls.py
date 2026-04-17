from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='accounts:login_selection'), name='home'),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('gate/', include('gate_guard.urls')),
    path('sticker/', include('sticker_portal.urls')),
    path('api/', include('api.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)