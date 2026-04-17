from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.login_selection, name='login_selection'),
    path('login/gate/', views.gate_login, name='gate_login'),
    path('login/sticker/', views.sticker_login, name='sticker_login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('login/applicant/', views.applicant_login, name='applicant_login'),
]