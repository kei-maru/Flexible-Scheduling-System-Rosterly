from django.views.generic import TemplateView

# [新增] Service 页面
class ServicePageView(TemplateView):
    template_name = 'service.html'

# [新增] Access 页面
class AccessPageView(TemplateView):
    template_name = 'access.html'

import json
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from .models import UserActivity
from casts.models import CastProfile

@require_POST
def track_activity(request):
    """
    前端埋点数据接收接口
    URL: /core/api/track/
    """
    try:
        data = json.loads(request.body)
        
        # [修改] 增加默认值 'UNKNOWN_ACTION'，防止为空
        action = data.get('action', 'UNKNOWN_ACTION') 
        target = data.get('target', 'UNKNOWN_TARGET')
        meta = data.get('meta', {})

        # 获取 IP 地址
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        
        meta['ip'] = ip
        meta['user_agent'] = request.META.get('HTTP_USER_AGENT', '')
        
        # [新增] 打印调试信息到终端，方便你看有没有收到
        print(f"--- TRACKING --- User: {request.user}, Action: {action}, Target: {target}")

        # 保存到数据库
        UserActivity.objects.create(
            user=request.user if request.user.is_authenticated else None,
            action=action, # 确保这里存进去了
            target=target,
            meta_data=meta
        )
        return JsonResponse({'status': 'success'})
    except Exception as e:
        print(f"--- TRACKING ERROR --- {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

def index(request):
    """
    首页视图：负责渲染 index.html 并传递 Cast 数据给轮播图
    """
    # 1. 获取所有状态为“公开”的 Cast
    # 2. 按后台设定的 display_order 排序
    # 3. prefetch_related('medias') 是为了优化性能，因为模板里用到了 cast.medias.first
    casts = CastProfile.objects.filter(is_active=True).order_by('display_order').prefetch_related('medias')
    
    context = {
        'casts': casts, # 把数据传给模板
    }
    
    return render(request, 'index.html', context)
