import hashlib
import hmac
import time

from django.conf import settings
from django.core.cache import cache
from django.utils.crypto import constant_time_compare
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

            ts_header_name = str(getattr(settings, 'SAAS_TIMESTAMP_HEADER', 'X-Tenant-Timestamp') or 'X-Tenant-Timestamp')
            sig_header_name = str(getattr(settings, 'SAAS_SIGNING_HEADER', 'X-Tenant-Signature') or 'X-Tenant-Signature')
            timestamp_header = (request.headers.get(ts_header_name) or '').strip()
            signature_header = (request.headers.get(sig_header_name) or '').strip()
            if not timestamp_header or not signature_header:
                return False

            try:
                req_ts = int(timestamp_header)
            except (TypeError, ValueError):
                return False

            now_ts = int(time.time())
            max_skew = int(getattr(settings, 'SAAS_SIGNATURE_MAX_SKEW_SECONDS', 300) or 300)
            if abs(now_ts - req_ts) > max_skew:
                return False

            api_secret = (getattr(tenant, 'api_secret', '') or '').strip()
            if not api_secret:
                return False

            method = (request.method or 'GET').upper()
            path_with_query = request.get_full_path()
            body_bytes = request.body or b''
            body_hash = hashlib.sha256(body_bytes).hexdigest()
            signing_payload = f"{method}\n{path_with_query}\n{timestamp_header}\n{body_hash}".encode('utf-8')
            expected_sig = hmac.new(api_secret.encode('utf-8'), signing_payload, hashlib.sha256).hexdigest()
            if not constant_time_compare(expected_sig, signature_header):
                return False

            replay_ttl = int(getattr(settings, 'SAAS_SIGNATURE_REPLAY_TTL_SECONDS', max_skew) or max_skew)
            replay_key = f"tenant_sig_replay:{tenant.id}:{signature_header}:{timestamp_header}"
            if not cache.add(replay_key, 1, timeout=max(1, replay_ttl)):
                return False

            # 将 tenant 对象绑定到 request 上，方便后续 View 使用
            request.tenant = tenant 
            return True
        except Tenant.DoesNotExist:
            return False