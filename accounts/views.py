from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit
from .forms import RegisterForm, LoginForm
from gate.audit import log_action


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def register_view(request):
    """Handles new applicant registration."""
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.role = 'applicant'  # All self-registered users are applicants
            user.save()
            messages.success(request, 'Account created! You can now log in.')
            return redirect('login')
        else:
            messages.error(request, 'Please fix the errors below.')
    else:
        form = RegisterForm()

    return render(request, 'accounts/register.html', {'form': form})


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