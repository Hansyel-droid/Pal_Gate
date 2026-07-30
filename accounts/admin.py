from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'get_full_name', 'role',
                    'classification', 'is_staff', 'is_active')
    list_filter = ('role', 'classification', 'is_staff', 'is_active')
    search_fields = ('username', 'email', 'first_name', 'last_name', 'id_number')

    fieldsets = UserAdmin.fieldsets + (
        ('PalSU Info', {
            'fields': ('role', 'college_department', 'id_number',
                       'classification', 'contact_number')
        }),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ('PalSU Info', {
            'fields': ('role', 'college_department', 'id_number',
                       'classification', 'contact_number')
        }),
    )
