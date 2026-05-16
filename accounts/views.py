from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.contrib.auth import logout as auth_logout

def login_selection(request):
    return render(request, 'accounts/login_selection.html')

def gate_login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None and user.user_type == 'security_officer':
            login(request, user)
            return redirect('gate_guard:overview')
        else:
            messages.error(request, 'Invalid credentials or not a Security Officer.')
    # For GET or failed POST, show the same login page
    return render(request, 'accounts/login_selection.html')

def sticker_login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None and user.user_type == 'sticker_admin':
            login(request, user)
            return redirect('sticker_portal:dashboard')
        else:
            messages.error(request, 'Invalid credentials or not a Sticker Admin.')
    return render(request, 'accounts/login_selection.html')

def applicant_login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None and user.user_type == 'applicant':
            login(request, user)
            return redirect('sticker_portal:apply')
        else:
            messages.error(request, 'Invalid credentials or not an applicant.')
    return render(request, 'accounts/applicant_login.html')

def custom_logout(request):
    """Log out the user and clear leftover messages."""
    if request.user.is_authenticated:
        # Remove all messages from the session so they don't bleed into the login page
        storage = messages.get_messages(request)
        storage.used = True   # mark all messages as used
    auth_logout(request)
    return redirect('accounts:login_selection')   # or wherever you want to land