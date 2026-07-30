from django.contrib import admin
from .models import RegistrationWindow, StickerApplication

@admin.register(RegistrationWindow)
class RegistrationWindowAdmin(admin.ModelAdmin):
    list_display = ('start_date', 'end_date', 'is_active')

@admin.register(StickerApplication)
class StickerApplicationAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'plate_number', 'status', 'classification', 'submitted_at')
    list_filter = ('status', 'classification', 'vehicle_type')
    search_fields = ('full_name', 'plate_number', 'id_number', 'sticker_id')