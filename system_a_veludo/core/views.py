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
from django.conf import settings
from django.core.cache import cache
from .models import UserActivity, BlockedIP
from casts.source import get_public_casts


def _get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return (request.META.get('REMOTE_ADDR') or '').strip()


def _is_whitelisted_ip(ip):
    whitelist = getattr(settings, 'TRACKING_IP_WHITELIST', ['127.0.0.1', '::1'])
    return ip in whitelist


def _is_blocked_ip(ip):
    if not ip:
        return False
    blocked = BlockedIP.objects.filter(ip=ip, is_active=True).first()
    return bool(blocked and blocked.is_currently_blocked)


def _should_autoban(ip):
    if not ip or _is_whitelisted_ip(ip):
        return False, 0

    window_seconds = int(getattr(settings, 'TRACKING_BOT_WINDOW_SECONDS', 600))
    threshold = int(getattr(settings, 'TRACKING_BOT_THRESHOLD', 300))
    key = f"tracking:ip:count:{ip}"

    added = cache.add(key, 1, timeout=window_seconds)
    if added:
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=window_seconds)
            count = 1

    return count >= threshold, count


def _block_ip(ip, hit_count):
    if not ip:
        return
    blocked_ip, created = BlockedIP.objects.get_or_create(
        ip=ip,
        defaults={
            'reason': 'Auto blocked by tracking anti-bot threshold',
            'is_active': True,
            'hit_count': hit_count,
        }
    )
    if not created:
        blocked_ip.is_active = True
        blocked_ip.reason = 'Auto blocked by tracking anti-bot threshold'
        blocked_ip.hit_count = max(blocked_ip.hit_count, hit_count)
        blocked_ip.save(update_fields=['is_active', 'reason', 'hit_count', 'last_detected_at'])

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

        ip = _get_client_ip(request)

        if _is_blocked_ip(ip):
            return JsonResponse({'status': 'blocked', 'message': 'IP blocked'}, status=403)

        should_ban, hit_count = _should_autoban(ip)
        if should_ban:
            _block_ip(ip, hit_count)
            return JsonResponse({'status': 'blocked', 'message': 'IP auto blocked as bot'}, status=403)
        
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
    casts = get_public_casts()
    
    context = {
        'casts': casts, # 把数据传给模板
    }
    
    return render(request, 'index.html', context)
