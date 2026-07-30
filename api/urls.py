from django.urls import path
from . import views

urlpatterns = [
    path('scan/', views.scan_tag, name='api_scan'),
    path('register-uid/', views.register_uid, name='api_register_uid'),
    path('latest-pending-uid/', views.latest_pending_uid, name='api_latest_pending_uid'),
    path('gate-status/', views.gate_status, name='api_gate_status'),
]