from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView

# [修正] 从 accounts app 的 views 中导入
from accounts.views import BookingPageView 


urlpatterns = [
    path('admin/', admin.site.urls),
    
    # 首页及静态页面
    path('', TemplateView.as_view(template_name='index.html'), name='index'),
    path('service/', TemplateView.as_view(template_name='service.html'), name='service'), 
    path('access/', TemplateView.as_view(template_name='access.html'), name='access'),
    path('core/', include('core.urls')),

    # 用户及预约相关
    path('accounts/', include('accounts.urls')), 
    path('accounts/', include('allauth.urls')), # 添加这一行，接管 /accounts/discord/login/

    path('booking/', BookingPageView.as_view(), name='booking_page'),


    path('casts/', include('casts.urls')),

]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)