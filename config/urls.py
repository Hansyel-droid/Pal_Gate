from django.contrib import admin
from django.shortcuts import redirect
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django_ratelimit.decorators import ratelimit
from accounts.views import dashboard_redirect


def root_redirect(request):
    """
    The site root. Nothing was mapped here before, so anyone typing the
    bare address (the normal thing to do when handed a link) got a 404
    and reasonably concluded the site was broken.

    Signed-in users go to their own dashboard; everyone else goes to the
    login page.
    """
    if request.user.is_authenticated:
        return redirect('dashboard')
    return redirect('login')

# Admin branding
admin.site.site_header = 'PalSU Gate System Administration'
admin.site.site_title = 'PalSU Admin'
admin.site.index_title = 'System Administration'

# Password reset requests are rate-limited by IP — same pattern as login_view,
# since these endpoints send email / enumerate accounts and had no limit before.
password_reset_view = ratelimit(key='ip', rate='5/m', method='POST', block=True)(
    auth_views.PasswordResetView.as_view(template_name='accounts/password_reset.html')
)

urlpatterns = [
    path('', root_redirect, name='root'),
    path('palsu-system-admin-2025/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('dashboard/', dashboard_redirect, name='dashboard'),
    path('apply/', include('applications.urls')),
    path('sticker-admin/', include('sticker_admin.urls')),
    path('gate/', include('gate.urls')),
    path('api/', include('api.urls')),

    # Password reset
    path('accounts/password-reset/', password_reset_view, name='password_reset'),
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

# Media files (applicant ID documents etc.) are NOT served here — see
# applications.views.serve_document. Those files require auth + ownership
# checks, so they can't be handed out by Django's generic static() helper.
