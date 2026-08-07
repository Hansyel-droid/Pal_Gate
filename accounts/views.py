from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit
from .forms import RegisterForm, LoginForm, OTPVerifyForm
from .models import EmailOTP, User
from .otp import issue_otp, seconds_until_resend, verify_otp
from gate.audit import log_action

# Where we remember, between the sign-up POST and the code they type back,
# which half-finished account the browser is verifying.
PENDING_SESSION_KEY = 'pending_verification_user_id'


def _pending_user(request):
    """
    The unverified account this session started creating, or None.

    Scoped by the session key *and* by the unverified state, so the view
    can never be pointed at a real, active account — and so a stale key
    left over from a completed sign-up resolves to nothing.
    """
    user_id = request.session.get(PENDING_SESSION_KEY)
    if not user_id:
        return None

    user = User.objects.filter(
        pk=user_id, is_active=False, email_verified=False
    ).first()
    if user is None:
        request.session.pop(PENDING_SESSION_KEY, None)
    return user


def _release_abandoned_signups(request, username, email):
    """
    Delete half-finished sign-ups holding the username or email being
    submitted, so an abandoned attempt doesn't lock the address out.

    The filter is deliberately narrow: the account must be inactive,
    unverified, never logged in, and have an actual registration code on
    file. Any real account — including one an admin has suspended, or a
    legacy walk-in record with no password — fails at least one of those,
    so this can't be used to delete somebody else's account by guessing
    their username.

    That still wasn't narrow enough. This runs on raw POST data before the
    form has been validated, so it acted on any username a caller cared to
    type — including one belonging to somebody sitting on the verification
    page right then. Knowing a username was enough to delete their
    in-progress sign-up and strand them.

    So a match is only released when it is genuinely abandoned — its
    verification code has expired, which is the point the sign-up stops
    being completable on its own — or when it belongs to this very session,
    which covers the ordinary "start over" case in the same browser.
    """
    if not (username or email):
        return

    candidates = User.objects.filter(
        is_active=False,
        email_verified=False,
        last_login__isnull=True,
        email_otps__purpose=EmailOTP.PURPOSE_REGISTER,
    )

    # Resolve to ids first — the filter above spans a join, and deleting
    # through one is a good way to take more rows than intended.
    matched_ids = set()
    if username:
        matched_ids.update(
            candidates.filter(username__iexact=username.strip())
            .values_list('pk', flat=True)
        )
    if email:
        matched_ids.update(
            candidates.filter(email__iexact=email.strip())
            .values_list('pk', flat=True)
        )

    if not matched_ids:
        return

    # Anyone holding a code that is still live is mid-verification, and
    # this request has no business deleting them unless it IS them.
    still_verifying = set(
        EmailOTP.objects.filter(
            user_id__in=matched_ids,
            purpose=EmailOTP.PURPOSE_REGISTER,
            used_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).values_list('user_id', flat=True)
    )
    own_pending_id = request.session.get(PENDING_SESSION_KEY)

    releasable = {
        pk for pk in matched_ids
        if pk not in still_verifying or pk == own_pending_id
    }
    if releasable:
        User.objects.filter(pk__in=releasable).delete()


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def register_view(request):
    """
    Step 1 of registration: collect the details, create the account in an
    inactive state, and email a one-time code. The account only becomes
    usable in verify_email_view once that code comes back.
    """
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        _release_abandoned_signups(
            request,
            request.POST.get('username', ''),
            request.POST.get('email', ''),
        )

        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.role = 'applicant'  # All self-registered users are applicants
            # Inactive until the emailed code is confirmed — Django's
            # ModelBackend refuses to log in an inactive user, so this
            # is what actually enforces verification.
            user.is_active = False
            user.email_verified = False
            user.save()
            _, sent = issue_otp(user)

            request.session[PENDING_SESSION_KEY] = user.pk
            log_action(
                request,
                'register_started',
                f'{user.username} started registration; '
                f'verification code sent to {user.email}',
                target_user=user.username,
            )

            if sent:
                messages.success(
                    request,
                    f'We sent a {settings.OTP_LENGTH}-digit code to '
                    f'{user.email}. Enter it below to finish.'
                )
            else:
                messages.warning(
                    request,
                    'We could not send your verification code right now. '
                    'Try "Resend code" in a moment.'
                )
            return redirect('verify_email')
        else:
            messages.error(request, 'Please fix the errors below.')
    else:
        form = RegisterForm()

    return render(request, 'accounts/register.html', {
        'form': form,
        'email_domain': settings.REGISTRATION_EMAIL_DOMAIN,
    })


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def verify_email_view(request):
    """
    Step 2 of registration: type back the emailed code to activate the
    account.
    """
    if request.user.is_authenticated:
        return redirect('dashboard')

    user = _pending_user(request)
    if user is None:
        messages.error(
            request,
            'Your sign-up session expired. Please create your account again.'
        )
        return redirect('register')

    if request.method == 'POST':
        form = OTPVerifyForm(request.POST)
        if form.is_valid():
            ok, error = verify_otp(user, form.cleaned_data['code'])
            if ok:
                user.is_active = True
                user.email_verified = True
                user.save(update_fields=['is_active', 'email_verified'])
                request.session.pop(PENDING_SESSION_KEY, None)

                log_action(
                    request,
                    'email_verified',
                    f'{user.username} verified {user.email}',
                    target_user=user.username,
                )
                messages.success(
                    request,
                    'Email verified! Your account is ready — you can now log in.'
                )
                return redirect('login')
            messages.error(request, error)
        else:
            messages.error(request, 'Please fix the errors below.')
    else:
        form = OTPVerifyForm()

    return render(request, 'accounts/verify_email.html', {
        'form': form,
        'email': user.email,
        'code_length': settings.OTP_LENGTH,
        'expiry_minutes': settings.OTP_EXPIRY_MINUTES,
        'resend_in': seconds_until_resend(user),
    })


@require_POST
@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def resend_otp_view(request):
    """Send a fresh code, subject to the per-account cooldown."""
    user = _pending_user(request)
    if user is None:
        messages.error(
            request,
            'Your sign-up session expired. Please create your account again.'
        )
        return redirect('register')

    wait = seconds_until_resend(user)
    if wait > 0:
        messages.error(
            request,
            f'Please wait {wait} more second{"" if wait == 1 else "s"} '
            'before requesting another code.'
        )
        return redirect('verify_email')

    _, sent = issue_otp(user)
    if sent:
        messages.success(request, f'A new code is on its way to {user.email}.')
    else:
        messages.warning(
            request,
            'We could not send the code right now. Please try again shortly.'
        )
    return redirect('verify_email')


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']

            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                log_action(
                    request,
                    'login',
                    f'{username} logged in successfully',
                    target_user=username
                )
                return redirect('dashboard')
            else:
                log_action(
                    request,
                    'login_failed',
                    f'Failed login attempt for username: {username}',
                    target_user=username
                )
                messages.error(
                    request,
                    'Invalid username or password. '
                    'Your account will be locked after 5 failed attempts.'
                )
    else:
        form = LoginForm()

    return render(request, 'accounts/login.html', {'form': form})


@require_POST
def logout_view(request):
    """Logs the user out."""
    username = request.user.username
    log_action(request, 'logout', f'{username} logged out')
    logout(request)
    return redirect('login')


@login_required
def dashboard_redirect(request):
    """
    Sends each role to their own dashboard.
    This is the view for the /dashboard/ URL.
    """
    if request.user.role == 'admin':
        return redirect('admin_dashboard')
    elif request.user.role == 'security':
        return redirect('gate_live')
    else:
        return redirect('applicant_home')
