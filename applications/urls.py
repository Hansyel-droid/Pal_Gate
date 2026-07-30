from django.urls import path
from . import views

urlpatterns = [
    path('', views.applicant_home, name='applicant_home'),
    path('step1/', views.apply_step1, name='apply_step1'),
    path('step2/', views.apply_step2, name='apply_step2'),
    path('step3/', views.apply_step3, name='apply_step3'),
    path('my-applications/', views.my_applications, name='my_applications'),
]