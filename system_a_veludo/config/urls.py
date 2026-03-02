from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.generic import TemplateView
from django.views.static import serve

# [修正] 从 accounts app 的 views 中导入
from accounts.views import BookingPageView 
from core import views as core_views


urlpatterns = [
    path('admin/', admin.site.urls),
    
    # 首页及静态页面
    path('', core_views.index, name='index'),
    path('service/', TemplateView.as_view(template_name='service.html'), name='service'), 
    path('access/', TemplateView.as_view(template_name='access.html'), name='access'),
    path('core/', include('core.urls')),

    # 用户及预约相关
    path('accounts/', include('accounts.urls')), 
    path('accounts/', include('allauth.urls')), # 添加这一行，接管 /accounts/discord/login/

    path('booking/', BookingPageView.as_view(), name='booking_page'),


    path('casts/', include('casts.urls')),

]

# Local docker deployment: serve static/media directly even when DEBUG=False.
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    re_path(r"^static/(?P<path>.*)$", serve, {"document_root": settings.STATIC_ROOT}),
]
