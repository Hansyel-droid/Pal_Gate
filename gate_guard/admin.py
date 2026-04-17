from django.contrib import admin
from .models import RFIDTag, GateLog

@admin.register(RFIDTag)
class RFIDTagAdmin(admin.ModelAdmin):
    list_display = ('tag_id', 'sticker_application', 'is_active', 'issued_at')
    list_filter = ('is_active',)
    search_fields = ('tag_id', 'sticker_application__vehicle__plate_number')

@admin.register(GateLog)
class GateLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'plate_number', 'action', 'gate', 'driver_name')
    list_filter = ('action', 'gate', 'timestamp')
    search_fields = ('plate_number', 'driver_name')
    date_hierarchy = 'timestamp'