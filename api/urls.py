from django.urls import path
from . import views

app_name = 'api'

urlpatterns = [
    path('scan/', views.scan, name='scan'),
    path('register-uid/', views.register_uid, name='register_uid'),
    path('admin-status/', views.admin_status, name='admin_status'),
]