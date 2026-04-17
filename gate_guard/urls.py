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
]