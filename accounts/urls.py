from django.urls import path
from . import views

urlpatterns = [
    path('register/', views.register_view, name='register'),
    path('register/verify/', views.verify_email_view, name='verify_email'),
    path('register/resend/', views.resend_otp_view, name='resend_otp'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('campus-policy/', views.campus_policy_view, name='campus_policy'),
    path('notifications/', views.notifications_list_view, name='notifications_list'),
    path(
        'notifications/<int:pk>/open/',
        views.notification_open_view,
        name='notification_open',
    ),
    path(
        'notifications/mark-all-read/',
        views.notifications_mark_all_read_view,
        name='notifications_mark_all_read',
    ),
]