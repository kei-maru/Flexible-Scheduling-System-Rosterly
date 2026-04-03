# system_b/tenants/permissions.py
from rest_framework import permissions
from .models import Tenant

class IsTenantAuthorized(permissions.BasePermission):
    """
    架构文档 Source 147: Phase 1 检查 API Key
    验证 Header 中的 X-Tenant-Key 是否对应数据库中的 Tenant
    """
    def has_permission(self, request, view):
        # 1. 获取 Header 中的 Key
        api_key = request.headers.get('X-Tenant-Key') or request.headers.get('X-API-KEY')
        
        if not api_key:
            return False

        # 2. 查找对应的 Tenant
        try:
            tenant = Tenant.objects.get(api_key=api_key)
            if not getattr(tenant, 'is_api_enabled', True):
                return False
            # 将 tenant 对象绑定到 request 上，方便后续 View 使用
            request.tenant = tenant 
            return True
        except Tenant.DoesNotExist:
            return False