from django.urls import path
from . import views

urlpatterns = [
    path('api/track/', views.track_activity, name='track_activity'),
]