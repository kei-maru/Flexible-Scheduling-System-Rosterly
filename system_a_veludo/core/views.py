from django.views.generic import TemplateView

# [新增] Service 页面
class ServicePageView(TemplateView):
    template_name = 'service.html'

# [新增] Access 页面
class AccessPageView(TemplateView):
    template_name = 'access.html'

import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from .models import UserActivity

@require_POST
def track_activity(request):
    """
    前端埋点数据接收接口
    URL: /core/api/track/
    """
    try:
        data = json.loads(request.body)
        action = data.get('action')
        target = data.get('target', '')
        meta = data.get('meta', {})

        # 获取 IP 地址
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        
        meta['ip'] = ip
        
        # 保存到数据库
        UserActivity.objects.create(
            user=request.user if request.user.is_authenticated else None,
            action=action,
            target=target,
            meta_data=meta
        )
        return JsonResponse({'status': 'success'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)