from django.urls import path
from django.contrib.auth import views as auth_views
from .views import TenantDashboardView, DashboardLoginView

urlpatterns = [
    path('login/', DashboardLoginView.as_view(), name='dashboard_login'),

    # 2. 登出 (可选)
    path('logout/', auth_views.LogoutView.as_view(next_page='dashboard_login'), name='logout'),

    path('', TenantDashboardView.as_view(), name='tenant_dashboard'),
]
