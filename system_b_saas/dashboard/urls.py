from django.urls import path
from django.contrib.auth import views as auth_views
from .views import TenantDashboardView, DashboardLoginView, DashboardTermsView
from .schedule_views import (
    DashboardScheduleAvailabilityApi,
    DashboardBookingActionApi,
    DashboardScheduleEventsApi,
    DashboardScheduleRecurringConfigApi,
    DashboardScheduleTemplateApi,
)

urlpatterns = [
    path('login/', DashboardLoginView.as_view(), name='dashboard_login'),
    path('terms/', DashboardTermsView.as_view(), name='dashboard_terms'),

    # 2. 登出 (可选)
    path('logout/', auth_views.LogoutView.as_view(next_page='dashboard_login'), name='logout'),

    path('', TenantDashboardView.as_view(), name='tenant_dashboard'),
    path('api/schedule/events/', DashboardScheduleEventsApi.as_view(), name='dashboard_schedule_events'),
    path('api/schedule/availability/', DashboardScheduleAvailabilityApi.as_view(), name='dashboard_schedule_availability'),
    path('api/schedule/recurring-config/', DashboardScheduleRecurringConfigApi.as_view(), name='dashboard_schedule_recurring_config'),
    path('api/schedule/templates/', DashboardScheduleTemplateApi.as_view(), name='dashboard_schedule_templates'),
    path('api/bookings/<uuid:booking_id>/', DashboardBookingActionApi.as_view(), name='dashboard_booking_action'),
]
