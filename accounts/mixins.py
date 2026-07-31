import functools
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.contrib import messages


def role_required(role):
    """
    A decorator that checks if the logged-in user has the correct role.
    If not, they get redirected to their own dashboard with an error message.

    Usage:
        @role_required('admin')
        def my_view(request):
            ...
    """
    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # First check if they are logged in at all
            if not request.user.is_authenticated:
                return redirect('login')

            # Then check if they have the right role
            if request.user.role != role:
                messages.error(
                    request,
                    f'You do not have permission to access that page.'
                )
                return redirect('dashboard')

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator