from django.conf import settings
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django_ratelimit.decorators import ratelimit
from accounts.views import (
    CampusPasswordResetDoneView,
    CampusPasswordResetView,
    dashboard_redirect,
    password_reset_context,
    password_reset_resend_view,
)


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
admin.site.site_header = 'PalawanSU Gate System Administration'
admin.site.site_title = 'PalawanSU Admin'
admin.site.index_title = 'System Administration'

# The first two steps of the reset are ours rather than Django's stock
# views (see accounts.views): the form accepts a username as well as an
# address because staff sign in with usernames, the mail is in the same
# plain-text house style as everything else the system sends, and the
# "check your email" step can send the link again without making anyone
# type their username a second time.
#
# Password reset requests are rate-limited by IP — same pattern as
# login_view, since these endpoints send email / enumerate accounts and
# had no limit before.
_reset_rate_limit = ratelimit(key='ip', rate='5/m', method='POST', block=True)

password_reset_view = _reset_rate_limit(CampusPasswordResetView.as_view())

urlpatterns = [
    path('', root_redirect, name='root'),

    # The Django admin is a second front door, with its own login form, and
    # it is the door superusers use. Its login page renders a "Forgotten
    # your login credentials?" link only when the name `admin_password_reset`
    # resolves — Django's AdminSite does not register it, so until now that
    # door had no way out of a forgotten password at all.
    #
    # Pointed at our own reset flow rather than a second copy of Django's:
    # one set of pages, one branded email, one rate limit, and a superuser
    # who lands there sees the same screens as everybody else.
    path('palsu-system-admin-2025/password_reset/',
         lambda request: redirect('password_reset'),
         name='admin_password_reset'),
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
         CampusPasswordResetDoneView.as_view(),
         name='password_reset_done'),
    path('accounts/password-reset/resend/',
         password_reset_resend_view,
         name='password_reset_resend'),
    path('accounts/reset/<uidb64>/<token>/',
         auth_views.PasswordResetConfirmView.as_view(
             template_name='accounts/password_reset_confirm.html',
             extra_context=password_reset_context(),
         ),
         name='password_reset_confirm'),
    path('accounts/reset/done/',
         auth_views.PasswordResetCompleteView.as_view(
             template_name='accounts/password_reset_complete.html',
             extra_context=password_reset_context(),
         ),
         name='password_reset_complete'),
]

# Media files (applicant ID documents etc.) are NOT served here — see
# applications.views.serve_document. Those files require auth + ownership
# checks, so they can't be handed out by Django's generic static() helper.
