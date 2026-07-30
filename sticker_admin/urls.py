from django.urls import path
from . import views

urlpatterns = [
    path('', views.admin_dashboard, name='admin_dashboard'),
    path('registration-window/', views.registration_window, name='registration_window'),
    path('appointment-dates/', views.appointment_dates, name='appointment_dates'),
    path('applications/', views.application_list, name='application_list'),
    path('applications/<int:pk>/', views.application_detail, name='application_detail'),
    path('applications/<int:pk>/approve/', views.approve_application, name='approve_application'),
    path('applications/<int:pk>/reject/', views.reject_application, name='reject_application'),
    path('sticker-station/', views.sticker_station, name='sticker_station'),
    path('sticker-station/issue/<int:pk>/', views.issue_sticker, name='issue_sticker'),
    path('quick-register/', views.quick_register, name='quick_register'),
]