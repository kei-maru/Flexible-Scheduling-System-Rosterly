from django.views.generic import TemplateView

# [新增] Service 页面
class ServicePageView(TemplateView):
    template_name = 'service.html'

# [新增] Access 页面
class AccessPageView(TemplateView):
    template_name = 'access.html'