from django.urls import path
from . import views

app_name = 'api'

urlpatterns = [
    path('scan/', views.scan, name='scan'),
    path('register-uid/', views.register_uid, name='register_uid'),
    path('admin-status/', views.admin_status, name='admin_status'),
    path('gate-status/', views.gate_status, name='gate_status'),
    path('latest-pending-uid/', views.get_latest_pending_uid, name='get_latest_pending_uid'),
    path('hourly-traffic/', views.hourly_traffic_data, name='hourly_traffic_data'),
]