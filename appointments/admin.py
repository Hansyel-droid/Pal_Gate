from django.contrib import admin
from .models import AppointmentSlot, Appointment

@admin.register(AppointmentSlot)
class AppointmentSlotAdmin(admin.ModelAdmin):
    list_display = ('date', 'capacity', 'slots_remaining', 'is_active')

@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ('application', 'slot', 'time')