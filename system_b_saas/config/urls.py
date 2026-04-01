# system_b/config/urls.py
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve
from django.views.generic import RedirectView
from dashboard.schedule_views import SharedBookingListView, SharedHomeView, SharedProfileView, SharedScheduleView
from dashboard.views import LocalPasswordLoginView
from tenants.views import sso_authorize, sso_exchange, IntegrationIdentityView

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='dashboard_login', permanent=False), name='root_redirect'),
    path('admin/', admin.site.urls),
    path('login/', RedirectView.as_view(pattern_name='dashboard_login', permanent=False), name='login_alias'),
    path('sso/authorize', sso_authorize, name='sso_authorize'),
    path('api/v1/auth/sso/exchange', sso_exchange, name='sso_exchange'),
    path('sso/exchange', sso_exchange, name='sso_exchange_legacy'),
    path('api/v1/integration/identity', IntegrationIdentityView.as_view(), name='integration_identity'),
    
    # 架构文档 Source 169: Base URL: /api/v1/integration
    # 我们将 include 指向 resources.urls
    path('api/v1/integration/', include('resources.urls')),

    # 2. 指向 bookings (处理 bookings)
    # 注意：这里的前缀依然是 api/v1/integration/
    # 因为 bookings/urls.py 里面写的是 'bookings/'，所以拼接起来就是 api/v1/integration/bookings/
    path('api/v1/integration/', include('bookings.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('home/', SharedHomeView.as_view(), name='shared_home'),
    path('schedule/', SharedScheduleView.as_view(), name='shared_schedule'),
    path('profile/', SharedProfileView.as_view(), name='shared_profile'),
    path('bookings/', SharedBookingListView.as_view(), name='shared_bookings'),
    path('accounts/login/', LocalPasswordLoginView.as_view(), name='local_password_login'),
    path('accounts/', include('allauth.urls')),
]


urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    re_path(r"^static/(?P<path>.*)$", serve, {"document_root": settings.STATIC_ROOT}),
]
