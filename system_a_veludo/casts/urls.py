# cast/urls.py
from django.urls import path
from .views import CastListView, edit_cast_profile

urlpatterns = [
    path('', CastListView.as_view(), name='cast'), # Cast 列表页
    path('profile/edit/<int:user_id>/', edit_cast_profile, name='edit_cast_profile'),
]