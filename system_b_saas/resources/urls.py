# system_b_saas/resources/urls.py

from django.urls import path
from .integration_views import (
    IntegrationAvailabilityView, 
    IntegrationResourceView,
    RecurringConfigView,
    ScheduleTemplateView
)

urlpatterns = [
    # -----------------------------------------------------------
    # 1. 排班管理 (Availability)
    # -----------------------------------------------------------
    path('availability/', IntegrationAvailabilityView.as_view(), name='integration-availability-list'),
    
    path('availability/recurring-config/', RecurringConfigView.as_view()),
    
    path('availability/<uuid:pk>/', IntegrationAvailabilityView.as_view(), name='integration-availability-detail'),
    
    # -----------------------------------------------------------
    # 2. 模版管理 (Schedule Templates)
    # -----------------------------------------------------------
    # 修正后：与 availability/ 保持同级，前端访问 /accounts/api/availability/templates/ 才能匹配上
    path('availability/templates/', ScheduleTemplateView.as_view(), name='schedule_templates'),

    # -----------------------------------------------------------
    # 3. 资源同步 (Resources)
    # -----------------------------------------------------------
    path('resources/', IntegrationResourceView.as_view(), name='integration_resources'),
    path('resources/<uuid:pk>/', IntegrationResourceView.as_view(), name='integration_resource_detail'),
]
