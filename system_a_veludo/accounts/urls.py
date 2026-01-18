from django.urls import path
from . import views

urlpatterns = [
    # --- 认证相关 ---
    path('login/', views.CustomLoginView.as_view(), name='login'),
    #path('register/', views.RegisterView.as_view(), name='register'),
    path('logout/', views.logout_view, name='logout'),
    
    # --- 用户页面 ---
    path('profile/', views.ProfileView.as_view(), name='profile'),
    path('my-bookings/', views.MyBookingsPageView.as_view(), name='my_bookings'),
    path('schedule/', views.ScheduleView.as_view(), name='schedule'),
    
    # --- 管理员面板 ---
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),

    # --- 预约流程 ---
    path('booking/', views.BookingPageView.as_view(), name='booking_page'),

    # --- API 接口 ---
    path('api/availability/', views.AvailabilityAPIView.as_view(), name='api_availability'),
    path('api/availability/recurring-config/', views.recurring_config_proxy, name='api_recurring_config'),
    # [修正] 这里的路径不能和上面一样，加个后缀区分
    path('api/availability/proxy/', views.availability_proxy, name='api_availability_proxy'),
    
    path('api/booking/submit/', views.BookingActionAPI.as_view(), name='api_booking_submit'),
    path('api/cast/search/', views.CastSearchAPI.as_view(), name='api_cast_search'),
    path('api/booking/cancel/<str:pk>/', views.BookingCancelAPI.as_view(), name='api_booking_cancel'),
    path('api/booking/complete/<str:pk>/', views.BookingCompleteAPI.as_view(), name='api_booking_complete'),
]