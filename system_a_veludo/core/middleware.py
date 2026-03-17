from django.http import JsonResponse, HttpResponseForbidden

from core.models import BlockedIP


class BlockBlockedIPMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = (request.META.get('REMOTE_ADDR') or '').strip()

        if ip:
            blocked = BlockedIP.objects.filter(ip=ip, is_active=True).first()
            if blocked and blocked.is_currently_blocked:
                if request.path.startswith('/core/api/track/') or request.path.startswith('/accounts/api/'):
                    return JsonResponse({'status': 'blocked', 'message': 'IP blocked'}, status=403)
                return HttpResponseForbidden('Forbidden')

        return self.get_response(request)
