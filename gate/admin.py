from django.contrib import admin
from .models import GateLog, PendingRFID, AuditLog


@admin.register(GateLog)
class GateLogAdmin(admin.ModelAdmin):
    list_display = ('rfid_uid', 'action', 'gate_location', 'timestamp')
    list_filter = ('action', 'gate_location')
    search_fields = ('rfid_uid', 'application__plate_number',
                     'application__full_name')
    readonly_fields = ('rfid_uid', 'application', 'action', 'gate_location',
                       'denial_reason', 'timestamp')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PendingRFID)
class PendingRFIDAdmin(admin.ModelAdmin):
    list_display = ('uid', 'registered_at', 'claimed')
    list_filter = ('claimed',)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'actor', 'action', 'description',
                    'ip_address', 'target_user')
    list_filter = ('action',)
    search_fields = ('actor__username', 'description', 'target_user',
                     'ip_address')
    readonly_fields = ('actor', 'action', 'description', 'ip_address',
                       'target_user', 'extra_data', 'timestamp')

    # Audit logs are immutable — no one can add, edit, or delete them
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
