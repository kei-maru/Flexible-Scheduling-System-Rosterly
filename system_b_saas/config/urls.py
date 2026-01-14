# system_b/config/urls.py
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # 架构文档 Source 169: Base URL: /api/v1/integration
    # 我们将 include 指向 resources.urls
    path('api/v1/integration/', include('resources.urls')),
]