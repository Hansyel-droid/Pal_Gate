from django.urls import path
from . import views

app_name = 'gate_guard'

urlpatterns = [
    path('overview/', views.overview, name='overview'),   # <-- must have name='overview'
    path('logs/', views.logs, name='logs'),
    path('logs/export/', views.export_logs_csv, name='export_logs_csv'),
    path('campus-map/', views.campus_map, name='campus_map'),
    path('settings/', views.settings, name='settings'),
    path('incident/<int:log_id>/', views.incident_report, name='incident_report'),
    path('incident/<int:log_id>/pdf/', views.download_incident_pdf, name='download_incident_pdf'),
    path('register-rfid/', views.register_rfid, name='register_rfid'),
    path('toggle-admin/', views.toggle_admin_mode, name='toggle_admin_mode'),
]