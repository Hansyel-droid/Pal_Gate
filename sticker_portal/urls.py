from django.urls import path
from . import views

app_name = 'sticker_portal'

urlpatterns = [
    # Admin
    path('dashboard/', views.dashboard, name='dashboard'),
    path('pending/', views.pending_approvals, name='pending_approvals'),
    path('application/<int:app_id>/', views.application_detail, name='application_detail'),
    path('settings/', views.settings, name='settings'),
    # Applicant
    path('apply/', views.apply, name='apply'),
    path('my-applications/', views.my_applications, name='my_applications'),
]