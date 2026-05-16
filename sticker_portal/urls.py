from django.urls import path
from . import views

app_name = 'sticker_portal'

urlpatterns = [
    # Admin
    path('dashboard/', views.dashboard, name='dashboard'),
    path('application/<int:app_id>/', views.application_detail, name='application_detail'),
    path('settings/', views.settings, name='settings'),
    path('appointments/', views.appointment_management, name='appointment_management'),
    path('toggle-available-date/', views.toggle_available_date, name='toggle_available_date'),
    path('set-registration-period/', views.set_registration_period, name='set_registration_period'),
    
    # Sticker Station (Phase 2)
    path('station/', views.sticker_station, name='sticker_station'),

    # Applicant three‑step wizard
    path('apply/', views.apply_personal, name='apply'),
    path('apply/<int:app_id>/edit/', views.apply_personal, name='apply_edit'),
    path('apply/<int:app_id>/vehicle/', views.apply_vehicle, name='apply_vehicle'),
    path('application/<int:app_id>/confirm/', views.confirm_application, name='confirm_application'),
    path('application/<int:app_id>/success/', views.application_success, name='application_success'),
    path('my-applications/', views.my_applications, name='my_applications'),
    path('application/<int:app_id>/delete/', views.delete_draft, name='delete_draft'),

    # RFID
    path('register-rfid/', views.sticker_register_rfid, name='sticker_register_rfid'),
]