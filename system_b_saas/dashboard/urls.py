from django.urls import path
from django.contrib.auth import views as auth_views
from .views import TenantDashboardView
from django.conf.urls.static import static
from django.conf import settings

urlpatterns = [
    path('login/', auth_views.LoginView.as_view(
        template_name='dashboard/login.html', 
        redirect_authenticated_user=True # 如果已经登录了，访问登录页直接跳走
    ), name='login'),

    # 2. 登出 (可选)
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),

    path('', TenantDashboardView.as_view(), name='tenant_dashboard'),
]
