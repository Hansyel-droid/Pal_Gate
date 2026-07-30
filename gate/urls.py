from django.urls import path
from . import views

urlpatterns = [
    path('live/', views.gate_live, name='gate_live'),
    path('logs/', views.gate_logs, name='gate_logs'),
    path('logs/<int:pk>/', views.incident_report, name='incident_report'),
    path('logs/<int:pk>/pdf/', views.incident_pdf, name='incident_pdf'),
    path('logs/export/csv/', views.export_csv, name='export_csv'),
    path('time-tracker/', views.time_tracker, name='time_tracker'),
]