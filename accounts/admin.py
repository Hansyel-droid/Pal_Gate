from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User

class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Additional Info', {'fields': ('user_type', 'employee_id', 'student_id', 'college_department', 'classification', 'contact_number')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Additional Info', {'fields': ('user_type', 'employee_id', 'student_id', 'college_department', 'classification', 'contact_number')}),
    )

admin.site.register(User, CustomUserAdmin)