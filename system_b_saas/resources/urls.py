# system_b_saas/resources/urls.py

from django.urls import path
# 导入视图：确保三个核心 View 都被导入了
from .views import (
    IntegrationAvailabilityView, 
    IntegrationResourceView,
    RecurringConfigView
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
    path('availability/recurring-config/', RecurringConfigView.as_view()),

    # System A 请求地址: DELETE .../api/v1/integration/availability/{uuid}/
    # 含义: Cast 删除某一条特定的排班记录
    # 注意: 这里使用 <uuid:pk> 因为 System B 的 ID 是 UUID 格式
    path('availability/<uuid:pk>/', IntegrationAvailabilityView.as_view(), name='integration-availability-detail'),
    # -----------------------------------------------------------
    # 3. 资源同步 (Resources)
    # -----------------------------------------------------------
    # System A 请求地址: POST .../api/v1/integration/resources/
    # 含义: 
    #   - 当 System A 新注册一个 Cast 时，自动把名字和 ID 同步过来
    #   - 这样 System B 才知道“这个 ID=3 的人叫 Keimaru”
    path('resources/', IntegrationResourceView.as_view(), name='integration_resources'),
]