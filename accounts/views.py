from datetime import datetime, timedelta

from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit
from .forms import (
    CampusPasswordResetForm, RegisterForm, LoginForm, OTPVerifyForm,
)
from .models import EmailOTP, Notification, PolicyAcceptance, User
from .otp import issue_otp, seconds_until_resend, verify_otp
from .policy import CAMPUS_POLICY_VERSION, has_accepted_current_policy
from gate.audit import get_client_ip, log_action

# Where we remember, between the sign-up POST and the code they type back,
# which half-finished account the browser is verifying.
PENDING_SESSION_KEY = 'pending_verification_user_id'

# The same two facts for the password reset flow: who asked, and when we
# last mailed them. Kept in the session rather than on the account so
# "send it again" does not need the person to type their username a
# second time, and so nothing is written to the database for an
# identifier that may not match an account at all.
RESET_IDENTIFIER_KEY = 'password_reset_identifier'
RESET_SENT_AT_KEY = 'password_reset_sent_at'


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


def _lookup_pending_user_by_identifier(identifier):
    """
    Fallback for _pending_user() when the session that started
    registration isn't the one finishing it — checking email on a phone
    while signing up on a desktop, a different browser, a cleared cookie,
    or a cold-started server rotating session data are all ordinary ways
    for that to happen with real users. Scoped to inactive+unverified
    accounts only, same as _pending_user(), so this can't be used to
    look up anyone with a real, active account.
    """
    identifier = (identifier or '').strip()
    if not identifier:
        return None
    return User.objects.filter(
        Q(username__iexact=identifier) | Q(email__iexact=identifier),
        is_active=False, email_verified=False,
    ).first()


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

    # The fast path: the session set when registration started is still
    # here, so the person doesn't have to type anything but the code.
    user = _pending_user(request)
    need_identifier = user is None

    if request.method == 'POST':
        form = OTPVerifyForm(request.POST)
        if form.is_valid():
            lookup_user = user or _lookup_pending_user_by_identifier(
                form.cleaned_data['identifier']
            )
            if lookup_user is None:
                messages.error(
                    request,
                    "We couldn't find a pending sign-up for that username "
                    "or email. Double-check what you typed, or start over."
                )
            else:
                # Found by identifier rather than session — adopt them into
                # this session so a mistyped code doesn't force retyping
                # the identifier too on the next attempt.
                request.session[PENDING_SESSION_KEY] = lookup_user.pk
                ok, error = verify_otp(lookup_user, form.cleaned_data['code'])
                if ok:
                    lookup_user.is_active = True
                    lookup_user.email_verified = True
                    lookup_user.save(update_fields=['is_active', 'email_verified'])
                    request.session.pop(PENDING_SESSION_KEY, None)

                    log_action(
                        request,
                        'email_verified',
                        f'{lookup_user.username} verified {lookup_user.email}',
                        target_user=lookup_user.username,
                    )
                    messages.success(
                        request,
                        'Email verified! Your account is ready — you can now log in.'
                    )
                    return redirect('login')
                messages.error(request, error)
                user = lookup_user  # so the re-rendered page still shows their email
        else:
            messages.error(request, 'Please fix the errors below.')
        need_identifier = _pending_user(request) is None
    else:
        form = OTPVerifyForm()

    return render(request, 'accounts/verify_email.html', {
        'form': form,
        'email': user.email if user else None,
        'code_length': settings.OTP_LENGTH,
        'expiry_minutes': settings.OTP_EXPIRY_MINUTES,
        'resend_in': seconds_until_resend(user) if user else None,
        'need_identifier': need_identifier,
    })


@require_POST
@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def resend_otp_view(request):
    """Send a fresh code, subject to the per-account cooldown."""
    user = _pending_user(request) or _lookup_pending_user_by_identifier(
        request.POST.get('identifier', '')
    )
    if user is None:
        messages.error(
            request,
            "We couldn't find a pending sign-up for that username or "
            "email. Double-check what you typed, or start over."
        )
        return redirect('verify_email')

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
def campus_policy_view(request):
    """
    The Campus Access Policy — shown as a gate before an applicant can use
    the system, and reachable from the sidebar forever afterwards.

    One view serves both jobs. If the person hasn't accepted the version
    currently in force they get the Agree control; if they have, they get
    the same text plus a note of when they accepted it. Splitting these
    into two views would mean two copies of a 17-section legal document
    that could drift apart.
    """
    already_accepted = has_accepted_current_policy(request.user)

    if request.method == 'POST' and not already_accepted:
        # get_or_create, not create: the unique constraint would otherwise
        # turn an impatient double-click into an IntegrityError 500 on
        # what is, from the applicant's point of view, a successful action
        # they already completed.
        PolicyAcceptance.objects.get_or_create(
            user=request.user,
            version=CAMPUS_POLICY_VERSION,
            defaults={'ip_address': get_client_ip(request)},
        )
        log_action(
            request,
            'policy_accepted',
            f'{request.user.username} accepted Campus Access Policy '
            f'version {CAMPUS_POLICY_VERSION}'
        )
        messages.success(
            request,
            'Thank you. You can re-read the Campus Access Policy at any '
            'time from the sidebar.'
        )
        # Honour where the middleware wanted them to go, but only if it's a
        # path on this site — an open redirect here would be handed to
        # every applicant at login, which is the worst possible place for one.
        next_url = request.POST.get('next', '')
        if next_url.startswith('/') and not next_url.startswith('//'):
            return redirect(next_url)
        return redirect('dashboard')

    accepted_record = None
    if already_accepted:
        accepted_record = request.user.policy_acceptances.filter(
            version=CAMPUS_POLICY_VERSION
        ).first()

    return render(request, 'accounts/campus_policy.html', {
        'already_accepted': already_accepted,
        'accepted_record': accepted_record,
        'policy_version': CAMPUS_POLICY_VERSION,
        'next': request.GET.get('next', ''),
    })


@login_required
def notifications_list_view(request):
    """
    Everyone's inbox — the page the topbar bell links to. Same view for
    every role; each person only ever sees their own rows regardless
    (Notification.recipient is filtered to request.user), so there's
    nothing role-specific to branch on here.
    """
    notifications = request.user.notifications.all()[:50]
    return render(request, 'accounts/notifications.html', {
        'notifications': notifications,
    })


@login_required
def notification_open_view(request, pk):
    """
    Marks one notification read, then sends the person to whatever it was
    about. A plain GET works fine here — it's an ordinary "follow this
    link" action, and marking an already-read notification read again is
    harmless, so there's no state-mutation hazard a GET would normally
    need to avoid.

    Scoped to request.user's own notifications via get_object_or_404, so
    guessing another id doesn't leak whose it was or mark it read on
    someone else's behalf — a 404 either way.
    """
    notification = get_object_or_404(
        Notification, pk=pk, recipient=request.user
    )
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=['is_read'])

    if notification.link:
        return redirect(notification.link)
    return redirect('notifications_list')


@require_POST
@login_required
def notifications_mark_all_read_view(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return redirect('notifications_list')


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


# ── Password reset ───────────────────────────────────────────────────────────

def password_reset_context():
    """
    The two facts every screen and email in the reset flow states.

    `expiry_hours` is derived from PASSWORD_RESET_TIMEOUT rather than
    written out again, so the pages cannot go on promising an hour after
    ops has changed the setting.
    """
    return {
        'support_email': settings.SUPPORT_EMAIL,
        'expiry_hours': max(1, round(settings.PASSWORD_RESET_TIMEOUT / 3600)),
    }


def _reset_mail_options(request):
    """
    Everything CampusPasswordResetForm.save() needs to send our reset
    email.

    One function, used by both the first send and the resend, because
    they are meant to be the same email — a resend that quietly fell back
    to Django's stock template would be a strange thing to discover.
    """
    return {
        'use_https': request.is_secure(),
        'from_email': settings.DEFAULT_FROM_EMAIL,
        'email_template_name': 'emails/password_reset.txt',
        'subject_template_name': 'emails/password_reset_subject.txt',
        'request': request,
        'extra_email_context': password_reset_context(),
    }


def _remember_reset_request(request, identifier):
    request.session[RESET_IDENTIFIER_KEY] = identifier
    request.session[RESET_SENT_AT_KEY] = timezone.now().isoformat()


def seconds_until_reset_resend(request):
    """
    How long this browser still has to wait before we'll send another
    reset link. 0 means now.

    Same cooldown as the sign-up code (OTP_RESEND_COOLDOWN_SECONDS): both
    answer the same question — how often are we willing to mail the same
    person — and a second knob for it would only be a second thing to get
    out of step. Without the wait, "send it again" is a button that
    floods somebody else's inbox for as long as you keep clicking.
    """
    sent_at = request.session.get(RESET_SENT_AT_KEY)
    if not sent_at:
        return 0

    try:
        last = datetime.fromisoformat(sent_at)
    except (TypeError, ValueError):
        # A malformed timestamp must not lock the button forever.
        return 0

    if timezone.is_naive(last):
        last = timezone.make_aware(last, timezone.utc)

    ready_at = last + timedelta(seconds=settings.OTP_RESEND_COOLDOWN_SECONDS)
    remaining = (ready_at - timezone.now()).total_seconds()
    return max(0, int(remaining + 0.999))


class CampusPasswordResetView(auth_views.PasswordResetView):
    """
    The "which account?" step.

    Overrides form_valid rather than leaving it to Django so the send and
    the remembering happen together: the identifier has to survive into
    the next screen for the resend button there to have anything to send
    to.
    """

    template_name = 'accounts/password_reset.html'
    form_class = CampusPasswordResetForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(password_reset_context())
        return context

    def form_valid(self, form):
        identifier = form.cleaned_data['email']
        form.save(**_reset_mail_options(self.request))
        _remember_reset_request(self.request, identifier)
        return HttpResponseRedirect(self.get_success_url())


class CampusPasswordResetDoneView(auth_views.PasswordResetDoneView):
    """The "check your email" step, which is where resending happens."""

    template_name = 'accounts/password_reset_done.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(password_reset_context())
        context['resend_in'] = seconds_until_reset_resend(self.request)
        # Someone who reached this page directly, or whose session has
        # since expired, has nothing for us to resend — better to show no
        # button than one that can only apologise.
        context['can_resend'] = bool(
            self.request.session.get(RESET_IDENTIFIER_KEY)
        )
        return context


@require_POST
@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def password_reset_resend_view(request):
    """
    Send the reset link again, to whoever this browser asked about a
    moment ago.

    Deliberately says the same thing whether or not anything was sent.
    The page this returns to never reveals whether an account exists, and
    a resend button that said "sent!" for real accounts and something
    else for the rest would hand that back.
    """
    identifier = request.session.get(RESET_IDENTIFIER_KEY)
    if not identifier:
        messages.error(
            request,
            'Tell us which account to reset and we will email a link.'
        )
        return redirect('password_reset')

    wait = seconds_until_reset_resend(request)
    if wait > 0:
        messages.error(
            request,
            f'Please wait {wait} more second{"" if wait == 1 else "s"} '
            'before asking for another link.'
        )
        return redirect('password_reset_done')

    form = CampusPasswordResetForm({'email': identifier})
    if form.is_valid():
        form.save(**_reset_mail_options(request))

    _remember_reset_request(request, identifier)
    messages.success(
        request,
        'If that matches an account, another reset link is on its way.'
    )
    return redirect('password_reset_done')
