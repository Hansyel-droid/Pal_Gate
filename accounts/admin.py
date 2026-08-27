from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from .models import User


class RequireStaffEmailMixin:
    """
    Sticker administrators and security officers must have an email
    address; applicants are left alone.

    Staff accounts are only ever made here, by hand, and Django's stock
    "add user" page asks for nothing but a username and password — which
    is how the system ended up with staff who have no address at all.
    Those accounts cannot use "forgot password": there is nowhere to send
    the link, so the only route back in is a superuser resetting it for
    them, and a superuser in the same position has no route at all.

    Applicants are exempt because the legacy walk-in records predate the
    portal and were never logins (see applications/notifications.py) —
    requiring an address here would make those rows uneditable.
    """

    STAFF_ROLES = ('admin', 'security')

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get('role')
        email = (cleaned.get('email') or '').strip()

        if role in self.STAFF_ROLES and not email:
            self.add_error('email', forms.ValidationError(
                'Staff accounts need an email address — it is the only way '
                'to reset a forgotten password.'
            ))
        return cleaned


# Both forms name our User explicitly. Django's stock auth forms are
# declared against auth.User, which the admin quietly swaps for the real
# model when it builds the form — fine in the admin, but it means the
# classes cannot be instantiated anywhere else (a test, a script) without
# blowing up on the swapped-out manager.
class StaffAwareUserChangeForm(RequireStaffEmailMixin, UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = '__all__'


class StaffAwareUserCreationForm(RequireStaffEmailMixin, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'role',
                  'college_department', 'id_number', 'classification',
                  'contact_number')


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    form = StaffAwareUserChangeForm
    add_form = StaffAwareUserCreationForm

    list_display = ('username', 'email', 'get_full_name', 'role',
                    'classification', 'is_staff', 'is_active')
    list_filter = ('role', 'classification', 'is_staff', 'is_active')
    search_fields = ('username', 'email', 'first_name', 'last_name', 'id_number')

    fieldsets = UserAdmin.fieldsets + (
        ('PalawanSU Info', {
            'fields': ('role', 'college_department', 'id_number',
                       'classification', 'contact_number')
        }),
    )

    # Name and email are on the *add* page deliberately. They are editable
    # afterwards either way, but a field somebody has to come back for is a
    # field that stays blank, and a blank address is an account that can
    # never recover its own password.
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Contact', {
            'fields': ('first_name', 'last_name', 'email')
        }),
        ('PalawanSU Info', {
            'fields': ('role', 'college_department', 'id_number',
                       'classification', 'contact_number')
        }),
    )
