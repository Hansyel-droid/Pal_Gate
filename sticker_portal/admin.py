from django.contrib import admin
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from .models import Vehicle, StickerApplication, Document

@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ('plate_number', 'model', 'color', 'owner', 'is_owner')
    search_fields = ('plate_number', 'model', 'owner__username', 'owner__email')


@admin.register(StickerApplication)
class StickerApplicationAdmin(admin.ModelAdmin):
    list_display = ('id', 'applicant', 'vehicle', 'status', 'expiry_date', 'submitted_at')
    list_filter = ('status', 'submitted_at')
    search_fields = ('applicant__username', 'applicant__email', 'vehicle__plate_number')
    actions = ['approve_applications', 'reject_applications']

    def approve_applications(self, request, queryset):
        for app in queryset:
            app.status = 'approved'
            app.approved_at = timezone.now()
            app.approved_by = request.user
            app.save()
            # Send email
            subject = "Your Sticker Application Has Been Approved"
            message = "Your application has been approved. Please claim your sticker at the security office."
            recipient = app.applicant.email
            if recipient:
                send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [recipient])
    approve_applications.short_description = "Approve selected applications"

    def reject_applications(self, request, queryset):
        queryset.update(status='rejected')
    reject_applications.short_description = "Reject selected applications"


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('application', 'document_type', 'uploaded_at')
    list_filter = ('document_type',)