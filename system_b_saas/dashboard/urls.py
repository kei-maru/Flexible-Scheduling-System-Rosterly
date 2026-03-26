from django.urls import path
from .views import (
    TenantDashboardView,
    DashboardLoginView,
    DashboardRegisterShopRedirectView,
    DashboardShopSignupFormView,
    DashboardInviteAcceptView,
    DashboardTermsView,
    DashboardPublicBookingView,
    DashboardPublicBookingAvailabilityApi,
    DashboardPublicBookingCreateApi,
    dashboard_logout,
)
from .schedule_views import (
    DashboardScheduleAvailabilityApi,
    DashboardBookingActionApi,
    DashboardScheduleEventsApi,
    DashboardScheduleRecurringConfigApi,
    DashboardScheduleTemplateApi,
)

urlpatterns = [
    path('login/', DashboardLoginView.as_view(), name='dashboard_login'),
    path('register-shop/', DashboardRegisterShopRedirectView.as_view(), name='dashboard_register_shop'),
    path('register-shop/form/', DashboardShopSignupFormView.as_view(), name='dashboard_register_shop_form'),
    path('invite/<str:token>/', DashboardInviteAcceptView.as_view(), name='dashboard_invite_accept'),
    path('terms/', DashboardTermsView.as_view(), name='dashboard_terms'),
    path('book/<slug:tenant_slug>/', DashboardPublicBookingView.as_view(), name='dashboard_public_booking'),
    path('book/<slug:tenant_slug>/api/availability/', DashboardPublicBookingAvailabilityApi.as_view(), name='dashboard_public_booking_availability'),
    path('book/<slug:tenant_slug>/api/create/', DashboardPublicBookingCreateApi.as_view(), name='dashboard_public_booking_create'),

    # 2. 登出
    path('logout/', dashboard_logout, name='logout'),

    path('', TenantDashboardView.as_view(), name='tenant_dashboard'),
    path('api/schedule/events/', DashboardScheduleEventsApi.as_view(), name='dashboard_schedule_events'),
    path('api/schedule/availability/', DashboardScheduleAvailabilityApi.as_view(), name='dashboard_schedule_availability'),
    path('api/schedule/recurring-config/', DashboardScheduleRecurringConfigApi.as_view(), name='dashboard_schedule_recurring_config'),
    path('api/schedule/templates/', DashboardScheduleTemplateApi.as_view(), name='dashboard_schedule_templates'),
    path('api/bookings/<uuid:booking_id>/', DashboardBookingActionApi.as_view(), name='dashboard_booking_action'),
]
