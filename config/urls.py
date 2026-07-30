from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from accounts.views import dashboard_redirect

# Admin branding
admin.site.site_header = 'PalSU Gate System Administration'
admin.site.site_title = 'PalSU Admin'
admin.site.index_title = 'System Administration'

urlpatterns = [
    path('palsu-system-admin-2025/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('dashboard/', dashboard_redirect, name='dashboard'),
    path('apply/', include('applications.urls')),
    path('sticker-admin/', include('sticker_admin.urls')),
    path('gate/', include('gate.urls')),
    path('api/', include('api.urls')),

    # Password reset
    path('accounts/password-reset/',
         auth_views.PasswordResetView.as_view(
             template_name='accounts/password_reset.html'
         ),
         name='password_reset'),
    path('accounts/password-reset/done/',
         auth_views.PasswordResetDoneView.as_view(
             template_name='accounts/password_reset_done.html'
         ),
         name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/',
         auth_views.PasswordResetConfirmView.as_view(
             template_name='accounts/password_reset_confirm.html'
         ),
         name='password_reset_confirm'),
    path('accounts/reset/done/',
         auth_views.PasswordResetCompleteView.as_view(
             template_name='accounts/password_reset_complete.html'
         ),
         name='password_reset_complete'),
]

# Serve media files in all environments for local network deployment
# In production with nginx, nginx handles /media/ instead
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
