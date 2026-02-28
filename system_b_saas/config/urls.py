# system_b/config/urls.py
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # 架构文档 Source 169: Base URL: /api/v1/integration
    # 我们将 include 指向 resources.urls
    path('api/v1/integration/', include('resources.urls')),

    # 2. 指向 bookings (处理 bookings)
    # 注意：这里的前缀依然是 api/v1/integration/
    # 因为 bookings/urls.py 里面写的是 'bookings/'，所以拼接起来就是 api/v1/integration/bookings/
    path('api/v1/integration/', include('bookings.urls')),
    path('dashboard/', include('dashboard.urls')),
]


urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    re_path(r"^static/(?P<path>.*)$", serve, {"document_root": settings.STATIC_ROOT}),
]
