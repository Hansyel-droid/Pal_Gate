from django.urls import path
from . import views

app_name = 'api'

urlpatterns = [
    path('scan/', views.scan, name='scan'),
]