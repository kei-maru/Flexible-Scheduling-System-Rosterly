# system_b_saas/bookings/urls.py

from django.urls import path
from .views import IntegrationBookingView

urlpatterns = [
    # -----------------------------------------------------------
    # 预约管理 (Bookings)
    # -----------------------------------------------------------
    # System A 请求地址: POST .../api/v1/integration/bookings/
    # 功能: 创建预约 / 查询预约列表
    path('bookings/', IntegrationBookingView.as_view(), name='integration-booking-list-create'),

    # System A 请求地址: DELETE/PATCH .../api/v1/integration/bookings/{uuid}/
    # 功能: 取消预约 / 完结订单
    path('bookings/<uuid:pk>/', IntegrationBookingView.as_view(), name='integration-booking-detail'),
]