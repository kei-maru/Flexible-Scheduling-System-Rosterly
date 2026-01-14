# system_b_saas/resources/urls.py

from django.urls import path
# 导入视图：确保三个核心 View 都被导入了
from .views import (
    IntegrationAvailabilityView, 
    IntegrationBookingView, 
    IntegrationResourceView
)

urlpatterns = [
    # -----------------------------------------------------------
    # 1. 排班管理 (Availability)
    # -----------------------------------------------------------
    # System A 请求地址: GET/POST .../api/v1/integration/availability/
    # 含义: 
    #   - GET: System A 查询某位 Cast 的排班表 (用于日历显示)
    #   - POST: System A 的 Cast 设置自己的新排班
    path('availability/', IntegrationAvailabilityView.as_view(), name='integration-availability-list'),

    # System A 请求地址: DELETE .../api/v1/integration/availability/{uuid}/
    # 含义: Cast 删除某一条特定的排班记录
    # 注意: 这里使用 <uuid:pk> 因为 System B 的 ID 是 UUID 格式
    path('availability/<uuid:pk>/', IntegrationAvailabilityView.as_view(), name='integration-availability-detail'),


    # -----------------------------------------------------------
    # 2. 预约管理 (Bookings)
    # -----------------------------------------------------------
    # System A 请求地址: POST .../api/v1/integration/bookings/
    # 含义: 
    #   - POST: 客人提交预约申请 (System B 负责存入数据库、发邮件)
    #   - GET:  查询预约列表 (Cast看谁约了我，客人看我约了谁)
    path('bookings/', IntegrationBookingView.as_view(), name='integration-booking-create'),

    # System A 请求地址: DELETE/PATCH .../api/v1/integration/bookings/{uuid}/
    # 含义: 
    #   - DELETE: 取消预约 (System A 发起请求，System B 删库)
    #   - PATCH:  完结订单 (Cast 点击“完了报告”，更新状态为 COMPLETED)
    path('bookings/<uuid:pk>/', IntegrationBookingView.as_view(), name='booking-detail'),


    # -----------------------------------------------------------
    # 3. 资源同步 (Resources)
    # -----------------------------------------------------------
    # System A 请求地址: POST .../api/v1/integration/resources/
    # 含义: 
    #   - 当 System A 新注册一个 Cast 时，自动把名字和 ID 同步过来
    #   - 这样 System B 才知道“这个 ID=3 的人叫 Keimaru”
    path('resources/', IntegrationResourceView.as_view(), name='integration_resources'),
]