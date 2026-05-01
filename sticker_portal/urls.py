from django.urls import path
from . import views

app_name = 'sticker_portal'

urlpatterns = [
    # Admin
    path('dashboard/', views.dashboard, name='dashboard'),
    path('application/<int:app_id>/', views.application_detail, name='application_detail'),
    path('settings/', views.settings, name='settings'),
    # Applicant
    path('apply/', views.apply, name='apply'),
    path('my-applications/', views.my_applications, name='my_applications'),
    path('register-rfid/', views.sticker_register_rfid, name='sticker_register_rfid'),
    path('apply/', views.apply, name='apply'),
    path('application/<int:app_id>/confirm/', views.confirm_application, name='confirm_application'),
    path('application/<int:app_id>/success/', views.application_success, name='application_success'),
    path('application/<int:app_id>/schedule/', views.schedule_appointment, name='schedule_appointment'),
    path('appointments/', views.appointment_management, name='appointment_management'),
    path('toggle-available-date/', views.toggle_available_date, name='toggle_available_date'),
    path('application/<int:app_id>/delete/', views.delete_draft, name='delete_draft'),
]