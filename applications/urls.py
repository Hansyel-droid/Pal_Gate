from django.urls import path
from . import views

urlpatterns = [
    path('', views.applicant_home, name='applicant_home'),
    path('renew/<int:pk>/', views.apply_renew, name='apply_renew'),
    path('step1/', views.apply_step1, name='apply_step1'),
    path('step2/', views.apply_step2, name='apply_step2'),
    path('step3/', views.apply_step3, name='apply_step3'),
    path('step4/', views.apply_step4, name='apply_step4'),
    path('my-applications/', views.my_applications, name='my_applications'),
    path('documents/<int:pk>/<str:field_name>/', views.serve_document, name='serve_document'),
]