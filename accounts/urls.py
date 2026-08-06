from django.urls import path
from . import views

urlpatterns = [
    path('register/', views.register_view, name='register'),
    path('register/verify/', views.verify_email_view, name='verify_email'),
    path('register/resend/', views.resend_otp_view, name='resend_otp'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
]